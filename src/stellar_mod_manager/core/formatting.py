"""字节到可读字符串等展示层共用的格式化工具（无 Qt 依赖）。"""

from __future__ import annotations


def human_size(num_bytes: float | int) -> str:
    """把字节数格式化成 ``1.23 GB`` 这样的可读文本。"""
    size = float(num_bytes)
    if size < 0:
        return "-"
    units = ("B", "KB", "MB", "GB", "TB")
    for index, unit in enumerate(units):
        if size < 1024 or index == len(units) - 1:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}" if size < 100 else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover - 兜底


def human_duration(seconds: float) -> str:
    """把秒数格式化成 ``1 分 05 秒``。"""
    total = max(0, int(round(seconds)))
    if total < 60:
        return f"{total} 秒"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {secs:02d} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes:02d} 分"


def truncate_middle(text: str, limit: int = 48) -> str:
    """从中间截断过长文本，保留首尾。"""
    if len(text) <= limit:
        return text
    head = (limit - 1) // 2
    tail = limit - 1 - head
    return f"{text[:head]}…{text[-tail:]}"
