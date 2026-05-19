import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

PROJECT_STATUS_ACTIVE = "active"


class Project(Base):
    """A launch effort for a client, driven by a playbook."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    playbook_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default=PROJECT_STATUS_ACTIVE)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
