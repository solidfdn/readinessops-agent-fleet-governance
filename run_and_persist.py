import json
import subprocess
from pathlib import Path

from app.agent import validate_grounding_claims
from app.persistence import persist_draft_proposal
from app.schemas import GovernedAssessmentProposal


PROMPT = (
    "Assess a Case Triage Agent for production use. "
    "New evidence says cases involving financial hardship must always be reviewed by a human. "
    "Propose governance, value realization, model routing, portfolio, delegation boundary, "
    "and action implications. Do not approve or publish anything."
)

EVIDENCE = (
    "Case Triage Agent is being assessed for production use. "
    "Cases involving financial hardship must always be reviewed by a human."
)


def extract_final_json(output: str) -> dict:
    decoder = json.JSONDecoder()

    # Scan every possible JSON-object start and keep governed proposal objects.
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

# 1. Fixed schema validation — app side
proposal = GovernedAssessmentProposal.model_validate(proposal_dict)

# 2. Deterministic grounding validation — app side
grounding = json.loads(
    validate_grounding_claims(
        evidence=EVIDENCE,
        draft=json.dumps(proposal_dict, ensure_ascii=False),
    )
)

if grounding["status"] != "PASS":
    raise RuntimeError(f"Grounding validation failed: {grounding['issues']}")

# 3. Never trust model-declared PASS alone
proposal.grounding_status = "PASS"
proposal.grounding_issues = grounding["issues"]

# 4. Persist only as Draft / Review Required / Not Published
ids = persist_draft_proposal(
    proposal=proposal,
    source_evidence=EVIDENCE,
)

print("\n=== READINESSOPS FIRESTORE PERSIST PASS ===")
print(json.dumps(ids, indent=2))
