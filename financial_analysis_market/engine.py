from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from .agents import ResearchTeam
from .analysis import StockAnalyzer
from .conversation import ConversationService
from .errors import DataSourceError
from .forecast import WeeklyForecaster
from .kimi import KimiClient
from .providers import EastmoneyProvider
from .runtime import Context
from .scoring import ThreeFactorScorer
from .screener import StockScreener


class AnalysisEngine:
    def __init__(self, context: Context) -> None:
        self.context = context

    def analyze(self, query: str, use_ai: bool = True) -> dict[str, Any]:
        self.context.emit("analysis/started", {"query": query})
        provider = self.context.get("market_data")
        analyzer = self.context.get("analyzer")
        forecaster = self.context.get("forecaster")
        scorer = self.context.get("scorer")

        stock = provider.resolve(query)
        self.context.emit("stock/resolved", {"code": stock.code, "name": stock.name})
        bundle = provider.fetch_bundle(stock)
        self.context.emit(
            "market/loaded",
            {"code": stock.code, "bars": len(bundle.history), "reports": len(bundle.financials)},
        )
        warnings = list(bundle.warnings)
        sources = list(bundle.sources)
        try:
            market = provider.fetch_market_context(bundle.sector.code if bundle.sector else "")
            if market.indices or market.hotspots:
                sources.append("东方财富指数与行业热点行情")
        except DataSourceError:
            market = None
            warnings.append("市场热点与指数环境暂时不可用，相关预测因子按中性处理")
        technical = analyzer.technical(bundle.quote, bundle.history)
        fundamental = analyzer.fundamental(bundle.financials)
        historical = analyzer.historical(bundle.quote, bundle.history)
        scores = scorer.score(bundle, technical)
        forecast = forecaster.predict(
            bundle.history,
            bundle.quote.price,
            technical["rsi14"],
            sector=bundle.sector,
            market=market,
            news=bundle.news,
            announcement_review=scores["announcement_review"],
            financials=bundle.financials,
        )
        risks = analyzer.risks(technical, fundamental, bundle.financials)
        risks.extend(scores["risk_flags"])
        risks.extend(forecast["risk_notes"])
        if use_ai:
            ai_research = self.context.get("research_team").research(
                bundle, technical, fundamental, forecast, scores, risks, self.context, market
            )
        else:
            ai_research = {"enabled": False, "status": "disabled", "summary": "本次已关闭 Kimi 多 Agent 研究", "agents": []}
        self.context.emit("analysis/completed", {"code": stock.code})

        weekly_return = forecast["weekly_return_pct"]
        summary = (
            f"三维评分：{scores['summary']}。技术面{technical['signal']}，最新业绩{fundamental['status']}。"
            f"统计模型估计未来 5 个工作日累计涨跌幅为 {weekly_return:+.2f}%，"
            "该结果仅描述基准情景，实际走势可能显著偏离。"
        )
        return {
            "meta": {
                "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                "summary": summary,
                "disclaimer": "仅供数据研究与学习，不构成投资建议。短期预测不等于实际收益，请独立判断并控制风险。",
            },
            "stock": asdict(bundle.stock),
            "quote": asdict(bundle.quote),
            "technical": technical,
            "historical": historical,
            "fundamental": fundamental,
            "financials": [asdict(report) for report in bundle.financials],
            "scores": scores,
            "ai_research": ai_research,
            "sector": asdict(bundle.sector) if bundle.sector else None,
            "market_environment": asdict(market) if market else None,
            "news": [asdict(item) for item in bundle.news],
            "forecast": forecast,
            "history": [asdict(bar) for bar in bundle.history[-90:]],
            "risks": risks,
            "sources": sources,
            "warnings": warnings,
        }

    def chat(
        self,
        message: str,
        query: str = "",
        session_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        service = self.context.get("conversation")
        return service.respond(
            message=message,
            query=query,
            session_id=session_id,
            analysis_loader=lambda: self.analyze(query, use_ai=False),
        )

    def chat_snapshot(self, session_id: str) -> dict[str, Any]:
        return self.context.get("conversation").snapshot(session_id)

    def capabilities(self) -> dict[str, Any]:
        return {
            "ai": self.context.get("research_team").public_config(),
            "conversation": self.context.get("conversation").public_config(),
        }

    def screen(self, limit: int = 20) -> dict[str, Any]:
        self.context.emit("screen/started", {"limit": limit})
        result = self.context.get("screener").screen(limit)
        self.context.emit("screen/completed", {"screened": result["meta"]["screened"]})
        return result


def create_engine(timeout: float = 12.0) -> AnalysisEngine:
    context = Context()
    provider = EastmoneyProvider(timeout=timeout)
    analyzer = StockAnalyzer()
    scorer = ThreeFactorScorer()
    context.register("market_data", provider)
    context.register("analyzer", analyzer)
    context.register("scorer", scorer)
    context.register("forecaster", WeeklyForecaster())
    context.register("screener", StockScreener(provider, analyzer, scorer))
    kimi = KimiClient.from_env()
    context.register("research_team", ResearchTeam(kimi))
    context.register("conversation", ConversationService(kimi))
    return AnalysisEngine(context)
