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
