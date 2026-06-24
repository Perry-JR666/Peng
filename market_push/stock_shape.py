import argparse
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from http_utils import fetch_json, fetch_text, url_with_params
from pushplus_client import send_pushplus


CN_TZ = timezone(timedelta(hours=8))
TENCENT_HEADERS = {"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"}


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


def fetch_active_stocks(min_amount_yuan: float) -> list[dict]:
    stocks = []
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
            if price <= 0 or amount <= 0:
                continue
            if "ST" in name or "退" in name:
                continue
            if amount < min_amount_yuan:
                continue
            # Today should look like a pullback or pause, not a fresh chase point.
            if not (-8.8 <= pct <= 1.8):
                continue
            sec_market = "1" if market == "sh" else "0"
            stocks.append(
                {
                    "secid": f"{sec_market}.{code}",
                    "code": code,
                    "name": name,
                    "amount": amount,
                    "pct": pct,
                }
            )

    seen = set()
    unique = []
    for stock in stocks:
        if stock["code"] not in seen:
            seen.add(stock["code"])
            unique.append(stock)
    return unique


def fetch_kline(stock: dict) -> list[dict]:
    now = datetime.now(CN_TZ)
    beg = (now - timedelta(days=130)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    url = url_with_params(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        {
            "secid": stock["secid"],
            "fields1": "f1,f2,f3,f4,f5",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": 101,
            "fqt": 1,
            "beg": beg,
            "end": end,
        },
    )
    data = fetch_json(url, timeout=12, retries=1)
    rows = []
    for line in (data.get("data") or {}).get("klines") or []:
        parts = line.split(",")
        if len(parts) < 11:
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
                    "amount": float(parts[6]),
                    "amp": float(parts[7]),
                    "pct": float(parts[8]),
                    "chg": float(parts[9]),
                    "turn": float(parts[10]),
                }
            )
        except ValueError:
            continue
    return rows


def ma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def score_stock(stock: dict) -> dict | None:
    try:
        rows = fetch_kline(stock)
    except Exception:
        return None
    if len(rows) < 45:
        return None

    last = rows[-1]
    closes = [r["close"] for r in rows]
    lows = [r["low"] for r in rows]
    ma20 = ma(closes, 20)
    if not ma20:
        return None

    look = rows[-22:]
    peak_rel = max(range(len(look)), key=lambda i: look[i]["high"])
    peak = look[peak_rel]
    days_since_peak = len(look) - 1 - peak_rel
    if days_since_peak < 1 or days_since_peak > 7:
        return None

    drawdown = (peak["high"] - last["close"]) / peak["high"]
    if not 0.06 <= drawdown <= 0.17:
        return None

    peak_abs = len(rows) - 22 + peak_rel
    prior = rows[max(0, peak_abs - 45) : peak_abs + 1]
    base_low = min(r["low"] for r in prior)
    runup = (peak["high"] - base_low) / base_low
    if runup < 0.24:
        return None

    low_abs = max(0, peak_abs - 45) + min(range(len(prior)), key=lambda i: prior[i]["low"])
    if peak_abs - low_abs < 8:
        return None

    strong_days = [
        r
        for r in rows[max(0, peak_abs - 18) : peak_abs + 1]
        if r["pct"] >= 6.0 or (r["pct"] >= 4.5 and r["turn"] >= 5)
    ]
    if not strong_days:
        return None

    if last["close"] < ma20 * 0.95:
        return None
    if last["close"] < base_low * (1 + runup * 0.45):
        return None

    peak_vol = max(r["vol"] for r in rows[max(0, peak_abs - 10) : peak_abs + 1])
    vol_contract = last["vol"] / peak_vol if peak_vol else 1
    if vol_contract > 1.18 and last["pct"] < 0:
        return None
    if last["close"] <= min(lows[-20:]) * 1.03:
        return None

    pull_rows = rows[peak_abs + 1 :]
    if sum(1 for r in pull_rows if r["pct"] < 0) < 1:
        return None

    score = 50
    score += max(0, 18 - abs(drawdown - 0.11) * 180)
    score += min(16, max(0, (runup - 0.24) * 60))
    score += 10 if days_since_peak in (1, 2, 3) else 5
    score += 10 if last["close"] >= ma20 else 4
    score += 8 if vol_contract <= 0.75 else (4 if vol_contract <= 0.95 else 0)
    score += min(8, max(r["pct"] for r in strong_days))
    if last["pct"] < -7:
        score -= 8
    if last["pct"] > 1:
        score -= 4
    score = max(0, min(100, round(score, 1)))

    stop = min(last["low"], ma20 * 0.97)
    return {
        "code": stock["code"],
        "name": stock["name"],
        "score": score,
        "date": last["date"],
        "close": last["close"],
        "pct": last["pct"],
        "turn": last["turn"],
        "amount_yi": round(last["amount"] / 1e8, 2),
        "peak_date": peak["date"],
        "peak_high": peak["high"],
        "days_since_peak": days_since_peak,
        "drawdown_pct": round(drawdown * 100, 1),
        "runup_pct": round(runup * 100, 1),
        "ma20": round(ma20, 2),
        "vol_contract": round(vol_contract, 2),
        "trial_low": round(max(last["low"], last["close"] * 0.98), 2),
        "trial_high": round(min(last["high"], last["close"] * 1.03), 2),
        "stop": round(stop, 2),
        "pressure": round(peak["high"], 2),
    }


def scan_market(top_n: int, min_amount_yuan: float) -> list[dict]:
    stocks = fetch_active_stocks(min_amount_yuan)
    results = []
    workers = int(os.environ.get("STOCK_WORKERS", "18"))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(score_stock, stock) for stock in stocks]
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:top_n]


def format_report(candidates: list[dict]) -> tuple[str, str]:
    now = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")
    title = f"A股风华高科式形态筛选 {now[:10]}"
    if not candidates:
        return title, f"# {title}\n\n今天没有筛到足够接近的候选。"

    lines = [
        f"# {title}",
        "",
        "口径：强势启动后，第一次或早期分歧回踩；前面已证明强度，当前回踩不明显破坏趋势。",
        "",
        "| 股票 | 收盘 | 当日涨跌 | 相似度 | 回撤 | 试错观察区 | 失效位 | 压力位 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in candidates:
        lines.append(
            f"| {item['name']} {item['code']} | {item['close']:.2f} | {item['pct']:.2f}% | "
            f"{item['score']:.0f} | {item['drawdown_pct']:.1f}% | "
            f"{item['trial_low']:.2f}-{item['trial_high']:.2f} | {item['stop']:.2f} | {item['pressure']:.2f} |"
        )

    lines.extend(["", "## 重点观察", ""])
    for item in candidates[:5]:
        lines.append(
            f"- **{item['name']} {item['code']}**：{item['peak_date']} 高点 {item['peak_high']:.2f} 后第 "
            f"{item['days_since_peak']} 个交易日回踩，距高点回撤 {item['drawdown_pct']:.1f}%，"
            f"20日线 {item['ma20']:.2f}，量能约为峰值的 {item['vol_contract']:.2f} 倍。"
        )

    lines.extend(
        [
            "",
            "## 风险",
            "",
            "- 这是试错观察清单，不是买入建议。",
            "- 若次日跌破失效位，说明分歧可能演变成趋势破坏。",
            "- 大盘弱势时，强势股回踩可能继续扩大，需要等承接确认。",
        ]
    )
    return title, "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--top", type=int, default=int(os.environ.get("STOCK_TOP_N", "10")))
    args = parser.parse_args()

    min_amount = float(os.environ.get("STOCK_MIN_AMOUNT_YUAN", "120000000"))
    candidates = scan_market(args.top, min_amount)
    title, content = format_report(candidates)
    if args.dry_run:
        print(content)
    else:
        result = send_pushplus(title, content)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
