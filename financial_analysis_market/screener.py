from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .analysis import StockAnalyzer
from .errors import DataSourceError, FinancialAnalysisError
from .models import Stock, StockBundle
from .providers import EastmoneyProvider
from .scoring import ThreeFactorScorer


class StockScreener:
    def __init__(
        self,
        provider: EastmoneyProvider,
        analyzer: StockAnalyzer,
        scorer: ThreeFactorScorer,
        workers: int = 5,
    ) -> None:
        self.provider = provider
        self.analyzer = analyzer
        self.scorer = scorer
        self.workers = workers
        self._cache: dict[int, tuple[float, dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()

    def screen(self, limit: int = 20) -> dict[str, Any]:
        if not 5 <= limit <= 50:
            raise ValueError("候选池数量必须在 5～50 之间")
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(limit)
            if cached and now - cached[0] < 600:
                return cached[1]
        stocks = self.provider.list_candidates(limit)
        results = []
        failed = 0
        with ThreadPoolExecutor(max_workers=min(self.workers, len(stocks))) as pool:
            futures = {pool.submit(self._research, stock): stock for stock in stocks}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except FinancialAnalysisError:
                    failed += 1
        if not results:
            raise DataSourceError("候选股票均未获取到有效数据，请稍后重试")
        results.sort(key=lambda item: item["scores"]["overall_score"], reverse=True)
        output = {
            "meta": {
                "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                "universe": "A 股大市值研究样本",
                "universe_note": f"按总市值降序选取前 {limit} 只非 ST A 股作为精简候选池，不覆盖全市场；结果用于研究排序而非买入名单",
                "requested": limit,
                "screened": len(results),
                "failed": failed,
                "cache_seconds": 600,
            },
            "items": results,
            "qualified": [
                item
                for item in results
                if not item["scores"]["seven_factor"]["veto"]["triggered"]
                and min(
                    item["scores"]["good_stock"]["score"],
                    item["scores"]["good_price"]["score"],
                    item["scores"]["good_timing"]["score"],
                ) >= 65
            ][:10],
            "categories": {
                "overall": results[:6],
                "good_stock": sorted(results, key=lambda item: item["scores"]["good_stock"]["score"], reverse=True)[:6],
                "good_price": sorted(results, key=lambda item: item["scores"]["good_price"]["score"], reverse=True)[:6],
                "good_timing": sorted(results, key=lambda item: item["scores"]["good_timing"]["score"], reverse=True)[:6],
            },
            "method": {
                "好股票": "盈利、成长、现金流、毛利率和多期稳定性",
                "好价格": "分行业 PE/PB、盈利法和净资产法估值参考",
                "好时机": "趋势、动量、RSI、波动、板块相对强弱和近90日公告标题事件",
                "综合": "七维评分按有效权重归一化；重大风险否决项不参与平均，触发后不进入优选名单",
            },
            "disclaimer": "筛选是因子排序，不构成投资建议。候选池存在大市值偏差，公告关键词不能替代公告全文和财务报表附注。",
        }
        with self._cache_lock:
            self._cache[limit] = (now, output)
        return output

    def _research(self, stock: Stock) -> dict[str, Any]:
        bundle = self.provider.fetch_bundle(stock, include_news=False)
        return self._compact(bundle)

    def _compact(self, bundle: StockBundle) -> dict[str, Any]:
        technical = self.analyzer.technical(bundle.quote, bundle.history)
        fundamental = self.analyzer.fundamental(bundle.financials)
        historical = self.analyzer.historical(bundle.quote, bundle.history)
        scores = self.scorer.score(bundle, technical, historical)
        return {
            "stock": asdict(bundle.stock),
            "quote": asdict(bundle.quote),
            "technical": {
                "signal": technical["signal"],
                "rsi14": technical["rsi14"],
                "momentum_20d_pct": technical["momentum_20d_pct"],
                "momentum_60d_pct": technical["momentum_60d_pct"],
                "annual_volatility_pct": technical["annual_volatility_pct"],
            },
            "fundamental": fundamental,
            "earnings": asdict(bundle.financials[0]) if bundle.financials else None,
            "sector": asdict(bundle.sector) if bundle.sector else None,
            "scores": scores,
            "warnings": list(bundle.warnings),
        }
