from __future__ import annotations

import json
import re
import socket
import threading
import time
from dataclasses import replace
from datetime import datetime
from http.client import RemoteDisconnected
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from ..errors import DataSourceError, InvalidStockQuery
from ..models import (
    Announcement,
    FinancialReport,
    Hotspot,
    MarketContext,
    MarketIndex,
    NewsItem,
    PriceBar,
    Quote,
    SectorPerformance,
    Stock,
    StockBundle,
)


class EastmoneyProvider:
    SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"
    QUOTE_URLS = (
        "https://push2delay.eastmoney.com/api/qt/stock/get",
        "https://push2.eastmoney.com/api/qt/stock/get",
    )
    HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    FINANCE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
    ANNOUNCEMENT_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"

    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout
        self._sector_cache: dict[str, tuple[float, SectorPerformance | None]] = {}
        self._sector_cache_lock = threading.Lock()
        self._market_cache: dict[str, tuple[float, MarketContext]] = {}
        self._market_cache_lock = threading.Lock()

    def resolve(self, query: str) -> Stock:
        query = query.strip()
        if not query:
            raise InvalidStockQuery("请输入 A 股股票名称或 6 位股票代码")
        if len(query) > 40:
            raise InvalidStockQuery("股票名称过长，请重新输入")
        payload = self._get_json(
            self.SEARCH_URL,
            {
                "input": query,
                "type": "14",
                "token": "D43BF722C8E33BD1F56DE69FD3F78FD6",
                "count": "20",
            },
        )
        rows = (payload.get("QuotationCodeTable") or {}).get("Data") or []
        candidates = []
        for row in rows:
            code = str(row.get("Code") or "")
            secid = str(row.get("QuoteID") or "")
            if row.get("Classify") == "AStock" and re.fullmatch(r"\d{6}", code) and secid:
                candidates.append(
                    Stock(
                        code=code,
                        name=str(row.get("Name") or code),
                        secid=secid,
                        market=str(row.get("SecurityTypeName") or self._market_name(code)),
                    )
                )
        if not candidates:
            raise InvalidStockQuery(f"未找到 A 股“{query}”，请检查名称或改用 6 位代码")

        normalized = query.casefold()
        exact = [
            stock
            for stock in candidates
            if query in (stock.name, stock.code) or normalized == stock.name.casefold()
        ]
        if len(exact) == 1:
            return exact[0]
        if len(candidates) == 1:
            return candidates[0]
        choices = "、".join(f"{stock.name}({stock.code})" for stock in candidates[:6])
        raise InvalidStockQuery(f"找到多只相似股票，请输入完整名称或代码：{choices}")

    def list_candidates(self, limit: int) -> list[Stock]:
        payload = self._get_json(
            self.LIST_URL,
            {
                "pn": "1",
                "pz": str(min(limit * 2, 100)),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f20",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                "fields": "f12,f13,f14,f20",
            },
        )
        rows = (payload.get("data") or {}).get("diff") or []
        stocks = []
        for row in rows:
            code = str(row.get("f12") or "")
            name = str(row.get("f14") or "")
            if not re.fullmatch(r"\d{6}", code) or "ST" in name.upper():
                continue
            market = row.get("f13")
            stocks.append(Stock(code, name, f"{market}.{code}", self._market_name(code)))
            if len(stocks) >= limit:
                break
        if not stocks:
            raise DataSourceError("股票候选池为空，请稍后重试")
        return stocks

    def fetch_bundle(self, stock: Stock, research: bool = True, include_news: bool = True) -> StockBundle:
        quote = self.fetch_quote(stock)
        history = self.fetch_history(stock)
        warnings: list[str] = []
        try:
            financials = self.fetch_financials(stock)
        except DataSourceError:
            financials = ()
            warnings.append("财务接口暂时不可用，好股票与好价格评分不完整")
        if quote.industry in ("", "未知") and financials and financials[0].industry_name:
            quote = replace(quote, industry=financials[0].industry_name)
        announcements: tuple[Announcement, ...] = ()
        sector: SectorPerformance | None = None
        news: tuple[NewsItem, ...] = ()
        sources = [quote.source, "东方财富业绩报表接口"]
        if research:
            try:
                announcements = self.fetch_announcements(stock)
                sources.append("东方财富公告接口")
            except DataSourceError:
                warnings.append("公告接口暂时不可用，好时机评分未计入公告事件")
            try:
                sector = self.fetch_sector(financials, quote.industry)
                if sector:
                    sources.append("东方财富行业板块行情")
            except DataSourceError:
                warnings.append("行业板块行情暂时不可用，板块强弱按中性值处理")
        if include_news:
            try:
                news = self.fetch_news(stock)
                sources.append("东方财富个股新闻搜索")
            except DataSourceError:
                warnings.append("个股新闻暂时不可用，新闻舆情因子按中性处理")
        if not quote.quoted_at:
            quote = replace(quote, quoted_at=f"{history[-1].date}（最近交易日）")
        return StockBundle(
            stock=stock,
            quote=quote,
            history=history,
            financials=financials,
            sources=tuple(sources),
            warnings=tuple(warnings),
            announcements=announcements,
            sector=sector,
            news=news,
        )

    def fetch_quote(self, stock: Stock) -> Quote:
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": (
                "f43,f44,f45,f46,f47,f48,f57,f58,f60,f116,f117,f124,"
                "f162,f167,f127,f128,f168,f170,f171"
            ),
            "secid": stock.secid,
        }
        last_error: DataSourceError | None = None
        for url in self.QUOTE_URLS:
            try:
                payload = self._get_json(url, params)
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise DataSourceError("行情接口返回空数据")
                price = _number(data.get("f43"))
                previous_close = _number(data.get("f60"))
                if price is None or price <= 0 or previous_close is None or previous_close <= 0:
                    raise DataSourceError("该股票暂无有效行情，可能尚未上市或长期停牌")
                timestamp = _number(data.get("f124"))
                quoted_at = ""
                if timestamp:
                    quoted_at = datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Shanghai")).isoformat(
                        timespec="seconds"
                    )
                return Quote(
                    price=price,
                    previous_close=previous_close,
                    open=_number(data.get("f46")),
                    high=_number(data.get("f44")),
                    low=_number(data.get("f45")),
                    change_pct=_number(data.get("f170")),
                    volume=_number(data.get("f47")),
                    amount=_number(data.get("f48")),
                    turnover_pct=_number(data.get("f168")),
                    pe_dynamic=_number(data.get("f162")),
                    pb=_number(data.get("f167")),
                    total_market_value=_number(data.get("f116")),
                    float_market_value=_number(data.get("f117")),
                    industry=str(data.get("f127") or "未知"),
                    area=str(data.get("f128") or "未知"),
                    quoted_at=quoted_at,
                )
            except DataSourceError as exc:
                last_error = exc
        try:
            return self._fetch_quote_tencent(stock)
        except DataSourceError as fallback_error:
            raise last_error or fallback_error

    def _fetch_quote_tencent(self, stock: Stock) -> Quote:
        symbol = self._tencent_symbol(stock.code)
        raw = self._get_text(f"https://qt.gtimg.cn/q={symbol}", encoding="gbk")
        if "=" not in raw:
            raise DataSourceError("备用行情接口返回格式异常")
        fields = raw.split("=", 1)[1].strip().strip('";').split("~")
        if len(fields) < 47:
            raise DataSourceError("备用行情接口返回数据不完整")
        price = _number(fields[3])
        previous_close = _number(fields[4])
        if price is None or price <= 0 or previous_close is None or previous_close <= 0:
            raise DataSourceError("备用行情接口暂无有效价格")
        quoted_at = ""
        try:
            quoted_at = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(
                tzinfo=ZoneInfo("Asia/Shanghai")
            ).isoformat(timespec="seconds")
        except ValueError:
            pass
        return Quote(
            price=price,
            previous_close=previous_close,
            open=_number(fields[5]),
            high=_number(fields[33]),
            low=_number(fields[34]),
            change_pct=_number(fields[32]),
            volume=_number(fields[36]),
            amount=_number(fields[37]) * 10000 if _number(fields[37]) is not None else None,
            turnover_pct=_number(fields[38]),
            pe_dynamic=_number(fields[39]),
            pb=_number(fields[46]),
            total_market_value=_number(fields[45]) * 1e8 if _number(fields[45]) is not None else None,
            float_market_value=_number(fields[44]) * 1e8 if _number(fields[44]) is not None else None,
            industry="未知",
            area="未知",
            quoted_at=quoted_at,
            source="腾讯行情接口（备用）",
        )

    def fetch_history(self, stock: Stock) -> tuple[PriceBar, ...]:
        try:
            return self._fetch_history_eastmoney(stock)
        except DataSourceError:
            return self._fetch_history_tencent(stock)

    def _fetch_history_eastmoney(self, stock: Stock) -> tuple[PriceBar, ...]:
        payload = self._get_json(
            self.HISTORY_URL,
            {
                "secid": stock.secid,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "1",
                "beg": "20240101",
                "end": "20500101",
            },
        )
        data = payload.get("data") or {}
        rows = data.get("klines") or []
        bars: list[PriceBar] = []
        for row in rows:
            parts = row.split(",")
            if len(parts) < 11:
                continue
            try:
                bars.append(
                    PriceBar(
                        date=parts[0],
                        open=float(parts[1]),
                        close=float(parts[2]),
                        high=float(parts[3]),
                        low=float(parts[4]),
                        volume=float(parts[5]),
                        amount=float(parts[6]),
                        change_pct=float(parts[8]),
                    )
                )
            except ValueError:
                continue
        if len(bars) < 30:
            raise DataSourceError("有效历史行情不足 30 个交易日，无法生成五日预测")
        return tuple(bars[-320:])

    def _fetch_history_tencent(self, stock: Stock) -> tuple[PriceBar, ...]:
        symbol = self._tencent_symbol(stock.code)
        try:
            payload = self._get_json(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                {"param": f"{symbol},day,,,320,qfq"},
            )
        except DataSourceError:
            payload = self._get_json(
                "https://web.ifzq.gtimg.cn/appstock/app/kline/kline",
                {"param": f"{symbol},day,,,320"},
            )
        data = (payload.get("data") or {}).get(symbol) or {}
        rows = data.get("qfqday") or data.get("day") or []
        bars = []
        previous_close = None
        for row in rows:
            if len(row) < 6:
                continue
            try:
                close = float(row[2])
                bars.append(
                    PriceBar(
                        date=str(row[0]),
                        open=float(row[1]),
                        close=close,
                        high=float(row[3]),
                        low=float(row[4]),
                        volume=float(row[5]),
                        amount=0.0,
                        change_pct=(close / previous_close - 1) * 100 if previous_close else 0.0,
                    )
                )
                previous_close = close
            except (TypeError, ValueError):
                continue
        if len(bars) < 30:
            raise DataSourceError("备用源有效历史行情不足 30 个交易日")
        return tuple(bars[-320:])

    def fetch_financials(self, stock: Stock) -> tuple[FinancialReport, ...]:
        payload = self._get_json(
            self.FINANCE_URL,
            {
                "reportName": "RPT_LICO_FN_CPD",
                "columns": "ALL",
                "filter": f'(SECURITY_CODE="{stock.code}")',
                "pageNumber": "1",
                "pageSize": "4",
                "sortTypes": "-1",
                "sortColumns": "REPORTDATE",
            },
        )
        if not payload.get("success"):
            raise DataSourceError("财务接口暂时不可用")
        rows = (payload.get("result") or {}).get("data") or []
        reports = []
        for row in rows:
            reports.append(
                FinancialReport(
                    report_date=str(row.get("REPORTDATE") or "")[:10],
                    report_type=str(row.get("DATATYPE") or "定期报告"),
                    revenue=_number(row.get("TOTAL_OPERATE_INCOME")),
                    net_profit=_number(row.get("PARENT_NETPROFIT")),
                    revenue_yoy_pct=_number(row.get("YSTZ")),
                    net_profit_yoy_pct=_number(row.get("SJLTZ")),
                    eps=_number(row.get("BASIC_EPS")),
                    roe_pct=_number(row.get("WEIGHTAVG_ROE")),
                    gross_margin_pct=_number(row.get("XSMLL")),
                    bps=_number(row.get("BPS")),
                    cash_flow_per_share=_number(row.get("MGJYXJJE")),
                    industry_code=str(row.get("BOARD_CODE") or ""),
                    industry_name=str(row.get("PUBLISHNAME") or ""),
                )
            )
        return tuple(reports)

    def fetch_announcements(self, stock: Stock) -> tuple[Announcement, ...]:
        payload = self._get_json(
            self.ANNOUNCEMENT_URL,
            {
                "sr": "-1",
                "page_size": "20",
                "page_index": "1",
                "ann_type": "A",
                "client_source": "web",
                "stock_list": stock.code,
            },
        )
        if not payload.get("success"):
            raise DataSourceError("公告接口暂时不可用")
        rows = (payload.get("data") or {}).get("list") or []
        announcements = []
        for row in rows:
            art_code = str(row.get("art_code") or "")
            title = str(row.get("title") or "").strip()
            if not art_code or not title:
                continue
            announcements.append(
                Announcement(
                    date=str(row.get("notice_date") or "")[:10],
                    title=title,
                    url=f"https://data.eastmoney.com/notices/detail/{stock.code}/{art_code}.html",
                )
            )
        return tuple(announcements)

    def fetch_news(self, stock: Stock) -> tuple[NewsItem, ...]:
        inner = {
            "uid": "",
            "keyword": stock.code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "time",
                    "pageIndex": 1,
                    "pageSize": 10,
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }
        raw = self._get_text(
            f"{self.NEWS_URL}?" + urlencode({"cb": "financialMarket", "param": json.dumps(inner, ensure_ascii=False)})
        )
        try:
            if not raw.startswith("financialMarket(") or not raw.endswith(")"):
                raise ValueError("新闻 JSONP 格式异常")
            payload = json.loads(raw[len("financialMarket("):-1])
            rows = (payload.get("result") or {}).get("cmsArticleWebOld") or []
        except (ValueError, TypeError) as exc:
            raise DataSourceError("个股新闻返回格式异常") from exc
        items = []
        for row in rows:
            title = _strip_html(str(row.get("title") or ""))
            summary = _strip_html(str(row.get("content") or ""))[:500]
            code = str(row.get("code") or "")
            if not title or not code:
                continue
            items.append(
                NewsItem(
                    date=str(row.get("date") or "")[:19],
                    title=title,
                    summary=summary,
                    source=str(row.get("mediaName") or "东方财富"),
                    url=f"https://finance.eastmoney.com/a/{code}.html",
                    is_company_related=stock.name in f"{title}{summary}",
                )
            )
        return tuple(items)

    def fetch_market_context(self, sector_code: str = "") -> MarketContext:
        now = time.monotonic()
        with self._market_cache_lock:
            cached = self._market_cache.get(sector_code)
            if cached and now - cached[0] < 300:
                return cached[1]
        indices = []
        for code, name, secid in (("000001", "上证指数", "1.000001"), ("399001", "深证成指", "0.399001"), ("399006", "创业板指", "0.399006")):
            try:
                bars = self.fetch_history(Stock(code, name, secid, "指数"))
                closes = [bar.close for bar in bars]
                indices.append(
                    MarketIndex(
                        code=code,
                        name=name,
                        momentum_20d_pct=round((closes[-1] / closes[-21] - 1) * 100, 3),
                        momentum_60d_pct=round((closes[-1] / closes[-61] - 1) * 100, 3),
                        above_ma20=closes[-1] >= sum(closes[-20:]) / 20,
                    )
                )
            except DataSourceError:
                continue
        hotspots = []
        stock_sector_rank = None
        stock_sector_change_5d = None
        try:
            payload = self._get_json(
                self.LIST_URL,
                {
                    "pn": "1",
                    "pz": "500",
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f109",
                    "fs": "m:90+t:2+f:!50",
                    "fields": "f12,f14,f3,f8,f104,f105,f109,f160",
                },
            )
            rows = (payload.get("data") or {}).get("diff") or []
            for rank, row in enumerate(rows, 1):
                code = str(row.get("f12") or "")
                if sector_code and code == sector_code:
                    stock_sector_rank = rank
                    stock_sector_change_5d = _number(row.get("f109"))
                if rank <= 8:
                    hotspots.append(
                        Hotspot(
                            code=code,
                            name=str(row.get("f14") or code),
                            change_1d_pct=_number(row.get("f3")),
                            change_5d_pct=_number(row.get("f109")),
                            change_10d_pct=_number(row.get("f160")),
                            turnover_pct=_number(row.get("f8")),
                            up_count=int(_number(row.get("f104")) or 0),
                            down_count=int(_number(row.get("f105")) or 0),
                            is_stock_sector=bool(sector_code and code == sector_code),
                        )
                    )
        except DataSourceError:
            pass
        context = MarketContext(tuple(indices), tuple(hotspots), stock_sector_rank, stock_sector_change_5d)
        with self._market_cache_lock:
            self._market_cache[sector_code] = (now, context)
        return context

    def fetch_sector(
        self, financials: tuple[FinancialReport, ...], industry_name: str
    ) -> SectorPerformance | None:
        if not financials or not financials[0].industry_code:
            return None
        code = financials[0].industry_code
        now = time.monotonic()
        with self._sector_cache_lock:
            cached = self._sector_cache.get(code)
            if cached and now - cached[0] < 600:
                return cached[1]
        payload = self._get_json(
            self.HISTORY_URL,
            {
                "secid": f"90.{code}",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f53",
                "klt": "101",
                "fqt": "0",
                "beg": "20250101",
                "end": "20500101",
            },
        )
        data = payload.get("data") or {}
        closes = []
        for row in data.get("klines") or []:
            parts = row.split(",")
            if len(parts) >= 2:
                try:
                    closes.append(float(parts[1]))
                except ValueError:
                    continue
        if len(closes) < 21:
            raise DataSourceError("行业板块历史行情不足")
        cycle_window = closes[-120:]
        cycle_low = min(cycle_window)
        cycle_high = max(cycle_window)
        cycle_position = (closes[-1] - cycle_low) / (cycle_high - cycle_low) * 100 if cycle_high > cycle_low else 50.0
        momentum_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else None
        momentum_20d = (closes[-1] / closes[-21] - 1) * 100
        if cycle_position >= 80 and momentum_5d is not None and momentum_5d < 0:
            cycle_phase = "高位回落"
        elif cycle_position <= 30 and momentum_20d > 0:
            cycle_phase = "低位修复"
        elif momentum_20d > 0 and cycle_position >= 55:
            cycle_phase = "上升"
        elif momentum_20d < 0 and cycle_position <= 45:
            cycle_phase = "下降"
        else:
            cycle_phase = "震荡"
        sector = SectorPerformance(
            code=code,
            name=str(data.get("name") or industry_name),
            momentum_20d_pct=round(momentum_20d, 3),
            momentum_60d_pct=round((closes[-1] / closes[-61] - 1) * 100, 3) if len(closes) >= 61 else None,
            above_ma20=closes[-1] >= sum(closes[-20:]) / 20,
            momentum_5d_pct=round(momentum_5d, 3) if momentum_5d is not None else None,
            momentum_120d_pct=round((closes[-1] / closes[-121] - 1) * 100, 3) if len(closes) >= 121 else None,
            cycle_position_120d_pct=round(cycle_position, 2),
            cycle_phase=cycle_phase,
        )
        with self._sector_cache_lock:
            self._sector_cache[code] = (now, sector)
        return sector

    def _get_text(self, url: str, encoding: str = "utf-8") -> str:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request = Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://so.eastmoney.com/" if "eastmoney.com" in url else "https://gu.qq.com/",
                    },
                )
                with urlopen(request, timeout=self.timeout) as response:
                    return response.read().decode(encoding, errors="replace")
            except (HTTPError, URLError, RemoteDisconnected, ConnectionError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
        raise DataSourceError("备用行情服务连接失败") from last_error

    def _get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        full_url = f"{url}?{urlencode(params)}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request = Request(
                    full_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                        "Accept": "application/json,text/plain,*/*",
                        "Referer": "https://quote.eastmoney.com/",
                    },
                )
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise ValueError("响应不是 JSON 对象")
                return payload
            except (HTTPError, URLError, RemoteDisconnected, ConnectionError, TimeoutError, socket.timeout, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
        raise DataSourceError("数据服务连接失败，请检查网络后重试") from last_error

    @staticmethod
    def _secid(code: str) -> str:
        return f"{'1' if code.startswith('6') else '0'}.{code}"

    @staticmethod
    def _tencent_symbol(code: str) -> str:
        if code.startswith("6"):
            return f"sh{code}"
        if code.startswith(("4", "8", "92")):
            return f"bj{code}"
        return f"sz{code}"

    @staticmethod
    def _market_name(code: str) -> str:
        if code.startswith("6"):
            return "沪A"
        if code.startswith(("4", "8", "92")):
            return "北A"
        return "深A"


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None
