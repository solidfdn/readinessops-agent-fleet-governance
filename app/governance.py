from datetime import datetime, timezone
from uuid import uuid4
import re
import hashlib
import json

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
            "trace_id": record.get("trace_id"),
            "trace_revision_id": record["revision_id"],
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



def edit_delegation_boundary(
    proposal_id: str,
    actor: str,
    comment: str,
    *,
    permitted_actions: list[str],
    human_review_required_actions: list[str],
    permitted_tools: list[str],
    permitted_data_classes: list[str],
    permitted_case_impact: list[str],
    permitted_skills: list[str] | None = None,
    mandatory_human_review_conditions: list[str] | None = None,
    prohibited_actions: list[str] | None = None,
) -> dict:
    """Human-controlled edit of the proposed Delegation Boundary."""
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
            raise ValueError(
                "Delegation Boundary can only be edited while REVIEW_REQUIRED."
            )

        if record["publication_status"] != "NOT_PUBLISHED":
            raise ValueError("Published proposal cannot be edited.")

        original = record["proposal"]
        edited = dict(original)
        edited_boundary = dict(
            edited.get("delegation_boundary") or {}
        )

        edited_boundary["status"] = "PROPOSED"
        edited_boundary["permitted_actions"] = list(permitted_actions)
        edited_boundary["human_review_required_actions"] = list(
            human_review_required_actions
        )
        edited_boundary["permitted_tools"] = list(permitted_tools)
        edited_boundary["permitted_data_classes"] = list(
            permitted_data_classes
        )
        edited_boundary["permitted_case_impact"] = list(
            permitted_case_impact
        )

        if permitted_skills is not None:
            edited_boundary["permitted_skills"] = list(permitted_skills)

        if mandatory_human_review_conditions is not None:
            edited_boundary["mandatory_human_review_conditions"] = list(
                mandatory_human_review_conditions
            )

        if prohibited_actions is not None:
            edited_boundary["prohibited_actions"] = list(
                prohibited_actions
            )

        edited["delegation_boundary"] = edited_boundary

        validated = GovernedAssessmentProposal.model_validate(edited)

        updates = {
            "proposal": validated.model_dump(),
            "human_edited": True,
            "boundary_edited_by": actor,
            "boundary_edited_at": now,
            "boundary_edit_comment": comment,
            "updated_at": now,
        }

        if "original_ai_proposal" not in record:
            updates["original_ai_proposal"] = original

        transaction.update(proposal_ref, updates)

        transaction.set(audit_ref, {
            "audit_id": audit_ref.id,
            "event_type": "DELEGATION_BOUNDARY_EDITED",
            "proposal_id": proposal_id,
            "run_id": record["run_id"],
            "revision_id": record["revision_id"],
            "trace_id": record.get("trace_id"),
            "trace_revision_id": record["revision_id"],
            "actor_type": "HUMAN",
            "actor": actor,
            "comment": comment,
            "permitted_actions": list(permitted_actions),
            "permitted_tools": list(permitted_tools),
            "created_at": now,
        })

        return {
            "proposal_id": proposal_id,
            "proposal_status": "REVIEW_REQUIRED",
            "publication_status": "NOT_PUBLISHED",
            "boundary_edited_by": actor,
            "permitted_actions": list(permitted_actions),
            "permitted_tools": list(permitted_tools),
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
            "trace_id": record.get("trace_id"),
            "trace_revision_id": record["revision_id"],
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
            "trace_id": record.get("trace_id"),
            "trace_revision_id": record["revision_id"],
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
    boundary_id = _id("BOUNDARY")
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
        target_slug = _slug(target_agent)

        active_boundary_ref = (
            db.collection("active_delegation_boundaries").document(target_slug)
        )

        # Read before any transaction writes.
        active_boundary_snap = active_boundary_ref.get(transaction=transaction)

        previous_boundary_id = None
        previous_version = 0

        if active_boundary_snap.exists:
            active_data = active_boundary_snap.to_dict()
            previous_boundary_id = active_data.get("boundary_id")
            previous_version = int(active_data.get("version", 0))

        boundary_version = previous_version + 1

        proposed_boundary = dict(
            record["proposal"].get("delegation_boundary") or {}
        )

        boundary_policy = {
            "permitted_actions": list(
                proposed_boundary.get("permitted_actions", [])
            ),
            "human_review_required_actions": list(
                proposed_boundary.get(
                    "human_review_required_actions", []
                )
            ),
            "permitted_skills": list(
                proposed_boundary.get("permitted_skills", [])
            ),
            "permitted_tools": list(
                proposed_boundary.get("permitted_tools", [])
            ),
            "permitted_data_classes": list(
                proposed_boundary.get("permitted_data_classes", [])
            ),
            "permitted_case_impact": list(
                proposed_boundary.get("permitted_case_impact", [])
            ),
            "mandatory_human_review_conditions": list(
                proposed_boundary.get(
                    "mandatory_human_review_conditions", []
                )
            ),
            "prohibited_actions": list(
                proposed_boundary.get("prohibited_actions", [])
            ),
        }

        content_hash = hashlib.sha256(
            json.dumps(
                boundary_policy,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

        published_ref = (
            db.collection("published_records").document(publication_id)
        )
        revision_ref = (
            db.collection("revisions").document(record["revision_id"])
        )
        current_ref = (
            db.collection("current_states").document(target_slug)
        )
        boundary_ref = (
            db.collection("delegation_boundaries").document(boundary_id)
        )
        audit_ref = db.collection("audit_events").document(audit_id)

        if previous_boundary_id:
            previous_boundary_ref = (
                db.collection("delegation_boundaries")
                .document(previous_boundary_id)
            )
            transaction.update(previous_boundary_ref, {
                "status": "SUPERSEDED",
                "superseded_by": boundary_id,
                "superseded_at": now,
            })

        transaction.set(boundary_ref, {
            "boundary_id": boundary_id,
            "target_agent": target_agent,
            "proposal_id": proposal_id,
            "publication_id": publication_id,
            "revision_id": record["revision_id"],
            "trace_id": record.get("trace_id"),
            "trace_revision_id": record["revision_id"],
            "version": boundary_version,
            "status": "ACTIVE",
            **boundary_policy,
            "valid_from": now,
            "valid_until": None,
            "published_by": actor,
            "published_at": now,
            "content_hash": content_hash,
        })

        transaction.set(active_boundary_ref, {
            "target_agent": target_agent,
            "boundary_id": boundary_id,
            "version": boundary_version,
            "publication_id": publication_id,
            "revision_id": record["revision_id"],
            "trace_id": record.get("trace_id"),
            "trace_revision_id": record["revision_id"],
            "updated_at": now,
        })

        transaction.set(published_ref, {
            "publication_id": publication_id,
            "proposal_id": proposal_id,
            "run_id": record["run_id"],
            "agent_run_id": record["agent_run_id"],
            "revision_id": record["revision_id"],
            "trace_id": record.get("trace_id"),
            "trace_revision_id": record["revision_id"],
            "target_agent": target_agent,
            "proposal": record["proposal"],
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": boundary_version,
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
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": boundary_version,
            "published_by": actor,
            "published_at": now,
            "updated_at": now,
        })

        transaction.update(revision_ref, {
            "status": "PUBLISHED",
            "publication_id": publication_id,
            "delegation_boundary_id": boundary_id,
            "published_by": actor,
            "published_at": now,
        })

        transaction.set(current_ref, {
            "target_agent": target_agent,
            "publication_id": publication_id,
            "proposal_id": proposal_id,
            "run_id": record["run_id"],
            "revision_id": record["revision_id"],
            "trace_id": record.get("trace_id"),
            "trace_revision_id": record["revision_id"],
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": boundary_version,
            "updated_at": now,
        })

        transaction.set(audit_ref, {
            "audit_id": audit_id,
            "event_type": "PROPOSAL_PUBLISHED",
            "proposal_id": proposal_id,
            "publication_id": publication_id,
            "run_id": record["run_id"],
            "revision_id": record["revision_id"],
            "trace_id": record.get("trace_id"),
            "trace_revision_id": record["revision_id"],
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": boundary_version,
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
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": boundary_version,
            "trace_id": record.get("trace_id"),
            "trace_revision_id": record["revision_id"],
            "current_target_agent": target_agent,
            "published_by": actor,
        }

    return apply(transaction)



def issue_human_review_clearance(
    *,
    agent_id: str,
    action_name: str,
    actor: str,
    comment: str,
) -> dict:
    """Issue a verified human clearance bound to current governed state."""
    db = _db()
    now = datetime.now(timezone.utc)

    clearance_id = _id("REVIEW")
    audit_id = _id("AUDIT")

    agent_ref = db.collection("agents").document(agent_id)
    clearance_ref = (
        db.collection("human_review_clearances").document(clearance_id)
    )
    audit_ref = db.collection("audit_events").document(audit_id)

    transaction = db.transaction()

    @firestore.transactional
    def apply(transaction):
        agent_snap = agent_ref.get(transaction=transaction)

        if not agent_snap.exists:
            raise ValueError("Agent not found.")

        agent = agent_snap.to_dict()

        boundary_id = agent.get("active_delegation_boundary_id")
        publication_id = agent.get("readiness_publication_id")

        if not boundary_id or not publication_id:
            raise ValueError(
                "Agent has no active published Delegation Boundary."
            )

        boundary_ref = (
            db.collection("delegation_boundaries").document(boundary_id)
        )
        boundary_snap = boundary_ref.get(transaction=transaction)

        if not boundary_snap.exists:
            raise ValueError("Delegation Boundary not found.")

        boundary = boundary_snap.to_dict()

        trace_id = boundary.get("trace_id")
        trace_revision_id = (
            boundary.get("trace_revision_id")
            or boundary.get("revision_id")
        )

        if boundary.get("status") != "ACTIVE":
            raise ValueError("Delegation Boundary is not ACTIVE.")

        if boundary.get("publication_id") != publication_id:
            raise ValueError(
                "Boundary / Readiness Publication mismatch."
            )

        if action_name not in (
            boundary.get("permitted_actions") or []
        ):
            raise ValueError(
                "Human clearance cannot authorize an action "
                "outside the active Delegation Boundary."
            )

        transaction.set(clearance_ref, {
            "clearance_id": clearance_id,
            "status": "APPROVED",
            "agent_id": agent_id,
            "action_name": action_name,
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": boundary.get("version"),
            "publication_id": publication_id,
            "revision_id": boundary.get("revision_id"),
            "trace_id": trace_id,
            "trace_revision_id": trace_revision_id,
            "approved_by": actor,
            "approved_at": now,
            "comment": comment,
            "expires_at": None,
        })

        transaction.set(audit_ref, {
            "audit_id": audit_id,
            "event_type": "HUMAN_REVIEW_CLEARANCE_APPROVED",
            "clearance_id": clearance_id,
            "agent_id": agent_id,
            "action_name": action_name,
            "delegation_boundary_id": boundary_id,
            "publication_id": publication_id,
            "trace_id": trace_id,
            "trace_revision_id": trace_revision_id,
            "actor_type": "HUMAN",
            "actor": actor,
            "comment": comment,
            "created_at": now,
        })

        return {
            "clearance_id": clearance_id,
            "status": "APPROVED",
            "agent_id": agent_id,
            "action_name": action_name,
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": boundary.get("version"),
            "publication_id": publication_id,
            "trace_id": trace_id,
            "trace_revision_id": trace_revision_id,
            "approved_by": actor,
            "audit_id": audit_id,
        }

    return apply(transaction)
