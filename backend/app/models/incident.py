from datetime import datetime, timezone
from pydantic import BaseModel, Field

from app.models.evidence import Evidence


class IncidentContext(BaseModel):
    incident_id: str

    service: str
    namespace: str

    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    evidence: list[Evidence] = Field(default_factory=list)