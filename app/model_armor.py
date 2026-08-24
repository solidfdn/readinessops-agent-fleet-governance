import requests
import google.auth
from google.auth.transport.requests import Request


PROJECT_ID = "readinessops-agent-governance"
LOCATION = "asia-northeast1"
TEMPLATE = "readinessops-prompt-guard"

SANITIZE_URL = (
    f"https://modelarmor.{LOCATION}.rep.googleapis.com/"
    f"v1/projects/{PROJECT_ID}/locations/{LOCATION}/"
    f"templates/{TEMPLATE}:sanitizeUserPrompt"
)


def _access_token() -> str:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    return credentials.token


def sanitize_evidence(text: str) -> dict:
    response = requests.post(
        SANITIZE_URL,
        headers={
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
        },
        json={
            "userPromptData": {
                "text": text,
            }
        },
        timeout=30,
    )

    response.raise_for_status()
    data = response.json()

    result = data.get("sanitizationResult") or {}
    filter_results = result.get("filterResults") or {}

    pi = (
        filter_results
        .get("pi_and_jailbreak", {})
        .get("piAndJailbreakFilterResult", {})
    )

    return {
        "blocked": result.get("filterMatchState") == "MATCH_FOUND",
        "filter_match_state": result.get("filterMatchState"),
        "invocation_result": result.get("invocationResult"),
        "pi_match_state": pi.get("matchState"),
        "pi_execution_state": pi.get("executionState"),
        "confidence_level": pi.get("confidenceLevel"),
        "template": TEMPLATE,
    }
