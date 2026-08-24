import json
import re


def validate_grounding_claims(evidence: str, draft: str) -> str:
    """Deterministically validate measured facts and URLs before persistence."""
    issues = []

    def normalize_numbers(text: str) -> set[str]:
        found = re.findall(
            r"\$?\d+(?:,\d{3})*(?:\.\d+)?%?",
            text or "",
        )
        return {
            re.sub(r"[$,%]", "", value)
            for value in found
        }

    evidence_numbers = normalize_numbers(evidence)

    try:
        data = json.loads(draft)
    except json.JSONDecodeError:
        return json.dumps(
            {
                "status": "FAIL",
                "issues": ["INVALID_JSON"],
                "rule": "Draft must be valid structured JSON.",
            },
            ensure_ascii=False,
        )

    def walk(value):
        if isinstance(value, dict):
            measured = value.get("measured_value")

            if measured is not None:
                for number in normalize_numbers(str(measured)):
                    if number not in evidence_numbers:
                        issues.append(
                            f"UNSUPPORTED_MEASURED_VALUE: {measured}"
                        )

            if value.get("target_status") == "EVIDENCE_DEFINED":
                target = value.get("target_value")

                if target is not None:
                    for number in normalize_numbers(str(target)):
                        if number not in evidence_numbers:
                            issues.append(
                                f"UNSUPPORTED_EVIDENCE_TARGET: {target}"
                            )

            for child in value.values():
                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

        elif isinstance(value, str):
            for token in value.split():
                candidate = token.strip("()[]{}<>.,;:")
                candidate = candidate.strip('"').strip("'")

                if candidate.startswith(("http://", "https://")):
                    if candidate not in evidence:
                        issues.append(
                            f"UNSUPPORTED_URL: {candidate}"
                        )

    walk(data)

    return json.dumps(
        {
            "status": "PASS" if not issues else "FAIL",
            "issues": sorted(set(issues)),
            "rule": (
                "Measured values and EVIDENCE_DEFINED targets must be "
                "grounded in source evidence. Proposed targets are allowed "
                "when explicitly marked PROPOSED_TARGET."
            ),
        },
        ensure_ascii=False,
    )
