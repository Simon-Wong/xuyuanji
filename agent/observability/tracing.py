"""trace_id 生成与管理。"""
from __future__ import annotations

import time
import uuid


def generate_trace_id() -> str:
    """生成 trace_id：毫秒时间戳_UUID4无横杠。

    格式与 EventBus 的 event_id 一致，字符串排序 = 时间先后序。
    在用户输入新对话时调用，后续该对话内所有组件显式传递同一个 trace_id。
    """
    ts_ms = str(int(time.time() * 1000))
    hex32 = uuid.uuid4().hex
    return f"{ts_ms}_{hex32}"
