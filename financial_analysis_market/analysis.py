from __future__ import annotations

import math
import statistics
from typing import Any

from .models import FinancialReport, PriceBar, Quote


class StockAnalyzer:
    def technical(self, quote: Quote, history: tuple[PriceBar, ...]) -> dict[str, Any]:
        closes = [bar.close for bar in history]
        closes[-1] = quote.price
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        dif = [fast - slow for fast, slow in zip(ema12, ema26)]
        dea = _ema(dif, 9)
        rsi14 = _rsi(closes, 14)
        ma5 = _sma(closes, 5)
        ma20 = _sma(closes, 20)
        ma60 = _sma(closes, 60)
        returns = _log_returns(closes)
        daily_volatility = statistics.stdev(returns[-20:]) * 100 if len(returns) >= 20 else 0.0
        annual_volatility = daily_volatility * math.sqrt(244)
        atr14 = _atr(history, 14)

        score = 0
        score += 1 if ma20 is not None and quote.price >= ma20 else -1
        score += 1 if ma5 is not None and ma20 is not None and ma5 >= ma20 else -1
        score += 1 if dif[-1] >= dea[-1] else -1
        signal = "偏强" if score >= 2 else "偏弱" if score <= -2 else "震荡"

        return {
            "signal": signal,
            "ma5": _round(ma5),
            "ma20": _round(ma20),
            "ma60": _round(ma60),
            "rsi14": round(rsi14, 2),
            "macd_dif": round(dif[-1], 4),
            "macd_dea": round(dea[-1], 4),
            "macd_histogram": round(2 * (dif[-1] - dea[-1]), 4),
            "momentum_5d_pct": _round(_momentum(closes, 5)),
            "momentum_20d_pct": _round(_momentum(closes, 20)),
            "momentum_60d_pct": _round(_momentum(closes, 60)),
            "daily_volatility_pct": round(daily_volatility, 3),
            "annual_volatility_pct": round(annual_volatility, 2),
            "atr14": _round(atr14),
            "support_20d": round(min(bar.low for bar in history[-20:]), 2),
            "resistance_20d": round(max(bar.high for bar in history[-20:]), 2),
        }

    def historical(self, quote: Quote, history: tuple[PriceBar, ...]) -> dict[str, Any]:
        closes = [bar.close for bar in history]
        closes[-1] = quote.price
        returns = _log_returns(closes)
        year_window = history[-244:]
        year_closes = closes[-244:]
        peak = year_closes[0]
        max_drawdown = 0.0
        for close in year_closes:
            peak = max(peak, close)
            max_drawdown = min(max_drawdown, (close / peak - 1) * 100)
        high_52w = max(bar.high for bar in year_window)
        low_52w = min(bar.low for bar in year_window)
        yearly: dict[str, list[float]] = {}
        for bar in history:
            yearly.setdefault(bar.date[:4], []).append(bar.close)
        yearly_returns = [
            {"year": year, "return_pct": round((values[-1] / values[0] - 1) * 100, 2)}
            for year, values in sorted(yearly.items())
        ]
        changes = [bar.change_pct for bar in year_window]
        return {
            "period_start": history[0].date,
            "period_end": history[-1].date,
            "bars": len(history),
            "return_5d_pct": _round(_momentum(closes, 5)),
            "return_20d_pct": _round(_momentum(closes, 20)),
            "return_60d_pct": _round(_momentum(closes, 60)),
            "return_120d_pct": _round(_momentum(closes, 120)),
            "return_244d_pct": _round(_momentum(closes, 244)),
            "period_return_pct": _round((closes[-1] / closes[0] - 1) * 100),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "drawdown_from_52w_high_pct": _round((quote.price / high_52w - 1) * 100),
            "position_52w_pct": _round((quote.price - low_52w) / (high_52w - low_52w) * 100) if high_52w > low_52w else 50.0,
            "max_drawdown_1y_pct": _round(max_drawdown),
            "annual_volatility_1y_pct": round(statistics.stdev(returns[-244:]) * math.sqrt(244) * 100, 2) if len(returns) >= 2 else None,
            "ma244": _round(_sma(closes, 244)),
            "average_amount_20d": round(statistics.fmean(bar.amount for bar in history[-20:]), 2),
            "up_days_60d_pct": _round(sum(change > 0 for change in changes[-60:]) / len(changes[-60:]) * 100),
            "best_day_pct": _round(max(changes)),
            "worst_day_pct": _round(min(changes)),
            "yearly_returns": yearly_returns,
            "yearly_note": "年度涨跌按样本内每年首末收盘价估算，首年可能不是完整年度",
        }

    def fundamental(self, reports: tuple[FinancialReport, ...]) -> dict[str, Any]:
        if not reports:
            return {"status": "无数据", "summary": "暂未获取到最新定期报告，不能判断业绩趋势"}
        latest = reports[0]
        revenue_growth = latest.revenue_yoy_pct
        profit_growth = latest.net_profit_yoy_pct
        if revenue_growth is not None and profit_growth is not None:
            if revenue_growth > 0 and profit_growth > 0:
                status = "增长"
            elif revenue_growth < 0 and profit_growth < 0:
                status = "承压"
            else:
                status = "分化"
        else:
            status = "数据不足"
        summary = (
            f"{latest.report_type}：营收同比 {_signed(revenue_growth)}，"
            f"归母净利润同比 {_signed(profit_growth)}，加权 ROE {_percent(latest.roe_pct)}"
        )
        return {"status": status, "summary": summary}

    def risks(
        self,
        technical: dict[str, Any],
        fundamental: dict[str, Any],
        reports: tuple[FinancialReport, ...],
    ) -> list[str]:
        risks = [
            "五日结果来自历史价格统计外推，未包含突发公告、政策、停复牌和涨跌停影响",
            "预测日期按工作日推算，法定节假日及临时休市需以交易所日历为准",
        ]
        if technical["annual_volatility_pct"] > 35:
            risks.append(f"近 20 日年化波动率约 {technical['annual_volatility_pct']:.1f}%，短期波动较高")
        if technical["rsi14"] >= 70:
            risks.append(f"RSI14 为 {technical['rsi14']:.1f}，存在短线超买回落风险")
        elif technical["rsi14"] <= 30:
            risks.append(f"RSI14 为 {technical['rsi14']:.1f}，处于超卖区，趋势仍可能继续下行")
        if reports and reports[0].net_profit_yoy_pct is not None and reports[0].net_profit_yoy_pct < 0:
            risks.append(f"最新报告归母净利润同比下降 {abs(reports[0].net_profit_yoy_pct):.2f}%")
        if fundamental["status"] == "无数据":
            risks.append("缺少最新财务数据，基本面结论不完整")
        return risks


def _sma(values: list[float], window: int) -> float | None:
    return statistics.fmean(values[-window:]) if len(values) >= window else None


def _ema(values: list[float], window: int) -> list[float]:
    alpha = 2 / (window + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(result[-1] + alpha * (value - result[-1]))
    return result


def _rsi(values: list[float], window: int) -> float:
    changes = [values[index] - values[index - 1] for index in range(len(values) - window, len(values))]
    gains = statistics.fmean(max(change, 0) for change in changes)
    losses = statistics.fmean(max(-change, 0) for change in changes)
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100 - 100 / (1 + gains / losses)


def _atr(history: tuple[PriceBar, ...], window: int) -> float | None:
    if len(history) < window + 1:
        return None
    ranges = []
    for index in range(len(history) - window, len(history)):
        current = history[index]
        previous_close = history[index - 1].close
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous_close),
                abs(current.low - previous_close),
            )
        )
    return statistics.fmean(ranges)


def _log_returns(closes: list[float]) -> list[float]:
    return [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]


def _momentum(closes: list[float], days: int) -> float | None:
    if len(closes) <= days:
        return None
    return (closes[-1] / closes[-1 - days] - 1) * 100


def _round(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def _signed(value: float | None) -> str:
    return f"{value:+.2f}%" if value is not None else "暂无数据"


def _percent(value: float | None) -> str:
    return f"{value:.2f}%" if value is not None else "暂无数据"
