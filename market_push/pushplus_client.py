import json
import os
import urllib.request


PUSHPLUS_SEND_URL = "https://www.pushplus.plus/send"
PUSHPLUS_MAX_CHARS = 18000


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


def _split_content(content: str, max_chars: int = PUSHPLUS_MAX_CHARS) -> list[str]:
    if len(content) <= max_chars:
        return [content]

    chunks = []
    current = ""
    blocks = content.split("\n---\n")
    for block in blocks:
        candidate = block if not current else current + "\n---\n" + block
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(block) <= max_chars:
            current = block
            continue
        start = 0
        while start < len(block):
            chunks.append(block[start : start + max_chars])
            start += max_chars
    if current:
        chunks.append(current)
    return chunks


def send_pushplus_chunked(title: str, content: str, max_chars: int = PUSHPLUS_MAX_CHARS) -> list[dict]:
    parts = _split_content(content, max_chars=max_chars)
    results = []
    total = len(parts)
    for idx, part in enumerate(parts, 1):
        part_title = title if total == 1 else f"{title} ({idx}/{total})"
        results.append(send_pushplus(part_title, part))
    return results
