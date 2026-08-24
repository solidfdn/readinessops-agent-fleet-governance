from datetime import datetime, timezone
from uuid import uuid4
import re

from google.cloud import firestore

from .schemas import GovernedAssessmentProposal


PROJECT_ID = "readinessops-agent-governance"


def _db():
    return firestore.Client(project=PROJECT_ID)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def get_proposal(proposal_id: str) -> dict:
    snap = _db().collection("proposals").document(proposal_id).get()
    if not snap.exists:
        raise ValueError(f"Proposal not found: {proposal_id}")
    return snap.to_dict()


def get_current_state(target_agent: str) -> dict | None:
    snap = _db().collection("current_states").document(_slug(target_agent)).get()
    return snap.to_dict() if snap.exists else None


def review_and_edit_proposal(
    proposal_id: str,
    actor: str,
    comment: str,
    add_unresolved: str | None = None,
) -> dict:
    db = _db()
    proposal_ref = db.collection("proposals").document(proposal_id)
    audit_ref = db.collection("audit_events").document(_id("AUDIT"))
    transaction = db.transaction()
    now = datetime.now(timezone.utc)

    @firestore.transactional
    def apply(transaction):
        snap = proposal_ref.get(transaction=transaction)
        if not snap.exists:
            raise ValueError("Proposal not found.")

        record = snap.to_dict()

        if record["proposal_status"] != "REVIEW_REQUIRED":
            raise ValueError("Only REVIEW_REQUIRED proposals can be reviewed.")

        if record["publication_status"] != "NOT_PUBLISHED":
            raise ValueError("Published proposal cannot be edited.")

        original = record["proposal"]
        edited = dict(original)

        if add_unresolved:
            edited["unresolved"] = list(edited.get("unresolved", []))
            edited["unresolved"].append(add_unresolved)

        validated = GovernedAssessmentProposal.model_validate(edited)

        updates = {
            "proposal": validated.model_dump(),
            "human_edited": bool(add_unresolved),
            "reviewed_by": actor,
            "reviewed_at": now,
            "review_comment": comment,
            "updated_at": now,
        }

        if "original_ai_proposal" not in record:
            updates["original_ai_proposal"] = original

        transaction.update(proposal_ref, updates)

        transaction.set(audit_ref, {
            "audit_id": audit_ref.id,
            "event_type": (
                "HUMAN_EDIT_RECORDED"
                if add_unresolved
                else "HUMAN_REVIEW_RECORDED"
            ),
            "proposal_id": proposal_id,
            "run_id": record["run_id"],
            "revision_id": record["revision_id"],
            "actor_type": "HUMAN",
            "actor": actor,
            "comment": comment,
            "created_at": now,
        })

        return {
            "proposal_id": proposal_id,
            "proposal_status": "REVIEW_REQUIRED",
            "publication_status": "NOT_PUBLISHED",
            "reviewed_by": actor,
            "human_edited": bool(add_unresolved),
        }

    return apply(transaction)


def approve_proposal(proposal_id: str, actor: str, comment: str) -> dict:
    db = _db()
    proposal_ref = db.collection("proposals").document(proposal_id)
    audit_ref = db.collection("audit_events").document(_id("AUDIT"))
    transaction = db.transaction()
    now = datetime.now(timezone.utc)

    @firestore.transactional
    def apply(transaction):
        snap = proposal_ref.get(transaction=transaction)
        if not snap.exists:
            raise ValueError("Proposal not found.")

        record = snap.to_dict()

        if record["proposal_status"] != "REVIEW_REQUIRED":
            raise ValueError("Proposal is not REVIEW_REQUIRED.")

        if record["publication_status"] != "NOT_PUBLISHED":
            raise ValueError("Proposal is already published.")

        if record["grounding_status"] != "PASS":
            raise ValueError("Grounding validation has not passed.")

        transaction.update(proposal_ref, {
            "proposal_status": "APPROVED",
            "approved_by": actor,
            "approved_at": now,
            "approval_comment": comment,
            "updated_at": now,
        })

        transaction.update(
            db.collection("assessment_runs").document(record["run_id"]),
            {"status": "APPROVED"},
        )

        transaction.set(audit_ref, {
            "audit_id": audit_ref.id,
            "event_type": "PROPOSAL_APPROVED",
            "proposal_id": proposal_id,
            "run_id": record["run_id"],
            "revision_id": record["revision_id"],
            "actor_type": "HUMAN",
            "actor": actor,
            "comment": comment,
            "created_at": now,
        })

        return {
            "proposal_id": proposal_id,
            "proposal_status": "APPROVED",
            "publication_status": "NOT_PUBLISHED",
            "approved_by": actor,
        }

    return apply(transaction)


def reject_proposal(proposal_id: str, actor: str, reason: str) -> dict:
    db = _db()
    proposal_ref = db.collection("proposals").document(proposal_id)
    audit_ref = db.collection("audit_events").document(_id("AUDIT"))
    transaction = db.transaction()
    now = datetime.now(timezone.utc)

    @firestore.transactional
    def apply(transaction):
        snap = proposal_ref.get(transaction=transaction)
        if not snap.exists:
            raise ValueError("Proposal not found.")

        record = snap.to_dict()

        if record["proposal_status"] != "REVIEW_REQUIRED":
            raise ValueError("Only REVIEW_REQUIRED proposals can be rejected.")

        transaction.update(proposal_ref, {
            "proposal_status": "REJECTED",
            "rejected_by": actor,
            "rejected_at": now,
            "rejection_reason": reason,
            "updated_at": now,
        })

        transaction.set(audit_ref, {
            "audit_id": audit_ref.id,
            "event_type": "PROPOSAL_REJECTED",
            "proposal_id": proposal_id,
            "run_id": record["run_id"],
            "revision_id": record["revision_id"],
            "actor_type": "HUMAN",
            "actor": actor,
            "reason": reason,
            "created_at": now,
        })

        return {
            "proposal_id": proposal_id,
            "proposal_status": "REJECTED",
            "publication_status": "NOT_PUBLISHED",
        }

    return apply(transaction)


def publish_proposal(proposal_id: str, actor: str, comment: str) -> dict:
    db = _db()
    proposal_ref = db.collection("proposals").document(proposal_id)
    transaction = db.transaction()

    publication_id = _id("PUB")
    audit_id = _id("AUDIT")
    now = datetime.now(timezone.utc)

    @firestore.transactional
    def apply(transaction):
        snap = proposal_ref.get(transaction=transaction)
        if not snap.exists:
            raise ValueError("Proposal not found.")

        record = snap.to_dict()

        if record["proposal_status"] != "APPROVED":
            raise ValueError("Only APPROVED proposals can be published.")

        if record["publication_status"] != "NOT_PUBLISHED":
            raise ValueError("Proposal has already been published.")

        target_agent = record["target_agent"]

        published_ref = db.collection("published_records").document(publication_id)
        revision_ref = db.collection("revisions").document(record["revision_id"])
        current_ref = db.collection("current_states").document(_slug(target_agent))
        audit_ref = db.collection("audit_events").document(audit_id)

        transaction.set(published_ref, {
            "publication_id": publication_id,
            "proposal_id": proposal_id,
            "run_id": record["run_id"],
            "agent_run_id": record["agent_run_id"],
            "revision_id": record["revision_id"],
            "target_agent": target_agent,
            "proposal": record["proposal"],
            "approved_by": record["approved_by"],
            "approved_at": record["approved_at"],
            "published_by": actor,
            "published_at": now,
            "publication_comment": comment,
        })

        transaction.update(proposal_ref, {
            "proposal_status": "PUBLISHED",
            "publication_status": "PUBLISHED",
            "publication_id": publication_id,
            "published_by": actor,
            "published_at": now,
            "updated_at": now,
        })

        transaction.update(revision_ref, {
            "status": "PUBLISHED",
            "publication_id": publication_id,
            "published_by": actor,
            "published_at": now,
        })

        transaction.set(current_ref, {
            "target_agent": target_agent,
            "publication_id": publication_id,
            "proposal_id": proposal_id,
            "run_id": record["run_id"],
            "revision_id": record["revision_id"],
            "updated_at": now,
        })

        transaction.set(audit_ref, {
            "audit_id": audit_id,
            "event_type": "PROPOSAL_PUBLISHED",
            "proposal_id": proposal_id,
            "publication_id": publication_id,
            "run_id": record["run_id"],
            "revision_id": record["revision_id"],
            "actor_type": "HUMAN",
            "actor": actor,
            "comment": comment,
            "created_at": now,
        })

        return {
            "proposal_id": proposal_id,
            "proposal_status": "PUBLISHED",
            "publication_status": "PUBLISHED",
            "publication_id": publication_id,
            "current_target_agent": target_agent,
            "published_by": actor,
        }

    return apply(transaction)
