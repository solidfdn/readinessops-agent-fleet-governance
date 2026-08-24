import base64
import hashlib
import json
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from google.cloud import firestore, storage

from app.reassessment import reassess_and_suspend
from app.revision import create_draft_revision

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
        receipt_snap = receipt_ref.get()

        if receipt_snap.exists:
            receipt = receipt_snap.to_dict()

            if receipt.get("status") == "COMPLETED":
                return jsonify({
                    "status": "DUPLICATE_IGNORED",
                    "event_id": event_id,
                    "revision_id": receipt.get("revision_id"),
                }), 200

            revision_id = receipt.get("revision_id")
        else:
            revision_id = None
            receipt_ref.set({
                "event_id": event_id,
                "event_key": event_key,
                "status": "PROCESSING",
                "bucket": bucket,
                "object_id": object_id,
                "object_generation": generation,
                "pubsub_message_id": pubsub_message_id,
                "agent_id": agent_id,
                "created_at": datetime.now(timezone.utc),
            })

        if not revision_id:
            blob = storage.Client(project=PROJECT_ID).bucket(bucket).blob(
                object_id,
                generation=int(generation),
            )

            evidence_text = blob.download_as_text()

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

        result = reassess_and_suspend(
            agent_id=agent_id,
            revision_id=revision_id,
        )

        receipt_ref.update({
            "status": "COMPLETED",
            "result": result,
            "completed_at": datetime.now(timezone.utc),
        })

        return jsonify({
            "status": "COMPLETED",
            "event_id": event_id,
            "result": result,
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
