import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ApprovalOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    client_id: uuid.UUID
    client_name: str
    action_type: str
    tier: str
    payload: dict | None
    decision: str
    reviewer_id: uuid.UUID | None
    note: str | None
    executed_at: datetime | None
    execution_result: str | None
    result_document_id: uuid.UUID | None
    email_packet: dict | None = None
    email_sent_at: datetime | None = None
    email_message_id: str | None = None
    ts: datetime


class SendEmailBody(BaseModel):
    """Optional overrides if the operator wants to tweak the prefilled
    packet before clicking Send. Any field omitted falls back to the
    saved Approval.email_packet."""

    to: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=255)
    body: str | None = Field(default=None, max_length=20000)
    cc: list[str] | None = Field(default=None, max_length=20)
    # 'client' (default) uses the per-client email credential — the From
    # line is the client, which is what regulators expect for chain-of-
    # custody. 'platform' uses the operator's own SMTP — only useful
    # for internal mail (test sends, internal forwarding).
    via: Literal["client", "platform"] = "client"


class SendEmailResult(BaseModel):
    sent: bool
    message_id: str | None
    error: str | None


class RecordReplyBody(BaseModel):
    """Paste-the-reply affordance: when NECA / FCC / a carrier replies
    by email, the operator pastes the full reply text here. The
    platform spawns a new agent task with the reply as context so the
    same agent that drafted the original can produce the next step."""

    reply: str = Field(min_length=1, max_length=20000)
    from_address: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _strip_nonempty(self) -> "RecordReplyBody":
        if not self.reply.strip():
            raise ValueError("reply cannot be blank")
        return self


class RecordReplyResult(BaseModel):
    new_task_id: uuid.UUID
    ran: bool
    new_approval_ids: list[uuid.UUID]
    agent_reply: str | None


class ApproveBody(BaseModel):
    # Provide to edit-and-approve: replaces the queued payload and records
    # the decision as "edited".
    payload_override: dict | None = None
    note: str | None = Field(default=None, max_length=2000)


class RejectBody(BaseModel):
    # Required: every rejection carries a reason so the audit trail
    # explains why. Whitespace-only is rejected by the validator below.
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _strip_nonempty(self) -> "RejectBody":
        if not self.reason.strip():
            raise ValueError("reason cannot be blank")
        return self


class BatchBody(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _reject_needs_note(self) -> "BatchBody":
        if self.decision == "rejected" and not (self.note or "").strip():
            raise ValueError(
                "A note is required when rejecting a batch — explain why for "
                "the audit trail."
            )
        return self


class BatchResult(BaseModel):
    updated: list[uuid.UUID]
    skipped: list[uuid.UUID]


class SendBackBody(BaseModel):
    """Operator's correction: this approval is wrong; re-run the agent
    with this feedback so it produces an updated draft."""

    feedback: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _strip_nonempty(self) -> "SendBackBody":
        if not self.feedback.strip():
            raise ValueError("feedback cannot be blank")
        return self


class SendBackResult(BaseModel):
    rejected_approval_id: uuid.UUID
    new_task_id: uuid.UUID
    ran: bool
    # If ran=True: the new approval(s) the agent queued on its re-run.
    new_approval_ids: list[uuid.UUID]
    agent_reply: str | None
