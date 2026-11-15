from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .kimi import KimiClient
from .models import MarketContext, StockBundle
from .runtime import Context


SPECIALIST_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "maxLength": 600},
        "key_points": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 5},
        "risks": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 5},
        "evidence": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 5},
    },
    "required": ["summary", "key_points", "risks", "evidence"],
    "additionalProperties": False,
}

LEAD_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "maxLength": 120},
        "summary": {"type": "string", "maxLength": 1000},
        "strengths": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 5},
        "risks": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 5},
        "watch_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"topic": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["topic", "reason"],
                "additionalProperties": False,
            },
            "maxItems": 5,
        },
        "research_conclusion": {"type": "string"},
    },
    "required": ["headline", "summary", "strengths", "risks", "watch_items", "research_conclusion"],
    "additionalProperties": False,
}


class ResearchTeam:
    def __init__(self, client: KimiClient | None, workers: int = 3) -> None:
        self.client = client
        self.workers = workers
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()

    def public_config(self) -> dict[str, Any]:
        if self.client:
            config = self.client.public_config()
            config["specialists"] = ["基本面", "估值", "市场", "公告", "风险"]
            return config
        return {"available": False, "model": "kimi-k3.1-pro", "specialists": []}

    def research(
        self,
        bundle: StockBundle,
        technical: dict[str, Any],
        fundamental: dict[str, Any],
        forecast: dict[str, Any],
        scores: dict[str, Any],
        risks: list[str],
        context: Context | None = None,
        market: MarketContext | None = None,
    ) -> dict[str, Any]:
        if not self.client:
            return {
                "enabled": False,
                "status": "unavailable",
                "model": "kimi-k3.1-pro",
                "summary": "未配置 KIMI_API_KEY，当前仅展示确定性规则分析结果",
                "agents": [],
            }
        run_id = f"research-{uuid.uuid4().hex[:12]}"
        base = _base_evidence(bundle, technical, fundamental, forecast, scores, risks, market)
        digest = hashlib.sha256(
            json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(digest)
            if cached and now - cached[0] < 600:
                return {**cached[1], "cache_hit": True}
        events: list[dict[str, Any]] = []
        event_lock = threading.Lock()

        def record(event: str, agent: str, extra: dict[str, Any] | None = None) -> None:
            payload = {"event": event, "agent": agent, "time": _now(), **(extra or {})}
            with event_lock:
                events.append(payload)
            if context:
                context.emit(event, {"run_id": run_id, **payload})

        specs = _specialist_specs(base)
        specialists: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(self.workers, len(specs))) as pool:
            futures = [pool.submit(self._run_specialist, spec, record) for spec in specs]
            specialists = [future.result() for future in futures]

        lead = self._run_lead(base, specialists, record)
        all_usage = [agent["usage"] for agent in [*specialists, lead]]
        usage = {
            key: sum(item[key] for item in all_usage)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        successful = sum(agent["status"] == "completed" for agent in specialists)
        status = "completed" if lead["status"] == "completed" and successful == len(specialists) else "partial"
        if lead["status"] != "completed":
            status = "degraded"
        output = {
            "enabled": True,
            "status": status,
            "cache_hit": False,
            "run_id": run_id,
            "input_digest": digest,
            "model": self.client.model,
            "reasoning_effort": self.client.reasoning_effort,
            "headline": lead["headline"],
            "summary": lead["summary"],
            "strengths": lead["strengths"],
            "risks": lead["risks"],
            "watch_items": lead["watch_items"],
            "research_conclusion": lead["research_conclusion"],
            "agents": specialists,
            "lead": {"status": lead["status"], "duration_ms": lead["duration_ms"], "error": lead["error"]},
            "usage": usage,
            "events": events,
            "generated_at": _now(),
            "notice": "Kimi 仅解释输入事实与规则结果，不替代公告原文、财务附注和独立投资判断；页面不保存或展示思维链",
        }
        if status == "completed":
            with self._cache_lock:
                self._cache[digest] = (now, output)
        return output

    def _run_specialist(self, spec: dict[str, Any], record: Any) -> dict[str, Any]:
        agent_id = spec["id"]
        record("research/agent/started", agent_id)
        started = time.monotonic()
        result = _empty_specialist(agent_id, spec["title"])
        try:
            response = self.client.complete(
                role=spec["title"],
                instructions=spec["instructions"],
                evidence=spec["evidence"],
                schema=SPECIALIST_SCHEMA,
                max_completion_tokens=spec.get("max_completion_tokens", 2000),
            )
            result.update(_validate_specialist(response.content), usage=response.usage, status="completed")
            record("research/agent/completed", agent_id, {"duration_ms": _elapsed(started)})
        except Exception as exc:
            result["error"] = str(exc)[:240]
            result["summary"] = "该专家任务未完成，主编仅使用其他专家和规则模型结果"
            record("research/agent/failed", agent_id, {"duration_ms": _elapsed(started)})
        result["duration_ms"] = _elapsed(started)
        return result

    def _run_lead(
        self, base: dict[str, Any], specialists: list[dict[str, Any]], record: Any
    ) -> dict[str, Any]:
        record("research/lead/started", "lead")
        started = time.monotonic()
        result = _fallback_lead(base)
        result.update({"status": "failed", "duration_ms": 0, "usage": _zero_usage(), "error": None})
        evidence = {
            "stock": base["stock"],
            "quote": base["quote"],
            "deterministic_scores": base["scores"],
            "forecast": base["forecast"],
            "specialist_results": [
                {
                    "role": agent["title"],
                    "status": agent["status"],
                    "summary": agent["summary"],
                    "key_points": agent["key_points"],
                    "risks": agent["risks"],
                    "evidence": agent["evidence"],
                }
                for agent in specialists
            ],
        }
        try:
            response = self.client.complete(
                role="主编 Agent",
                instructions=(
                    "综合五个专家任务与确定性评分，形成简洁、相互一致的研究摘要。"
                    "专家观点与事实冲突时以事实为准；不得修改三维评分和五日预测。"
                    "说明优势、主要风险及后续需要跟踪的可验证事项。"
                ),
                evidence=evidence,
                schema=LEAD_SCHEMA,
                max_completion_tokens=2200,
            )
            result.update(_validate_lead(response.content), usage=response.usage, status="completed")
            record("research/lead/completed", "lead", {"duration_ms": _elapsed(started)})
        except Exception as exc:
            result["error"] = str(exc)[:240]
            record("research/lead/failed", "lead", {"duration_ms": _elapsed(started)})
        result["duration_ms"] = _elapsed(started)
        return result


def _specialist_specs(base: dict[str, Any]) -> list[dict[str, Any]]:
    common = {"stock": base["stock"], "quote": base["quote"], "data_as_of": base["data_as_of"]}
    return [
        {
            "id": "fundamental",
            "title": "基本面 Agent",
            "instructions": "分析盈利质量、营收与利润成长、现金含量和多期稳定性，不重新计算或修改好股票分数。",
            "evidence": {**common, "financials": base["financials"], "fundamental": base["fundamental"], "score": base["scores"]["good_stock"]},
        },
        {
            "id": "valuation",
            "title": "估值 Agent",
            "instructions": "结合行业估值口径分析 PE、PB 与估值参考区间，强调区间依赖模型假设，不是目标价。",
            "evidence": {**common, "latest_financial": base["financials"][0] if base["financials"] else None, "industry_note": base["scores"]["industry_note"], "score": base["scores"]["good_price"]},
        },
        {
            "id": "market",
            "title": "市场 Agent",
            "instructions": "只输出简洁结论：分析趋势、RSI、行业相对强弱和预测不确定性，不修改或逐日复述预测数字，最终摘要不超过200字。",
            "max_completion_tokens": 4000,
            "evidence": {
                **common,
                "technical": base["technical"],
                "sector": base["sector"],
                "market_environment": base["market_environment"],
                "news_review": base["forecast"]["news_review"],
                "forecast": {
                    "method": base["forecast"]["method"],
                    "weekly_return_pct": base["forecast"]["weekly_return_pct"],
                    "weekly_direction": base["forecast"]["weekly_direction"],
                    "interval_note": base["forecast"]["interval_note"],
                },
                "score": base["scores"]["good_timing"],
            },
        },
        {
            "id": "announcement",
            "title": "公告 Agent",
            "instructions": "只根据公告标题和日期识别事件，不得声称读过公告全文或推断未提供的合同金额和业绩影响。",
            "evidence": {**common, "announcement_review": base["scores"]["announcement_review"], "news_review": base["forecast"]["news_review"], "warnings": base["warnings"]},
        },
        {
            "id": "risk",
            "title": "风险 Agent",
            "instructions": "作为红队审查财务、估值、技术、行业、公告、数据完整性和模型风险，优先指出其他专家可能忽略的反证。",
            "evidence": {**common, "fundamental": base["fundamental"], "technical": base["technical"], "scores": base["scores"], "known_risks": base["known_risks"]},
        },
    ]


def _base_evidence(
    bundle: StockBundle,
    technical: dict[str, Any],
    fundamental: dict[str, Any],
    forecast: dict[str, Any],
    scores: dict[str, Any],
    risks: list[str],
    market: MarketContext | None = None,
) -> dict[str, Any]:
    return {
        "stock": asdict(bundle.stock),
        "quote": asdict(bundle.quote),
        "data_as_of": bundle.quote.quoted_at,
        "financials": [asdict(report) for report in bundle.financials],
        "fundamental": fundamental,
        "technical": technical,
        "sector": asdict(bundle.sector) if bundle.sector else None,
        "market_environment": asdict(market) if market else None,
        "news": [asdict(item) for item in bundle.news],
        "forecast": forecast,
        "scores": scores,
        "known_risks": risks,
        "warnings": list(bundle.warnings),
    }


def _validate_specialist(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": _text(content, "summary"),
        "key_points": _text_list(content, "key_points"),
        "risks": _text_list(content, "risks"),
        "evidence": _text_list(content, "evidence"),
    }


def _validate_lead(content: dict[str, Any]) -> dict[str, Any]:
    watch_items = content.get("watch_items")
    if not isinstance(watch_items, list):
        raise ValueError("主编 watch_items 必须是数组")
    normalized_watch = []
    for item in watch_items[:5]:
        if not isinstance(item, dict):
            raise ValueError("watch_items 元素必须是对象")
        normalized_watch.append({"topic": _text(item, "topic"), "reason": _text(item, "reason")})
    return {
        "headline": _text(content, "headline"),
        "summary": _text(content, "summary"),
        "strengths": _text_list(content, "strengths"),
        "risks": _text_list(content, "risks"),
        "watch_items": normalized_watch,
        "research_conclusion": _text(content, "research_conclusion"),
    }


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


def _empty_specialist(agent_id: str, title: str) -> dict[str, Any]:
    return {
        "id": agent_id,
        "title": title,
        "status": "failed",
        "summary": "",
        "key_points": [],
        "risks": [],
        "evidence": [],
        "duration_ms": 0,
        "usage": _zero_usage(),
        "error": None,
    }


def _fallback_lead(base: dict[str, Any]) -> dict[str, Any]:
    scores = base["scores"]
    return {
        "headline": f"{base['stock']['name']}：{scores['verdict']}",
        "summary": f"Kimi 主编任务未完成，当前使用规则结果：{scores['summary']}。",
        "strengths": [scores["good_stock"]["reasons"][0], scores["good_timing"]["reasons"][0]],
        "risks": base["known_risks"][:5],
        "watch_items": [
            {"topic": "下一期定期报告", "reason": "核对营收、利润和经营现金流是否延续当前趋势"},
            {"topic": "公告全文", "reason": "确认标题事件的交易金额、条件与潜在影响"},
            {"topic": "行业相对强弱", "reason": "观察个股是否继续跑赢或跑输所属板块"},
        ],
        "research_conclusion": "保留确定性三维评分，等待更多可验证信息，不形成买卖指令",
    }


def _zero_usage() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _elapsed(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
