from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field

from app.models.evidence import Evidence


class IncidentContext(BaseModel):
    incident_id: str

    service: str
    namespace: str
    label_selector: str | None = None
    deployment_name: str | None = None
    # Unknown is conservative: routing evidence is required until an actual
    # observation or explicit configuration establishes workload-only scope.
    service_required: bool = False
    service_applicability: Literal["unknown", "service_backed", "workload_only", "missing"] = "unknown"

    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    evidence: list[Evidence] = Field(default_factory=list)
