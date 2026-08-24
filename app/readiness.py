from datetime import datetime, timezone
from uuid import uuid4

from google.cloud import firestore

PROJECT_ID = "readinessops-agent-governance"


def _db():
    return firestore.Client(project=PROJECT_ID)


def activate_ready_state(
    agent_id: str,
    target_agent: str,
    publication_id: str,
    revision_id: str,
    actor: str,
    basis: str,
) -> dict:
    db = _db()
    now = datetime.now(timezone.utc)

    pub = db.collection("published_records").document(publication_id).get()
    if not pub.exists:
        raise ValueError("Published record does not exist.")

    pub_data = pub.to_dict()

    if pub_data["revision_id"] != revision_id:
        raise ValueError("Publication / Revision mismatch.")

    audit_id = f"AUDIT_{uuid4().hex[:16]}"

    batch = db.batch()

    agent_ref = db.collection("agents").document(agent_id)

    batch.update(agent_ref, {
        "readiness_status": "READY",
        "current_revision_id": revision_id,
        "readiness_publication_id": publication_id,
        "readiness_basis": basis,
        "readiness_activated_by": actor,
        "readiness_activated_at": now,
    })

    batch.set(db.collection("audit_events").document(audit_id), {
        "audit_id": audit_id,
        "event_type": "AGENT_READINESS_ACTIVATED",
        "agent_id": agent_id,
        "target_agent": target_agent,
        "from_status": "DISCOVERED",
        "to_status": "READY",
        "publication_id": publication_id,
        "revision_id": revision_id,
        "basis": basis,
        "actor_type": "HUMAN",
        "actor": actor,
        "created_at": now,
    })

    batch.commit()

    return {
        "agent_id": agent_id,
        "from_status": "DISCOVERED",
        "to_status": "READY",
        "publication_id": publication_id,
        "revision_id": revision_id,
        "audit_id": audit_id,
    }
