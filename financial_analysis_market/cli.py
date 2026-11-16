from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .engine import create_engine
from .errors import FinancialAnalysisError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fam",
        description="输入 A 股名称或代码，获取最新行情、业绩和未来 5 个工作日统计预测",
    )
    parser.add_argument("query", nargs="?", help="股票名称或 6 位代码，例如：贵州茅台")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 结果")
    parser.add_argument("--no-ai", action="store_true", help="关闭 Kimi 多 Agent，仅运行规则模型")
    parser.add_argument("--screen", action="store_true", help="运行好股票、好价格、好时机三维筛选")
    parser.add_argument("--limit", type=int, default=20, help="筛选候选池数量，5～50，默认 20")
    parser.add_argument("--web", action="store_true", help="启动本地可视化网页")
    parser.add_argument("--host", default="127.0.0.1", help="网页监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="网页端口，默认 8000")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.web:
        if args.query:
            parser.error("--web 模式不需要同时输入股票名称")
        from .web import serve

        serve(create_engine(), args.host, args.port)
        return 0
    if args.screen:
        if args.query:
            parser.error("--screen 模式不需要同时输入股票名称")
        if not 5 <= args.limit <= 50:
            parser.error("--limit 必须在 5～50 之间")
        try:
            result = create_engine().screen(args.limit)
        except FinancialAnalysisError as exc:
            print(f"筛选失败：{exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        else:
            _print_screen(result)
        return 0
    if not args.query:
        parser.print_help()
        return 2

    try:
        result = create_engine().analyze(args.query, use_ai=not args.no_ai)
    except FinancialAnalysisError as exc:
        print(f"分析失败：{exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        _print_text(result)
    return 0


def _print_text(result: dict) -> None:
    stock = result["stock"]
    quote = result["quote"]
    technical = result["technical"]
    forecast = result["forecast"]
    print(f"\n{stock['name']}（{stock['code']}）  {quote['industry']}")
    print(f"最新价：{quote['price']:.2f} 元    当日涨跌：{_number(quote['change_pct'], signed=True)}")
    print(f"行情时间：{quote['quoted_at'].replace('T', ' ')}")
    print(f"技术状态：{technical['signal']}    RSI14：{technical['rsi14']:.2f}")
    print(f"业绩状态：{result['fundamental']['summary']}")
    scores = result["scores"]
    print(
        f"三维参考：好股票 {scores['good_stock']['score']:.1f} / "
        f"好价格 {scores['good_price']['score']:.1f} / "
        f"好时机 {scores['good_timing']['score']:.1f}"
    )
    seven = scores["seven_factor"]
    print(
        f"七维综合：{scores['overall_score']:.1f} / {seven['verdict']} / "
        f"有效维度 {seven['coverage']['available_dimensions']}/7"
    )
    print("、".join(f"{item['name']}={item['score'] if item['score'] is not None else '待研究'}" for item in seven["dimensions"]))
    if seven["veto"]["triggered"]:
        print("重大风险否决：" + "；".join(item["detail"] for item in seven["veto"]["flags"]))
    print(f"研究结论：{scores['verdict']}（{scores['industry_style']}行业模板）")
    ai = result["ai_research"]
    if ai.get("enabled"):
        print(f"\nKimi 多 Agent（{ai['model']}）：{ai['headline']}")
        print(ai["research_conclusion"])
        print("Agent 状态：" + "、".join(f"{agent['title']}={agent['status']}" for agent in ai["agents"]))
        print(f"Token 用量：{ai['usage']['total_tokens']}")
    else:
        print(f"\n{ai['summary']}")
    print("\n未来 5 个工作日统计预测")
    print("日期         方向    预计涨跌幅    预测收盘价    约80%收盘区间")
    for day in forecast["days"]:
        print(
            f"{day['date']}  {day['direction']:<4}  {day['change_pct']:>+9.2f}%"
            f"  {day['predicted_close']:>10.2f} 元"
            f"  {day['lower_close_80']:.2f} ~ {day['upper_close_80']:.2f}"
        )
    print(f"\n五日累计估计：{forecast['weekly_return_pct']:+.2f}%")
    print(result["meta"]["summary"])
    print("\n风险提示")
    for risk in result["risks"]:
        print(f"- {risk}")
    print(f"\n{result['meta']['disclaimer']}")


def _print_screen(result: dict) -> None:
    meta = result["meta"]
    print(f"\n{meta['universe']}：有效 {meta['screened']} 只，失败 {meta['failed']} 只")
    print(meta["universe_note"])
    titles = (("overall", "综合三好"), ("good_stock", "好股票"), ("good_price", "好价格"), ("good_timing", "好时机"))
    for key, title in titles:
        print(f"\n{title} TOP 6")
        for item in result["categories"][key]:
            scores = item["scores"]
            value = scores["overall_score"] if key == "overall" else scores[key]["score"]
            print(
                f"{item['stock']['code']} {item['stock']['name']:<8} {value:>5.1f} 分  "
                f"综合 {scores['overall_score']:>5.1f}  {scores['verdict']}"
            )
    print(f"\n{result['disclaimer']}")


def _number(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "暂无数据"
    return f"{value:+.2f}%" if signed else f"{value:.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
