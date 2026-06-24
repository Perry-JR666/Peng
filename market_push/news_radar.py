import argparse
import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from a_share_map import leader_line, market_preference_score
from brief_style import cls_header, cls_meta, cls_tag, color_bar, soft_panel_end, soft_panel_start
from http_utils import fetch_text
from pushplus_client import send_pushplus


CN_TZ = timezone(timedelta(hours=8))


SOURCE_BOOST = {
    "财联社": 18,
    "证券时报": 16,
    "中国证券报": 15,
    "上海证券报": 15,
    "第一财经": 14,
    "华尔街见闻": 14,
    "21财经": 13,
    "21世纪经济报道": 13,
    "新浪财经": 10,
    "Investing.com": 10,
    "Moomoo": 8,
    "Reuters": 16,
    "Bloomberg": 16,
    "CNBC": 12,
    "Nikkei": 12,
    "The Information": 12,
    "TechCrunch": 8,
}


THEMES = [
    {
        "name": "AI算力",
        "queries": ["AI 算力 GPU 数据中心 液冷", "英伟达 AI 数据中心 液冷 Rubin", "Nvidia AI datacenter China supply chain"],
        "boards": "算力、液冷、服务器、光模块、PCB",
        "keywords": ["AI", "算力", "GPU", "数据中心", "液冷", "光模块", "服务器", "Nvidia", "英伟达"],
    },
    {
        "name": "半导体",
        "queries": ["半导体 存储 芯片 先进封装 HBM", "国产半导体 设备 材料 存储", "semiconductor memory advanced packaging HBM"],
        "boards": "半导体设备、材料、先进封装、存储",
        "keywords": ["半导体", "芯片", "存储", "先进封装", "晶圆", "HBM", "DRAM", "NAND"],
    },
    {
        "name": "机器人",
        "queries": ["人形机器人 机器人 产业链", "特斯拉 人形机器人 进展", "humanoid robot supply chain"],
        "boards": "机器人、减速器、伺服、电机、传感器",
        "keywords": ["机器人", "人形机器人", "减速器", "伺服", "电机", "传感器", "robot"],
    },
    {
        "name": "低空经济",
        "queries": ["低空经济 eVTOL 无人机 政策", "无人机 空管 通航 低空", "eVTOL drone China policy"],
        "boards": "低空经济、无人机、eVTOL、空管",
        "keywords": ["低空", "eVTOL", "无人机", "通航", "空管"],
    },
    {
        "name": "新能源",
        "queries": ["新能源 锂电 光伏 储能 固态电池", "固态电池 储能 光伏 政策 订单", "solid state battery energy storage solar"],
        "boards": "锂电、固态电池、光伏、储能",
        "keywords": ["锂电", "光伏", "储能", "固态电池", "电池", "新能源"],
    },
    {
        "name": "军工航天",
        "queries": ["军工 航天 商业航天 卫星", "商业航天 火箭 卫星互联网", "space satellite defense industry"],
        "boards": "军工、商业航天、卫星互联网、航空发动机",
        "keywords": ["军工", "航天", "卫星", "导弹", "航空发动机", "商业航天"],
    },
    {
        "name": "医药",
        "queries": ["创新药 医药 FDA 临床 数据", "减重药 创新药 临床 数据", "biotech FDA clinical trial China"],
        "boards": "创新药、CXO、医疗器械",
        "keywords": ["创新药", "医药", "FDA", "临床", "药品", "biotech"],
    },
    {
        "name": "资源品",
        "queries": ["铜 黄金 稀土 锂 钨 价格", "有色金属 黄金 稀土 涨价", "copper gold rare earth lithium tungsten price"],
        "boards": "有色金属、黄金、稀土、锂、钨",
        "keywords": ["黄金", "铜", "稀土", "锂", "钨", "价格", "涨价", "commodity"],
    },
    {
        "name": "政策与新产业",
        "queries": ["产业政策 新质生产力 会议 规划", "部委 产业政策 技术突破 规划", "China industrial policy breakthrough"],
        "boards": "政策催化、新质生产力、国产替代、战略新兴产业",
        "keywords": ["政策", "规划", "会议", "突破", "国产替代", "新质生产力", "战略新兴产业"],
    },
]


EXPLOSIVE_WORDS = {
    "强催化": ["首次", "重磅", "突破", "量产", "商业化", "大单", "订单", "中标", "涨价", "短缺", "禁令", "出口管制", "获批", "FDA", "临床成功", "官宣", "发射成功"],
    "政策催化": ["政策", "规划", "会议", "补贴", "试点", "标准", "行动方案", "指导意见", "部委"],
    "趋势催化": ["扩产", "放量", "供不应求", "上调指引", "capex", "guidance", "新品", "发布", "产业链"],
}


def google_news_rss(query: str) -> str:
    from urllib.parse import quote

    encoded = quote(query + " when:1d")
    return f"https://news.google.com/rss/search?q={encoded}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def dedupe_key(title: str) -> str:
    main = re.split(r"\s+-\s+", title, maxsplit=1)[0]
    main = re.sub(r"[^\w\u4e00-\u9fff]+", "", main.lower())
    return main[:80]


def event_cluster_key(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('desc', '')}".lower()
    clusters = [
        ("nvidia_liquid_cooling", ["英伟达", "nvidia"], ["液冷", "liquid cooling", "rubin"]),
        ("hbm_thermal", ["hbm", "存储"], ["散热", "热管理", "thermal"]),
        ("advanced_packaging", ["先进封装", "封装"], ["扩产", "放量", "产能"]),
        ("humanoid_robot", ["人形机器人", "humanoid"], ["特斯拉", "tesla", "量产", "供应链"]),
        ("solid_state_battery", ["固态电池", "solid state"], ["量产", "突破", "电池"]),
        ("low_altitude", ["低空", "evtol", "无人机"], ["政策", "航线", "空管"]),
        ("gold_copper", ["黄金", "铜", "gold", "copper"], ["价格", "新高", "涨价"]),
    ]
    for key, group_a, group_b in clusters:
        if any(word in text for word in group_a) and any(word in text for word in group_b):
            return key
    return f"{item.get('theme', '')}:{dedupe_key(item.get('title', ''))[:28]}"


def source_from_title(title: str) -> str:
    parts = re.split(r"\s+-\s+", title)
    return parts[-1].strip() if len(parts) > 1 else ""


def source_score(title: str) -> int:
    source = source_from_title(title)
    return max((boost for name, boost in SOURCE_BOOST.items() if name.lower() in source.lower()), default=0)


def catalyst_score(item: dict) -> int:
    text = f"{item.get('title', '')} {item.get('desc', '')}".lower()
    score = 0
    for words in EXPLOSIVE_WORDS.values():
        score += sum(1 for word in words if word.lower() in text) * 8
    return min(32, score)


def catalyst_label(item: dict) -> str:
    score = catalyst_score(item)
    if score >= 24:
        return "强：可能近期发酵，适合加入重点观察"
    if score >= 12:
        return "中：有催化，但需要盘面确认"
    return "观察：题材相关，等待资金选择"


def summarize_item(item: dict) -> str:
    text = f"{item.get('title', '')} {item.get('desc', '')}".lower()
    theme = item.get("theme", "")
    boards = item.get("boards", "")
    cluster = event_cluster_key(item)

    if cluster == "nvidia_liquid_cooling":
        return "核心在于海外AI数据中心散热路线继续向液冷升级，强化服务器、液冷、光模块和PCB的硬件链逻辑。A股要看液冷分支能否从事件刺激扩散成板块梯队。"
    if cluster == "hbm_thermal":
        return "核心在于高端存储不只拼容量和带宽，散热/热管理正在成为HBM迭代的约束环节。A股映射到先进封装、材料、散热和存储链。"
    if cluster == "advanced_packaging":
        return "核心在于先进制程扩产会同步拉动先进封装、设备和材料需求，属于半导体产业链的中期景气线索。A股要看封测、设备、材料是否形成联动。"
    if cluster == "humanoid_robot":
        return "核心在于人形机器人从主题想象逐步进入量产和供应链验证阶段，容易带动减速器、伺服、电机、传感器等环节反复活跃。"
    if cluster == "solid_state_battery":
        return "核心在于固态电池从研发叙事走向产业化验证，若后续出现量产、订单或车企导入，可能带动电池材料和设备链发酵。"
    if cluster == "low_altitude":
        return "核心在于低空经济从政策框架走向航线、空管、运营和制造落地，市场更容易炒作确定性试点和订单兑现。"
    if cluster == "gold_copper":
        return "核心在于大宗价格变化会直接影响资源股盈利预期，若价格继续走强，黄金、有色和稀缺金属容易成为防守兼进攻方向。"

    if any(word in text for word in ["甲骨文", "oracle", "裁员", "预算", "capex"]):
        return "核心在于海外大厂继续把预算向AI基础设施倾斜，说明AI资本开支仍在挤占传统IT和人力成本。A股主要看算力硬件链能否获得重新定价。"
    if any(word in text for word in ["政策", "规划", "会议", "试点", "补贴"]):
        return f"核心在于政策端可能给产业方向提供持续催化，不一定立刻兑现业绩，但容易形成主题资金的预期差。A股映射到{boards}。"
    if any(word in text for word in ["订单", "中标", "量产", "扩产", "放量"]):
        return f"核心在于催化从概念走向订单、产能或量产验证，市场通常会更重视产业链兑现度。A股映射到{boards}。"
    if any(word in text for word in ["涨价", "短缺", "供不应求", "新高"]):
        return f"核心在于供需变化可能改善相关公司的价格和利润预期，短线资金容易沿着涨价链寻找弹性标的。A股映射到{boards}。"
    if any(word in text for word in ["突破", "首个", "首次", "获批", "临床"]):
        return f"核心在于技术、产品或审批进展可能打开产业想象空间，后续要看是否出现订单、验证或同类公司跟进。A股映射到{boards}。"

    return f"核心在于这条消息可能改变市场对{theme}的预期强度，不一定马上兑现，但若后续有政策、订单或龙头股确认，可能形成阶段性题材。A股映射到{boards}。"


def is_market_recap(title: str, desc: str = "") -> bool:
    text = f"{title} {desc}"
    recap_words = ["收评", "午评", "盘中", "尾盘", "复盘", "沪指", "创业板", "恒生科技", "恒科指", "领涨", "领跌", "齐涨", "齐跌"]
    catalyst_words = ["发布", "官宣", "突破", "订单", "中标", "涨价", "政策", "会议", "量产", "获批", "禁令", "补贴", "短缺"]
    return any(word in text for word in recap_words) and not any(word in text for word in catalyst_words)


def is_low_quality_source(title: str, desc: str = "") -> bool:
    text = f"{title} {desc}"
    bad_words = ["热门讨论", "怎么样", "股吧", "社区", "问答", "雪球", "东方财富股吧", "同花顺股吧", "盘前情报", "财富号"]
    return any(word in text for word in bad_words)


def parse_feed(xml_text: str, theme: dict) -> list[dict]:
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
        items.append(
            {
                "theme": theme["name"],
                "boards": theme["boards"],
                "title": title,
                "desc": desc,
                "pub_dt": pub_dt,
                "keywords": theme["keywords"],
                "source": source_from_title(title),
            }
        )
    return items


def collect_news() -> list[dict]:
    news = []
    seen = set()
    jobs = [(theme, query) for theme in THEMES for query in theme["queries"]]

    def fetch_job(job):
        theme, query = job
        try:
            xml_text = fetch_text(google_news_rss(query), timeout=8, retries=0)
            return parse_feed(xml_text, theme)
        except Exception:
            return []

    workers = int(os.environ.get("NEWS_WORKERS", "8"))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_job, job) for job in jobs]
        for future in as_completed(futures):
            for item in future.result():
                key = dedupe_key(item["title"])
                if key in seen:
                    continue
                seen.add(key)
                news.append(item)
    return news


def score_item(item: dict) -> float:
    text = f"{item.get('title', '')} {item.get('desc', '')}".lower()
    score = 28
    score += min(25, sum(1 for keyword in item["keywords"] if keyword.lower() in text) * 8)
    score += source_score(item.get("title", ""))
    score += market_preference_score(item)
    score += catalyst_score(item)
    if item.get("pub_dt"):
        age_hours = (datetime.now(CN_TZ) - item["pub_dt"]).total_seconds() / 3600
        if age_hours <= 8:
            score += 15
        elif age_hours <= 24:
            score += 8
    return max(10, min(96, score))


def fallback_rank(news: list[dict], top_n: int) -> list[dict]:
    ranked = []
    for item in news:
        item = dict(item)
        item["probability"] = round(score_item(item))
        item["duration"] = "高" if item["probability"] >= 88 else ("中" if item["probability"] >= 68 else "低")
        item["leaders"] = leader_line(item)
        item["catalyst"] = catalyst_label(item)
        item["summary"] = summarize_item(item)
        ranked.append(item)
    ranked.sort(key=lambda x: x["probability"], reverse=True)

    diversified = []
    used_clusters = set()
    used_themes = {}
    max_per_theme = int(os.environ.get("NEWS_MAX_PER_THEME", "2"))
    for item in ranked:
        cluster = event_cluster_key(item)
        theme = item.get("theme", "")
        if cluster in used_clusters:
            continue
        if used_themes.get(theme, 0) >= max_per_theme:
            continue
        diversified.append(item)
        used_clusters.add(cluster)
        used_themes[theme] = used_themes.get(theme, 0) + 1
        if len(diversified) >= top_n:
            break

    if len(diversified) < top_n:
        for item in ranked:
            cluster = event_cluster_key(item)
            if cluster in used_clusters:
                continue
            diversified.append(item)
            used_clusters.add(cluster)
            if len(diversified) >= top_n:
                break
    return diversified[:top_n]


def ask_openai(news: list[dict], top_n: int) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.environ.get("OPENAI_MODEL", "gpt-5.4").strip() or "gpt-5.4"
    compact = []
    for item in fallback_rank(news, min(40, len(news))):
        compact.append(
            {
                "theme": item["theme"],
                "boards": item["boards"],
                "leaders": item["leaders"],
                "catalyst": item["catalyst"],
                "summary": item["summary"],
                "title": item["title"],
                "source": item.get("source", ""),
                "desc": item["desc"][:300],
                "time": item["pub_dt"].strftime("%Y-%m-%d %H:%M") if item.get("pub_dt") else "",
            }
        )
    prompt = (
        "你是一名面向中国A股投资者的产业消息雷达。不要写当天A股复盘，不要输出链接。"
        f"请从以下新闻里挑出最可能在近期被A股资金拿来炒作的行业热点，最多{top_n}条。"
        "不要只看当前热门板块；如果消息足够爆、可能在未来几天发酵，也要列入。"
        "每条包含：消息标题、来源/发生地、A股映射板块、A股映射龙头股、潜在爆点、炒作逻辑、持续性、概率、反证风险。"
        "每条标题下面必须先给1-2句话消息摘要，说明发生了什么、为什么可能影响A股。"
        "最后给出3条开盘重点观察主线。用适合手机阅读的Markdown输出。"
    )
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": "只输出中文Markdown，不提供买入建议，不输出链接。"},
            {"role": "user", "content": prompt + "\n\n新闻列表：\n" + json.dumps(compact, ensure_ascii=False)},
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


def format_card(idx: int, item: dict) -> list[str]:
    pub = item["pub_dt"].strftime("%m-%d %H:%M") if item.get("pub_dt") else "时间未知"
    source = item.get("source") or "聚合源"
    return [
        color_bar(idx),
        soft_panel_start(idx),
        f"## {idx}. {item['title']}",
        "",
        cls_meta(source, pub, item["probability"], item["duration"]),
        "",
        f"{cls_tag(item['theme'])}{cls_tag('A股映射')}{cls_tag('潜在催化')}",
        "",
        f"**A股映射**：{item['boards']}",
        "",
        f"**消息摘要**：{item.get('summary') or summarize_item(item)}",
        "",
        f"**代表龙头**：{item['leaders']}",
        "",
        f"**潜在爆点**：{item['catalyst']}",
        "",
        f"**交易观察**：若同主题龙头高开后仍有换手承接，可能扩散到产业链；若不是当下主线，则重点看是否连续两天有资金回流。",
        "",
        f"**反证风险**：龙头冲高回落、板块无梯队、消息股独涨不扩散，说明催化可能暂未被市场认可。",
        "",
        soft_panel_end(),
        "",
        "---",
        "",
    ]


def format_fallback(news: list[dict], top_n: int) -> str:
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    ranked = fallback_rank(news, top_n)
    lines = [
        cls_header(
            f"A股潜在催化雷达 {now}",
            "不只看当下热门板块，也纳入未来几天可能发酵的强催化；已剔除复盘稿、股吧/讨论页和重复事件。",
        ),
        "",
    ]
    if not ranked:
        lines.append("暂未抓到足够有效的产业催化消息。")
        return "\n".join(lines)
    for idx, item in enumerate(ranked, 1):
        lines.extend(format_card(idx, item))

    top_themes = []
    for item in ranked:
        if item["theme"] not in top_themes:
            top_themes.append(item["theme"])
    lines.extend(["## 开盘重点观察", ""])
    for theme in top_themes[:3]:
        lines.append(f"- {theme}：看代表龙头是否主动走强，以及板块是否出现梯队。")
    return "\n".join(lines)


def build_report(top_n: int) -> tuple[str, str]:
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    title = f"A股潜在催化雷达 {now}"
    news = collect_news()
    generated = ask_openai(news, top_n)
    if generated and not generated.startswith("> OpenAI生成失败"):
        return title, generated
    fallback = format_fallback(news, top_n)
    if generated:
        fallback = generated + fallback
    return title, fallback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--top", type=int, default=int(os.environ.get("NEWS_TOP_N", "10")))
    args = parser.parse_args()
    title, content = build_report(args.top)
    if args.dry_run:
        print(content)
    else:
        result = send_pushplus(title, content)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
