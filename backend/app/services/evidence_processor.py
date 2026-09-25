from app.models.evidence import Evidence


IMPORTANT_EVENT_REASONS = {
    "BackOff",
    "Failed",
    "Unhealthy",
    "FailedMount",
    "FailedScheduling",
    "OOMKilling",
}


def normalize_logs(text: str) -> str:
    if text.startswith("b'") or text.startswith('b"'):
        text = text[2:-1]

    return (
        text
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\'", "'")
        .replace('\\"', '"')
    )


def process_evidence(evidence_list: list[Evidence]) -> list[Evidence]:
    processed = []
    seen_events = set()

    for evidence in evidence_list:

        if evidence.category == "kubernetes_event":
            reason = evidence.data.get("reason")

            # Keep warnings and important events.
            if (
                evidence.severity != "warning"
                and reason not in IMPORTANT_EVENT_REASONS
            ):
                continue

            key = (
                reason,
                evidence.data.get("message"),
                evidence.reference,
            )

            if key in seen_events:
                continue

            seen_events.add(key)

        if evidence.category == "application_log":
            logs = evidence.data.get("logs")

            if isinstance(logs, str):
                evidence.data["logs"] = normalize_logs(logs)

        processed.append(evidence)

    return processed