"""Capture and replay operator corrections to AI agents.

Wired in two places:
1. app/api/routes/approvals.py: when an approval is rejected with a
   reason or edited+approved with a note, we write an AgentLesson
   tagged with the agent that originally queued the action.
2. app/agents/base.py::run_agent: before the loop starts, the agent's
   most-recent lessons are fetched and prepended to the system prompt
   so the model sees its own past mistakes in context.

Owner-scoped: an operator's corrections only inform their own runs.
Volume-capped: we inject at most LESSON_INJECT_LIMIT lessons per run
so the prompt doesn't bloat unboundedly.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentLesson
from app.models.agent_lesson import SOURCE_EDIT, SOURCE_REJECTION

# How many recent lessons to inject into a single run's system prompt.
# Tuned high enough to be useful, low enough to keep the prompt budget
# bounded. Operators with more than this many lessons see the most
# recent ones; older ones still live in the DB and are still listed
# in the GUI.
LESSON_INJECT_LIMIT = 30


def _diff_payload(before: dict | None, after: dict | None) -> str:
    """Plain-language summary of what the operator changed about an
    approval's payload. Limits the diff to the keys that actually
    differ so the lesson text stays short."""
    before = before or {}
    after = after or {}
    keys = set(before) | set(after)
    diffs: list[str] = []
    for k in sorted(keys):
        b = before.get(k)
        a = after.get(k)
        if b == a:
            continue
        if k not in before:
            diffs.append(f"added `{k}` = {json.dumps(a)[:80]}")
        elif k not in after:
            diffs.append(f"removed `{k}`")
        else:
            diffs.append(
                f"changed `{k}` from {json.dumps(b)[:60]} to {json.dumps(a)[:60]}"
            )
    return "; ".join(diffs) or "(no field-level change)"


async def record_rejection_lesson(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    agent_name: str,
    action_type: str,
    reason: str,
    approval_id: uuid.UUID,
) -> None:
    """Operator rejected the agent's work — store the reason verbatim
    so the agent reads it before proposing similar actions again."""
    lesson = (
        f"When you queued `{action_type}`, the operator REJECTED it for "
        f"this reason: {reason}. Do not repeat the same mistake."
    )
    db.add(
        AgentLesson(
            owner_id=owner_id,
            agent_name=agent_name,
            source=SOURCE_REJECTION,
            action_type=action_type,
            lesson=lesson,
            source_approval_id=approval_id,
        )
    )


async def record_edit_lesson(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    agent_name: str,
    action_type: str,
    note: str | None,
    before_payload: dict | None,
    after_payload: dict | None,
    approval_id: uuid.UUID,
) -> None:
    """Operator approved-with-edits — record what they had to change so
    the agent gets it right next time without supervision."""
    diff = _diff_payload(before_payload, after_payload)
    extra = f" Operator note: {note}." if note else ""
    lesson = (
        f"When you queued `{action_type}`, the operator EDITED the payload "
        f"before approving: {diff}.{extra} Prefer this shape next time."
    )
    db.add(
        AgentLesson(
            owner_id=owner_id,
            agent_name=agent_name,
            source=SOURCE_EDIT,
            action_type=action_type,
            lesson=lesson,
            source_approval_id=approval_id,
        )
    )


async def fetch_recent_lessons(
    db: AsyncSession, *, owner_id: uuid.UUID, agent_name: str, limit: int = LESSON_INJECT_LIMIT
) -> list[AgentLesson]:
    rows = await db.scalars(
        select(AgentLesson)
        .where(
            AgentLesson.owner_id == owner_id,
            AgentLesson.agent_name == agent_name,
        )
        .order_by(AgentLesson.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())


def format_lessons_block(lessons: list[AgentLesson]) -> str:
    """Plain-text block prepended to the agent's system prompt. Empty
    string when there are no lessons, so an untrained agent's prompt
    is unchanged."""
    if not lessons:
        return ""
    bullets = "\n".join(f"- {lsn.lesson}" for lsn in reversed(lessons))
    return (
        "PAST CORRECTIONS FROM YOUR OPERATOR — read these before "
        "proposing anything. They reflect mistakes you made on prior "
        "runs that the operator had to fix. Avoid repeating them:\n"
        f"{bullets}\n"
    )
