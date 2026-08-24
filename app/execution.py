from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from google.cloud import firestore

PROJECT_ID = "readinessops-agent-governance"


def _db():
    return firestore.Client(project=PROJECT_ID)


def evaluate_execution_gate(
    *,
    agent: dict,
    boundary: dict | None,
    active_pointer: dict | None,
    action_name: str,
    tool_name: str | None = None,
    data_class: str | None = None,
    case_impact: str | None = None,
    human_review_cleared: bool = False,
    now: datetime | None = None,
) -> list[str]:
    """Return deterministic denial reasons. Empty list means PERMITTED."""
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    if agent.get("readiness_status") != "READY":
        reasons.append(
            f"Agent readiness_status={agent.get('readiness_status')}; READY required."
        )

    if not boundary:
        reasons.append("No active Delegation Boundary record.")
        return reasons

    if not active_pointer:
        reasons.append("No active Delegation Boundary pointer.")
        return reasons

    boundary_id = boundary.get("boundary_id")

    if boundary.get("status") != "ACTIVE":
        reasons.append(
            f"Delegation Boundary status={boundary.get('status')}; ACTIVE required."
        )

    if agent.get("active_delegation_boundary_id") != boundary_id:
        reasons.append("Agent / Delegation Boundary mismatch.")

    if active_pointer.get("boundary_id") != boundary_id:
        reasons.append("Active pointer / Delegation Boundary mismatch.")

    if (
        agent.get("active_delegation_boundary_version")
        != boundary.get("version")
    ):
        reasons.append("Delegation Boundary version mismatch.")

    if (
        agent.get("readiness_publication_id")
        != boundary.get("publication_id")
    ):
        reasons.append("Readiness Publication / Boundary mismatch.")

    if (
        agent.get("current_revision_id")
        != boundary.get("revision_id")
    ):
        reasons.append("Current Revision / Boundary mismatch.")

    if (
        agent.get("target_agent")
        and agent.get("target_agent") != boundary.get("target_agent")
    ):
        reasons.append("Agent target / Boundary target mismatch.")

    permitted_actions = boundary.get("permitted_actions") or []
    prohibited_actions = boundary.get("prohibited_actions") or []

    # Empty permitted_actions is intentionally fail-closed.
    if action_name not in permitted_actions:
        reasons.append(
            f"Action '{action_name}' is not permitted by active boundary."
        )

    if action_name in prohibited_actions:
        reasons.append(
            f"Action '{action_name}' is explicitly prohibited."
        )

    if tool_name is not None:
        if tool_name not in (boundary.get("permitted_tools") or []):
            reasons.append(
                f"Tool '{tool_name}' is not permitted by active boundary."
            )

    if data_class is not None:
        if data_class not in (
            boundary.get("permitted_data_classes") or []
        ):
            reasons.append(
                f"Data class '{data_class}' is not permitted."
            )

    if case_impact is not None:
        if case_impact not in (
            boundary.get("permitted_case_impact") or []
        ):
            reasons.append(
                f"Case impact '{case_impact}' is not permitted."
            )

    human_review_required = action_name in (
        boundary.get("human_review_required_actions") or []
    )

    if human_review_required and not human_review_cleared:
        reasons.append(
            "Active Delegation Boundary requires a valid "
            "human review clearance for this action."
        )

    valid_until = boundary.get("valid_until")
    if valid_until is not None and valid_until <= now:
        reasons.append("Delegation Boundary has expired.")

    return reasons


def request_protected_action(
    agent_id: str,
    action_name: str,
    actor: str,
    *,
    idempotency_key: str,
    tool_name: str | None = None,
    data_class: str | None = None,
    case_impact: str | None = None,
    human_review_clearance_id: str | None = None,
) -> dict:
    db = _db()
    now = datetime.now(timezone.utc)

    request_id = f"ACTREQ_{uuid4().hex[:16]}"
    audit_id = f"AUDIT_{uuid4().hex[:16]}"

    agent_ref = db.collection("agents").document(agent_id)

    transaction = db.transaction()

    @firestore.transactional
    def apply(transaction):
        agent_snap = agent_ref.get(transaction=transaction)

        if not agent_snap.exists:
            raise ValueError("Agent not found.")

        agent = agent_snap.to_dict()

        target_agent = agent.get("target_agent")
        boundary_id = agent.get("active_delegation_boundary_id")

        active_pointer = None
        boundary = None

        if target_agent:
            import re
            target_slug = re.sub(
                r"[^a-z0-9]+",
                "-",
                target_agent.lower(),
            ).strip("-")

            pointer_ref = (
                db.collection("active_delegation_boundaries")
                .document(target_slug)
            )
            pointer_snap = pointer_ref.get(transaction=transaction)

            if pointer_snap.exists:
                active_pointer = pointer_snap.to_dict()

        if boundary_id:
            boundary_ref = (
                db.collection("delegation_boundaries")
                .document(boundary_id)
            )
            boundary_snap = boundary_ref.get(transaction=transaction)

            if boundary_snap.exists:
                boundary = boundary_snap.to_dict()

        human_review_cleared = False
        clearance_validation_reason = None

        if human_review_clearance_id:
            clearance_ref = (
                db.collection("human_review_clearances")
                .document(human_review_clearance_id)
            )
            clearance_snap = clearance_ref.get(transaction=transaction)

            if not clearance_snap.exists:
                clearance_validation_reason = (
                    "Human review clearance record does not exist."
                )
            else:
                clearance = clearance_snap.to_dict()
                expires_at = clearance.get("expires_at")

                checks = [
                    (
                        clearance.get("status") == "APPROVED",
                        "Human review clearance is not APPROVED.",
                    ),
                    (
                        clearance.get("agent_id") == agent_id,
                        "Human review clearance agent mismatch.",
                    ),
                    (
                        clearance.get("action_name") == action_name,
                        "Human review clearance action mismatch.",
                    ),
                    (
                        clearance.get("delegation_boundary_id")
                        == boundary_id,
                        "Human review clearance boundary mismatch.",
                    ),
                    (
                        clearance.get("publication_id")
                        == agent.get("readiness_publication_id"),
                        "Human review clearance publication mismatch.",
                    ),
                    (
                        expires_at is None or expires_at > now,
                        "Human review clearance has expired.",
                    ),
                ]

                failures = [reason for ok, reason in checks if not ok]

                if failures:
                    clearance_validation_reason = "; ".join(failures)
                else:
                    human_review_cleared = True

        denial_reasons = evaluate_execution_gate(
            agent=agent,
            boundary=boundary,
            active_pointer=active_pointer,
            action_name=action_name,
            tool_name=tool_name,
            data_class=data_class,
            case_impact=case_impact,
            human_review_cleared=human_review_cleared,
            now=now,
        )

        boundary_requires_review = (
            action_name in (
                boundary.get("human_review_required_actions") or []
            )
            if boundary else False
        )

        if (
            boundary_requires_review
            and human_review_clearance_id
            and not human_review_cleared
            and clearance_validation_reason
        ):
            denial_reasons.append(clearance_validation_reason)

        gate_status = "DENIED" if denial_reasons else "PERMITTED"

        publication_scope = agent.get("readiness_publication_id") or "NONE"
        boundary_version_scope = (
            str(boundary.get("version")) if boundary else "NONE"
        )

        idem_id = sha256(
            (
                f"{agent_id}|{action_name}|{publication_scope}|"
                f"{boundary_version_scope}|{idempotency_key}"
            ).encode("utf-8")
        ).hexdigest()

        idem_ref = db.collection("action_idempotency").document(idem_id)
        idem_snap = idem_ref.get(transaction=transaction)

        if idem_snap.exists:
            existing = idem_snap.to_dict()
            return {
                "duplicate": True,
                "gate_status": "DUPLICATE_IGNORED",
                "original_action_request_id": existing.get(
                    "action_request_id"
                ),
                "original_status": existing.get("status"),
                "idempotency_key": idempotency_key,
                "publication_id": publication_scope,
                "delegation_boundary_version": existing.get(
                    "delegation_boundary_version"
                ),
            }

        reason = (
            "; ".join(denial_reasons)
            if denial_reasons
            else (
                "Protected action permitted by deterministic "
                "Readiness + Published Delegation Boundary gate."
            )
        )

        action_ref = (
            db.collection("action_requests").document(request_id)
        )
        audit_ref = (
            db.collection("audit_events").document(audit_id)
        )

        record = {
            "action_request_id": request_id,
            "agent_id": agent_id,
            "action_name": action_name,
            "tool_name": tool_name,
            "data_class": data_class,
            "case_impact": case_impact,
            "human_review_required": boundary_requires_review,
            "human_review_clearance_id": human_review_clearance_id,
            "status": gate_status,
            "reason": reason,
            "gate_reasons": denial_reasons,
            "readiness_status_at_request": agent.get(
                "readiness_status"
            ),
            "publication_id": agent.get(
                "readiness_publication_id"
            ),
            "revision_id": agent.get("current_revision_id"),
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": (
                boundary.get("version") if boundary else None
            ),
            "idempotency_key": idempotency_key,
            "requested_by": actor,
            "created_at": now,
        }

        transaction.set(action_ref, record)

        # DENIED requests are auditable but do not consume the
        # idempotency key. A later human clearance or newly published
        # boundary must be able to retry the same business action.
        if gate_status == "PERMITTED":
            transaction.set(idem_ref, {
                "idempotency_id": idem_id,
                "idempotency_key": idempotency_key,
                "agent_id": agent_id,
                "action_name": action_name,
                "action_request_id": request_id,
                "status": gate_status,
                "publication_id": agent.get("readiness_publication_id"),
                "delegation_boundary_id": boundary_id,
                "delegation_boundary_version": (
                    boundary.get("version") if boundary else None
                ),
                "human_review_clearance_id": human_review_clearance_id,
                "created_at": now,
            })

        transaction.set(audit_ref, {
            "audit_id": audit_id,
            "event_type": f"PROTECTED_ACTION_{gate_status}",
            "action_request_id": request_id,
            "agent_id": agent_id,
            "action_name": action_name,
            "readiness_status": agent.get("readiness_status"),
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": (
                boundary.get("version") if boundary else None
            ),
            "actor_type": "REQUESTER",
            "actor": actor,
            "reason": reason,
            "created_at": now,
        })

        return {
            "duplicate": False,
            "action_request_id": request_id,
            "agent_id": agent_id,
            "action_name": action_name,
            "gate_status": gate_status,
            "gate_reasons": denial_reasons,
            "readiness_status": agent.get("readiness_status"),
            "delegation_boundary_id": boundary_id,
            "delegation_boundary_version": (
                boundary.get("version") if boundary else None
            ),
            "idempotency_key": idempotency_key,
            "audit_id": audit_id,
        }

    return apply(transaction)


def attempt_protected_action(
    agent_id: str,
    action_name: str,
    actor: str,
) -> dict:
    """Compatibility wrapper. New code should use request_protected_action."""
    return request_protected_action(
        agent_id,
        action_name,
        actor,
        idempotency_key=f"legacy-{uuid4().hex}",
    )
