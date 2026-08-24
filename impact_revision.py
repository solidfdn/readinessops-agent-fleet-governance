import json
from datetime import datetime, timezone

from google import genai
from google.genai import types
from google.cloud import firestore

from app.schemas import EvidenceImpactResult

PROJECT = "readinessops-agent-governance"
LOCATION = "asia-northeast1"
REVISION_ID = "REV_c4de5b795abe46d1"

db = firestore.Client(project=PROJECT)

rev_ref = db.collection("revisions").document(REVISION_ID)
rev = rev_ref.get().to_dict()

evidence_id = rev["evidence_ids"][0]
evidence = db.collection("evidence_items").document(evidence_id).get().to_dict()

published = (
    db.collection("published_records")
    .document(rev["base_publication_id"])
    .get()
    .to_dict()
)

current_proposal = published["proposal"]
new_evidence = evidence["evidence_text"]

client = genai.Client(
    vertexai=True,
    project=PROJECT,
    location=LOCATION,
)

prompt = f"""
You are the ReadinessOps Evidence Impact Agent.

Compare the governed published current state with the newly added evidence.

RULES:
- Published Current is the BEFORE state.
- New Evidence is the change trigger.
- Do not approve or publish anything.
- Do not invent facts or metrics.
- Distinguish material operational changes from wording changes.
- If a previously required safety capability is no longer verified, treat this as a material change requiring reassessment and safe-side suspension.

PUBLISHED CURRENT:
{json.dumps(current_proposal, ensure_ascii=False)}

NEW EVIDENCE:
{new_evidence}
"""

response = client.models.generate_content(
    model="gemini-3.5-flash",
    contents=prompt,
    config=types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json",
        response_schema=EvidenceImpactResult,
    ),
)

impact = EvidenceImpactResult.model_validate_json(response.text)

# Deterministic fail-safe for explicit capability loss.
loss_terms = (
    "no longer",
    "removed",
    "revoked",
    "disabled",
    "not verified",
    "cannot be verified",
)

if any(term in new_evidence.lower() for term in loss_terms):
    impact.material_change = True
    impact.impact = "MATERIAL"
    if impact.treatment == "NO_CHANGE":
        impact.treatment = "SUSPEND"

now = datetime.now(timezone.utc)

rev_ref.update({
    "evidence_impact_status": "COMPLETED",
    "evidence_impact": impact.model_dump(),
    "material_change": impact.material_change,
    "impact_completed_at": now,
})

audit_id = "AUDIT_IMPACT_" + REVISION_ID.split("_", 1)[1]

db.collection("audit_events").document(audit_id).set({
    "audit_id": audit_id,
    "event_type": "EVIDENCE_IMPACT_COMPLETED",
    "revision_id": REVISION_ID,
    "evidence_id": evidence_id,
    "impact": impact.impact,
    "treatment": impact.treatment,
    "material_change": impact.material_change,
    "actor_type": "AI",
    "actor": "evidence_impact_agent",
    "created_at": now,
})

print(json.dumps({
    "revision_id": REVISION_ID,
    "evidence_id": evidence_id,
    **impact.model_dump(),
    "audit_id": audit_id,
}, ensure_ascii=False, indent=2))
