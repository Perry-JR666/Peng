import argparse
import json
import os
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from a_share_map import match_bucket
from brief_style import cls_header, color_bar, soft_panel_end, soft_panel_start
from company_profiles import get_profile
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


DEEP_BUCKET_TEMPLATES = {
    "AI算力/液冷/服务器": {
        "deep_mapping": "更深层通常不只是单个订单或公告，而是是否切入 AI 资本开支扩散链条，包括服务器、液冷、电源、连接和国产替代。",
        "hidden_angle": "市场首日常只交易公告标题，第二天才会去挖它在核心客户链中的位置，以及是否属于低位补涨的细分环节。",
        "path": "个股首板 -> 算力细分补涨 -> 服务器/液冷/连接件扩散",
        "mispriced": "大多数散户只会看到“AI概念”四个字，不会继续拆到它究竟受益哪一段资本开支。",
    },
    "半导体/存储/先进封装": {
        "deep_mapping": "更值得挖的是产线验证、国产替代、扩产节奏和下游客户导入，而不只是单一芯片/设备新闻。",
        "hidden_angle": "如果公司处在设备、材料、封测这些中游环节，首日容易被忽略，后续反而可能成为同链条补涨。",
        "path": "个股首板 -> 同环节低位补涨 -> 设备/材料/封测联动",
        "mispriced": "市场经常先炒设计或大票，真正的边际变化可能发生在中游制造链。",
    },
    "PCB/消费电子": {
        "deep_mapping": "深层预期差往往在新机型导入、单机价值量提升、工艺升级或大客户份额切换。",
        "hidden_angle": "如果公司不是整机龙头，而是材料、结构件、PCB、显示链条，散户通常不容易第一天想到它的弹性。",
        "path": "个股首板 -> 苹果链/AI 终端链扩散 -> 配套零部件跟涨",
        "mispriced": "公开标题只会说消费电子回暖，但不会直接告诉你是哪条供应链在吃增量。",
    },
    "机器人/自动驾驶": {
        "deep_mapping": "真正能发酵的不是“机器人概念”本身，而是有没有切到核心零部件验证、车厂导入、量产节奏或新客户。",
        "hidden_angle": "如果公司是减速器、丝杠、电机、传感器这类隐蔽零部件，市场首日可能只把它当概念票。",
        "path": "个股首板 -> 零部件补涨 -> 机器人/智驾板块共振",
        "mispriced": "散户更容易盯住整机和热门概念名词，忽略真正有壁垒的配套环节。",
    },
    "低空经济/商业航天": {
        "deep_mapping": "更深层要看的是空域、运营、试点、适航、军民融合和订单兑现，不只是“低空经济”标题本身。",
        "hidden_angle": "很多票真正的预期差在运营牌照、地方试点或配套环节，而不是飞行器制造本体。",
        "path": "个股首板 -> 地方试点/运营链扩散 -> 低空全链条轮动",
        "mispriced": "市场容易先炒概念标识度，后面才去挖哪些公司真正能承接产业落地。",
    },
    "新能源/储能/固态电池": {
        "deep_mapping": "更深层往往在价差修复、客户结构、技术路线切换、储能渗透或材料环节的盈利弹性。",
        "hidden_angle": "如果首板表面因为订单或业绩，后续真正的预期差可能是单吨盈利改善或切入更高景气子赛道。",
        "path": "个股首板 -> 材料/设备/储能链扩散 -> 中报预期强化",
        "mispriced": "散户通常只盯锂价和整车销量，不会马上穿透到细分材料或储能端的弹性。",
    },
    "创新药/医疗器械": {
        "deep_mapping": "深层预期差往往在适应症扩展、商业化节奏、海外授权或器械放量，不只是单一批文新闻。",
        "hidden_angle": "如果公司只是因为阶段性数据上板，后续还要看能否延伸成估值重估和同赛道映射。",
        "path": "个股首板 -> 同靶点/同器械链补涨 -> 医药情绪扩散",
        "mispriced": "新闻会说获批或临床，但不会直接反映商业化空间和同类标的映射。",
    },
    "资源品/黄金/有色": {
        "deep_mapping": "更深层通常在于供给约束、涨价持续性和利润弹性，而不是当天单一商品涨跌。",
        "hidden_angle": "如果公司兼具资源属性和加工属性，市场可能首日只按资源股处理，忽略利润弹性的层级差。",
        "path": "个股首板 -> 同品种低位补涨 -> 资源链与加工链扩散",
        "mispriced": "散户往往只看商品价格，不会细分谁的盈利弹性最大、谁只是跟风。",
    },
    "军工/航空发动机": {
        "deep_mapping": "深层预期差通常在型号放量、配套环节渗透、军贸、商航或新材料替代。",
        "hidden_angle": "首日如果只是军工情绪驱动，后面真正能走出来的是有型号绑定和放量逻辑的细分公司。",
        "path": "个股首板 -> 配套环节补涨 -> 军工细分扩散",
        "mispriced": "散户更容易笼统看军工，不会马上拆到具体型号和配套链条。",
    },
    "政策/信创/工业互联网": {
        "deep_mapping": "更深层要看订单兑现、地方国资推动、国产替代节奏以及是否能从主题走到收入确认。",
        "hidden_angle": "如果只是政策标题上板，后续能不能发酵取决于公司是不是政策执行链中的受益主体。",
        "path": "个股首板 -> 同政策方向扩散 -> 信创/工业软件补涨",
        "mispriced": "很多人只会看到政策热词，不会区分谁是真受益、谁只是蹭概念。",
    },
}


TRIGGER_OVERRIDES = {
    "并购重组/股权变动": {
        "deep": "真正的预期差通常不在公告本身，而在资产注入质量、控制权稳定后是否会触发估值体系重构。",
        "hidden": "散户通常只看“重组”两个字，容易忽略后续资产质量、注入节奏和壳价值重估。",
        "path": "公告刺激 -> 资产注入预期发酵 -> 同类重组票联动",
        "mispriced": "如果市场首日只当事件股处理，第二天才可能开始重估资产弹性。",
    },
    "订单/中标/合作": {
        "deep": "更深层往往是客户验证和份额切换，而不只是订单金额本身。",
        "hidden": "如果这是切入核心客户链的第一次验证，后续预期差可能比订单金额更大。",
        "path": "订单首板 -> 客户链映射 -> 同供应链补涨",
        "mispriced": "散户通常只看订单数额，不会第一时间判断客户层级和持续性。",
    },
    "业绩/预增/扭亏": {
        "deep": "更深层要分辨是一次性修复，还是单吨盈利、稼动率、客户结构共同改善。",
        "hidden": "如果利润改善能延续到中报预告，这种票容易从事件驱动走向业绩驱动。",
        "path": "业绩首板 -> 中报预期加强 -> 同行业低估值修复",
        "mispriced": "市场首日常只看同比，未必会细拆利润质量和持续性。",
    },
}


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


def market_style_hint(stock: dict) -> str:
    code = stock["code"]
    if code.startswith(("300", "301", "688")):
        return "20cm/科创属性意味着弹性更高，若题材成立更容易被短线资金反复强化。"
    amount = stock.get("amount_yi", 0)
    if amount and amount < 12:
        return "成交额不算大，若次日情绪延续，容易被资金当成同题材高弹性载体。"
    if amount and amount > 50:
        return "成交额较大，若还能继续走强，说明这不只是纯情绪板，更可能带板块容量。"
    return "容量中等，若出现同题材扩散，容易在龙头与补涨之间承担情绪中继角色。"


def pick_deep_template(main_reason: str, bucket_name: str) -> dict:
    override = TRIGGER_OVERRIDES.get(main_reason)
    bucket_tpl = DEEP_BUCKET_TEMPLATES.get(bucket_name) or DEEP_BUCKET_TEMPLATES.get("政策/信创/工业互联网")
    return {
        "deep": (override or {}).get("deep") or bucket_tpl["deep_mapping"],
        "hidden": (override or {}).get("hidden") or bucket_tpl["hidden_angle"],
        "path": (override or {}).get("path") or bucket_tpl["path"],
        "mispriced": (override or {}).get("mispriced") or bucket_tpl["mispriced"],
    }


def derive_signal_lines(stock: dict, news_items: list[dict], main_reason: str, bucket_name: str) -> dict:
    text = " ".join(f"{item['title']} {item['desc']}" for item in news_items).lower()
    template = pick_deep_template(main_reason, bucket_name)
    profile = get_profile(stock["code"])

    deep_parts = [template["deep"]]
    hidden_parts = [template["hidden"]]
    mispriced_parts = [template["mispriced"], market_style_hint(stock)]
    business_parts = []
    chain_parts = []

    if any(word in text for word in ["订单", "中标", "客户", "供货", "合作", "框架协议"]):
        hidden_parts.append("更值得盯的是这是不是首次切入更高等级客户，或者从边缘供应商变成主供。")
    if any(word in text for word in ["扩产", "量产", "投产", "产能", "放量"]):
        deep_parts.append("如果背后是产能爬坡或量产验证，后续预期差通常会从题材转向业绩兑现。")
    if any(word in text for word in ["预增", "扭亏", "增长", "中报", "净利润", "业绩"]):
        hidden_parts.append("若利润改善不是一次性项目，而是毛利率和稼动率同步修复，中报预告阶段还可能再被重估。")
    if any(word in text for word in ["控制权", "并购", "重组", "股权转让", "注入"]):
        deep_parts.append("后续真正的弹性要看注入资产质量和是否触发资产负债表、估值体系双重重估。")
    if any(word in text for word in ["涨价", "短缺", "供给", "禁运", "稀缺"]):
        hidden_parts.append("如果首板背后是供给端变化，第二天市场往往会去挖同链条更低位、更高弹性的补涨票。")
    if any(word in text for word in ["试点", "政策", "会议", "指导意见", "规划"]):
        deep_parts.append("若有政策/试点推进，发酵不一定停在个股，容易扩散到同地区或同环节公司。")
    if not news_items:
        mispriced_parts.append("目前公开线索不强，反而说明若次日继续超预期，资金可能在交易隐含题材身份而不是显性新闻。")
    if profile:
        business_parts.append(profile["business"])
        if profile.get("products"):
            business_parts.append(f"核心产品：{'、'.join(profile['products'][:4])}。")
        chain_parts.append(profile["supply_chain"])
        chain_parts.append(profile["customers"])
        for point in profile.get("hidden_points") or []:
            hidden_parts.append(point)
    else:
        chain_parts.append(f"当前主要按 {bucket_name} 题材身份映射，后续可补充更细的客户与产品数据库。")

    return {
        "business_profile": " ".join(dict.fromkeys(business_parts)) if business_parts else "暂无本地业务画像，当前主要基于题材与公告线索推断。",
        "supply_chain_view": " ".join(dict.fromkeys(chain_parts)),
        "deep_mapping": " ".join(dict.fromkeys(deep_parts)),
        "hidden_angle": " ".join(dict.fromkeys(hidden_parts)),
        "fermentation_path": template["path"],
        "why_mispriced": " ".join(dict.fromkeys(mispriced_parts)),
    }


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
            deep = derive_signal_lines(stock, news_items, summary["main_reason"], bucket["name"])
            enriched.append(
                {
                    **stock,
                    **summary,
                    **deep,
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
                f"**公司业务画像**：{item['business_profile']}",
                "",
                f"**供应链/客户映射**：{item['supply_chain_view']}",
                "",
                f"**深层产业映射**：{item['deep_mapping']}",
                "",
                f"**潜在预期差**：{other_angles}",
                "",
                f"**隐藏预期差**：{item['hidden_angle']}",
                "",
                f"**次日发酵路径**：{item['fermentation_path']}",
                "",
                f"**为什么可能还没充分交易**：{item['why_mispriced']}",
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
