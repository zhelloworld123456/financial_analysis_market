from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo


TraceListener = Callable[[dict[str, Any]], None]


class TraceRecorder:
    def __init__(self, session_id: str, listener: TraceListener) -> None:
        self.trace_id = f"trace-{uuid.uuid4().hex[:16]}"
        self.session_id = session_id
        self.root_span_id = f"span-{uuid.uuid4().hex[:12]}"
        self.listener = listener
        self.events: list[dict[str, Any]] = []
        self.started = time.monotonic()

    def start(self, stock_query: str, question: str) -> None:
        self.record(
            "trace/started",
            name="多 Agent 对话轮次",
            status="running",
            span_id=self.root_span_id,
            detail=question,
            data={"stock_query": stock_query, "mode": "sequential_dialogue"},
        )

    def span_started(
        self,
        name: str,
        agent_id: str,
        agent: str,
        input_from: list[str],
    ) -> str:
        span_id = f"span-{uuid.uuid4().hex[:12]}"
        self.record(
            "span/started",
            name=name,
            status="running",
            span_id=span_id,
            parent_span_id=self.root_span_id,
            agent_id=agent_id,
            agent=agent,
            detail=f"输入来源：{'、'.join(input_from)}",
            data={"input_from": input_from},
        )
        return span_id

    def span_completed(
        self,
        span_id: str,
        name: str,
        agent_id: str,
        agent: str,
        started: float,
        usage: dict[str, int] | None = None,
        request_id: str = "",
        detail: str = "",
    ) -> None:
        self.record(
            "span/completed",
            name=name,
            status="completed",
            span_id=span_id,
            parent_span_id=self.root_span_id,
            agent_id=agent_id,
            agent=agent,
            detail=detail,
            duration_ms=elapsed(started),
            usage=usage,
            request_id=request_id or None,
        )

    def span_failed(
        self,
        span_id: str,
        name: str,
        agent_id: str,
        agent: str,
        started: float,
        error: Exception,
    ) -> None:
        self.record(
            "span/failed",
            name=name,
            status="failed",
            span_id=span_id,
            parent_span_id=self.root_span_id,
            agent_id=agent_id,
            agent=agent,
            detail=str(error)[:300],
            duration_ms=elapsed(started),
        )

    def message(
        self,
        span_id: str,
        agent_id: str,
        agent: str,
        target: str,
        content: str,
        key_points: list[str] | None = None,
        handoff: str = "",
        status: str = "completed",
    ) -> dict[str, Any]:
        message = {
            "id": f"message-{uuid.uuid4().hex[:12]}",
            "role": "agent",
            "agent_id": agent_id,
            "agent": agent,
            "target": target,
            "content": content,
            "key_points": key_points or [],
            "handoff": handoff,
            "status": status,
            "trace_id": self.trace_id,
            "span_id": span_id,
            "timestamp": now(),
        }
        self.record(
            "agent/message",
            name=f"{agent} → {target}",
            status=status,
            span_id=span_id,
            parent_span_id=self.root_span_id,
            agent_id=agent_id,
            agent=agent,
            detail=content,
            message=message,
        )
        return message

    def user_message(self, content: str) -> dict[str, Any]:
        message = {
            "id": f"message-{uuid.uuid4().hex[:12]}",
            "role": "user",
            "agent_id": "user",
            "agent": "用户",
            "target": "研究主持 Agent",
            "content": content,
            "key_points": [],
            "status": "completed",
            "trace_id": self.trace_id,
            "span_id": self.root_span_id,
            "timestamp": now(),
        }
        self.record(
            "user/message",
            name="用户 → 研究主持 Agent",
            status="completed",
            span_id=self.root_span_id,
            agent_id="user",
            agent="用户",
            detail=content,
            message=message,
        )
        return message

    def complete(self, status: str, usage: dict[str, int]) -> None:
        self.record(
            "trace/completed",
            name="多 Agent 对话轮次",
            status=status,
            span_id=self.root_span_id,
            detail="本轮公开对话与最终回复已生成",
            duration_ms=elapsed(self.started),
            usage=usage,
        )

    def fail(self, error: Exception) -> None:
        self.record(
            "trace/failed",
            name="多 Agent 对话轮次",
            status="failed",
            span_id=self.root_span_id,
            detail=str(error)[:300],
            duration_ms=elapsed(self.started),
        )

    def record(self, event: str, **fields: Any) -> None:
        item = {
            "sequence": len(self.events) + 1,
            "event": event,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "timestamp": now(),
            **{key: value for key, value in fields.items() if value is not None},
        }
        self.events.append(item)
        self.listener({"type": "trace", "trace": item})


def zero_usage() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def add_usage(total: dict[str, int], usage: dict[str, int]) -> None:
    for key in total:
        total[key] += int(usage.get(key) or 0)


def elapsed(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="milliseconds")
