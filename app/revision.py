from datetime import datetime, timezone
from uuid import uuid4
import re

from google.cloud import firestore

PROJECT_ID = "readinessops-agent-governance"


def _db():
    return firestore.Client(project=PROJECT_ID)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def create_draft_revision(
    target_agent: str,
    evidence_text: str,
    source_label: str,
    actor: str,
    *,
    actor_type: str = "HUMAN",
    source_uri: str | None = None,
    source_event_id: str | None = None,
    object_generation: str | None = None,
) -> dict:
    db = _db()
    now = datetime.now(timezone.utc)

    current_ref = db.collection("current_states").document(_slug(target_agent))
    current = current_ref.get()

    if not current.exists:
        raise ValueError("Published Current does not exist.")

    current_data = current.to_dict()

    evidence_id = _id("EVID")
    revision_id = _id("REV")
    audit_id = _id("AUDIT")

    batch = db.batch()

    batch.set(db.collection("evidence_items").document(evidence_id), {
        "evidence_id": evidence_id,
        "target_agent": target_agent,
        "source_label": source_label,
        "evidence_text": evidence_text,
        "evidence_status": "ADDED",
        "source_uri": source_uri,
        "source_event_id": source_event_id,
        "object_generation": object_generation,
        "created_by": actor,
        "created_by_type": actor_type,
        "created_at": now,
    })

    batch.set(db.collection("revisions").document(revision_id), {
        "revision_id": revision_id,
        "target_agent": target_agent,
        "status": "DRAFT",
        "is_current": False,
        "parent_revision_id": current_data["revision_id"],
        "base_publication_id": current_data["publication_id"],
        "base_proposal_id": current_data["proposal_id"],
        "evidence_ids": [evidence_id],
        "evidence_impact_status": "PENDING",
        "created_by": actor,
        "created_at": now,
    })

    batch.set(db.collection("audit_events").document(audit_id), {
        "audit_id": audit_id,
        "event_type": "DRAFT_REVISION_CREATED",
        "target_agent": target_agent,
        "revision_id": revision_id,
        "parent_revision_id": current_data["revision_id"],
        "evidence_id": evidence_id,
        "actor_type": actor_type,
        "actor": actor,
        "created_at": now,
    })

    batch.commit()

    return {
        "target_agent": target_agent,
        "evidence_id": evidence_id,
        "evidence_status": "ADDED",
        "revision_id": revision_id,
        "revision_status": "DRAFT",
        "parent_revision_id": current_data["revision_id"],
        "base_publication_id": current_data["publication_id"],
        "evidence_impact_status": "PENDING",
        "current_changed": False,
        "audit_id": audit_id,
    }
