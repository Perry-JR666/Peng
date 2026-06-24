import argparse
import json
import os
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from a_share_map import leader_line, match_bucket
from brief_style import cls_header, color_bar, soft_panel_end, soft_panel_start
from http_utils import fetch_text
from news_radar import clean_text, dedupe_key, is_low_quality_source, is_market_recap
from pushplus_client import send_pushplus


CN_TZ = timezone(timedelta(hours=8))
TENCENT_HEADERS = {"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"}
THEME_RULES = [
    {
        "name": "并购重组/股权变动",
        "aliases": ["并购", "重组", "收购", "注入", "借壳", "控制权", "股权转让", "定增", "增持", "回购"],
    },
    {
        "name": "订单/中标/合作",
        "aliases": ["订单", "中标", "签约", "合作", "供货", "客户", "采购", "框架协议"],
    },
    {
        "name": "业绩/预增/扭亏",
        "aliases": ["业绩", "预增", "扭亏", "增长", "净利润", "一季报", "半年报", "中报", "业绩预告"],
    },
    {
        "name": "AI算力/液冷/服务器",
        "aliases": ["AI", "算力", "液冷", "服务器", "数据中心", "英伟达", "GPU", "光模块"],
    },
    {
        "name": "半导体/先进封装",
        "aliases": ["半导体", "芯片", "存储", "先进封装", "HBM", "晶圆", "设备", "材料"],
    },
    {
        "name": "机器人/自动驾驶",
        "aliases": ["机器人", "人形机器人", "减速器", "伺服", "传感器", "自动驾驶", "智驾"],
    },
    {
        "name": "低空经济/无人机",
        "aliases": ["低空", "无人机", "eVTOL", "通航", "空管", "航线"],
    },
    {
        "name": "新能源/固态/储能",
        "aliases": ["锂电", "固态电池", "储能", "电池", "磷酸铁锂", "光伏", "新能源"],
    },
    {
        "name": "医药/器械",
        "aliases": ["创新药", "临床", "FDA", "医药", "器械", "药品", "减重药"],
    },
    {
        "name": "资源品/涨价",
        "aliases": ["涨价", "碳酸锂", "稀土", "黄金", "铜", "钨", "锑", "供给", "短缺"],
    },
    {
        "name": "消费电子/苹果链",
        "aliases": ["消费电子", "苹果", "折叠屏", "AIPC", "AI手机", "PCB", "显示"],
    },
    {
        "name": "军工/商业航天",
        "aliases": ["军工", "卫星", "导弹", "商业航天", "火箭", "航发"],
    },
]


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def symbol_ranges():
    symbols = []
    for n in range(1, 4000):
        symbols.append(f"sz{n:06d}")
    for n in range(300000, 303500):
        symbols.append(f"sz{n:06d}")
    for n in range(600000, 606800):
        symbols.append(f"sh{n:06d}")
    for n in range(688000, 690200):
        symbols.append(f"sh{n:06d}")
    for n in range(920000, 922000):
        symbols.append(f"bj{n:06d}")
    return symbols


def board_limit_ratio(market: str, code: str) -> float:
    if market == "bj":
        return 0.30
    if code.startswith("688") or code.startswith("300") or code.startswith("301"):
        return 0.20
    return 0.10


def google_news_rss(query: str, days: int = 2) -> str:
    encoded = urllib.parse.quote(f"{query} when:{days}d")
    return f"https://news.google.com/rss/search?q={encoded}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"


def parse_news_feed(xml_text: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    channel = root.find("channel")
    if channel is None:
        return items
    for node in channel.findall("item"):
        title = clean_text(node.findtext("title"))
        desc = clean_text(node.findtext("description"))
        pub = clean_text(node.findtext("pubDate"))
        pub_dt = None
        if pub:
            try:
                pub_dt = parsedate_to_datetime(pub).astimezone(CN_TZ)
            except Exception:
                pub_dt = None
        if not title:
            continue
        if is_market_recap(title, desc) or is_low_quality_source(title, desc):
            continue
        items.append({"title": title, "desc": desc, "pub_dt": pub_dt})
    return items


def fetch_candidate_quotes(min_amount_yuan: float) -> list[dict]:
    stocks = []
    import re

    pattern = re.compile(r'v_([a-z]{2})(\d{6})="([^"]*)";')
    for batch in chunked(symbol_ranges(), 180):
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        try:
            text = fetch_text(url, timeout=10, retries=1, encoding="gbk", headers=TENCENT_HEADERS)
        except Exception:
            continue
        for match in pattern.finditer(text):
            market, code, body = match.groups()
            parts = body.split("~")
            if len(parts) < 45:
                continue
            try:
                name = parts[1]
                price = float(parts[3])
                pct = float(parts[32])
                amount = float(parts[37]) * 10000
            except Exception:
                continue
            if price <= 0 or amount < min_amount_yuan:
                continue
            if "ST" in name or "退" in name:
                continue
            if name.startswith(("N", "C")):
                continue
            limit_pct = board_limit_ratio(market, code) * 100
            tolerance = 0.6 if limit_pct >= 20 else 0.35
            if pct < limit_pct - tolerance:
                continue
            sec_market = "1" if market == "sh" else "0"
            stocks.append(
                {
                    "market": market,
                    "secid": f"{sec_market}.{code}",
                    "code": code,
                    "name": name,
                    "amount": amount,
                    "pct": pct,
                }
            )
    return stocks


def fetch_kline(market: str, code: str) -> list[dict]:
    symbol = f"{market}{code}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,40,qfq"
    import json as _json

    data = _json.loads(fetch_text(url, timeout=12, retries=2, headers=TENCENT_HEADERS))
    stock_data = ((data.get("data") or {}).get(symbol) or {})
    lines = stock_data.get("qfqday") or stock_data.get("day") or []
    rows = []
    for parts in lines:
        if len(parts) < 6:
            continue
        try:
            rows.append(
                {
                    "date": parts[0],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "vol": float(parts[5]),
                    "amount": 0.0,
                    "amp": 0.0,
                    "pct": 0.0,
                    "chg": 0.0,
                    "turn": 0.0,
                }
            )
        except ValueError:
            continue
    for idx in range(1, len(rows)):
        prev_close = rows[idx - 1]["close"]
        cur = rows[idx]
        if prev_close:
            cur["chg"] = cur["close"] - prev_close
            cur["pct"] = (cur["close"] - prev_close) / prev_close * 100
    if rows:
        rows[0]["chg"] = 0.0
        rows[0]["pct"] = 0.0
    return rows


def is_limit_up(row: dict, market: str, code: str) -> bool:
    limit_pct = board_limit_ratio(market, code) * 100
    tolerance = 0.6 if limit_pct >= 20 else 0.35
    return row["pct"] >= limit_pct - tolerance and abs(row["close"] - row["high"]) <= 0.02


def analyze_stock(stock: dict) -> dict | None:
    try:
        rows = fetch_kline(stock["market"], stock["code"])
    except Exception:
        return None
    if len(rows) < 3:
        return None
    last = rows[-1]
    prev = rows[-2]
    if not is_limit_up(last, stock["market"], stock["code"]):
        return None
    if is_limit_up(prev, stock["market"], stock["code"]):
        return None
    return {
        **stock,
        "date": last["date"],
        "close": last["close"],
        "turn": last["turn"],
        "amount_yi": round(last["amount"] / 1e8, 2),
    }


def classify_themes(text: str) -> list[str]:
    hits = []
    lower = text.lower()
    for rule in THEME_RULES:
        if any(alias.lower() in lower for alias in rule["aliases"]):
            hits.append(rule["name"])
    return hits


def summarize_news(stock: dict, news_items: list[dict]) -> dict:
    if not news_items:
        bucket = match_bucket(title=stock["name"])
        return {
            "main_reason": "盘口选择/板块共振，公开新闻催化不突出",
            "other_angles": [bucket["name"]],
            "summary": "当天封板更像资金从盘口结构和板块联动中选择出来，未必有单一重磅公告驱动。",
            "source_line": "公开聚合新闻未抓到足够有效的单一事件源",
        }

    theme_counter = Counter()
    source_titles = []
    for item in news_items[:8]:
        text = f"{item['title']} {item['desc']}"
        for theme in classify_themes(text):
            theme_counter[theme] += 1
        source_titles.append(item["title"])

    ordered_themes = [name for name, _ in theme_counter.most_common()]
    if not ordered_themes:
        bucket = match_bucket(title=stock["name"], desc=" ".join(source_titles[:4]))
        ordered_themes = [bucket["name"]]

    main_reason = ordered_themes[0]
    other_angles = ordered_themes[1:3]
    if not other_angles:
        bucket = match_bucket(title=stock["name"], desc=" ".join(source_titles[:4]))
        candidate = bucket["name"]
        if candidate != main_reason:
            other_angles.append(candidate)

    summary = clean_text("；".join(source_titles[:2]))
    summary = summary[:120] if summary else "聚合新闻显示，公司当日相关催化主要集中在同一主题方向。"
    source_line = " / ".join(source_titles[:3])[:180]
    return {
        "main_reason": main_reason,
        "other_angles": other_angles,
        "summary": summary,
        "source_line": source_line or "新闻标题聚合",
    }


def collect_news_for_stock(stock: dict) -> list[dict]:
    queries = [
        f"{stock['name']} 公告 订单 合作 扩产",
        f"{stock['name']} 涨停 原因",
        f"{stock['name']} {stock['code']}",
    ]
    seen = set()
    items = []
    for query in queries:
        try:
            xml_text = fetch_text(google_news_rss(query, days=3), timeout=8, retries=0)
        except Exception:
            continue
        for item in parse_news_feed(xml_text):
            key = dedupe_key(item["title"])
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    items.sort(key=lambda x: x.get("pub_dt") or datetime(2000, 1, 1, tzinfo=CN_TZ), reverse=True)
    return items[:8]


def build_pool(top_n: int, min_amount_yuan: float) -> list[dict]:
    quotes = fetch_candidate_quotes(min_amount_yuan)
    first_boards = []
    workers = int(os.environ.get("FIRST_LIMIT_WORKERS", "12"))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(analyze_stock, stock) for stock in quotes]
        for future in as_completed(futures):
            try:
                item = future.result()
            except Exception:
                item = None
            if item:
                first_boards.append(item)
    first_boards.sort(key=lambda x: (x["amount"], x["pct"]), reverse=True)
    limit_all = top_n <= 0
    selected = first_boards if limit_all else first_boards[: top_n * 2]

    enriched = []
    with ThreadPoolExecutor(max_workers=min(6, max(2, len(selected)))) as executor:
        futures = {executor.submit(collect_news_for_stock, stock): stock for stock in selected}
        for future in as_completed(futures):
            stock = futures[future]
            try:
                news_items = future.result()
            except Exception:
                news_items = []
            summary = summarize_news(stock, news_items)
            bucket = match_bucket(title=stock["name"], desc=" ".join(item["title"] for item in news_items[:4]))
            enriched.append(
                {
                    **stock,
                    **summary,
                    "leaders": f"{bucket['name']}：{'、'.join(bucket['leaders'][:4])}",
                }
            )
    enriched.sort(key=lambda x: (x["amount"], x["pct"]), reverse=True)
    return enriched if limit_all else enriched[:top_n]


def format_report(items: list[dict], top_n: int) -> tuple[str, str]:
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    title = f"A股首板涨停预期差 {now[:10]}"
    lines = [
        cls_header(
            f"A股首板涨停预期差 {now}",
            "聚焦当日首板涨停股：先拆主因，再看是否还有未被充分交易的发酵角度。",
        ),
        "",
    ]
    if not items:
        lines.append("今天未筛到足够可信的首板涨停样本，可能是非交易日、收盘数据未更新，或公开新闻线索过弱。")
        return title, "\n".join(lines)

    lines.extend(
        [
            (
                f"共筛出 {len(items)} 只首板涨停样本，默认按成交额排序，已展示全部样本。"
                if top_n <= 0
                else f"共筛出 {len(items)} 只重点样本，默认按成交额排序，最多展示 {top_n} 只。"
            ),
            "",
        ]
    )

    for idx, item in enumerate(items, 1):
        other_angles = "、".join(item["other_angles"][:2]) if item["other_angles"] else "暂未识别出更强分支"
        lines.extend(
            [
                color_bar(idx),
                soft_panel_start(idx),
                f"## {idx}. {item['name']} {item['code']}",
                "",
                f"**收盘表现**：涨幅 {item['pct']:.2f}% | 成交额 {item['amount_yi']:.2f} 亿 | 换手 {item['turn']:.2f}%",
                "",
                f"**首板主因**：{item['main_reason']}",
                "",
                f"**公开线索**：{item['summary']}",
                "",
                f"**潜在预期差**：{other_angles}",
                "",
                f"**同题材观察**：{item['leaders']}",
                "",
                f"**需要警惕**：如果次日只有个股情绪、没有同题材跟风，往往说明这更像日内资金点火，而不是能继续发酵的板块催化。",
                "",
                f"**线索来源**：{item['source_line']}",
                "",
                soft_panel_end(),
                "",
                "---",
                "",
            ]
        )

    lines.extend(["## 使用方式", "", "- 先看首板主因是不是你熟悉的主线；", "- 再看潜在预期差能不能延伸出次日板块扩散；", "- 如果只有个股自身新闻、没有行业映射，就更偏独立事件。"])
    return title, "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--top", type=int, default=int(os.environ.get("FIRST_LIMIT_TOP_N", "0")))
    args = parser.parse_args()

    min_amount = float(os.environ.get("FIRST_LIMIT_MIN_AMOUNT_YUAN", "150000000"))
    items = build_pool(args.top, min_amount)
    title, content = format_report(items, args.top)
    if args.dry_run:
        print(content)
    else:
        result = send_pushplus(title, content)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
