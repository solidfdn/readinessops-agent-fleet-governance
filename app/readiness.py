from datetime import datetime, timezone
from uuid import uuid4
import re

from google.cloud import firestore
from google.cloud.firestore_v1 import DELETE_FIELD

PROJECT_ID = "readinessops-agent-governance"


def _db():
    return firestore.Client(project=PROJECT_ID)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


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

    publication_ref = (
        db.collection("published_records").document(publication_id)
    )
    agent_ref = db.collection("agents").document(agent_id)
    active_boundary_ref = (
        db.collection("active_delegation_boundaries")
        .document(_slug(target_agent))
    )
    audit_ref = (
        db.collection("audit_events")
        .document(f"AUDIT_{uuid4().hex[:16]}")
    )

    transaction = db.transaction()

    @firestore.transactional
    def apply(transaction):
        publication_snap = publication_ref.get(transaction=transaction)
        agent_snap = agent_ref.get(transaction=transaction)
        active_boundary_snap = active_boundary_ref.get(
            transaction=transaction
        )

        if not publication_snap.exists:
            raise ValueError("Published record does not exist.")

        if not agent_snap.exists:
            raise ValueError("Agent does not exist.")

        if not active_boundary_snap.exists:
            raise ValueError(
                "No active Delegation Boundary exists for target agent."
            )

        publication = publication_snap.to_dict()
        agent = agent_snap.to_dict()
        active_boundary = active_boundary_snap.to_dict()

        if publication["revision_id"] != revision_id:
            raise ValueError("Publication / Revision mismatch.")

        if active_boundary["publication_id"] != publication_id:
            raise ValueError(
                "Active Delegation Boundary / Publication mismatch."
            )

        if active_boundary["revision_id"] != revision_id:
            raise ValueError(
                "Active Delegation Boundary / Revision mismatch."
            )

        boundary_id = active_boundary["boundary_id"]
        boundary_ref = (
            db.collection("delegation_boundaries").document(boundary_id)
        )
        boundary_snap = boundary_ref.get(transaction=transaction)

        if not boundary_snap.exists:
            raise ValueError("Active Delegation Boundary record is missing.")

        boundary = boundary_snap.to_dict()

        if boundary.get("status") != "ACTIVE":
            raise ValueError("Delegation Boundary is not ACTIVE.")

        if boundary.get("target_agent") != target_agent:
            raise ValueError("Delegation Boundary target mismatch.")

        from_status = agent.get("readiness_status", "UNKNOWN")

        transaction.update(agent_ref, {
            "target_agent": target_agent,
            "readiness_status": "READY",
            "current_revision_id": revision_id,
            "readiness_publication_id": publication_id,
            "active_delegation_boundary_id": boundary_id,
            "active_delegation_boundary_version": active_boundary["version"],
            "readiness_basis": basis,
            "readiness_activated_by": actor,
            "readiness_activated_at": now,
            "suspended_at": DELETE_FIELD,
            "suspension_reason": DELETE_FIELD,
            "suspension_revision_id": DELETE_FIELD,
            "suspension_evidence_id": DELETE_FIELD,
        })

        transaction.set(audit_ref, {
            "audit_id": audit_ref.id,
            "event_type": "AGENT_READINESS_ACTIVATED",
            "agent_id": agent_id,
            "target_agent": target_agent,
            "from_status": from_status,
            "to_status": "READY",
            "publication_id": publication_id,
            "revision_id": revision_id,
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": active_boundary["version"],
            "basis": basis,
            "actor_type": "HUMAN",
            "actor": actor,
            "created_at": now,
        })

        return {
            "agent_id": agent_id,
            "from_status": from_status,
            "to_status": "READY",
            "publication_id": publication_id,
            "revision_id": revision_id,
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": active_boundary["version"],
            "audit_id": audit_ref.id,
        }

    return apply(transaction)
