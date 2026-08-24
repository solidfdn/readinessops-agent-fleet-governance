import base64
import hashlib
import json
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from google.cloud import firestore, storage

from app.grounding import validate_grounding_claims
from app.persistence import persist_reassessment_proposal
from app.model_armor import sanitize_evidence
from app.reassessment import reassess_and_suspend
from app.revision import create_draft_revision
from app.runtime_client import run_governance_reassessment
from app.schemas import GovernedAssessmentProposal

PROJECT_ID = "readinessops-agent-governance"

app = Flask(__name__)


def _db():
    return firestore.Client(project=PROJECT_ID)


def _event_key(bucket: str, object_id: str, generation: str) -> str:
    raw = f"{bucket}|{object_id}|{generation}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@app.get("/")
def health():
    return {"status": "ok", "service": "readinessops-evidence-worker"}


@app.post("/")
def pubsub_push():
    envelope = request.get_json(silent=True) or {}
    message = envelope.get("message") or {}
    attributes = message.get("attributes") or {}

    if attributes.get("eventType") != "OBJECT_FINALIZE":
        return jsonify({"status": "IGNORED", "reason": "not OBJECT_FINALIZE"}), 200

    bucket = attributes.get("bucketId")
    object_id = attributes.get("objectId")
    generation = attributes.get("objectGeneration")
    pubsub_message_id = message.get("messageId")

    if not bucket or not object_id or not generation:
        return jsonify({"status": "INVALID_EVENT"}), 400

    parts = object_id.split("/", 1)
    if len(parts) != 2:
        return jsonify({
            "status": "IGNORED",
            "reason": "object path must start with agent_id/"
        }), 200

    agent_id = parts[0]
    event_key = _event_key(bucket, object_id, generation)
    event_id = f"GCS_{event_key[:24]}"

    db = _db()
    receipt_ref = db.collection("event_receipts").document(event_key)

    try:
        transaction = db.transaction()

        @firestore.transactional
        def claim_event(transaction):
            receipt_snap = receipt_ref.get(transaction=transaction)

            if receipt_snap.exists:
                return receipt_snap.to_dict()

            receipt = {
                "event_id": event_id,
                "event_key": event_key,
                "status": "PROCESSING",
                "bucket": bucket,
                "object_id": object_id,
                "object_generation": generation,
                "pubsub_message_id": pubsub_message_id,
                "agent_id": agent_id,
                "created_at": datetime.now(timezone.utc),
            }
            transaction.set(receipt_ref, receipt)
            return receipt

        receipt = claim_event(transaction)

        if receipt.get("status") in ("COMPLETED", "BLOCKED"):
            return jsonify({
                "status": "DUPLICATE_IGNORED",
                "event_id": event_id,
                "revision_id": receipt.get("revision_id"),
            }), 200

        revision_id = receipt.get("revision_id")

        if not revision_id:
            blob = storage.Client(project=PROJECT_ID).bucket(bucket).blob(
                object_id,
                generation=int(generation),
            )

            evidence_text = blob.download_as_text()

            # Security gate BEFORE Revision / Gemini / Proposal creation.
            armor = sanitize_evidence(evidence_text)

            if armor["blocked"]:
                now = datetime.now(timezone.utc)
                audit_id = (
                    "AUDIT_SECURITY_"
                    + hashlib.sha256(
                        f"{event_id}|MODEL_ARMOR_BLOCK".encode("utf-8")
                    ).hexdigest()[:16]
                )

                batch = db.batch()

                batch.set(
                    db.collection("audit_events").document(audit_id),
                    {
                        "audit_id": audit_id,
                        "event_type": "MODEL_ARMOR_INPUT_BLOCKED",
                        "event_id": event_id,
                        "agent_id": agent_id,
                        "source_uri": f"gs://{bucket}/{object_id}",
                        "object_generation": generation,
                        "model_armor_template": armor["template"],
                        "filter_match_state": armor["filter_match_state"],
                        "pi_match_state": armor["pi_match_state"],
                        "confidence_level": armor["confidence_level"],
                        "invocation_result": armor["invocation_result"],
                        "actor_type": "SYSTEM",
                        "actor": "readinessops_evidence_worker",
                        "created_at": now,
                    },
                )

                batch.update(
                    receipt_ref,
                    {
                        "status": "BLOCKED",
                        "security_status": "MODEL_ARMOR_BLOCKED",
                        "model_armor": armor,
                        "audit_id": audit_id,
                        "trace_id": event_id,
                        "last_error": None,
                        "completed_at": now,
                    },
                )

                batch.commit()

                return jsonify({
                    "status": "BLOCKED",
                    "event_id": event_id,
                    "trace_id": event_id,
                    "security_control": "MODEL_ARMOR",
                }), 200

            agent_snap = db.collection("agents").document(agent_id).get()

            if not agent_snap.exists:
                raise ValueError(f"Agent not found: {agent_id}")

            agent = agent_snap.to_dict()
            target_agent = agent.get("target_agent")

            if not target_agent:
                raise ValueError("Agent target_agent is missing.")

            draft = create_draft_revision(
                target_agent=target_agent,
                evidence_text=evidence_text,
                source_label=object_id,
                actor="readinessops_evidence_worker",
                actor_type="SYSTEM",
                source_uri=f"gs://{bucket}/{object_id}",
                source_event_id=event_id,
                object_generation=generation,
            )

            revision_id = draft["revision_id"]

            receipt_ref.update({
                "revision_id": revision_id,
                "evidence_id": draft["evidence_id"],
                "draft_created_at": datetime.now(timezone.utc),
            })

        impact_result = reassess_and_suspend(
            agent_id=agent_id,
            revision_id=revision_id,
        )

        # Re-read the governed revision after Evidence Impact persistence.
        revision = (
            db.collection("revisions")
            .document(revision_id)
            .get()
            .to_dict()
        )

        evidence_id = revision["evidence_ids"][0]

        evidence = (
            db.collection("evidence_items")
            .document(evidence_id)
            .get()
            .to_dict()
        )

        published = (
            db.collection("published_records")
            .document(revision["base_publication_id"])
            .get()
            .to_dict()
        )

        if not published:
            raise ValueError("Published baseline is missing.")

        proposal_dict = run_governance_reassessment(
            target_agent=revision["target_agent"],
            published_current=published["proposal"],
            new_evidence=evidence["evidence_text"],
            evidence_impact=revision.get("evidence_impact") or impact_result,
            source_event_id=event_id,
        )

        # Fixed schema validation — application side.
        proposal = GovernedAssessmentProposal.model_validate(
            proposal_dict
        )

        # Deterministic grounding validation — model cannot self-certify.
        grounding = json.loads(
            validate_grounding_claims(
                evidence=evidence["evidence_text"],
                draft=json.dumps(
                    proposal_dict,
                    ensure_ascii=False,
                ),
            )
        )

        if grounding["status"] != "PASS":
            raise ValueError(
                "Grounding validation failed: "
                + "; ".join(grounding["issues"])
            )

        proposal.grounding_status = "PASS"
        proposal.grounding_issues = grounding["issues"]

        proposal_result = persist_reassessment_proposal(
            proposal=proposal,
            source_evidence=evidence["evidence_text"],
            revision_id=revision_id,
            evidence_id=evidence_id,
            source_event_id=event_id,
            trace_id=event_id,
        )

        receipt_ref.update({
            "status": "COMPLETED",
            "result": impact_result,
            "proposal_id": proposal_result["proposal_id"],
            "run_id": proposal_result["run_id"],
            "agent_run_id": proposal_result["agent_run_id"],
            "trace_id": event_id,
            "last_error": None,
            "completed_at": datetime.now(timezone.utc),
        })

        return jsonify({
            "status": "COMPLETED",
            "event_id": event_id,
            "trace_id": event_id,
            "impact": impact_result,
            "proposal": proposal_result,
        }), 200

    except Exception as exc:
        receipt_ref.set({
            "status": "FAILED",
            "last_error": str(exc)[:2000],
            "failed_at": datetime.now(timezone.utc),
        }, merge=True)

        return jsonify({
            "status": "FAILED",
            "event_id": event_id,
            "error": str(exc),
        }), 500
