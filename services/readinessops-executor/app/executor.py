import base64
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

import google.auth
import requests
from google.auth.transport.requests import Request
from google.cloud import firestore

PROJECT_ID = "readinessops-agent-governance"
TOPIC = "readinessops-protected-actions"
EXECUTOR_NAME = "readinessops-executor"


def _db():
    return firestore.Client(
        project=PROJECT_ID,
        client_options={
            "api_endpoint": "firestore.mtls.googleapis.com"
        },
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _claim_action(action_request_id: str) -> dict:
    """Fail-closed revalidation and at-most-once execution claim."""
    db = _db()
    now = datetime.now(timezone.utc)

    action_ref = db.collection("action_requests").document(action_request_id)
    claim_ref = db.collection("execution_claims").document(action_request_id)
    audit_ref = db.collection("audit_events").document(
        f"AUDIT_{uuid4().hex[:16]}"
    )

    transaction = db.transaction()

    @firestore.transactional
    def apply(transaction):
        action_snap = action_ref.get(transaction=transaction)
        claim_snap = claim_ref.get(transaction=transaction)

        if not action_snap.exists:
            raise ValueError("Action request does not exist.")

        action = action_snap.to_dict()

        if claim_snap.exists:
            claim = claim_snap.to_dict()
            return {
                "duplicate": True,
                "execution_status": "DUPLICATE_IGNORED",
                "action_request_id": action_request_id,
                "existing_claim_status": claim.get("status"),
                "message_id": claim.get("message_id"),
                "trace_id": claim.get("trace_id"),
            }

        agent_id = action.get("agent_id")
        boundary_id = action.get("delegation_boundary_id")

        agent_ref = db.collection("agents").document(agent_id)
        boundary_ref = db.collection("delegation_boundaries").document(
            boundary_id or "__missing__"
        )

        agent_snap = agent_ref.get(transaction=transaction)
        boundary_snap = boundary_ref.get(transaction=transaction)

        agent = agent_snap.to_dict() if agent_snap.exists else None
        boundary = boundary_snap.to_dict() if boundary_snap.exists else None

        pointer = None
        if agent and agent.get("target_agent"):
            pointer_ref = (
                db.collection("active_delegation_boundaries")
                .document(_slug(agent["target_agent"]))
            )
            pointer_snap = pointer_ref.get(transaction=transaction)
            if pointer_snap.exists:
                pointer = pointer_snap.to_dict()

        clearance = None
        clearance_id = action.get("human_review_clearance_id")
        if clearance_id:
            clearance_ref = (
                db.collection("human_review_clearances")
                .document(clearance_id)
            )
            clearance_snap = clearance_ref.get(transaction=transaction)
            if clearance_snap.exists:
                clearance = clearance_snap.to_dict()

        reasons = []

        if action.get("status") != "PERMITTED":
            reasons.append("Action request is not PERMITTED.")

        if not agent:
            reasons.append("Agent record is missing.")
        elif agent.get("readiness_status") != "READY":
            reasons.append(
                f"Agent readiness_status={agent.get('readiness_status')}; "
                "READY required."
            )

        if not boundary:
            reasons.append("Published Delegation Boundary is missing.")
        else:
            if boundary.get("status") != "ACTIVE":
                reasons.append("Delegation Boundary is not ACTIVE.")

            if action.get("action_name") not in (
                boundary.get("permitted_actions") or []
            ):
                reasons.append("Action is outside the active boundary.")

            if action.get("tool_name") not in (
                boundary.get("permitted_tools") or []
            ):
                reasons.append("Tool is outside the active boundary.")

            if action.get("data_class") not in (
                boundary.get("permitted_data_classes") or []
            ):
                reasons.append("Data class is outside the active boundary.")

            if action.get("case_impact") not in (
                boundary.get("permitted_case_impact") or []
            ):
                reasons.append("Case impact is outside the active boundary.")

        if agent and boundary:
            checks = [
                (
                    agent.get("active_delegation_boundary_id")
                    == boundary.get("boundary_id"),
                    "Agent / Boundary mismatch.",
                ),
                (
                    agent.get("active_delegation_boundary_version")
                    == boundary.get("version"),
                    "Boundary version mismatch.",
                ),
                (
                    agent.get("readiness_publication_id")
                    == boundary.get("publication_id"),
                    "Readiness Publication / Boundary mismatch.",
                ),
                (
                    agent.get("current_revision_id")
                    == boundary.get("revision_id"),
                    "Revision / Boundary mismatch.",
                ),
                (
                    action.get("publication_id")
                    == boundary.get("publication_id"),
                    "Action request / Publication mismatch.",
                ),
                (
                    action.get("revision_id")
                    == boundary.get("revision_id"),
                    "Action request / Revision mismatch.",
                ),
                (
                    action.get("delegation_boundary_id")
                    == boundary.get("boundary_id"),
                    "Action request / Boundary mismatch.",
                ),
            ]
            reasons.extend(reason for ok, reason in checks if not ok)

        if pointer and boundary:
            if pointer.get("boundary_id") != boundary.get("boundary_id"):
                reasons.append("Active pointer / Boundary mismatch.")
            if pointer.get("version") != boundary.get("version"):
                reasons.append("Active pointer version mismatch.")
        else:
            reasons.append("Active Delegation Boundary pointer is missing.")

        requires_review = bool(
            boundary
            and action.get("action_name")
            in (boundary.get("human_review_required_actions") or [])
        )

        if requires_review:
            if not clearance:
                reasons.append("Required Human Review Clearance is missing.")
            else:
                clearance_checks = [
                    (
                        clearance.get("status") == "APPROVED",
                        "Human Review Clearance is not APPROVED.",
                    ),
                    (
                        clearance.get("agent_id") == agent_id,
                        "Human Review Clearance agent mismatch.",
                    ),
                    (
                        clearance.get("action_name")
                        == action.get("action_name"),
                        "Human Review Clearance action mismatch.",
                    ),
                    (
                        clearance.get("delegation_boundary_id")
                        == boundary_id,
                        "Human Review Clearance boundary mismatch.",
                    ),
                    (
                        clearance.get("publication_id")
                        == action.get("publication_id"),
                        "Human Review Clearance publication mismatch.",
                    ),
                ]
                reasons.extend(
                    reason for ok, reason in clearance_checks if not ok
                )

        if reasons:
            transaction.set(audit_ref, {
                "audit_id": audit_ref.id,
                "event_type": "EXECUTOR_REVALIDATION_DENIED",
                "action_request_id": action_request_id,
                "agent_id": agent_id,
                "reasons": reasons,
                "executor": EXECUTOR_NAME,
                "trace_id": action.get("trace_id"),
                "trace_revision_id": action.get("trace_revision_id"),
                "created_at": now,
            })

            return {
                "duplicate": False,
                "execution_status": "DENIED",
                "action_request_id": action_request_id,
                "reasons": reasons,
            }

        transaction.set(claim_ref, {
            "action_request_id": action_request_id,
            "status": "CLAIMED",
            "agent_id": agent_id,
            "action_name": action.get("action_name"),
            "delegation_boundary_id": boundary_id,
            "publication_id": action.get("publication_id"),
            "trace_id": action.get("trace_id"),
            "trace_revision_id": action.get("trace_revision_id"),
            "executor": EXECUTOR_NAME,
            "claimed_at": now,
        })

        return {
            "duplicate": False,
            "execution_status": "CLAIMED",
            "action_request": action,
        }

    return apply(transaction)


def execute_permitted_action(action_request_id: str) -> dict:
    claimed = _claim_action(action_request_id)

    if claimed.get("duplicate"):
        return claimed

    if claimed["execution_status"] == "DENIED":
        return claimed

    action = claimed["action_request"]

    credentials, detected_project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())

    project = detected_project or PROJECT_ID

    payload = {
        "action_request_id": action_request_id,
        "agent_id": action["agent_id"],
        "action_name": action["action_name"],
        "publication_id": action.get("publication_id"),
        "delegation_boundary_id": action.get("delegation_boundary_id"),
        "delegation_boundary_version": action.get(
            "delegation_boundary_version"
        ),
        "trace_id": action.get("trace_id"),
        "trace_revision_id": action.get("trace_revision_id"),
    }

    encoded = base64.b64encode(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).decode("ascii")

    url = (
        f"https://pubsub.googleapis.com/v1/projects/{project}/"
        f"topics/{TOPIC}:publish"
    )

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        },
        json={
            "messages": [{
                "data": encoded,
                "attributes": {
                    "readinessops_action_request_id": action_request_id,
                    "readinessops_executor": EXECUTOR_NAME,
                    "readinessops_trace_id": (
                        action.get("trace_id") or "UNRESOLVED"
                    ),
                },
            }]
        },
        timeout=20,
    )

    db = _db()
    now = datetime.now(timezone.utc)
    claim_ref = db.collection("execution_claims").document(action_request_id)
    action_ref = db.collection("action_requests").document(action_request_id)
    audit_ref = db.collection("audit_events").document(
        f"AUDIT_{uuid4().hex[:16]}"
    )

    if response.ok:
        body = response.json()
        message_ids = body.get("messageIds", [])
        message_id = message_ids[0] if message_ids else None

        batch = db.batch()
        batch.update(claim_ref, {
            "status": "EXECUTED",
            "message_id": message_id,
            "executed_at": now,
        })
        batch.update(action_ref, {
            "execution_status": "EXECUTED",
            "executor": EXECUTOR_NAME,
            "message_id": message_id,
            "executed_at": now,
        })
        batch.set(audit_ref, {
            "audit_id": audit_ref.id,
            "event_type": "PROTECTED_ACTION_EXECUTED",
            "action_request_id": action_request_id,
            "agent_id": action["agent_id"],
            "action_name": action["action_name"],
            "delegation_boundary_id": action.get(
                "delegation_boundary_id"
            ),
            "publication_id": action.get("publication_id"),
            "message_id": message_id,
            "executor": EXECUTOR_NAME,
            "trace_id": action.get("trace_id"),
            "trace_revision_id": action.get("trace_revision_id"),
            "created_at": now,
        })
        batch.commit()

        return {
            "execution_status": "EXECUTED",
            "action_request_id": action_request_id,
            "message_id": message_id,
            "destination": "pubsub.googleapis.com",
            "trace_id": action.get("trace_id"),
        }

    error_text = response.text[:1000]

    batch = db.batch()
    batch.update(claim_ref, {
        "status": "FAILED",
        "http_status": response.status_code,
        "error": error_text,
        "failed_at": now,
    })
    batch.update(action_ref, {
        "execution_status": "FAILED",
        "executor": EXECUTOR_NAME,
        "execution_error": error_text,
        "updated_at": now,
    })
    batch.set(audit_ref, {
        "audit_id": audit_ref.id,
        "event_type": "PROTECTED_ACTION_EXECUTION_FAILED",
        "action_request_id": action_request_id,
        "http_status": response.status_code,
        "error": error_text,
        "executor": EXECUTOR_NAME,
        "trace_id": action.get("trace_id"),
        "trace_revision_id": action.get("trace_revision_id"),
        "created_at": now,
    })
    batch.commit()

    return {
        "execution_status": "FAILED",
        "action_request_id": action_request_id,
        "http_status": response.status_code,
        "error": error_text,
        "trace_id": action.get("trace_id"),
    }
