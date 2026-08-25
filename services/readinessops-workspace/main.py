import os
import re
from datetime import datetime, timezone
from uuid import uuid4

import google.auth
import requests
from flask import Flask, jsonify, render_template, request
from google.auth.transport.requests import Request
from google.cloud import firestore, storage

from app.execution import request_protected_action
from app.governance import (
    approve_proposal,
    edit_delegation_boundary,
    publish_proposal,
    review_and_edit_proposal,
)
from app.readiness import activate_ready_state


PROJECT_ID = os.getenv(
    "GOOGLE_CLOUD_PROJECT",
    "readinessops-agent-governance",
)
PROJECT_NUMBER = os.getenv(
    "READINESSOPS_PROJECT_NUMBER",
    "649947189508",
)
REGION = os.getenv(
    "GOOGLE_CLOUD_REGION",
    "asia-northeast1",
)
EVIDENCE_BUCKET = os.getenv(
    "READINESSOPS_EVIDENCE_BUCKET",
    f"readinessops-evidence-{PROJECT_NUMBER}",
)
EXECUTOR_RUNTIME_ID = os.getenv(
    "READINESSOPS_EXECUTOR_RUNTIME_ID",
    "7675545537969389568",
)
GOVERNANCE_RUNTIME_ID = os.getenv(
    "READINESSOPS_GOVERNANCE_RUNTIME_ID",
    "2493591216726212608",
)
DEFAULT_AGENT_ID = os.getenv(
    "READINESSOPS_DEFAULT_AGENT_ID",
    "case-triage-agent",
)
DEFAULT_ACTOR = os.getenv(
    "READINESSOPS_DEFAULT_ACTOR",
    "demo-operator@solid-fdn.co.jp",
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024


def _db() -> firestore.Client:
    return firestore.Client(project=PROJECT_ID)


def _storage() -> storage.Client:
    return storage.Client(project=PROJECT_ID)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _clean(value):
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _doc(
    db: firestore.Client,
    collection: str,
    document_id: str | None,
) -> dict:
    if not document_id:
        return {}
    snap = db.collection(collection).document(document_id).get()
    return snap.to_dict() if snap.exists else {}


def _recent(
    db: firestore.Client,
    collection: str,
    limit: int = 40,
) -> list[dict]:
    query = (
        db.collection(collection)
        .order_by(
            "created_at",
            direction=firestore.Query.DESCENDING,
        )
        .limit(limit)
    )
    return [snap.to_dict() for snap in query.stream()]


def _actor() -> str:
    header = request.headers.get(
        "X-Goog-Authenticated-User-Email",
        "",
    )
    if header:
        return header.removeprefix("accounts.google.com:")
    return DEFAULT_ACTOR


def _access_token() -> str:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    return credentials.token


def _runtime_url(runtime_id: str, path: str) -> str:
    return (
        f"https://{REGION}-aiplatform.googleapis.com/"
        f"reasoningEngines/v1/projects/{PROJECT_NUMBER}/"
        f"locations/{REGION}/reasoningEngines/{runtime_id}/api/{path}"
    )


def _parse_list(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    return [
        str(item).strip()
        for item in items
        if str(item).strip()
    ]


def _latest_for_agent(
    records: list[dict],
    agent_id: str,
    target_agent: str,
) -> dict:
    for record in records:
        if record.get("agent_id") == agent_id:
            return record
        if record.get("target_agent") == target_agent:
            return record
    return {}


def _workspace_state(agent_id: str) -> dict:
    db = _db()
    agent = _doc(db, "agents", agent_id)
    if not agent:
        raise ValueError(f"Agent not found: {agent_id}")

    target_agent = agent.get("target_agent") or agent.get("name")
    if not target_agent:
        raise ValueError("Agent target_agent is missing.")

    current = _doc(
        db,
        "current_states",
        _slug(target_agent),
    )

    proposals = _recent(db, "proposals", 50)
    revisions = _recent(db, "revisions", 50)
    events = _recent(db, "event_receipts", 80)
    actions = _recent(db, "action_requests", 80)
    audits = _recent(db, "audit_events", 160)

    agent_proposals = [
        item
        for item in proposals
        if item.get("target_agent") == target_agent
    ]

    current_updated_at = current.get("updated_at")

    def _is_after_current(item):
        created_at = item.get("created_at")
        if not created_at:
            return False
        if not current_updated_at:
            return True
        return created_at > current_updated_at

    # Only a proposal created after the official Current state is a live pending
    # reassessment. Older REVIEW_REQUIRED records remain audit history.
    pending = next(
        (
            item
            for item in agent_proposals
            if item.get("proposal_status")
            in ("REVIEW_REQUIRED", "APPROVED")
            and item.get("publication_status") == "NOT_PUBLISHED"
            and _is_after_current(item)
        ),
        {},
    )

    current_proposal = _doc(
        db,
        "proposals",
        current.get("proposal_id"),
    )

    proposal = (
        pending
        or current_proposal
        or (agent_proposals[0] if agent_proposals else {})
    )

    revision_id = (
        proposal.get("revision_id")
        or current.get("revision_id")
        or agent.get("current_revision_id")
    )
    revision = _doc(db, "revisions", revision_id)

    boundary_id = (
        current.get("delegation_boundary_id")
        or agent.get("active_delegation_boundary_id")
    )
    boundary = _doc(
        db,
        "delegation_boundaries",
        boundary_id,
    )

    trace_id = (
        proposal.get("trace_id")
        or revision.get("trace_id")
        or current.get("trace_id")
    )

    # Keep Evidence / Reassessment / Proposal on the same governance trace.
    latest_event = next(
        (
            item
            for item in events
            if item.get("event_id") == trace_id
            or item.get("trace_id") == trace_id
        ),
        {},
    )
    if not latest_event:
        latest_event = _latest_for_agent(
            events,
            agent_id,
            target_agent,
        )

    agent_actions = [
        item
        for item in actions
        if item.get("agent_id") == agent_id
        or item.get("target_agent") == target_agent
    ]
    executed_action = next(
        (
            item
            for item in agent_actions
            if item.get("execution_status") == "EXECUTED"
        ),
        {},
    )
    denied_action = next(
        (
            item
            for item in agent_actions
            if item.get("status") == "DENIED"
        ),
        {},
    )

    trace_audits = [
        item
        for item in audits
        if item.get("trace_id") == trace_id
    ]
    trace_audits.reverse()

    payload = proposal.get("proposal") or {}
    proposed_boundary = payload.get("delegation_boundary") or {}
    decision_packs = {
        "governance": payload.get("governance"),
        "value_realization": payload.get("value_realization"),
        "model_routing": payload.get("model_routing"),
        "portfolio": payload.get("portfolio"),
    }

    return _clean(
        {
            "actor": _actor(),
            "agent_id": agent_id,
            "target_agent": target_agent,
            "agent": {
                "name": agent.get("name") or target_agent,
                "readiness_status": agent.get("readiness_status"),
                "current_revision_id": agent.get("current_revision_id"),
                "readiness_publication_id": agent.get(
                    "readiness_publication_id"
                ),
                "active_delegation_boundary_id": agent.get(
                    "active_delegation_boundary_id"
                ),
                "active_delegation_boundary_version": agent.get(
                    "active_delegation_boundary_version"
                ),
                "readiness_basis": agent.get("readiness_basis"),
            },
            "current": current,
            "revision": {
                "revision_id": revision.get("revision_id"),
                "status": revision.get("status"),
                "material_change": revision.get("material_change"),
                "evidence_impact_status": revision.get(
                    "evidence_impact_status"
                ),
                "evidence_impact": revision.get("evidence_impact"),
                "proposal_id": revision.get("proposal_id"),
                "proposal_status": revision.get("proposal_status"),
                "trace_id": revision.get("trace_id"),
            },
            "proposal": {
                "proposal_id": proposal.get("proposal_id"),
                "proposal_status": proposal.get("proposal_status"),
                "publication_status": proposal.get("publication_status"),
                "revision_id": proposal.get("revision_id"),
                "publication_id": proposal.get("publication_id"),
                "grounding_status": proposal.get("grounding_status"),
                "reviewed_by": proposal.get("reviewed_by"),
                "reviewed_at": proposal.get("reviewed_at"),
                "approved_by": proposal.get("approved_by"),
                "approved_at": proposal.get("approved_at"),
                "published_by": proposal.get("published_by"),
                "published_at": proposal.get("published_at"),
                "trace_id": proposal.get("trace_id"),
                "source_evidence": proposal.get("source_evidence"),
                "proposed_boundary": proposed_boundary,
            },
            "boundary": {
                "boundary_id": boundary.get("boundary_id"),
                "version": boundary.get("version"),
                "status": boundary.get("status"),
                "permitted_actions": boundary.get("permitted_actions"),
                "human_review_required_actions": boundary.get(
                    "human_review_required_actions"
                ),
                "permitted_tools": boundary.get("permitted_tools"),
                "permitted_data_classes": boundary.get(
                    "permitted_data_classes"
                ),
                "permitted_case_impact": boundary.get(
                    "permitted_case_impact"
                ),
                "prohibited_actions": boundary.get("prohibited_actions"),
                "mandatory_human_review_conditions": boundary.get(
                    "mandatory_human_review_conditions"
                ),
            },
            "latest_event": {
                "event_id": latest_event.get("event_id"),
                "object_id": latest_event.get("object_id"),
                "status": latest_event.get("status"),
                "security_status": latest_event.get("security_status"),
                "model_armor": latest_event.get("model_armor"),
                "revision_id": latest_event.get("revision_id"),
                "proposal_id": latest_event.get("proposal_id"),
                "last_error": latest_event.get("last_error"),
                "created_at": latest_event.get("created_at"),
                "completed_at": latest_event.get("completed_at"),
            },
            "decision_packs": decision_packs,
            "execution": {
                "executed_action": executed_action,
                "denied_action": denied_action,
            },
            "trace_id": trace_id,
            "timeline": trace_audits,
        }
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "readinessops-workspace",
        "access": "authenticated",
    }


@app.get("/api/agents")
def agents():
    db = _db()
    records = []
    for snap in db.collection("agents").stream():
        item = snap.to_dict()
        records.append(
            {
                "agent_id": snap.id,
                "name": (
                    item.get("name")
                    or item.get("target_agent")
                    or snap.id
                ),
                "readiness_status": item.get("readiness_status"),
            }
        )
    return jsonify(_clean(records))


@app.get("/api/workspace")
def api_workspace():
    agent_id = request.args.get("agent_id", DEFAULT_AGENT_ID)
    try:
        return jsonify(_workspace_state(agent_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/evidence")
def upload_evidence():
    agent_id = request.form.get("agent_id", DEFAULT_AGENT_ID)
    label = request.form.get("label", "").strip()
    text = request.form.get("text", "")
    uploaded = request.files.get("file")

    if uploaded and uploaded.filename:
        if not uploaded.filename.lower().endswith(".txt"):
            return jsonify(
                {"error": "Hackathon workspace accepts .txt evidence only."}
            ), 400
        payload = uploaded.read()
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return jsonify(
                {"error": "Evidence file must be UTF-8 text."}
            ), 400
        label = label or uploaded.filename

    if not text.strip():
        return jsonify({"error": "Evidence text is required."}), 400

    if len(text.encode("utf-8")) > 512_000:
        return jsonify(
            {"error": "Evidence must be 500 KB or smaller."}
        ), 400

    safe_label = _slug(label or "workspace-evidence")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    object_name = (
        f"{agent_id}/{safe_label}-{timestamp}-{uuid4().hex[:8]}.txt"
    )

    blob = _storage().bucket(EVIDENCE_BUCKET).blob(object_name)
    blob.metadata = {
        "readinessops_actor": _actor(),
        "readinessops_source": "governance_workspace",
    }
    blob.upload_from_string(
        text,
        content_type="text/plain; charset=utf-8",
    )

    return jsonify(
        {
            "status": "UPLOADED",
            "agent_id": agent_id,
            "object_name": object_name,
            "source_uri": f"gs://{EVIDENCE_BUCKET}/{object_name}",
            "next": "The event-driven worker will reassess this evidence.",
        }
    )


@app.post("/api/review")
def review():
    data = request.get_json(silent=True) or {}
    proposal_id = data.get("proposal_id")
    comment = str(data.get("comment") or "").strip()
    add_unresolved = (
        str(data.get("add_unresolved") or "").strip() or None
    )

    if not proposal_id or not comment:
        return jsonify(
            {"error": "proposal_id and comment are required."}
        ), 400

    try:
        result = review_and_edit_proposal(
            proposal_id=proposal_id,
            actor=_actor(),
            comment=comment,
            add_unresolved=add_unresolved,
        )
        return jsonify(_clean(result))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/boundary")
def save_boundary():
    data = request.get_json(silent=True) or {}
    try:
        result = edit_delegation_boundary(
            proposal_id=data["proposal_id"],
            actor=_actor(),
            comment=str(data.get("comment") or "").strip(),
            permitted_actions=_parse_list(
                data.get("permitted_actions")
            ),
            human_review_required_actions=_parse_list(
                data.get("human_review_required_actions")
            ),
            permitted_tools=_parse_list(data.get("permitted_tools")),
            permitted_data_classes=_parse_list(
                data.get("permitted_data_classes")
            ),
            permitted_case_impact=_parse_list(
                data.get("permitted_case_impact")
            ),
            permitted_skills=_parse_list(
                data.get("permitted_skills")
            ),
            mandatory_human_review_conditions=_parse_list(
                data.get("mandatory_human_review_conditions")
            ),
            prohibited_actions=_parse_list(
                data.get("prohibited_actions")
            ),
        )
        return jsonify(_clean(result))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/approve")
def approve():
    data = request.get_json(silent=True) or {}
    try:
        result = approve_proposal(
            proposal_id=data["proposal_id"],
            actor=_actor(),
            comment=str(data.get("comment") or "").strip(),
        )
        return jsonify(_clean(result))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/publish")
def publish():
    data = request.get_json(silent=True) or {}
    try:
        result = publish_proposal(
            proposal_id=data["proposal_id"],
            actor=_actor(),
            comment=str(data.get("comment") or "").strip(),
        )
        return jsonify(_clean(result))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/activate")
def activate():
    data = request.get_json(silent=True) or {}
    agent_id = data.get("agent_id", DEFAULT_AGENT_ID)
    db = _db()
    agent = _doc(db, "agents", agent_id)
    target_agent = agent.get("target_agent") or agent.get("name")

    # Activation is governed by the official Published Current state,
    # not by a UI-held proposal reference.
    current = _doc(db, "current_states", agent_id)
    proposal = _doc(db, "proposals", data.get("proposal_id"))

    publication_id = (
        current.get("publication_id")
        or data.get("publication_id")
        or proposal.get("publication_id")
    )
    revision_id = (
        current.get("revision_id")
        or data.get("revision_id")
        or proposal.get("revision_id")
    )

    if not all([target_agent, publication_id, revision_id]):
        return jsonify(
            {
                "error": (
                    "Published proposal, publication, and revision "
                    "are required."
                )
            }
        ), 400

    try:
        result = activate_ready_state(
            agent_id=agent_id,
            target_agent=target_agent,
            publication_id=publication_id,
            revision_id=revision_id,
            actor=_actor(),
            basis=(
                str(data.get("basis") or "").strip()
                or (
                    "Reactivated from Governance Workspace against "
                    "the explicitly published Delegation Boundary."
                )
            ),
        )
        return jsonify(_clean(result))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/action/request")
def action_request():
    data = request.get_json(silent=True) or {}
    try:
        result = request_protected_action(
            agent_id=data.get("agent_id", DEFAULT_AGENT_ID),
            action_name=data["action_name"],
            actor=_actor(),
            idempotency_key=(
                data.get("idempotency_key")
                or f"workspace-{uuid4().hex}"
            ),
            tool_name=data.get("tool_name"),
            data_class=data.get("data_class"),
            case_impact=data.get("case_impact"),
            human_review_clearance_id=data.get(
                "human_review_clearance_id"
            ),
        )
        return jsonify(_clean(result))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/action/execute")
def action_execute():
    data = request.get_json(silent=True) or {}
    action_request_id = data.get("action_request_id")
    if not action_request_id:
        return jsonify(
            {"error": "action_request_id is required."}
        ), 400

    try:
        response = requests.post(
            _runtime_url(
                EXECUTOR_RUNTIME_ID,
                "execute-protected-action",
            ),
            headers={
                "Authorization": f"Bearer {_access_token()}",
                "Content-Type": "application/json",
            },
            json={"action_request_id": action_request_id},
            timeout=90,
        )
        body = response.json()
        return jsonify(
            {
                "http_status": response.status_code,
                "result": body,
            }
        ), 200 if response.ok else 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.post("/api/identity-a-probe")
def identity_a_probe():
    try:
        response = requests.post(
            _runtime_url(
                GOVERNANCE_RUNTIME_ID,
                "protected-action-probe",
            ),
            headers={
                "Authorization": f"Bearer {_access_token()}",
                "Content-Type": "application/json",
            },
            json={},
            timeout=45,
        )
        body = response.json()
        return jsonify(
            {
                "outcome": body.get("outcome"),
                "protected_http_status": body.get("http_status"),
                "destination": body.get("destination"),
            }
        ), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
    )
