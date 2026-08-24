import json
from uuid import uuid4

import google.auth
import requests
from google.auth.transport.requests import Request


PROJECT_ID = "readinessops-agent-governance"
PROJECT_NUMBER = "649947189508"
LOCATION = "asia-northeast1"
GOVERNANCE_RUNTIME_ID = "2493591216726212608"

A2A_URL = (
    f"https://{LOCATION}-aiplatform.googleapis.com/"
    f"reasoningEngines/v1/projects/{PROJECT_NUMBER}/locations/{LOCATION}/"
    f"reasoningEngines/{GOVERNANCE_RUNTIME_ID}/api/a2a/app"
)


def _access_token() -> str:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    return credentials.token


def _extract_proposal(data: dict) -> dict:
    texts: list[str] = []

    result = data.get("result") or {}

    for artifact in result.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            if isinstance(part.get("text"), str):
                texts.append(part["text"])

    for message in result.get("history") or []:
        if message.get("role") != "agent":
            continue
        for part in message.get("parts") or []:
            if isinstance(part.get("text"), str):
                texts.append(part["text"])

    decoder = json.JSONDecoder()
    proposals = []

    for text in texts:
        for index, char in enumerate(text):
            if char != "{":
                continue

            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue

            if (
                isinstance(value, dict)
                and value.get("schema_version")
                == "readinessops.google.v1"
            ):
                proposals.append(value)

    if not proposals:
        raise RuntimeError(
            "Governed assessment proposal was not found in A2A response."
        )

    return proposals[-1]


def run_governance_reassessment(
    *,
    target_agent: str,
    published_current: dict,
    new_evidence: str,
    evidence_impact: dict,
    source_event_id: str,
) -> dict:
    message_id = f"reassessment-{uuid4().hex}"

    prompt = f"""
Perform a ReadinessOps reassessment for {target_agent}.

This is triggered asynchronously by new evidence.

Use your specialist agents for:
- evidence impact
- governance
- value realization / portfolio
- model routing
- action implications

Return one structured GovernedAssessmentProposal containing all four
Decision Packs and the proposed Delegation Boundary.

Rules:
- AI proposes only.
- proposal_status must remain REVIEW_REQUIRED.
- publication_status must remain NOT_PUBLISHED.
- Do not approve, publish, reactivate, or execute anything.
- Do not invent facts, metrics, systems, URLs, queues, or policies.
- Treat missing information as UNRESOLVED.

SOURCE EVENT:
{source_event_id}

PUBLISHED CURRENT:
{json.dumps(published_current, ensure_ascii=False)}

NEW EVIDENCE:
{new_evidence}

EVIDENCE IMPACT:
{json.dumps(evidence_impact, ensure_ascii=False)}
"""

    response = requests.post(
        A2A_URL,
        headers={
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": message_id,
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": message_id,
                    "role": "user",
                    "parts": [
                        {
                            "kind": "text",
                            "text": prompt,
                        }
                    ],
                }
            },
        },
        timeout=300,
    )

    response.raise_for_status()

    data = response.json()

    state = (
        (data.get("result") or {})
        .get("status", {})
        .get("state")
    )

    if state == "failed":
        raise RuntimeError(
            f"Governance Runtime A2A task failed: {json.dumps(data)}"
        )

    return _extract_proposal(data)
