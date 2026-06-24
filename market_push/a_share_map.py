BUCKETS = [
    {
        "name": "AI算力/液冷/服务器",
        "aliases": ["AI", "算力", "GPU", "数据中心", "液冷", "服务器", "光模块", "Nvidia", "英伟达", "datacenter", "liquid cooling"],
        "leaders": ["工业富联", "中际旭创", "新易盛", "天孚通信", "浪潮信息", "中科曙光", "沪电股份", "胜宏科技"],
        "weight": 18,
    },
    {
        "name": "半导体/存储/先进封装",
        "aliases": ["半导体", "芯片", "存储", "HBM", "DRAM", "NAND", "先进封装", "晶圆", "semiconductor", "memory", "chip"],
        "leaders": ["中芯国际", "北方华创", "中微公司", "寒武纪", "海光信息", "长电科技", "通富微电", "兆易创新", "佰维存储"],
        "weight": 17,
    },
    {
        "name": "PCB/消费电子",
        "aliases": ["PCB", "覆铜板", "消费电子", "AI手机", "AIPC", "苹果", "折叠屏"],
        "leaders": ["沪电股份", "东山精密", "胜宏科技", "鹏鼎控股", "景旺电子", "立讯精密", "歌尔股份", "蓝思科技"],
        "weight": 15,
    },
    {
        "name": "机器人/自动驾驶",
        "aliases": ["机器人", "人形机器人", "humanoid", "robot", "减速器", "伺服", "电机", "传感器", "自动驾驶"],
        "leaders": ["三花智控", "拓普集团", "绿的谐波", "中大力德", "鸣志电器", "柯力传感", "埃斯顿"],
        "weight": 14,
    },
    {
        "name": "低空经济/商业航天",
        "aliases": ["低空", "eVTOL", "无人机", "通航", "空管", "商业航天", "卫星", "火箭"],
        "leaders": ["万丰奥威", "宗申动力", "中信海直", "莱斯信息", "深城交", "四川九洲", "中国卫星", "航天电子"],
        "weight": 13,
    },
    {
        "name": "新能源/储能/固态电池",
        "aliases": ["新能源", "锂电", "固态电池", "储能", "光伏", "电池", "lithium", "solar", "storage", "EV"],
        "leaders": ["宁德时代", "阳光电源", "亿纬锂能", "德业股份", "天齐锂业", "赣锋锂业", "固德威"],
        "weight": 10,
    },
    {
        "name": "创新药/医疗器械",
        "aliases": ["创新药", "医药", "FDA", "临床", "CXO", "医疗器械", "biotech", "clinical", "drug"],
        "leaders": ["恒瑞医药", "百济神州-U", "药明康德", "泰格医药", "迈瑞医疗", "联影医疗", "信达生物"],
        "weight": 10,
    },
    {
        "name": "资源品/黄金/有色",
        "aliases": ["黄金", "铜", "稀土", "锂", "钨", "石油", "油价", "gold", "copper", "rare earth", "commodity", "oil"],
        "leaders": ["紫金矿业", "洛阳钼业", "山东黄金", "中金黄金", "北方稀土", "中国稀土", "厦门钨业", "中国石油"],
        "weight": 12,
    },
    {
        "name": "军工/航空发动机",
        "aliases": ["军工", "导弹", "航发", "航空发动机", "defense", "aerospace"],
        "leaders": ["中航沈飞", "中航西飞", "航发动力", "中无人机", "铂力特", "光启技术"],
        "weight": 11,
    },
    {
        "name": "政策/信创/工业互联网",
        "aliases": ["政策", "规划", "会议", "新质生产力", "国产替代", "信创", "工业互联网", "数字化", "信息化", "两化融合", "战略新兴产业"],
        "leaders": ["中国软件", "太极股份", "中科曙光", "浪潮信息", "宝信软件", "用友网络", "东方国信", "鼎捷数智"],
        "weight": 9,
    },
]


def match_bucket(theme: str = "", boards: str = "", title: str = "", desc: str = "") -> dict:
    text = f"{theme} {boards} {title} {desc}".lower()
    best = None
    best_score = -1
    for bucket in BUCKETS:
        hits = sum(1 for alias in bucket["aliases"] if alias.lower() in text)
        score = hits * 10 + bucket["weight"]
        if score > best_score:
            best = bucket
            best_score = score
    return best or BUCKETS[0]


def market_preference_score(item: dict) -> int:
    bucket = match_bucket(item.get("theme", ""), item.get("boards", ""), item.get("title", ""), item.get("desc", ""))
    return int(bucket["weight"])


def leader_line(item: dict, max_names: int = 6) -> str:
    bucket = match_bucket(item.get("theme", ""), item.get("boards", ""), item.get("title", ""), item.get("desc", ""))
    return f"{bucket['name']}：{'、'.join(bucket['leaders'][:max_names])}"
