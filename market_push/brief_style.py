CLS_RED = "#d71920"
CLS_DARK = "#222222"
CLS_MUTED = "#666666"
CLS_BORDER = "#e8e8e8"
CLS_BG = "#fff7f7"


def color_bar(index: int) -> str:
    return f'<div style="height:3px;background:{CLS_RED};margin:18px 0 0 0;"></div>'


def soft_panel_start(index: int) -> str:
    return (
        f'<div style="border:1px solid {CLS_BORDER};border-top:0;'
        f'padding:12px 12px 10px 12px;margin:0 0 12px 0;background:#ffffff;">'
    )


def soft_panel_end() -> str:
    return "</div>"


def cls_header(title: str, subtitle: str) -> str:
    return (
        f'<div style="border-left:5px solid {CLS_RED};padding:8px 0 8px 12px;'
        f'margin:4px 0 14px 0;background:{CLS_BG};">'
        f'<div style="font-size:18px;font-weight:700;color:{CLS_DARK};">{title}</div>'
        f'<div style="font-size:12px;color:{CLS_MUTED};margin-top:4px;">{subtitle}</div>'
        f"</div>"
    )


def cls_meta(source: str, time_text: str, probability: int, duration: str) -> str:
    return (
        f'<div style="font-size:12px;color:{CLS_MUTED};margin:6px 0 10px 0;">'
        f'<span style="color:{CLS_RED};font-weight:600;">快讯解读</span>'
        f' | 来源：{source} | 时间：{time_text} | 概率：{probability}% | 持续性：{duration}'
        f"</div>"
    )


def cls_tag(text: str) -> str:
    return (
        f'<span style="display:inline-block;border:1px solid {CLS_RED};color:{CLS_RED};'
        f'padding:1px 6px;margin-right:4px;font-size:12px;border-radius:2px;">{text}</span>'
    )
