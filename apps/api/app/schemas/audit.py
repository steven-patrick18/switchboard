import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ts: datetime
    actor: str
    action: str
    subject: str
    before: dict | None
    after: dict | None
