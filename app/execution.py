from datetime import datetime, timezone
from uuid import uuid4

from google.cloud import firestore

PROJECT_ID = "readinessops-agent-governance"


def attempt_protected_action(
    agent_id: str,
    action_name: str,
    actor: str,
) -> dict:
    db = firestore.Client(project=PROJECT_ID)
    now = datetime.now(timezone.utc)

    agent_ref = db.collection("agents").document(agent_id)
    agent_snap = agent_ref.get()

    if not agent_snap.exists:
        raise ValueError("Agent not found.")

    agent = agent_snap.to_dict()

    execution_id = f"EXEC_{uuid4().hex[:16]}"
    audit_id = f"AUDIT_{uuid4().hex[:16]}"

    readiness_status = agent.get("readiness_status")

    if readiness_status != "READY":
        status = "DENIED"
        reason = (
            f"Protected action denied because agent readiness_status="
            f"{readiness_status}. READY is required."
        )
    else:
        status = "EXECUTED"
        reason = "Protected action allowed by ReadinessOps readiness gate."

    batch = db.batch()

    batch.set(db.collection("action_executions").document(execution_id), {
        "execution_id": execution_id,
        "agent_id": agent_id,
        "action_name": action_name,
        "status": status,
        "reason": reason,
        "readiness_status_at_request": readiness_status,
        "requested_by": actor,
        "created_at": now,
    })

    batch.set(db.collection("audit_events").document(audit_id), {
        "audit_id": audit_id,
        "event_type": f"PROTECTED_ACTION_{status}",
        "execution_id": execution_id,
        "agent_id": agent_id,
        "action_name": action_name,
        "readiness_status": readiness_status,
        "actor_type": "HUMAN_TEST",
        "actor": actor,
        "reason": reason,
        "created_at": now,
    })

    batch.commit()

    return {
        "execution_id": execution_id,
        "agent_id": agent_id,
        "action_name": action_name,
        "readiness_status": readiness_status,
        "execution_status": status,
        "reason": reason,
        "audit_id": audit_id,
    }
