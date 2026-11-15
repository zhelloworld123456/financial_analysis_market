from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .models import Announcement, StockBundle


PROFILES = {
    "金融": {
        "keywords": ("银行", "保险", "证券", "多元金融"),
        "pe": (4.0, 15.0),
        "pb": (0.5, 2.0),
        "note": "金融行业更重视 ROE 的可持续性与 PB，现金流和毛利率不作横向比较",
    },
    "消费": {
        "keywords": ("白酒", "食品", "饮料", "家电", "商贸", "美容", "农业", "养殖"),
        "pe": (12.0, 35.0),
        "pb": (2.0, 8.0),
        "note": "消费行业更重视品牌盈利、毛利率和增长持续性",
    },
    "医药": {
        "keywords": ("医药", "医疗", "生物", "中药"),
        "pe": (15.0, 45.0),
        "pb": (2.0, 7.0),
        "note": "医药行业给予成长较高权重，同时关注估值对研发与政策风险的补偿",
    },
    "科技": {
        "keywords": ("半导体", "电子", "计算机", "通信", "软件", "互联网", "元件", "光学"),
        "pe": (18.0, 60.0),
        "pb": (2.0, 10.0),
        "note": "科技行业更重视营收与利润成长，但高估值需要更高增长兑现",
    },
    "周期": {
        "keywords": ("煤炭", "有色", "钢铁", "化工", "石油", "地产", "建材", "航运"),
        "pe": (6.0, 20.0),
        "pb": (0.7, 3.0),
        "note": "周期行业低 PE 可能对应盈利高点，估值同时参考 PB 与增长持续性",
    },
    "制造": {
        "keywords": ("机械", "汽车", "电力", "设备", "军工", "电气", "新能源", "电池", "光伏"),
        "pe": (10.0, 35.0),
        "pb": (1.5, 6.0),
        "note": "制造行业综合考虑资本回报、订单成长和估值，避免只按低价筛选",
    },
}

POSITIVE_KEYWORDS = ("业绩预增", "预增", "扭亏", "回购", "增持", "中标", "重大合同", "现金分红", "利润分配", "获批")
NEGATIVE_KEYWORDS = ("立案", "处罚", "退市", "减持", "亏损", "诉讼", "仲裁", "问询", "警示", "冻结", "终止", "风险")


class ThreeFactorScorer:
    WEIGHTS = {"good_stock": 0.45, "good_price": 0.35, "good_timing": 0.20}

    def score(self, bundle: StockBundle, technical: dict[str, Any]) -> dict[str, Any]:
        profile = _profile(bundle.quote.industry)
        good_stock = self._good_stock(bundle, profile)
        good_price = self._good_price(bundle, profile)
        announcement_review = _review_announcements(bundle.announcements)
        good_timing = self._good_timing(bundle, technical, announcement_review)
        overall = round(
            good_stock["score"] * self.WEIGHTS["good_stock"]
            + good_price["score"] * self.WEIGHTS["good_price"]
            + good_timing["score"] * self.WEIGHTS["good_timing"],
            1,
        )
        if min(good_stock["score"], good_price["score"], good_timing["score"]) >= 70:
            verdict = "三好共振"
        elif overall >= 70:
            verdict = "重点观察"
        elif overall >= 55:
            verdict = "等待更好条件"
        else:
            verdict = "谨慎研究"
        return {
            "overall_score": overall,
            "verdict": verdict,
            "summary": f"好股票 {good_stock['score']:.1f}，好价格 {good_price['score']:.1f}，好时机 {good_timing['score']:.1f}；{verdict}",
            "good_stock": good_stock,
            "good_price": good_price,
            "good_timing": good_timing,
            "industry_style": profile["name"],
            "industry_note": profile["note"],
            "announcement_review": announcement_review,
            "risk_flags": [
                f"公告标题风险关键词：{item['title']}"
                for item in announcement_review["items"]
                if item["impact"] == "风险"
            ][:2],
            "weights": {"好股票": 45, "好价格": 35, "好时机": 20},
            "method_note": "分数用于同一候选池内的研究排序，不代表预期收益；公告仅分析标题关键词，需阅读原文核实",
        }

    def _good_stock(self, bundle: StockBundle, profile: dict[str, Any]) -> dict[str, Any]:
        if not bundle.financials:
            return {"score": 0.0, "label": "数据不足", "reasons": ["缺少定期报告，无法评价盈利质量"], "metrics": {}}
        latest = bundle.financials[0]
        factor = 12 / max(1, int(latest.report_date[5:7] or 12))
        annual_roe = latest.roe_pct * factor if latest.roe_pct is not None else None
        annual_eps = latest.eps * factor if latest.eps is not None else None
        roe_score = _lerp(annual_roe, [(-10, 0), (0, 20), (6, 45), (10, 65), (15, 85), (25, 100)])
        revenue_score = _lerp(latest.revenue_yoy_pct, [(-30, 0), (0, 40), (10, 70), (25, 90), (50, 100)])
        profit_score = _lerp(latest.net_profit_yoy_pct, [(-50, 0), (0, 40), (15, 70), (40, 90), (100, 100)])
        gross_score = None if profile["name"] == "金融" else _lerp(
            latest.gross_margin_pct, [(0, 10), (10, 35), (25, 60), (40, 80), (60, 100)]
        )
        cash_score = None
        cash_conversion = None
        if profile["name"] != "金融" and latest.cash_flow_per_share is not None and latest.eps and latest.eps > 0:
            cash_conversion = latest.cash_flow_per_share / latest.eps
            cash_score = _lerp(cash_conversion, [(-1, 0), (0, 20), (0.5, 55), (1, 90), (1.5, 100)])
        growth_values = [
            value
            for report in bundle.financials
            for value in (report.revenue_yoy_pct, report.net_profit_yoy_pct)
            if value is not None
        ]
        stability_score = 100 * sum(value > 0 for value in growth_values) / len(growth_values) if growth_values else None
        score = _weighted(
            (roe_score, 35),
            (revenue_score, 15),
            (profit_score, 20),
            (gross_score, 10),
            (cash_score, 10),
            (stability_score, 10),
        )
        reasons = [
            f"最新报告营收同比 {_signed(latest.revenue_yoy_pct)}，归母净利润同比 {_signed(latest.net_profit_yoy_pct)}",
            f"按报告期简单年化 ROE 约 {_percent(annual_roe)}，仅用于横向评分",
        ]
        if latest.gross_margin_pct is not None and profile["name"] != "金融":
            reasons.append(f"销售毛利率 {_percent(latest.gross_margin_pct)}")
        if cash_conversion is not None:
            reasons.append(f"每股经营现金流/每股收益约 {cash_conversion:.2f}，用于观察利润现金含量")
        reasons.append(f"近 {len(bundle.financials)} 期增长指标稳定性得分 {stability_score:.0f}/100" if stability_score is not None else "增长稳定性数据不足")
        return {
            "score": score,
            "label": _label(score),
            "reasons": reasons,
            "metrics": {"annualized_roe_pct": _round(annual_roe), "annualized_eps": _round(annual_eps), "cash_conversion": _round(cash_conversion)},
        }

    def _good_price(self, bundle: StockBundle, profile: dict[str, Any]) -> dict[str, Any]:
        quote = bundle.quote
        pe_low, pe_high = profile["pe"]
        pb_low, pb_high = profile["pb"]
        pe_score = 5.0 if quote.pe_dynamic is not None and quote.pe_dynamic <= 0 else _lerp(
            quote.pe_dynamic,
            [(0, 85), (pe_low, 90), (pe_high, 65), (pe_high * 1.8, 30), (pe_high * 3, 5)],
        )
        pb_score = 5.0 if quote.pb is not None and quote.pb <= 0 else _lerp(
            quote.pb,
            [(0, 85), (pb_low, 90), (pb_high, 65), (pb_high * 1.8, 30), (pb_high * 3, 5)],
        )
        value_low, value_high, fair_pe, fair_pb = _value_range(bundle, profile)
        value_score = None
        if value_low is not None and value_high is not None:
            midpoint = (value_low + value_high) / 2
            ratio = quote.price / midpoint if midpoint > 0 else 99
            value_score = _lerp(ratio, [(0.4, 95), (0.7, 90), (0.9, 80), (1.1, 65), (1.4, 40), (2, 15), (3, 5)])
        score = _weighted((pe_score, 45), (pb_score, 20), (value_score, 35))
        reasons = [
            f"动态 PE {_multiple(quote.pe_dynamic)}、PB {_multiple(quote.pb)}，按{profile['name']}行业区间评价",
        ]
        if value_low is not None:
            reasons.append(f"盈利法与净资产法估值参考区间 {value_low:.2f}～{value_high:.2f} 元，现价 {quote.price:.2f} 元")
            reasons.append(f"估值锚：合理 PE 约 {fair_pe:.1f} 倍、合理 PB 约 {fair_pb:.1f} 倍，属于模型假设")
        else:
            reasons.append("盈利或净资产数据不足，未计算估值参考区间")
        reasons.append(profile["note"])
        return {
            "score": score,
            "label": _label(score),
            "reasons": reasons,
            "metrics": {
                "value_low": _round(value_low),
                "value_high": _round(value_high),
                "fair_pe": _round(fair_pe),
                "fair_pb": _round(fair_pb),
            },
        }

    def _good_timing(
        self, bundle: StockBundle, technical: dict[str, Any], announcement_review: dict[str, Any]
    ) -> dict[str, Any]:
        quote = bundle.quote
        ma20, ma60 = technical["ma20"], technical["ma60"]
        trend_parts = [
            100 if ma20 is not None and quote.price >= ma20 else 20,
            100 if ma20 is not None and ma60 is not None and ma20 >= ma60 else 20,
            100 if technical["macd_histogram"] >= 0 else 20,
        ]
        trend_score = sum(trend_parts) / len(trend_parts)
        momentum_score = _lerp(technical["momentum_20d_pct"], [(-25, 0), (-5, 35), (0, 55), (10, 85), (25, 100), (50, 55)])
        rsi_score = _lerp(technical["rsi14"], [(0, 15), (30, 40), (40, 65), (50, 90), (60, 100), (70, 65), (80, 20), (100, 10)])
        volatility_score = _lerp(technical["annual_volatility_pct"], [(0, 80), (15, 90), (30, 75), (50, 45), (80, 15), (150, 5)])
        sector = bundle.sector
        relative_strength = None
        if sector and sector.momentum_20d_pct is not None and technical["momentum_20d_pct"] is not None:
            relative_strength = technical["momentum_20d_pct"] - sector.momentum_20d_pct
            sector_score = _weighted(
                (_lerp(sector.momentum_20d_pct, [(-20, 0), (-5, 35), (0, 55), (10, 80), (25, 100)]), 50),
                (_lerp(relative_strength, [(-20, 0), (-5, 35), (0, 60), (10, 90), (30, 100)]), 50),
            )
        else:
            sector_score = 50.0
        announcement_score = announcement_review["score"]
        score = _weighted(
            (trend_score, 25),
            (momentum_score, 15),
            (rsi_score, 15),
            (volatility_score, 10),
            (sector_score, 20),
            (announcement_score, 15),
        )
        reasons = [
            f"价格趋势{technical['signal']}，20 日动量 {_signed(technical['momentum_20d_pct'])}，RSI14 为 {technical['rsi14']:.1f}",
            f"近 20 日年化波动率 {_percent(technical['annual_volatility_pct'])}，用于控制追涨和波动风险",
        ]
        if sector:
            reasons.append(
                f"{sector.name}板块 20 日涨跌 {_signed(sector.momentum_20d_pct)}，"
                f"个股相对板块 {_signed(relative_strength)}"
            )
        else:
            reasons.append("缺少行业板块行情，板块项按中性 50 分处理")
        reasons.append(
            f"近 90 日公告标题识别：利好 {announcement_review['positive_count']} 条、"
            f"风险 {announcement_review['negative_count']} 条，未命中事件按中性处理"
        )
        return {
            "score": score,
            "label": _label(score),
            "reasons": reasons,
            "metrics": {"relative_sector_20d_pct": _round(relative_strength), "announcement_score": announcement_score},
        }


def _review_announcements(announcements: tuple[Announcement, ...]) -> dict[str, Any]:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    items = []
    positive_count = 0
    negative_count = 0
    for announcement in announcements:
        try:
            age = (today - datetime.strptime(announcement.date, "%Y-%m-%d").date()).days
        except ValueError:
            continue
        if not 0 <= age <= 90:
            continue
        negative = [word for word in NEGATIVE_KEYWORDS if word in announcement.title]
        positive = [word for word in POSITIVE_KEYWORDS if word in announcement.title]
        if negative:
            negative_count += 1
            impact = "风险"
            keywords = negative
        elif positive:
            positive_count += 1
            impact = "利好"
            keywords = positive
        else:
            impact = "中性"
            keywords = []
        item = asdict(announcement)
        item.update({"impact": impact, "keywords": keywords})
        items.append(item)
    return {
        "score": round(_clamp(50 + positive_count * 4 - negative_count * 7, 0, 100), 1),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "items": items[:12],
    }


def _value_range(bundle: StockBundle, profile: dict[str, Any]) -> tuple[float | None, float | None, float, float]:
    fair_pe = sum(profile["pe"]) / 2
    fair_pb = sum(profile["pb"]) / 2
    if not bundle.financials:
        return None, None, fair_pe, fair_pb
    latest = bundle.financials[0]
    factor = 12 / max(1, int(latest.report_date[5:7] or 12))
    growth = latest.net_profit_yoy_pct
    if growth is not None:
        fair_pe = _clamp(8 + max(0, growth) * 0.35, profile["pe"][0], profile["pe"][1])
    if latest.roe_pct is not None:
        annual_roe = latest.roe_pct * factor
        fair_pb = _clamp(annual_roe / 10, profile["pb"][0], profile["pb"][1])
    values = []
    if latest.eps is not None and latest.eps > 0:
        values.append(latest.eps * factor * fair_pe)
    if latest.bps is not None and latest.bps > 0:
        values.append(latest.bps * fair_pb)
    if not values:
        return None, None, fair_pe, fair_pb
    return min(values), max(values), fair_pe, fair_pb


def _profile(industry: str) -> dict[str, Any]:
    for name, profile in PROFILES.items():
        if any(keyword in industry for keyword in profile["keywords"]):
            return {"name": name, **profile}
    return {
        "name": "综合",
        "pe": (10.0, 35.0),
        "pb": (1.0, 5.0),
        "note": "未命中特定行业模板，采用综合估值区间，并保留盈利、成长、现金流和技术因素",
    }


def _weighted(*items: tuple[float | None, float]) -> float:
    valid = [(score, weight) for score, weight in items if score is not None]
    if not valid:
        return 0.0
    return round(sum(score * weight for score, weight in valid) / sum(weight for _, weight in valid), 1)


def _lerp(value: float | None, points: list[tuple[float, float]]) -> float | None:
    if value is None:
        return None
    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return None


def _label(score: float) -> str:
    if score >= 80:
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 55:
        return "一般"
    return "偏弱"


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def _signed(value: float | None) -> str:
    return f"{value:+.2f}%" if value is not None else "暂无数据"


def _percent(value: float | None) -> str:
    return f"{value:.2f}%" if value is not None else "暂无数据"


def _multiple(value: float | None) -> str:
    return f"{value:.2f} 倍" if value is not None else "暂无数据"
