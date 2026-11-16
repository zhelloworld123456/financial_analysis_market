from __future__ import annotations

import copy
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .kimi import KimiClient
from .tracing import TraceRecorder, add_usage, elapsed, now, zero_usage


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "maxLength": 600},
        "focus": {"type": "string", "maxLength": 240},
    },
    "required": ["message", "focus"],
    "additionalProperties": False,
}

DIALOGUE_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "maxLength": 1200},
        "key_points": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 4},
        "handoff": {"type": "string", "maxLength": 300},
    },
    "required": ["message", "key_points", "handoff"],
    "additionalProperties": False,
}

LEAD_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "maxLength": 2400},
        "key_points": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 5},
        "caveats": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 4},
    },
    "required": ["answer", "key_points", "caveats"],
    "additionalProperties": False,
}


@dataclass
class ChatSession:
    id: str
    query: str
    created_at: str
    analysis: dict[str, Any] | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class ConversationService:
    def __init__(self, client: KimiClient | None, max_sessions: int = 24) -> None:
        self.client = client
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, ChatSession] = OrderedDict()
        self._sessions_lock = threading.Lock()

    def public_config(self) -> dict[str, Any]:
        return {
            "available": self.client is not None,
            "model": self.client.model if self.client else "kimi-k3.1-pro",
            "mode": "sequential_dialogue",
            "agents": ["研究主持", "基本面", "市场", "风险审查", "主编"],
        }

    def respond(
        self,
        message: str,
        query: str,
        session_id: str | None,
        analysis_loader: Callable[[], dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        if not self.client:
            yield {"type": "error", "error": "未配置 KIMI_API_KEY，无法运行多 Agent 对话"}
            return
        session = self._resolve_session(query, session_id)
        pending: list[dict[str, Any]] = []
        trace = TraceRecorder(session.id, pending.append)
        with session.lock:
            trace.start(session.query, message)
            yield from self._drain(pending)
            total_usage = zero_usage()
            turn_messages: list[dict[str, Any]] = []
            degraded = False
            try:
                if session.analysis is None:
                    data_span = trace.span_started("构建股票事实快照", "data", "数据上下文", ["东方财富", "规则模型"])
                    data_started = time.monotonic()
                    try:
                        session.analysis = analysis_loader()
                    except Exception as exc:
                        trace.span_failed(data_span, "构建股票事实快照", "data", "数据上下文", data_started, exc)
                        raise
                    stock = session.analysis["stock"]
                    trace.span_completed(
                        data_span,
                        "构建股票事实快照",
                        "data",
                        "数据上下文",
                        data_started,
                        detail=f"{stock['name']}（{stock['code']}）事实快照已就绪",
                    )
                else:
                    data_span = trace.span_started("复用股票事实快照", "data", "数据上下文", ["当前会话"])
                    trace.span_completed(
                        data_span,
                        "复用股票事实快照",
                        "data",
                        "数据上下文",
                        time.monotonic(),
                        detail="沿用本会话行情与财报快照；重新选股可开启新会话",
                    )
                yield from self._drain(pending)

                user_message = trace.user_message(message)
                turn_messages.append(user_message)
                session.messages.append(user_message)
                session.messages = session.messages[-16:]
                yield from self._drain(pending)

                evidence = self._evidence(session, message, turn_messages)
                host = self._run_agent(
                    trace,
                    evidence,
                    agent_id="orchestrator",
                    agent="研究主持 Agent",
                    target="基本面 Agent",
                    role="研究主持 Agent",
                    instructions=(
                        "理解用户当前问题，结合历史对话确定本轮研究重点。"
                        "用公开消息向基本面 Agent 分派任务，不要直接给出最终投资结论。"
                    ),
                    schema=PLAN_SCHEMA,
                    output_kind="plan",
                    fallback="本轮按基本面、市场、风险审查、主编的顺序讨论用户问题。",
                )
                total_usage = self._merge_agent_usage(total_usage, host)
                turn_messages.append(host["message"])
                degraded = degraded or host["degraded"]
                yield from self._drain(pending)

                specs = [
                    (
                        "fundamental",
                        "基本面 Agent",
                        "市场 Agent",
                        "分析盈利质量、成长、现金流与估值事实。回应研究主持的公开任务，并把需要市场 Agent 补充的问题写入 handoff。",
                    ),
                    (
                        "market",
                        "市场 Agent",
                        "风险审查 Agent",
                        "分析趋势、行业相对强弱和五日统计预测的不确定性。明确回应上一位 Agent 的公开观点与 handoff，可以赞同或反驳，再把需要风险审查的问题写入 handoff。",
                    ),
                    (
                        "risk",
                        "风险审查 Agent",
                        "主编 Agent",
                        "作为红队回应前面 Agent 的公开观点，优先检查 seven_factor.veto、red_flags 和数据覆盖度，指出证据不足、相互矛盾和主要下行风险。不得为了质疑而编造事实，并向主编给出需要保留的风险边界。",
                    ),
                ]
                for agent_id, agent, target, focus in specs:
                    evidence["current_turn_messages"] = self._public_dialogue(turn_messages)
                    result = self._run_agent(
                        trace,
                        evidence,
                        agent_id=agent_id,
                        agent=agent,
                        target=target,
                        role=agent,
                        instructions=(
                            "这是多 Agent 连续公开讨论，不是独立研究报告。必须阅读 current_turn_messages，"
                            f"回应前一位 Agent 后再补充本领域判断。{focus}"
                        ),
                        schema=DIALOGUE_SCHEMA,
                        output_kind="dialogue",
                        fallback="该 Agent 本轮未能完成回应，后续 Agent 将依据事实快照和其他公开意见继续讨论。",
                    )
                    total_usage = self._merge_agent_usage(total_usage, result)
                    turn_messages.append(result["message"])
                    degraded = degraded or result["degraded"]
                    yield from self._drain(pending)

                evidence["current_turn_messages"] = self._public_dialogue(turn_messages)
                lead = self._run_agent(
                    trace,
                    evidence,
                    agent_id="lead",
                    agent="主编 Agent",
                    target="用户",
                    role="主编 Agent",
                    instructions=(
                        "阅读本轮所有公开对话，直接回答用户当前问题。整合有证据的共识，说明专家分歧，"
                        "不要逐位复述发言，不要输出思维链。结论必须区分事实、规则模型和统计预测，保留必要风险提示。"
                    ),
                    schema=LEAD_SCHEMA,
                    output_kind="lead",
                    max_completion_tokens=1600,
                    fallback=self._fallback_answer(session.analysis),
                )
                total_usage = self._merge_agent_usage(total_usage, lead)
                turn_messages.append(lead["message"])
                degraded = degraded or lead["degraded"]
                session.messages.append(lead["message"])
                session.messages = session.messages[-16:]
                session.updated_at = now()
                yield from self._drain(pending)

                status = "degraded" if degraded else "completed"
                trace.complete(status, total_usage)
                yield from self._drain(pending)
                turn = {
                    "id": f"turn-{uuid.uuid4().hex[:12]}",
                    "trace_id": trace.trace_id,
                    "question": message,
                    "answer": lead["message"]["content"],
                    "status": status,
                    "usage": total_usage,
                    "duration_ms": elapsed(trace.started),
                    "created_at": now(),
                    "messages": turn_messages,
                    "events": trace.events,
                }
                session.turns.append(turn)
                session.turns = session.turns[-12:]
                yield {
                    "type": "done",
                    "session_id": session.id,
                    "trace_id": trace.trace_id,
                    "turn_id": turn["id"],
                    "status": status,
                    "usage": total_usage,
                    "duration_ms": turn["duration_ms"],
                }
            except Exception as exc:
                trace.fail(exc)
                yield from self._drain(pending)
                yield {"type": "error", "session_id": session.id, "trace_id": trace.trace_id, "error": str(exc)[:400]}

    def snapshot(self, session_id: str) -> dict[str, Any]:
        with self._sessions_lock:
            session = self._sessions.get(session_id)
        if not session:
            raise ValueError("对话会话不存在或服务已重启，请新建会话")
        with session.lock:
            stock = session.analysis["stock"] if session.analysis else None
            return {
                "id": session.id,
                "query": session.query,
                "stock": copy.deepcopy(stock),
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "messages": copy.deepcopy(session.messages),
                "turns": copy.deepcopy(session.turns),
            }

    def _resolve_session(self, query: str, session_id: str | None) -> ChatSession:
        with self._sessions_lock:
            if session_id:
                session = self._sessions.get(session_id)
                if not session:
                    raise ValueError("对话会话不存在或服务已重启，请新建会话")
                if query and query != session.query:
                    raise ValueError("同一会话不能更换股票，请点击“新会话”")
                self._sessions.move_to_end(session_id)
                return session
            if not query:
                raise ValueError("新会话需要输入股票名称或代码")
            session = ChatSession(id=f"chat-{uuid.uuid4().hex[:16]}", query=query, created_at=now())
            self._sessions[session.id] = session
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)
            return session

    def _evidence(self, session: ChatSession, message: str, turn_messages: list[dict[str, Any]]) -> dict[str, Any]:
        assert session.analysis is not None
        analysis = session.analysis
        return {
            "fact_snapshot": {
                key: analysis[key]
                for key in (
                    "stock",
                    "quote",
                    "technical",
                    "historical",
                    "fundamental",
                    "financials",
                    "scores",
                    "sector",
                    "market_environment",
                    "forecast",
                    "risks",
                    "warnings",
                )
            },
            "conversation_history": self._public_dialogue(session.messages[:-1]),
            "current_user_message": message,
            "current_turn_messages": self._public_dialogue(turn_messages),
        }

    def _run_agent(
        self,
        trace: TraceRecorder,
        evidence: dict[str, Any],
        agent_id: str,
        agent: str,
        target: str,
        role: str,
        instructions: str,
        schema: dict[str, Any],
        output_kind: str,
        fallback: str,
        max_completion_tokens: int = 1100,
    ) -> dict[str, Any]:
        input_from = ["事实快照", "历史对话", "用户问题"]
        if evidence["current_turn_messages"]:
            input_from.append("本轮前序 Agent 公开消息")
        span_id = trace.span_started(f"{agent}处理中", agent_id, agent, input_from)
        started = time.monotonic()
        usage = zero_usage()
        degraded = False
        try:
            assert self.client is not None
            response = self.client.complete(
                role=role,
                instructions=instructions,
                evidence=evidence,
                schema=schema,
                max_completion_tokens=max_completion_tokens,
            )
            content = self._validate_output(response.content, output_kind)
            usage = response.usage
            public_message = trace.message(
                span_id,
                agent_id,
                agent,
                target,
                content["message"],
                key_points=content["key_points"],
                handoff=content["handoff"],
            )
            trace.span_completed(
                span_id,
                f"{agent}处理中",
                agent_id,
                agent,
                started,
                usage=usage,
                request_id=response.request_id,
                detail=f"公开回复已发送给{target}",
            )
        except Exception as exc:
            degraded = True
            trace.span_failed(span_id, f"{agent}处理中", agent_id, agent, started, exc)
            public_message = trace.message(
                span_id,
                agent_id,
                agent,
                target,
                fallback,
                handoff="模型调用未完成，请依据事实快照和其他公开意见继续。",
                status="failed",
            )
        return {"message": public_message, "usage": usage, "degraded": degraded}

    def _validate_output(self, content: dict[str, Any], kind: str) -> dict[str, Any]:
        if kind == "plan":
            return {
                "message": _text(content, "message"),
                "key_points": [_text(content, "focus")],
                "handoff": _text(content, "focus"),
            }
        if kind == "dialogue":
            return {
                "message": _text(content, "message"),
                "key_points": _text_list(content, "key_points"),
                "handoff": _text(content, "handoff"),
            }
        return {
            "message": _text(content, "answer"),
            "key_points": _text_list(content, "key_points"),
            "handoff": "；".join(_text_list(content, "caveats")),
        }

    def _public_dialogue(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "sender": item["agent"],
                "target": item["target"],
                "message": item["content"],
                "handoff": item.get("handoff", ""),
            }
            for item in messages
        ]

    def _merge_agent_usage(self, total: dict[str, int], result: dict[str, Any]) -> dict[str, int]:
        add_usage(total, result["usage"])
        return total

    def _fallback_answer(self, analysis: dict[str, Any]) -> str:
        stock = analysis["stock"]
        scores = analysis["scores"]
        return (
            f"主编模型本轮调用未完成。依据已保存的规则结果，{stock['name']}当前结论为“{scores['verdict']}”："
            f"{scores['summary']}。以上仅为规则模型结果，请结合各 Agent 已生成的公开意见与原始公告独立判断。"
        )

    def _drain(self, pending: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        while pending:
            yield pending.pop(0)


def _text(content: dict[str, Any], key: str) -> str:
    value = content.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError(f"模型字段 {key} 无效")
    return value.strip()


def _text_list(content: dict[str, Any], key: str) -> list[str]:
    value = content.get(key)
    if not isinstance(value, list):
        raise ValueError(f"模型字段 {key} 必须是数组")
    return [_text({"value": item}, "value") for item in value[:5]]
