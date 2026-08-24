import json
from datetime import datetime, timezone
from uuid import uuid4

from google import genai
from google.cloud import firestore
from google.genai import types

from app.schemas import EvidenceImpactResult

PROJECT_ID = "readinessops-agent-governance"
LOCATION = "asia-northeast1"


def reassess_and_suspend(
    *,
    agent_id: str,
    revision_id: str,
) -> dict:
    """Reassess new evidence and fail-closed suspend a READY agent on material safety drift."""

    db = firestore.Client(project=PROJECT_ID)

    rev_ref = db.collection("revisions").document(revision_id)
    rev_snap = rev_ref.get()

    if not rev_snap.exists:
        raise ValueError("Revision not found.")

    rev = rev_snap.to_dict()

    agent_ref = db.collection("agents").document(agent_id)
    agent_snap = agent_ref.get()

    if not agent_snap.exists:
        raise ValueError("Agent not found.")

    agent = agent_snap.to_dict()

    readiness_before = agent.get("readiness_status")

    if readiness_before not in ("READY", "SUSPENDED"):
        return {
            "agent_id": agent_id,
            "revision_id": revision_id,
            "impact": "UNRESOLVED",
            "treatment": "NO_CHANGE",
            "material_change": False,
            "state_changed": False,
            "reason": (
                f"Reassessment recorded without readiness transition; "
                f"current_status={readiness_before}."
            ),
        }

    if rev.get("parent_revision_id") != agent.get("current_revision_id"):
        raise ValueError(
            "Draft parent does not match the agent's READY revision."
        )

    if rev.get("base_publication_id") != agent.get(
        "readiness_publication_id"
    ):
        raise ValueError(
            "Draft publication does not match the agent's READY publication."
        )

    evidence_id = rev["evidence_ids"][0]

    evidence_snap = (
        db.collection("evidence_items")
        .document(evidence_id)
        .get()
    )
    if not evidence_snap.exists:
        raise ValueError("Evidence item not found.")

    evidence = evidence_snap.to_dict()

    published_snap = (
        db.collection("published_records")
        .document(rev["base_publication_id"])
        .get()
    )
    if not published_snap.exists:
        raise ValueError("Published baseline not found.")

    published = published_snap.to_dict()

    before = published["proposal"]
    new_evidence = evidence["evidence_text"]

    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
    )

    prompt = f"""
You are the ReadinessOps Evidence Impact Agent.

Compare the PUBLISHED CURRENT state with the NEW EVIDENCE.

Rules:
- Published Current is BEFORE.
- New Evidence is the change trigger.
- Do not approve or publish.
- Do not invent facts or metrics.
- If a previously verified mandatory safety capability is lost or can
  no longer be verified, classify this as MATERIAL and recommend SUSPEND.
- Produce concise Before / After summaries.

PUBLISHED CURRENT:
{json.dumps(before, ensure_ascii=False)}

NEW EVIDENCE:
{new_evidence}
"""

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=EvidenceImpactResult,
        ),
    )

    impact = EvidenceImpactResult.model_validate_json(response.text)

    # Deterministic safety override.
    loss_terms = (
        "no longer",
        "removed",
        "revoked",
        "disabled",
        "not verified",
        "cannot be verified",
    )

    if any(term in new_evidence.lower() for term in loss_terms):
        impact.impact = "MATERIAL"
        impact.treatment = "SUSPEND"
        impact.material_change = True

    if not impact.material_change:
        now = datetime.now(timezone.utc)
        audit_id = f"AUDIT_{uuid4().hex[:16]}"

        batch = db.batch()
        batch.update(rev_ref, {
            "evidence_impact_status": "COMPLETED",
            "evidence_impact": impact.model_dump(),
            "material_change": False,
            "impact_completed_at": now,
        })
        batch.set(
            db.collection("audit_events").document(audit_id),
            {
                "audit_id": audit_id,
                "event_type": "EVIDENCE_IMPACT_COMPLETED",
                "agent_id": agent_id,
                "revision_id": revision_id,
                "evidence_id": evidence_id,
                "impact": impact.impact,
                "treatment": impact.treatment,
                "material_change": False,
                "actor_type": "AI",
                "actor": "evidence_impact_agent",
                "created_at": now,
            },
        )
        batch.commit()

        return {
            "agent_id": agent_id,
            "revision_id": revision_id,
            "evidence_id": evidence_id,
            "impact": impact.impact,
            "treatment": impact.treatment,
            "material_change": False,
            "state_changed": False,
            "audit_id": audit_id,
        }

    if impact.treatment != "SUSPEND":
        raise ValueError(
            "Material change established but SUSPEND treatment was not produced."
        )

    now = datetime.now(timezone.utc)

    impact_audit_id = f"AUDIT_{uuid4().hex[:16]}"
    suspend_audit_id = f"AUDIT_{uuid4().hex[:16]}"

    batch = db.batch()

    batch.update(rev_ref, {
        "evidence_impact_status": "COMPLETED",
        "evidence_impact": impact.model_dump(),
        "material_change": True,
        "impact_completed_at": now,
    })

    if readiness_before == "READY":
        batch.update(agent_ref, {
            "readiness_status": "SUSPENDED",
            "suspended_at": now,
            "suspension_reason": impact.rationale,
            "suspension_revision_id": revision_id,
            "suspension_evidence_id": evidence_id,
            "previous_readiness_status": "READY",
        })

    batch.set(
        db.collection("audit_events").document(impact_audit_id),
        {
            "audit_id": impact_audit_id,
            "event_type": "EVIDENCE_IMPACT_COMPLETED",
            "agent_id": agent_id,
            "revision_id": revision_id,
            "evidence_id": evidence_id,
            "impact": impact.impact,
            "treatment": impact.treatment,
            "material_change": True,
            "actor_type": "AI",
            "actor": "evidence_impact_agent",
            "created_at": now,
        },
    )

    batch.set(
        db.collection("audit_events").document(suspend_audit_id),
        {
            "audit_id": suspend_audit_id,
            "event_type": "AGENT_SAFETY_SUSPENDED",
            "agent_id": agent_id,
            "revision_id": revision_id,
            "evidence_id": evidence_id,
            "from_status": "READY",
            "to_status": "SUSPENDED",
            "reason": impact.rationale,
            "actor_type": "SYSTEM",
            "actor": "readinessops_reassessment_worker",
            "created_at": now,
        },
    )

    batch.commit()

    return {
        "agent_id": agent_id,
        "from_status": readiness_before,
        "to_status": (
            "SUSPENDED" if readiness_before == "READY"
            else readiness_before
        ),
        "revision_id": revision_id,
        "evidence_id": evidence_id,
        "impact": impact.impact,
        "treatment": impact.treatment,
        "confidence": impact.confidence,
        "material_change": True,
        "before_summary": impact.before_summary,
        "after_summary": impact.after_summary,
        "current_published_state_changed": False,
        "impact_audit_id": impact_audit_id,
        "suspend_audit_id": suspend_audit_id,
    }
