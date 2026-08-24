import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from google.cloud import firestore

from app.agent import validate_grounding_claims
from app.persistence import persist_draft_proposal
from app.schemas import GovernedAssessmentProposal


PROJECT = "readinessops-agent-governance"

EVIDENCE = """
SYNTHETIC DEMO BASELINE EVIDENCE — NOT PRODUCTION RESULTS.

The current Case Triage Agent demo build contains a verified financial-hardship classifier.
In a controlled synthetic validation set of 20 financial-hardship cases:
- 20 out of 20 cases were identified and routed to human review.
- Protected automated resolution was blocked for all 20 out of 20 hardship cases.
- The human-review routing path was successfully verified.

These measurements are synthetic demo validation results only.
"""

PROMPT = f"""
Assess the Case Triage Agent for readiness using ONLY the supplied synthetic demo evidence below.

{EVIDENCE}

Produce governance, value realization, model routing, portfolio,
delegation boundary, and action implications.

Clearly label these as synthetic demo measurements, not production results.
Do not approve, publish, or execute anything.
"""


def extract_final_json(output: str) -> dict:
    decoder = json.JSONDecoder()
    proposals = []

    for i, ch in enumerate(output):
        if ch != "{":
            continue

        try:
            data, _ = decoder.raw_decode(output[i:])
        except json.JSONDecodeError:
            continue

        if (
            isinstance(data, dict)
            and data.get("schema_version") == "readinessops.google.v1"
        ):
            proposals.append(data)

    if not proposals:
        raise RuntimeError("Final governed proposal JSON was not found.")

    return proposals[-1]


result = subprocess.run(
    ["agents-cli", "run", PROMPT],
    cwd=Path.home() / "readinessops-google-adk",
    capture_output=True,
    text=True,
    check=True,
)

print(result.stdout)

proposal_dict = extract_final_json(result.stdout)
proposal = GovernedAssessmentProposal.model_validate(proposal_dict)

grounding = json.loads(
    validate_grounding_claims(
        evidence=EVIDENCE,
        draft=json.dumps(proposal_dict, ensure_ascii=False),
    )
)

if grounding["status"] != "PASS":
    raise RuntimeError(f"Grounding validation failed: {grounding['issues']}")

proposal.grounding_status = "PASS"
proposal.grounding_issues = grounding["issues"]

db = firestore.Client(project=PROJECT)

evidence_id = f"EVID_{uuid4().hex[:16]}"
now = datetime.now(timezone.utc)

db.collection("evidence_items").document(evidence_id).set({
    "evidence_id": evidence_id,
    "target_agent": "Case Triage Agent",
    "source_label": "synthetic-demo-ready-baseline",
    "evidence_text": EVIDENCE,
    "evidence_status": "ADDED",
    "evidence_kind": "SYNTHETIC_DEMO",
    "is_production_evidence": False,
    "created_at": now,
})

ids = persist_draft_proposal(
    proposal=proposal,
    source_evidence=EVIDENCE,
)

db.collection("proposals").document(ids["proposal_id"]).update({
    "evidence_ids": [evidence_id],
    "evidence_kind": "SYNTHETIC_DEMO",
})

db.collection("revisions").document(ids["revision_id"]).update({
    "evidence_ids": [evidence_id],
    "evidence_kind": "SYNTHETIC_DEMO",
})

print("\n=== SYNTHETIC READY BASELINE DRAFT PASS ===")
print(json.dumps({
    **ids,
    "evidence_id": evidence_id,
    "evidence_kind": "SYNTHETIC_DEMO",
}, indent=2))
