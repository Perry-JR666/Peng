import json
import os
import time
import urllib.request


PUSHPLUS_SEND_URL = "http://www.pushplus.plus/send"


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
        "timestamp": str(int(time.time() * 1000)),
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
