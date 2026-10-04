from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


class KnowledgeDocument(BaseModel):
    source_type: Literal["runbook", "kubernetes_docs"]
    title: str
    url: str | None = None
    section: str | None = None
    content: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "content")
    @classmethod
    def nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("knowledge title and content must not be empty")
        return value

    @model_validator(mode="after")
    def validate_source(self):
        if self.source_type == "kubernetes_docs":
            parsed = urlparse(self.url or "")
            if parsed.scheme != "https" or parsed.hostname != "kubernetes.io" or not parsed.path.startswith("/docs/") or parsed.username or parsed.password:
                raise ValueError("Kubernetes documentation must use https://kubernetes.io/docs/")
        return self
