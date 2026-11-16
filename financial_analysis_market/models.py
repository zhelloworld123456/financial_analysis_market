from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Stock:
    code: str
    name: str
    secid: str
    market: str


@dataclass(frozen=True, slots=True)
class Quote:
    price: float
    previous_close: float
    open: float | None
    high: float | None
    low: float | None
    change_pct: float | None
    volume: float | None
    amount: float | None
    turnover_pct: float | None
    pe_dynamic: float | None
    pb: float | None
    total_market_value: float | None
    float_market_value: float | None
    industry: str
    area: str
    quoted_at: str
    source: str = "东方财富行情接口"


@dataclass(frozen=True, slots=True)
class PriceBar:
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    change_pct: float


@dataclass(frozen=True, slots=True)
class FinancialReport:
    report_date: str
    report_type: str
    revenue: float | None
    net_profit: float | None
    revenue_yoy_pct: float | None
    net_profit_yoy_pct: float | None
    eps: float | None
    roe_pct: float | None
    gross_margin_pct: float | None
    bps: float | None
    cash_flow_per_share: float | None = None
    industry_code: str = ""
    industry_name: str = ""
    deduct_net_profit: float | None = None
    operating_cash_flow: float | None = None
    capital_expenditure: float | None = None
    free_cash_flow: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    debt_asset_ratio_pct: float | None = None
    current_ratio: float | None = None
    monetary_funds: float | None = None
    short_loan: float | None = None
    accounts_receivable: float | None = None
    inventory: float | None = None
    operate_profit: float | None = None
    total_profit: float | None = None
    income_tax: float | None = None
    finance_expense: float | None = None
    dividend_yield_pct: float | None = None


@dataclass(frozen=True, slots=True)
class Announcement:
    date: str
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class SectorPerformance:
    code: str
    name: str
    momentum_20d_pct: float | None
    momentum_60d_pct: float | None
    above_ma20: bool
    momentum_5d_pct: float | None = None
    momentum_120d_pct: float | None = None
    cycle_position_120d_pct: float | None = None
    cycle_phase: str = "未知"


@dataclass(frozen=True, slots=True)
class NewsItem:
    date: str
    title: str
    summary: str
    source: str
    url: str
    is_company_related: bool = False


@dataclass(frozen=True, slots=True)
class MarketIndex:
    code: str
    name: str
    momentum_20d_pct: float | None
    momentum_60d_pct: float | None
    above_ma20: bool


@dataclass(frozen=True, slots=True)
class Hotspot:
    code: str
    name: str
    change_1d_pct: float | None
    change_5d_pct: float | None
    change_10d_pct: float | None
    turnover_pct: float | None
    up_count: int
    down_count: int
    is_stock_sector: bool = False


@dataclass(frozen=True, slots=True)
class MarketContext:
    indices: tuple[MarketIndex, ...]
    hotspots: tuple[Hotspot, ...]
    stock_sector_rank: int | None
    stock_sector_change_5d_pct: float | None


@dataclass(frozen=True, slots=True)
class StockBundle:
    stock: Stock
    quote: Quote
    history: tuple[PriceBar, ...]
    financials: tuple[FinancialReport, ...]
    sources: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    announcements: tuple[Announcement, ...] = ()
    sector: SectorPerformance | None = None
    news: tuple[NewsItem, ...] = ()
