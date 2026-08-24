from datetime import datetime, timezone
from uuid import uuid4

from google.cloud import firestore

from .schemas import GovernedAssessmentProposal


PROJECT_ID = "readinessops-agent-governance"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def persist_draft_proposal(
    proposal: GovernedAssessmentProposal,
    source_evidence: str,
) -> dict:
    if proposal.grounding_status != "PASS":
        raise ValueError("Grounding validation must PASS before persistence.")

    if proposal.proposal_status != "REVIEW_REQUIRED":
        raise ValueError("AI proposal must start as REVIEW_REQUIRED.")

    if proposal.publication_status != "NOT_PUBLISHED":
        raise ValueError("AI proposal cannot be published automatically.")

    db = firestore.Client(project=PROJECT_ID)

    now = datetime.now(timezone.utc)

    run_id = new_id("RUN")
    agent_run_id = new_id("ARUN")
    proposal_id = new_id("PROP")
    revision_id = new_id("REV")

    record = {
        "run_id": run_id,
        "agent_run_id": agent_run_id,
        "proposal_id": proposal_id,
        "revision_id": revision_id,
        "target_agent": proposal.target_agent,
        "proposal_status": "REVIEW_REQUIRED",
        "publication_status": "NOT_PUBLISHED",
        "revision_status": "DRAFT",
        "grounding_status": proposal.grounding_status,
        "grounding_issues": proposal.grounding_issues,
        "source_evidence": source_evidence,
        "proposal": proposal.model_dump(),
        "created_at": now,
        "updated_at": now,
        "published_at": None,
        "published_by": None,
        "approved_at": None,
        "approved_by": None,
    }

    batch = db.batch()

    proposal_ref = db.collection("proposals").document(proposal_id)
    run_ref = db.collection("assessment_runs").document(run_id)
    revision_ref = db.collection("revisions").document(revision_id)

    batch.set(proposal_ref, record)

    batch.set(run_ref, {
        "run_id": run_id,
        "agent_run_id": agent_run_id,
        "proposal_id": proposal_id,
        "revision_id": revision_id,
        "target_agent": proposal.target_agent,
        "status": "REVIEW_REQUIRED",
        "created_at": now,
    })

    batch.set(revision_ref, {
        "revision_id": revision_id,
        "run_id": run_id,
        "proposal_id": proposal_id,
        "status": "DRAFT",
        "is_current": False,
        "created_at": now,
    })

    audit_id = new_id("AUDIT")
    batch.set(db.collection("audit_events").document(audit_id), {
        "audit_id": audit_id,
        "event_type": "AI_DRAFT_CREATED",
        "run_id": run_id,
        "agent_run_id": agent_run_id,
        "proposal_id": proposal_id,
        "revision_id": revision_id,
        "actor_type": "AI",
        "actor": "readinessops_orchestrator",
        "created_at": now,
    })

    batch.commit()

    return {
        "run_id": run_id,
        "agent_run_id": agent_run_id,
        "proposal_id": proposal_id,
        "revision_id": revision_id,
        "audit_id": audit_id,
        "status": "REVIEW_REQUIRED",
        "publication_status": "NOT_PUBLISHED",
    }


def persist_reassessment_proposal(
    *,
    proposal: GovernedAssessmentProposal,
    source_evidence: str,
    revision_id: str,
    evidence_id: str,
    source_event_id: str | None = None,
    trace_id: str | None = None,
) -> dict:
    """Persist REVIEW_REQUIRED proposal onto an existing Draft Revision."""

    if proposal.grounding_status != "PASS":
        raise ValueError(
            "Grounding validation must PASS before persistence."
        )

    if proposal.proposal_status != "REVIEW_REQUIRED":
        raise ValueError(
            "AI reassessment proposal must be REVIEW_REQUIRED."
        )

    if proposal.publication_status != "NOT_PUBLISHED":
        raise ValueError(
            "AI reassessment proposal cannot publish automatically."
        )

    db = firestore.Client(project=PROJECT_ID)
    now = datetime.now(timezone.utc)

    revision_ref = db.collection("revisions").document(revision_id)

    run_id = new_id("RUN")
    agent_run_id = new_id("ARUN")
    proposal_id = new_id("PROP")
    audit_id = new_id("AUDIT")

    transaction = db.transaction()

    @firestore.transactional
    def apply(transaction):
        revision_snap = revision_ref.get(transaction=transaction)

        if not revision_snap.exists:
            raise ValueError("Draft Revision does not exist.")

        revision = revision_snap.to_dict()

        # Retry/idempotency: never create a second proposal
        # for the same reassessment revision.
        existing_proposal_id = revision.get("proposal_id")

        if existing_proposal_id:
            return {
                "run_id": revision.get("run_id"),
                "agent_run_id": revision.get("agent_run_id"),
                "proposal_id": existing_proposal_id,
                "revision_id": revision_id,
                "status": "REVIEW_REQUIRED",
                "publication_status": "NOT_PUBLISHED",
                "reused": True,
            }

        if revision.get("target_agent") != proposal.target_agent:
            raise ValueError(
                "Proposal target does not match Draft Revision target."
            )

        record = {
            "run_id": run_id,
            "agent_run_id": agent_run_id,
            "proposal_id": proposal_id,
            "revision_id": revision_id,
            "target_agent": proposal.target_agent,
            "proposal_status": "REVIEW_REQUIRED",
            "publication_status": "NOT_PUBLISHED",
            "revision_status": "DRAFT",
            "grounding_status": proposal.grounding_status,
            "grounding_issues": proposal.grounding_issues,
            "source_evidence": source_evidence,
            "source_evidence_id": evidence_id,
            "source_event_id": source_event_id,
            "trace_id": trace_id,
            "proposal": proposal.model_dump(),
            "created_at": now,
            "updated_at": now,
            "published_at": None,
            "published_by": None,
            "approved_at": None,
            "approved_by": None,
        }

        transaction.set(
            db.collection("proposals").document(proposal_id),
            record,
        )

        transaction.set(
            db.collection("assessment_runs").document(run_id),
            {
                "run_id": run_id,
                "agent_run_id": agent_run_id,
                "proposal_id": proposal_id,
                "revision_id": revision_id,
                "target_agent": proposal.target_agent,
                "status": "REVIEW_REQUIRED",
                "source_event_id": source_event_id,
                "trace_id": trace_id,
                "created_at": now,
            },
        )

        transaction.update(
            revision_ref,
            {
                "run_id": run_id,
                "agent_run_id": agent_run_id,
                "proposal_id": proposal_id,
                "proposal_status": "REVIEW_REQUIRED",
                "review_required_at": now,
                "trace_id": trace_id,
            },
        )

        transaction.set(
            db.collection("audit_events").document(audit_id),
            {
                "audit_id": audit_id,
                "event_type": "AI_REASSESSMENT_DRAFT_CREATED",
                "run_id": run_id,
                "agent_run_id": agent_run_id,
                "proposal_id": proposal_id,
                "revision_id": revision_id,
                "evidence_id": evidence_id,
                "source_event_id": source_event_id,
                "trace_id": trace_id,
                "actor_type": "AI",
                "actor": "readinessops_orchestrator",
                "created_at": now,
            },
        )

        return {
            "run_id": run_id,
            "agent_run_id": agent_run_id,
            "proposal_id": proposal_id,
            "revision_id": revision_id,
            "audit_id": audit_id,
            "status": "REVIEW_REQUIRED",
            "publication_status": "NOT_PUBLISHED",
            "reused": False,
        }

    return apply(transaction)
