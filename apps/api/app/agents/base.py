import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_learning import fetch_recent_lessons, format_lessons_block
from app.agents.tools import AUTO_TIERS, GATED_TIERS, Tool, ToolContext
from app.audit import record_audit
from app.config import settings
from app.models.agent_run import AgentRun
from app.models.approval import DECISION_PENDING, Approval
from app.notifications import notify_approval_queued

# USD per 1M tokens (input, output). Cache reads ~0.1x, writes ~1.25x.
_PRICING = {
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@dataclass(frozen=True)
class AgentSpec:
    name: str
    system_prompt: str
    tools: list[Tool]
    model: str | None = None  # None → settings.agent_model

    def tool(self, name: str) -> Tool | None:
        return next((t for t in self.tools if t.name == name), None)


@dataclass
class RunResult:
    text: str
    approval_ids: list[uuid.UUID] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    duration_ms: int = 0
    iterations: int = 0


def _cost(model: str, usage) -> float:
    in_price, out_price = _PRICING.get(model, (5.0, 25.0))
    ti = getattr(usage, "input_tokens", 0) or 0
    to = getattr(usage, "output_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (
        ti / 1e6 * in_price
        + to / 1e6 * out_price
        + cw / 1e6 * in_price * 1.25
        + cr / 1e6 * in_price * 0.1
    )


async def run_agent(
    spec: AgentSpec,
    *,
    client,
    db: AsyncSession,
    task_id: uuid.UUID,
    instruction: str,
    client_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
) -> RunResult:
    """Manual agentic loop. Every T2/T3 tool call is intercepted and lands
    in the approval queue instead of executing — the queue is the product."""

    model = spec.model or settings.agent_model
    ctx = ToolContext(
        db=db,
        client_id=client_id,
        project_id=project_id,
        task_id=task_id,
        agent_name=spec.name,
    )
    # Frozen system prompt → cache the prefix (tools + system).
    # Past corrections are prepended UNCACHED so the agent's most recent
    # lessons re-render each run; the operator's prompt itself stays
    # cacheable. Net result: free supervised learning across runs.
    system: list[dict] = []
    if owner_id is not None:
        lessons = await fetch_recent_lessons(
            db, owner_id=owner_id, agent_name=spec.name
        )
        block = format_lessons_block(lessons)
        if block:
            system.append({"type": "text", "text": block})
    system.append(
        {
            "type": "text",
            "text": spec.system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    )
    anthropic_tools = [t.to_anthropic() for t in spec.tools]
    messages: list[dict] = [{"role": "user", "content": instruction}]

    result = RunResult(text="")
    started = time.monotonic()

    for _ in range(settings.agent_max_iterations):
        result.iterations += 1
        resp = await client.messages.create(
            model=model,
            max_tokens=settings.agent_max_tokens,
            system=system,
            tools=anthropic_tools,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.agent_effort},
            messages=messages,
        )

        usage = getattr(resp, "usage", None)
        if usage is not None:
            result.tokens_in += getattr(usage, "input_tokens", 0) or 0
            result.tokens_out += getattr(usage, "output_tokens", 0) or 0
            result.cost += _cost(model, usage)

        if resp.stop_reason != "tool_use":
            result.text = "".join(
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            )
            break

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            tool: Tool | None = spec.tool(block.name)
            if tool is None:
                # Scoped whitelist: agent may not use unlisted tools.
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Tool '{block.name}' is not permitted for this agent.",
                        "is_error": True,
                    }
                )
            elif tool.tier in AUTO_TIERS and tool.db_runner is not None:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": await tool.db_runner(dict(block.input), ctx),
                    }
                )
            elif tool.tier in AUTO_TIERS and tool.runner is not None:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": tool.runner(dict(block.input)),
                    }
                )
            elif tool.tier in GATED_TIERS:
                approval = Approval(
                    task_id=task_id,
                    action_type=tool.name,
                    tier=tool.tier,
                    payload=dict(block.input),
                    decision=DECISION_PENDING,
                )
                db.add(approval)
                await db.flush()
                result.approval_ids.append(approval.id)
                await record_audit(
                    db,
                    actor=spec.name,
                    action="approval.queued",
                    subject=f"approval:{approval.id}",
                    client_id=client_id,
                    after={"tier": tool.tier, "payload": dict(block.input)},
                )
                # Best-effort email — never blocks the agent loop.
                await _notify_owner_of_approval(
                    db, client_id, approval, tool.name, tool.tier
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": (
                            f"Action queued for operator approval (tier "
                            f"{tool.tier}, approval {approval.id}). It will "
                            f"NOT execute until a human approves. Do not retry; "
                            f"summarize what you prepared and stop."
                        ),
                    }
                )
            else:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Tool '{tool.name}' is misconfigured.",
                        "is_error": True,
                    }
                )
        messages.append({"role": "user", "content": tool_results})

    result.duration_ms = int((time.monotonic() - started) * 1000)

    db.add(
        AgentRun(
            agent=spec.name,
            task_id=task_id,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost=round(result.cost, 4),
            duration_ms=result.duration_ms,
        )
    )
    await db.commit()
    return result


async def _notify_owner_of_approval(
    db: AsyncSession,
    client_id: uuid.UUID,
    approval: Approval,
    action_type: str,
    tier: str,
) -> None:
    """Look up the client's owning operator and best-effort email them
    that a tool-use was queued for approval. The notify helper swallows
    SMTP errors so the agent loop is never blocked by a misconfig."""
    # Local imports to avoid touching the import order of the agents
    # package on test paths that never trigger gated tools.
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.client import Client  # noqa: PLC0415
    from app.models.user import User  # noqa: PLC0415

    row = (
        await db.execute(
            select(User.email, Client.name)
            .join(Client, Client.owner_id == User.id)
            .where(Client.id == client_id)
        )
    ).first()
    if row is None:
        return
    email, client_name = row
    await notify_approval_queued(
        db=db,
        to=email,
        client_name=client_name,
        action_type=action_type,
        tier=tier,
        approval_id=str(approval.id),
    )
