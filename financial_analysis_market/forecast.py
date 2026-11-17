from __future__ import annotations

import math
import statistics
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .models import FinancialReport, MarketContext, NewsItem, PriceBar, SectorPerformance


class WeeklyForecaster:
    METHOD = "技术动量 + AR(1) 均值回复 + 行业周期 + 市场热点 + 公告新闻 + 业绩因子"

    def predict(
        self,
        history: tuple[PriceBar, ...],
        current_price: float,
        rsi14: float,
        days: int = 5,
        today: date | None = None,
        sector: SectorPerformance | None = None,
        market: MarketContext | None = None,
        news: tuple[NewsItem, ...] = (),
        announcement_review: dict[str, Any] | None = None,
        financials: tuple[FinancialReport, ...] = (),
    ) -> dict[str, Any]:
        closes = [bar.close for bar in history]
        closes[-1] = current_price
        returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
        sample = returns[-120:]
        daily_volatility = max(statistics.stdev(sample[-60:]), 0.002)
        mean_5 = statistics.fmean(sample[-5:])
        mean_20 = statistics.fmean(sample[-20:])
        mean_60 = statistics.fmean(sample[-60:])
        momentum = 0.5 * mean_5 + 0.3 * mean_20 + 0.2 * mean_60
        baseline = 0.65 * momentum + 0.35 * statistics.fmean(sample[-60:])
        baseline += max(-0.12 * daily_volatility, min(0.12 * daily_volatility, (50 - rsi14) / 50 * daily_volatility * 0.08))
        baseline = _clamp(baseline, -min(0.02, 1.5 * daily_volatility), min(0.02, 1.5 * daily_volatility))
        factors, factor_adjustment, news_review = _prediction_factors(
            baseline, daily_volatility, sector, market, news, announcement_review, financials
        )
        phi = _ar1(sample)
        weekday_means = {
            weekday: statistics.fmean([value for index, value in enumerate(sample) if _weekday_at_return(history, index, len(sample)) == weekday])
            for weekday in range(5)
        }
        overall_mean = statistics.fmean(sample)
        start = max(date.fromisoformat(history[-1].date), today or datetime.now(ZoneInfo("Asia/Shanghai")).date())
        trading_days = _next_weekdays(start, days)
        atr14 = _average_true_range(history, 14)
        atr_ratio = atr14 / current_price

        predictions = []
        price = current_price
        statistical_price = current_price
        previous_return = sample[-1]
        previous_statistical_return = sample[-1]
        for horizon, trading_day in enumerate(trading_days, 1):
            previous_price = price
            seasonal = (weekday_means[trading_day.weekday()] - overall_mean) * 0.1
            seasonal = _clamp(seasonal, -0.1 * daily_volatility, 0.1 * daily_volatility)
            statistical_return = baseline * (0.72 ** (horizon - 1)) + phi * (previous_statistical_return - baseline) * (0.85 ** (horizon - 1)) + seasonal
            event_return = factor_adjustment * (0.85 ** (horizon - 1))
            expected_return = statistical_return + event_return
            limit = min(0.08, 2 * daily_volatility)
            expected_return = _clamp(expected_return, -limit, limit)
            price *= math.exp(expected_return)
            statistical_price *= math.exp(statistical_return)
            uncertainty_multiplier = 1 + min(0.25, abs(factor_adjustment) / daily_volatility * 0.2)
            interval = 1.2816 * daily_volatility * math.sqrt(horizon) * uncertainty_multiplier
            change_pct = math.expm1(expected_return) * 100
            predicted_open = previous_price * math.exp(expected_return * 0.15)
            intraday_range = _clamp((0.75 * atr_ratio + 0.45 * daily_volatility) * (1 + 0.05 * min(horizon - 1, 4)), 0.006, 0.20)
            predicted_high = max(predicted_open, price) * (1 + intraday_range * 0.55)
            predicted_low = min(predicted_open, price) * (1 - intraday_range * 0.45)
            lower_close = price * math.exp(-interval)
            upper_close = price * math.exp(interval)
            predictions.append(
                {
                    "date": trading_day.isoformat(),
                    "weekday": "一二三四五六日"[trading_day.weekday()],
                    "direction": "上涨" if change_pct > 0.05 else "下跌" if change_pct < -0.05 else "震荡",
                    "change_pct": round(change_pct, 3),
                    "cumulative_return_pct": round((price / current_price - 1) * 100, 3),
                    "statistical_change_pct": round(math.expm1(statistical_return) * 100, 3),
                    "factor_adjustment_pct": round(math.expm1(event_return) * 100, 3),
                    "predicted_open": round(predicted_open, 2),
                    "predicted_high": round(predicted_high, 2),
                    "predicted_low": round(predicted_low, 2),
                    "high_change_pct": round((predicted_high / previous_price - 1) * 100, 3),
                    "low_change_pct": round((predicted_low / previous_price - 1) * 100, 3),
                    "amplitude_pct": round((predicted_high - predicted_low) / previous_price * 100, 3),
                    "predicted_close": round(price, 2),
                    "lower_close_80": round(lower_close, 2),
                    "upper_close_80": round(upper_close, 2),
                    "close_lower_change_pct": round((lower_close / previous_price - 1) * 100, 3),
                    "close_upper_change_pct": round((upper_close / previous_price - 1) * 100, 3),
                }
            )
            previous_return = expected_return
            previous_statistical_return = statistical_return

        weekly_return = (price / current_price - 1) * 100
        statistical_weekly_return = (statistical_price / current_price - 1) * 100
        risk_notes = []
        if news_review["negative_count"]:
            risk_notes.append(f"公司相关新闻中有 {news_review['negative_count']} 条利空关键词，需阅读全文核实")
        if market and market.stock_sector_change_5d_pct is not None and market.stock_sector_change_5d_pct < 0:
            risk_notes.append("所属行业近5日热点涨幅为负，行业动量可能拖累短期预测")
        if not market or not market.indices:
            risk_notes.append("大盘指数环境缺失，市场因子按中性处理")
        return {
            "method": self.METHOD,
            "days": predictions,
            "weekly_return_pct": round(weekly_return, 3),
            "statistical_weekly_return_pct": round(statistical_weekly_return, 3),
            "factor_weekly_impact_pct": round(weekly_return - statistical_weekly_return, 3),
            "weekly_direction": "上涨" if weekly_return > 0.05 else "下跌" if weekly_return < -0.05 else "震荡",
            "factors": factors,
            "news_review": news_review,
            "risk_notes": risk_notes,
            "factor_note": "因子调整是基于规则的有限修正，不是各因素独立预期收益；总调整设有上限并随预测日衰减",
            "interval_note": "收盘上下限为约 80% 模型统计区间，并考虑事件不确定性，不是收益保证",
            "intraday_note": "日内高低价、高低点涨跌幅和振幅由 ATR 与历史波动估计，不是实际最高最低价承诺",
            "date_note": "日期按后续工作日估算，不含法定节假日校准",
        }

    def predict_month(
        self,
        history: tuple[PriceBar, ...],
        current_price: float,
        rsi14: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = self.predict(history, current_price, rsi14, days=20, **kwargs)
        days = result["days"]
        monthly_return = result["weekly_return_pct"]
        statistical_return = result["statistical_weekly_return_pct"]
        predicted_closes = [current_price] + [day["predicted_close"] for day in days]
        peak = predicted_closes[0]
        max_drawdown = 0.0
        for close in predicted_closes:
            peak = max(peak, close)
            max_drawdown = min(max_drawdown, (close / peak - 1) * 100)
        checkpoints = []
        for horizon in (5, 10, 15, 20):
            day = days[horizon - 1]
            checkpoints.append(
                {
                    "horizon": horizon,
                    "date": day["date"],
                    "predicted_close": day["predicted_close"],
                    "cumulative_return_pct": day["cumulative_return_pct"],
                    "lower_close_80": day["lower_close_80"],
                    "upper_close_80": day["upper_close_80"],
                }
            )
        monthly = {
            key: value
            for key, value in result.items()
            if key not in {
                "weekly_return_pct",
                "statistical_weekly_return_pct",
                "factor_weekly_impact_pct",
                "weekly_direction",
            }
        }
        monthly.update(
            {
                "horizon_trading_days": 20,
                "start_date": days[0]["date"],
                "end_date": days[-1]["date"],
                "monthly_return_pct": monthly_return,
                "statistical_monthly_return_pct": statistical_return,
                "factor_monthly_impact_pct": result["factor_weekly_impact_pct"],
                "monthly_direction": result["weekly_direction"],
                "predicted_close": days[-1]["predicted_close"],
                "lower_close_80": days[-1]["lower_close_80"],
                "upper_close_80": days[-1]["upper_close_80"],
                "predicted_high": max(day["predicted_high"] for day in days),
                "predicted_low": min(day["predicted_low"] for day in days),
                "max_predicted_drawdown_pct": round(max_drawdown, 3),
                "up_days": sum(day["direction"] == "上涨" for day in days),
                "down_days": sum(day["direction"] == "下跌" for day in days),
                "flat_days": sum(day["direction"] == "震荡" for day in days),
                "checkpoints": checkpoints,
                "interval_note": "月末上下限为约 80% 模型统计区间；期限越长不确定性越大，不是收益保证或目标价",
                "intraday_note": "日内区间仅用于逐日路径估计；月度最高与最低值是模型路径极值，不是实际极端行情边界",
                "date_note": "一个月按 20 个后续工作日估算，不含沪深港交易所法定节假日和临时休市校准",
            }
        )
        return monthly


def _prediction_factors(
    baseline: float,
    volatility: float,
    sector: SectorPerformance | None,
    market: MarketContext | None,
    news: tuple[NewsItem, ...],
    announcement_review: dict[str, Any] | None,
    financials: tuple[FinancialReport, ...],
) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    factors = [_factor("个股技术", 0.0, f"历史动量与AR(1)基准日收益约 {math.expm1(baseline) * 100:.3f}%")]
    if sector and sector.momentum_20d_pct is not None:
        position = sector.cycle_position_120d_pct if sector.cycle_position_120d_pct is not None else 50.0
        cycle_score = _clamp(sector.momentum_20d_pct / 15, -1, 1) * 0.6 + (position - 50) / 50 * 0.4
        adjustment = cycle_score * 0.12 * volatility
        factors.append(_factor("行业周期", adjustment, f"{sector.name}处于{sector.cycle_phase}阶段，20日 {sector.momentum_20d_pct:+.2f}%，120日位置 {position:.1f}%"))
    else:
        factors.append(_factor("行业周期", 0.0, "行业历史行情不足，按中性处理"))
    if market and market.stock_sector_rank:
        rank = market.stock_sector_rank
        change_5d = market.stock_sector_change_5d_pct or 0.0
        hot_score = 1.0 if rank <= 10 else 0.7 if rank <= 30 else 0.4 if rank <= 80 else 0.1 if rank <= 200 else 0.0
        if change_5d < 0:
            hot_score = -0.4
        adjustment = hot_score * 0.10 * volatility
        factors.append(_factor("市场热点", adjustment, f"所属行业5日热点排名第 {rank}，涨跌幅 {change_5d:+.2f}%"))
    else:
        factors.append(_factor("市场热点", 0.0, "所属行业未进入热点榜或数据缺失，按中性处理"))
    if market and market.indices:
        values = [index.momentum_20d_pct for index in market.indices if index.momentum_20d_pct is not None]
        average_20d = statistics.fmean(values) if values else 0.0
        breadth = sum(index.above_ma20 for index in market.indices) / len(market.indices)
        market_score = _clamp(average_20d / 8, -1, 1) * 0.7 + (breadth - 0.5) * 0.6
        adjustment = market_score * 0.08 * volatility
        factors.append(_factor("大盘环境", adjustment, f"三大指数20日平均 {average_20d:+.2f}%，{sum(index.above_ma20 for index in market.indices)}/{len(market.indices)} 位于20日线上方"))
    else:
        factors.append(_factor("大盘环境", 0.0, "指数数据不足，按中性处理"))
    review = announcement_review or {"score": 50, "positive_count": 0, "negative_count": 0}
    announcement_score = _clamp((review.get("score", 50) - 50) / 50, -1, 1)
    adjustment = announcement_score * 0.12 * volatility
    factors.append(_factor("公司公告", adjustment, f"近90日标题关键词：利好 {review.get('positive_count', 0)} 条、风险 {review.get('negative_count', 0)} 条；未阅读全文"))
    news_review = _review_news(news)
    related_count = news_review["related_count"]
    news_score = (news_review["positive_count"] - news_review["negative_count"]) / max(2, related_count) if related_count else 0.0
    adjustment = _clamp(news_score, -1, 1) * 0.12 * volatility
    factors.append(_factor("新闻舆情", adjustment, f"公司相关新闻 {related_count} 条，利好 {news_review['positive_count']} 条、利空 {news_review['negative_count']} 条；市场相关新闻不直接计分"))
    if financials:
        latest = financials[0]
        growth_values = [_clamp(value / 20, -1, 1) for value in (latest.revenue_yoy_pct, latest.net_profit_yoy_pct) if value is not None]
        growth_score = statistics.fmean(growth_values) if growth_values else 0.0
        adjustment = growth_score * 0.06 * volatility
        factors.append(_factor("业绩动量", adjustment, f"最新营收同比 {_signed(latest.revenue_yoy_pct)}、归母净利同比 {_signed(latest.net_profit_yoy_pct)}，短周期仅低权重修正"))
    else:
        factors.append(_factor("业绩动量", 0.0, "缺少最新业绩，按中性处理"))
    total = _clamp(sum(factor["adjustment"] for factor in factors[1:]), -0.35 * volatility, 0.35 * volatility)
    for factor in factors:
        factor["daily_adjustment_pct"] = round(math.expm1(factor["adjustment"]) * 100, 4)
        factor["impact"] = "利好" if factor["daily_adjustment_pct"] > 0.005 else "利空" if factor["daily_adjustment_pct"] < -0.005 else "中性"
        del factor["adjustment"]
    return factors, total, news_review


def _review_news(news: tuple[NewsItem, ...]) -> dict[str, Any]:
    positive_words = ("利好", "增长", "预增", "回购", "增持", "中标", "分红", "创新高", "获批", "合作", "提价", "盈利")
    negative_words = ("利空", "下滑", "减持", "亏损", "处罚", "立案", "诉讼", "下调", "风险", "违约", "停产", "降价", "调查", "爆雷")
    items = []
    positive_count = 0
    negative_count = 0
    related_count = 0
    for item in news:
        impact = "中性"
        if item.is_company_related:
            related_count += 1
            text = f"{item.title}{item.summary}"
            if any(word in text for word in negative_words):
                impact = "利空"
                negative_count += 1
            elif any(word in text for word in positive_words):
                impact = "利好"
                positive_count += 1
        value = asdict(item)
        value["impact"] = impact
        items.append(value)
    return {"related_count": related_count, "positive_count": positive_count, "negative_count": negative_count, "items": items}


def _factor(name: str, adjustment: float, summary: str) -> dict[str, Any]:
    return {"name": name, "adjustment": adjustment, "summary": summary}


def _average_true_range(history: tuple[PriceBar, ...], window: int) -> float:
    ranges = []
    for index in range(len(history) - window, len(history)):
        current = history[index]
        previous_close = history[index - 1].close
        ranges.append(max(current.high - current.low, abs(current.high - previous_close), abs(current.low - previous_close)))
    return statistics.fmean(ranges)


def _ar1(returns: list[float]) -> float:
    x = returns[:-1]
    y = returns[1:]
    mean_x = statistics.fmean(x)
    mean_y = statistics.fmean(y)
    variance = sum((value - mean_x) ** 2 for value in x)
    if variance == 0:
        return 0.0
    covariance = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    return _clamp(covariance / variance, -0.35, 0.35)


def _weekday_at_return(history: tuple[PriceBar, ...], sample_index: int, sample_size: int) -> int:
    history_index = len(history) - sample_size + sample_index
    history_index = max(1, min(len(history) - 1, history_index))
    return date.fromisoformat(history[history_index].date).weekday()


def _next_weekdays(start: date, count: int) -> list[date]:
    result = []
    current = start
    while len(result) < count:
        current += timedelta(days=1)
        if current.weekday() < 5:
            result.append(current)
    return result


def _signed(value: float | None) -> str:
    return f"{value:+.2f}%" if value is not None else "暂无数据"


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
