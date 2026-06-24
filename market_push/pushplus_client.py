import json
import os
import re
import time
import urllib.request


PUSHPLUS_SEND_URL = "https://www.pushplus.plus/send"
PUSHPLUS_MAX_CHARS = 12000
PUSHPLUS_CHUNK_DELAY_SEC = 3.0


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def send_pushplus(title: str, content: str) -> dict:
    token = _env("PUSHPLUS_TOKEN")
    if not token:
        raise RuntimeError("PUSHPLUS_TOKEN is not configured")

    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",
        "channel": _env("PUSHPLUS_CHANNEL", "wechat") or "wechat",
    }
    topic = _env("PUSHPLUS_TOPIC")
    if topic:
        payload["topic"] = topic

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        PUSHPLUS_SEND_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"raw": text}
    code = result.get("code")
    if code != 200:
        raise RuntimeError(f"PushPlus send failed: {result}")
    return result


def send_pushplus_with_retry(title: str, content: str, retries: int = 3) -> dict:
    last_error = None
    for attempt in range(retries + 1):
        try:
            return send_pushplus(title, content)
        except RuntimeError as exc:
            last_error = exc
            message = str(exc)
            if "推送频率过快" not in message or attempt >= retries:
                raise
            time.sleep(PUSHPLUS_CHUNK_DELAY_SEC * (2 ** attempt))
    raise last_error


def _split_content(content: str, max_chars: int = PUSHPLUS_MAX_CHARS) -> list[str]:
    if len(content) <= max_chars:
        return [content]

    chunks = []
    current = ""
    blocks = re.split(r"\n\s*---\s*\n", content)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        candidate = block if not current else current + "\n\n---\n\n" + block
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(block) <= max_chars:
            current = block
            continue
        paragraphs = [p for p in block.split("\n\n") if p.strip()]
        para_current = ""
        for para in paragraphs:
            para_candidate = para if not para_current else para_current + "\n\n" + para
            if len(para_candidate) <= max_chars:
                para_current = para_candidate
                continue
            if para_current:
                chunks.append(para_current)
                para_current = ""
            if len(para) <= max_chars:
                para_current = para
                continue
            start = 0
            while start < len(para):
                chunks.append(para[start : start + max_chars])
                start += max_chars
        if para_current:
            current = para_current
    if current:
        chunks.append(current)
    return chunks


def send_pushplus_chunked(title: str, content: str, max_chars: int = PUSHPLUS_MAX_CHARS) -> list[dict]:
    parts = _split_content(content, max_chars=max_chars)
    results = []
    total = len(parts)
    for idx, part in enumerate(parts, 1):
        part_title = title if total == 1 else f"{title} ({idx}/{total})"
        if idx > 1:
            time.sleep(PUSHPLUS_CHUNK_DELAY_SEC)
        results.append(send_pushplus_with_retry(part_title, part))
    return results
