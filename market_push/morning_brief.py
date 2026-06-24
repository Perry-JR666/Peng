import argparse
import json
import os
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from a_share_map import leader_line
from brief_style import cls_header, cls_meta, cls_tag, color_bar, soft_panel_end, soft_panel_start
from http_utils import fetch_json, fetch_text, url_with_params
from news_radar import fallback_rank, google_news_rss, parse_feed, summarize_item
from pushplus_client import send_pushplus


CN_TZ = timezone(timedelta(hours=8))


US_INDEXES = [
    ("^GSPC", "标普500"),
    ("^IXIC", "纳斯达克"),
    ("^DJI", "道琼斯"),
    ("^RUT", "罗素2000"),
]


US_SECTOR_ETFS = [
    ("XLK", "科技"),
    ("XLC", "通信服务"),
    ("XLY", "可选消费"),
    ("XLF", "金融"),
    ("XLV", "医疗"),
    ("XLI", "工业"),
    ("XLE", "能源"),
    ("XLB", "材料"),
    ("XLU", "公用事业"),
    ("XLP", "必选消费"),
    ("XLRE", "房地产"),
    ("SMH", "半导体"),
    ("SOXX", "半导体"),
    ("BOTZ", "机器人/自动化"),
    ("URA", "铀矿/核电"),
]


OVERNIGHT_THEMES = [
    {
        "name": "美股AI算力",
        "queries": ["US stock market AI Nvidia datacenter overnight", "AI datacenter liquid cooling Nvidia overnight", "英伟达 AI 算力 数据中心 液冷 隔夜"],
        "boards": "AI算力、液冷、服务器、光模块、PCB",
        "keywords": ["AI", "Nvidia", "datacenter", "liquid cooling", "GPU", "server", "算力", "液冷"],
    },
    {
        "name": "半导体与存储",
        "queries": ["semiconductor memory HBM advanced packaging overnight", "US chip stocks semiconductor overnight", "半导体 存储 HBM 先进封装 隔夜"],
        "boards": "半导体设备、材料、先进封装、存储、HBM",
        "keywords": ["semiconductor", "chip", "memory", "HBM", "advanced packaging", "芯片", "半导体", "存储"],
    },
    {
        "name": "机器人与自动驾驶",
        "queries": ["humanoid robot Tesla robotics breakthrough overnight", "autonomous driving robot industry overnight", "人形机器人 自动驾驶 技术突破 隔夜"],
        "boards": "机器人、减速器、伺服、电机、传感器、智能驾驶",
        "keywords": ["robot", "humanoid", "Tesla", "autonomous", "机器人", "自动驾驶", "传感器"],
    },
    {
        "name": "新能源与储能",
        "queries": ["solid state battery energy storage solar overnight", "lithium battery EV supply chain overnight", "固态电池 储能 光伏 锂电 隔夜"],
        "boards": "锂电、固态电池、光伏、储能、新能源车",
        "keywords": ["battery", "lithium", "solid state", "solar", "storage", "EV", "锂电", "光伏", "储能"],
    },
    {
        "name": "医药与生物科技",
        "queries": ["biotech FDA clinical trial breakthrough overnight", "pharma drug approval overnight stocks", "创新药 FDA 临床 数据 隔夜"],
        "boards": "创新药、CXO、医疗器械、减重药",
        "keywords": ["biotech", "FDA", "clinical", "drug", "pharma", "approval", "创新药", "临床"],
    },
    {
        "name": "资源品与大宗",
        "queries": ["gold copper rare earth lithium price overnight", "commodities oil gold copper overnight", "黄金 铜 稀土 锂 价格 隔夜"],
        "boards": "黄金、铜、稀土、锂、钨、石油",
        "keywords": ["gold", "copper", "rare earth", "lithium", "oil", "commodity", "黄金", "铜", "稀土"],
    },
    {
        "name": "军工与航天",
        "queries": ["defense aerospace satellite overnight news", "commercial space satellite launch overnight", "军工 航天 卫星 商业航天 隔夜"],
        "boards": "军工、商业航天、卫星互联网、航空发动机",
        "keywords": ["defense", "aerospace", "satellite", "space", "launch", "军工", "航天", "卫星"],
    },
]


def yahoo_chart(symbol: str) -> dict | None:
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    url = url_with_params(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}",
        {"range": "7d", "interval": "1d"},
    )
    try:
        data = fetch_json(url, timeout=12, retries=1, headers={"User-Agent": "Mozilla/5.0"})
    except Exception:
        return None
    result = ((data.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return None
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
    closes = [x for x in quote.get("close", []) if isinstance(x, (int, float))]
    if len(closes) < 2:
        return None
    last = closes[-1]
    prev = closes[-2]
    if not prev:
        return None
    pct = (last - prev) / prev * 100
    return {"symbol": symbol, "close": last, "pct": pct}


def fetch_us_market() -> tuple[list[dict], list[dict]]:
    def fetch_named(pair):
        symbol, name = pair
        item = yahoo_chart(symbol)
        if item:
            item["name"] = name
        return item

    indexes = []
    sectors = []
    workers = int(os.environ.get("MORNING_MARKET_WORKERS", "8"))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        index_futures = {executor.submit(fetch_named, pair): pair for pair in US_INDEXES}
        sector_futures = {executor.submit(fetch_named, pair): pair for pair in US_SECTOR_ETFS}
        for future in as_completed(index_futures):
            item = future.result()
            if item:
                indexes.append(item)
        for future in as_completed(sector_futures):
            item = future.result()
            if item:
                sectors.append(item)
    index_order = {symbol: i for i, (symbol, _) in enumerate(US_INDEXES)}
    indexes.sort(key=lambda x: index_order.get(x["symbol"], 999))
    sectors.sort(key=lambda x: x["pct"], reverse=True)
    return indexes, sectors


def collect_overnight_news() -> list[dict]:
    news = []
    seen = set()
    jobs = [(theme, query) for theme in OVERNIGHT_THEMES for query in theme["queries"]]

    def fetch_job(job):
        theme, query = job
        try:
            xml_text = fetch_text(google_news_rss(query), timeout=8, retries=0)
            return parse_feed(xml_text, theme)
        except Exception:
            return []

    workers = int(os.environ.get("MORNING_NEWS_WORKERS", "8"))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_job, job) for job in jobs]
        for future in as_completed(futures):
            for item in future.result():
                key = item["title"].split(" - ", 1)[0].strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                news.append(item)
    return news


def ask_openai(indexes: list[dict], sectors: list[dict], news: list[dict], top_n: int) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.environ.get("OPENAI_MODEL", "gpt-5.4").strip() or "gpt-5.4"
    compact_news = []
    for item in fallback_rank(news, min(40, len(news))):
        compact_news.append(
            {
                "theme": item["theme"],
                "boards": item["boards"],
                "leaders": item.get("leaders") or leader_line(item),
                "catalyst": item.get("catalyst", ""),
                "summary": item.get("summary") or summarize_item(item),
                "title": item["title"],
                "desc": item.get("desc", "")[:260],
                "time": item["pub_dt"].strftime("%Y-%m-%d %H:%M") if item.get("pub_dt") else "",
            }
        )
    prompt = (
        "你是一名服务中国A股投资者的开盘前海外与产业情报助手。不要输出链接。"
        "请根据隔夜美股指数、热门板块ETF和全球产业新闻，生成早8点简报。"
        "不要只看当前热门板块；如果消息足够爆、可能未来几天发酵，也要列入。"
        f"最多列{top_n}条产业/市场线索，并给出A股映射、A股映射龙头股、潜在爆点、概率、持续性和反证。"
        "每条标题下面必须先给1-2句话消息摘要，说明发生了什么、为什么可能影响A股。"
        "最后给出今日开盘前最值得观察的3条主线。"
    )
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": "只输出中文Markdown，简洁、适合手机阅读，不提供买入建议，不输出链接。"},
            {
                "role": "user",
                "content": prompt
                + "\n\n美股指数：\n"
                + json.dumps(indexes, ensure_ascii=False)
                + "\n\n美股板块ETF：\n"
                + json.dumps(sectors[:8], ensure_ascii=False)
                + "\n\n产业新闻：\n"
                + json.dumps(compact_news, ensure_ascii=False),
            },
        ],
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    import urllib.request

    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return f"> OpenAI生成失败，已降级为规则版。原因：{exc}\n\n"

    texts = []
    for item in result.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in ("output_text", "text"):
                texts.append(content.get("text", ""))
    return "\n".join(texts).strip() or None


def format_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def format_fallback(indexes: list[dict], sectors: list[dict], news: list[dict], top_n: int) -> str:
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    ranked_news = fallback_rank(news, top_n)
    lines = [
        cls_header(
            f"A股早8点隔夜情报 {now}",
            "隔夜美股 + 全球产业催化 + 可能近期发酵的潜在爆点；不输出链接，方便手机阅读。",
        ),
        "",
        "## 隔夜美股",
        "",
    ]
    if indexes:
        lines.append("| 指数 | 涨跌 | 收盘 |")
        lines.append("|---|---:|---:|")
        for item in indexes:
            lines.append(f"| {item['name']} | {format_pct(item['pct'])} | {item['close']:.2f} |")
    else:
        lines.append("暂未取得美股指数数据。")

    lines.extend(["", "## 热门美股板块", ""])
    if sectors:
        lines.append("| 板块/ETF | 涨跌 | A股可能映射 |")
        lines.append("|---|---:|---|")
        mapping = {
            "科技": "AI、算力、软件、消费电子",
            "半导体": "半导体设备、材料、先进封装、存储",
            "机器人/自动化": "机器人、减速器、伺服、电机",
            "能源": "石油、煤炭、油服",
            "材料": "有色、化工、资源品",
            "医疗": "创新药、医疗器械",
            "可选消费": "汽车、消费电子、品牌消费",
        }
        for item in sectors[:6]:
            mapped = mapping.get(item["name"], item["name"])
            lines.append(f"| {item['name']} {item['symbol']} | {format_pct(item['pct'])} | {mapped} |")
    else:
        lines.append("暂未取得美股板块ETF数据。")

    lines.extend(["", "## 产业与潜在爆点", ""])
    if not ranked_news:
        lines.append("暂未抓到足够有效的隔夜产业消息。")
    for idx, item in enumerate(ranked_news, 1):
        pub = item["pub_dt"].strftime("%m-%d %H:%M") if item.get("pub_dt") else "时间未知"
        lines.extend(
            [
                color_bar(idx),
                soft_panel_start(idx),
                f"### {idx}. {item['title']}",
                cls_meta(item.get("source") or "聚合源", pub, item["probability"], item["duration"]),
                "",
                f"{cls_tag(item['theme'])}{cls_tag('隔夜映射')}{cls_tag('潜在催化')}",
                "",
                f"**A股映射**：{item['boards']}",
                "",
                f"**消息摘要**：{item.get('summary') or summarize_item(item)}",
                "",
                f"**代表龙头**：{item.get('leaders') or leader_line(item)}",
                "",
                f"**潜在爆点**：{item.get('catalyst', '观察：等待盘面确认')}",
                "",
                "**交易观察**：看代表龙头是否主动走强，以及同方向是否形成梯队。",
                "",
                "**反证风险**：龙头高开低走、板块无扩散，说明消息可能暂未被市场认可。",
                "",
                soft_panel_end(),
                "",
                "---",
                "",
            ]
        )

    top_themes = []
    for item in ranked_news:
        if item["theme"] not in top_themes:
            top_themes.append(item["theme"])
    lines.extend(["## 今日开盘前重点看", ""])
    for theme in top_themes[:3] or ["AI算力", "半导体", "资源品"]:
        lines.append(f"- {theme}：看代表龙头是否主动走强，以及板块是否形成梯队。")
    return "\n".join(lines)


def build_report(top_n: int) -> tuple[str, str]:
    title = f"A股早8点隔夜情报 {datetime.now(CN_TZ).strftime('%Y-%m-%d')}"
    indexes, sectors = fetch_us_market()
    news = collect_overnight_news()
    generated = ask_openai(indexes, sectors, news, top_n)
    if generated and not generated.startswith("> OpenAI生成失败"):
        return title, generated
    fallback = format_fallback(indexes, sectors, news, top_n)
    if generated:
        fallback = generated + fallback
    return title, fallback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--top", type=int, default=int(os.environ.get("MORNING_TOP_N", "10")))
    args = parser.parse_args()
    title, content = build_report(args.top)
    if args.dry_run:
        print(content)
    else:
        result = send_pushplus(title, content)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
