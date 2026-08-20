# 开发任务 3 执行计划：订阅发布-事件机制 (EventBus)
> 对应 `dev_plan.md` 阶段 0 的任务 #3「订阅发布-事件机制」
> 本文档为任务 #3 的讨论结论定版与执行计划，与 exe_plan_part_1 风格保持一致。

---

## 一、文档定位与范围

### 1.1 任务目标
实现 Agent 架构的**事件驱动基础组件 EventBus**，承担两大职责：
1. **注册事件 + before/after 处理链条**：供业务层注册默认副作用（框架尽量少做具体的事情，handler 做实际处理）
2. **订阅发布**：按 5 层主题 pattern 分发给所有订阅者

### 1.2 与其他模块的关系
- **上游**：AgentApp（CLI / Web Server 共用入口）持有一个 EventBus 实例。
- **下游**：上下文管理器、路由器、核验器、汇报器、展示器等所有被动组件都通过 `subscribe` 接收事件；调度器、任务组等通过 `register_object / update_object / trigger_event` 驱动对象生命周期。
- **零依赖**：不依赖 `config`、不依赖后续阶段的"核心数据结构"、不引入任何第三方库。
- **后续扩展**："主题路由 pattern 订阅"已在本轮一起实现（不再分两轮），后续开发只需往 `register_event` 追加处理链条、`subscribe` 订阅即可。

---

## 二、已讨论并确认的设计决策

### 2.1 Python 版本与基础约束
| 项 | 结论 |
|---|---|
| Python 基线 | **Python 3.11**（使用 PEP 604 `X \| Y`、`typing.Self`（如需要）、内置泛型） |
| 单例？ | **否**。EventBus 是普通类，显式构造，AgentApp 统一持有；测试环境可 new 多个独立实例。 |
| 语法糖？ | **不要**，API 只保留原始形式，不提供 `subscribe_object / subscribe_by_event_type` 等快捷封装。 |
| once / strict / 双轨同步异步 | once=True 订阅 + strict 默认严格模式 + 同步 publish。异步 `publish_async` 本轮不做（后续需要再加）。 |

### 2.2 对象/事件 仓库结构（唯一定义，不再加其他 map）
严格按用户的原话。EventBus 内**只有两个独立 map**（内部全部用 `threading.RLock` 保护）：

```python
# map 1：对象仓库。对象生命周期内一直存在，remove_object 才删除。
#   key   = object_id（字符串；None 时自动生成 毫秒_UUID4）
#   value = 长度为 2 的 list：[ 对象数据本身, metadata 字典 ]
#
#   位置 0（data）：任意 JSON 可序列化类型（dict/list/str/int 都行）。不强制为 dict，
#                   不注入任何保留键，永远不与业务字段冲突。
#   位置 1（metadata）：dict，EventBus 内部管理，当前固定 4 个元数据键：
#                      {
#                         "_user_id":         str,
#                         "_session_id":      str,
#                         "_conversation_id": str,
#                         "_object_id":       str,
#                      }
#                      后续可自由扩展 created_at / event_count 等其他 key，无需改类定义。
_objects: dict[str, list[Any, dict]]

# map 2：事件仓库（临时存）。每个事件 publish 完立即从这里删除。
#   key   = event_id（毫秒_UUID4；None 时自动生成）
#   value = Event dataclass 实例
_events: dict[str, Event]
```

### 2.3 5 层主题（订阅路由）
- 顺序：`user_id . session_id . conversation_id . object_id . event_type`
- 缺失占位：`"-"`（当前 register_object / trigger_event 的参数必填 user/session/conv/object_id，占位仅作为边缘 case 的约定保留）
- 分隔符：`.`
- 通配符：
  - `*`：匹配**单层**（一个层级）
  - `>`：匹配从当前位置开始的**所有后续层级**，只能出现在 pattern 的**最后一位**
  - pattern 含 `>` 但不在末尾 → `ValueError` 拒绝订阅

### 2.4 Event dataclass（9 字段，前 4 层独立暴露）
```python
@dataclass
class Event:
    event_id: str                # 空时 _publish_internal 自动生成：毫秒_UUID4
    trace_id: str                # 链路追踪 ID，必填（空则自动生成 UUID4）
    user_id: str                 # 5 层主题第 1 层
    session_id: str              # 5 层主题第 2 层
    conversation_id: str         # 5 层主题第 3 层
    object_id: str               # 5 层主题第 4 层
    event_type: str              # 5 层主题第 5 层（仅此一个类型字段，不再拆分 kind/event_type）
    data: Any = None             # 完整载荷。框架原样透传，由调用方 & handler 自定内容
    timestamp: float = field(default_factory=time.time)
```
> 说明：前 4 层作为独立字段直接暴露，handler 直接取 `event.user_id` 等，**无需 split 字符串消耗 CPU**。匹配 pattern 时由 `_publish_internal` 内部拼一次 topic 字符串用于通配符匹配。

### 2.5 ID 生成格式（毫秒_UUID4 无横杠）
正则：`^\d{13}_[0-9a-f]{32}$`
- 13 位毫秒时间戳 + `_` + 32 位无横杠 UUID4 hex。
- 字符串字典序等价于时间先后序（天然排序）。
- 适用范围：`object_id` 空时、`event_id` 空时、`trace_id` 空时。

### 2.6 注册事件 & 处理链条
**API 签名**：
```python
def register_event(self, event_type: str, before: list[Handler], after: list[Handler]) -> None
```

**Handler 统一签名**：
```python
Handler = Callable[["EventBus", Event], None]
# 例：def my_handler(bus: EventBus, event: Event) -> None: ...
# （链条 handler 需要修改对象仓库所以拿 bus 引用；subscribe_handler 的签名另见 2.7）
```

**核心规则**：
- **追加模式，形成处理链条**。对同一 `event_type` 多次调用 `register_event`：
  ```
  第 1 次：register_event("更新", [h1], [a1])  →  before=[h1],   after=[a1]
  第 2 次：register_event("更新", [h3], [a3])  →  before=[h1,h3], after=[a1,a3]
  ```
  **不做覆盖**（后续不再动态修改链条，纯启动期静态注册）。
- **预置 3 个 debug print handler**（注册在对应链条的 before 列表链头）：
  ```python
  def _debug_h_idle(bus, evt):     print("call idle")
  def _debug_h_update(bus, evt):   print("call 更新")
  def _debug_h_complete(bus, evt): print("call 完成")
  ```
  调试时 stdout 直接可见事件流过；后续想替换直接追加用户自己的链条或加过滤。

### 2.7 订阅发布（subscribe / unsubscribe）
```python
def subscribe(self, pattern: str, handler: SubHandler, *, once: bool = False) -> None
def unsubscribe(self, pattern: str, handler: SubHandler) -> None
```
- **SubHandler 签名**（与链条 handler 区分）：`Callable[[str, Event], None]` → `(topic, event) -> None`。不需要 bus 引用，纯被动通知；需要 bus 时用闭包自行捕获。
- `once=True`：触发匹配一次后**自动从 _subs 列表移除**。
- `pattern` 校验：`>` 必须在末尾 → 否则 `ValueError`。
- 匹配时遍历 _subs 列表，所有匹配 pattern 的 handler **按注册顺序依次调用**。

### 2.8 对象 API（对外核心操作）
```python
# ── 新增对象：写入 _objects → 自动触发 idle 事件 publish → 返回 object_id
def register_object(
    self,
    user_id: str,
    session_id: str,
    conversation_id: str,
    object_id: str | None = None,
    data: Any = None,
    trace_id: str | None = None,
) -> str

# ── 触发事件（不修改 _objects！对象数据更新完全下放给处理链条的 handler）
def update_object(
    self,
    object_id: str,
    event_type: str = "更新",
    data: Any = None,
    trace_id: str | None = None,
) -> None
# ⚠️  注意：update_object 本身**不触碰 _objects 的 data**，只构造 Event → 调内部 publish 流程。
#       修改对象数据必须在 register_event 注册的 before/after handler 里自己做。

# ── 直接删除对象（不校验生命周期）
def remove_object(self, object_id: str) -> None
# 从 _objects 直接删除；"完成状态才允许 remove"的规则完全由业务层自己控制调用时机。

# ── 便捷：非对象变更触发的事件（系统心跳、自定义事件），不写 _objects
def trigger_event(
    self,
    user_id: str,
    session_id: str,
    conversation_id: str,
    object_id: str,
    event_type: str,
    data: Any = None,
    trace_id: str | None = None,
) -> None

# ── 只读辅助
def get_object(self, object_id: str) -> Any | None   # 返回 _objects[object_id][0]（对象数据本体）
def get_object_with_meta(self, object_id: str) -> tuple[Any, dict] | None  # 返回完整 [data, meta]
def list_objects(self) -> dict[str, Any]             # 返回 {object_id: 对象数据} 的浅拷贝
```

### 2.9 publish 内部完整流程（`_publish_internal`，私有方法）
外部不直接调 publish；对象 API（register_object / update_object / trigger_event）内部自动触发。流程严格按：

```
publish 生命周期：
  【前置】
    1. event_id 为空 → 自动生成（毫秒_UUID4）
    2. trace_id 为空 → 自动生成 UUID4
    3. 从 event 的 user_id/session_id/conversation_id/object_id/event_type 拼出 topic 字符串（用于 pattern 匹配）
    4. 事件实例存入 _events map
    5. 从 _chains[event.event_type] 取 (before_list, after_list)，未注册用空列表

  【阶段 ①：before 处理链条】
    按注册顺序依次调用 before_list 每个 handler(bus, event)
    strict=True  → 异常上抛，中断全部后续
    strict=False → 异常记录日志，继续下一个 handler

  【阶段 ②：自动发布 = 通知订阅者】
    遍历 _subs.copy()：
      若 topic 匹配 pattern：
        调用 subscribe_handler(topic, event)
        once=True → 调用完后从 _subs 移除对应 (pattern, handler, once)
    strict 规则同阶段 ①

  【阶段 ③：after 处理链条】
    按注册顺序依次调用 after_list 每个 handler(bus, event)
    strict 规则同阶段 ①

  【收尾】（用 try/finally 保证无论是否异常都执行）
    6. 从 _events map 删除此 event.event_id
```

**关键保证**：
- strict=True 异常中断后仍然清理 `_events`（不会内存泄漏）。
- 调用顺序固定：**先改状态（before 链条）→ 再通知订阅者（拿到最新状态读）→ 再 after（清理/汇总）**。
- register_object 触发的 idle 事件、update_object 触发的更新事件、trigger_event 触发的自定义事件，全部走这一套流程。

---

## 三、实现范围与文件结构

### 3.1 新文件清单
本轮新增一个独立包 `agent/event_bus/`（在 agent 包下）：

```
xuyuanji/
├─ agent/                          # 本轮新增：Agent 核心包
│   └─ event_bus/                  # 事件总线子包
│       ├─ __init__.py             # re-export 公共 API（Event、EventBus、generate_id）
│       └─ bus.py                  # EventBus 类 + Event dataclass + 匹配/ID 辅助
├─ config/                         # 上一轮公共配置（已存在）
│   ├─ __init__.py
│   └─ loader.py
└─ main_body/
    └─ server_main.py              # 后续接入时 import
```

### 3.2 `agent/event_bus/__init__.py` 内容（约定）
```python
from .bus import Event, EventBus, generate_id

__all__ = ["Event", "EventBus", "generate_id"]
```
后续其他模块两种 import 等价：
```python
from agent.event_bus.bus import EventBus
# 或
from agent.event_bus import EventBus
```

### 3.3 `agent/event_bus/bus.py` 代码骨架（精确到实现）
```python
"""
EventBus：事件驱动核心组件
========================

两大职责：
    1. register_event：事件类型 → before/after 处理链条（追加模式）
    2. subscribe：    5 层主题 pattern 订阅 + 分发

对象生命周期 API：register_object / update_object / remove_object + trigger_event
（内部自动触发 _publish_internal 流程，不对外暴露 publish 函数）

两个内部 map：
    _objects[object_id] = [对象数据, metadata_dict]      （长生命周期）
    _events[event_id]  = Event 实例                       （临时，publish 完即删）

5 层主题顺序：user_id . session_id . conversation_id . object_id . event_type
通配符：* 单层  /  > 多层末尾
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


Handler = Callable[["EventBus", "Event"], None]
SubHandler = Callable[[str, "Event"], None]


# ==============================================================================
# Event：9 字段，前 4 层独立暴露（无需 split 消耗 CPU）
# ==============================================================================
@dataclass
class Event:
    event_id: str
    trace_id: str
    user_id: str
    session_id: str
    conversation_id: str
    object_id: str
    event_type: str
    data: Any = None
    timestamp: float = field(default_factory=time.time)


# ==============================================================================
# EventBus：普通类，不做单例，RLock 保护两个 map
# ==============================================================================
class EventBus:
    # ----------------------------------------------------------------------
    # 构造
    # ----------------------------------------------------------------------
    def __init__(self, strict: bool = True) -> None:
        self._strict = strict

        # 两个核心 map（严格按约定，不再加其他）
        self._objects: dict[str, list[Any, dict]] = {}
        self._events:  dict[str, Event]         = {}

        # 链条字典 + 订阅列表 + 锁
        self._chains: dict[str, tuple[list[Handler], list[Handler]]] = {}
        self._subs:   list[tuple[str, SubHandler, bool]] = []
        self._lock = threading.RLock()

        # 预置 3 个 debug print handler（before 链头）
        self._init_builtin_debug_handlers()

    def _init_builtin_debug_handlers(self) -> None:
        """内置 3 条空链条 + print 调试 handler 追加进 before 列表。"""
        self.register_event("idle",   [lambda b, e: print("call idle")],   [])
        self.register_event("更新",   [lambda b, e: print("call 更新")],   [])
        self.register_event("完成",   [lambda b, e: print("call 完成")],   [])

    # ----------------------------------------------------------------------
    # 内部辅助：ID 生成 + topic 匹配 + metadata 读写
    # ----------------------------------------------------------------------
    @staticmethod
    def _generate_id() -> str:
        """毫秒时间戳_UUID4无横杠。字符串排序 = 时间先后序。"""
        ts_ms = str(int(time.time() * 1000))
        hex32 = uuid.uuid4().hex
        return f"{ts_ms}_{hex32}"

    @staticmethod
    def _ensure_trace(trace_id: str | None) -> str:
        return trace_id if trace_id else str(uuid.uuid4())

    @staticmethod
    def _match_topic(pattern: str, topic: str) -> bool:
        """5 层 pattern 匹配：* 单层，> 仅末尾多层。
           pattern 含 > 但不在末尾应在 subscribe 时拦截，本函数假设 pattern 合法。"""
        p_segs = pattern.split(".")
        t_segs = topic.split(".")

        i = 0
        while i < len(p_segs):
            p = p_segs[i]
            if p == ">":
                # 只能是最后一段
                return True
            if i >= len(t_segs):
                return False
            if p != "*" and p != t_segs[i]:
                return False
            i += 1
        # 所有 pattern segment 匹配完毕且长度也相同
        return i == len(t_segs)

    # ---------- metadata 辅助（register_object 写，update 读）----------
    def _get_meta_fields(self, object_id: str) -> tuple[str, str, str]:
        """从 _objects[object_id][1] 的 metadata 取回 user_id, session_id, conversation_id。"""
        if object_id not in self._objects:
            raise KeyError(f"[EventBus] _objects 中不存在 object_id={object_id}，请先 register_object")
        meta = self._objects[object_id][1]
        return meta["_user_id"], meta["_session_id"], meta["_conversation_id"]

    # ----------------------------------------------------------------------
    # 公共 API 1/3：register_event（链条，追加模式）
    # ----------------------------------------------------------------------
    def register_event(self, event_type: str, before: list[Handler], after: list[Handler]) -> None:
        with self._lock:
            if event_type not in self._chains:
                self._chains[event_type] = ([], [])
            cur_before, cur_after = self._chains[event_type]
            cur_before.extend(before)
            cur_after.extend(after)

    # ----------------------------------------------------------------------
    # 公共 API 2/3：subscribe / unsubscribe（5 层主题 pattern）
    # ----------------------------------------------------------------------
    def subscribe(self, pattern: str, handler: SubHandler, *, once: bool = False) -> None:
        # 合法性校验：> 只能出现在最后一位
        segs = pattern.split(".")
        for idx, s in enumerate(segs):
            if s == ">" and idx != len(segs) - 1:
                raise ValueError(
                    f"[EventBus] pattern={pattern!r} 非法：通配符 '>' 只能出现在末尾"
                )
        with self._lock:
            self._subs.append((pattern, handler, once))

    def unsubscribe(self, pattern: str, handler: SubHandler) -> None:
        with self._lock:
            self._subs = [
                (p, h, o) for (p, h, o) in self._subs if not (p == pattern and h is handler)
            ]

    # ----------------------------------------------------------------------
    # 公共 API 3/3：对象生命周期（register_object / update_object / remove_object / trigger_event）
    # ----------------------------------------------------------------------
    def register_object(
        self,
        user_id: str,
        session_id: str,
        conversation_id: str,
        object_id: str | None = None,
        data: Any = None,
        trace_id: str | None = None,
    ) -> str:
        if object_id is None:
            object_id = self._generate_id()
        meta: dict = {
            "_user_id":         user_id,
            "_session_id":      session_id,
            "_conversation_id": conversation_id,
            "_object_id":       object_id,
        }
        event = Event(
            event_id=self._generate_id(),
            trace_id=self._ensure_trace(trace_id),
            user_id=user_id,
            session_id=session_id,
            conversation_id=conversation_id,
            object_id=object_id,
            event_type="idle",
            data=data,
        )
        with self._lock:
            self._objects[object_id] = [data, meta]
        # ↓ 对象存入后再 publish（锁分开：publish 可能触发外部回调耗时久）
        self._publish_internal(event)
        return object_id

    def update_object(
        self,
        object_id: str,
        event_type: str = "更新",
        data: Any = None,
        trace_id: str | None = None,
    ) -> None:
        with self._lock:
            user_id, session_id, conversation_id = self._get_meta_fields(object_id)
        event = Event(
            event_id=self._generate_id(),
            trace_id=self._ensure_trace(trace_id),
            user_id=user_id,
            session_id=session_id,
            conversation_id=conversation_id,
            object_id=object_id,
            event_type=event_type,
            data=data,
        )
        self._publish_internal(event)  # ⚠️ update_object 本身不修改 _objects[object_id][0]！完全下放 handler

    def remove_object(self, object_id: str) -> None:
        """直接从 _objects 删除（不校验任何生命周期规则，业务层自行控制时机）。"""
        with self._lock:
            if object_id in self._objects:
                del self._objects[object_id]

    def trigger_event(
        self,
        user_id: str,
        session_id: str,
        conversation_id: str,
        object_id: str,
        event_type: str,
        data: Any = None,
        trace_id: str | None = None,
    ) -> None:
        """不触碰对象数据的纯事件触发（心跳 / 聚合自定义事件等）。"""
        event = Event(
            event_id=self._generate_id(),
            trace_id=self._ensure_trace(trace_id),
            user_id=user_id,
            session_id=session_id,
            conversation_id=conversation_id,
            object_id=object_id,
            event_type=event_type,
            data=data,
        )
        self._publish_internal(event)

    # ---------- 只读辅助 ----------
    def get_object(self, object_id: str) -> Any | None:
        with self._lock:
            if object_id not in self._objects:
                return None
            return self._objects[object_id][0]

    def get_object_with_meta(self, object_id: str) -> tuple[Any, dict] | None:
        with self._lock:
            if object_id not in self._objects:
                return None
            d, m = self._objects[object_id]
            return d, dict(m)  # 浅拷贝，防止外部改 meta

    def list_objects(self) -> dict[str, Any]:
        with self._lock:
            return {oid: entry[0] for oid, entry in self._objects.items()}

    # ----------------------------------------------------------------------
    # 内部 publish 流程（私有，对象 API 自动调）
    # ----------------------------------------------------------------------
    def _publish_internal(self, event: Event) -> None:
        # 前置：拼一次 topic 字符串（用于 pattern 匹配）+ 存入 _events
        topic = ".".join([
            event.user_id, event.session_id,
            event.conversation_id, event.object_id, event.event_type,
        ])
        with self._lock:
            self._events[event.event_id] = event

        try:
            # 取链条（未注册为空列表）
            with self._lock:
                before_list, after_list = self._chains.get(event.event_type, ([], []))
                before_list = list(before_list)
                after_list  = list(after_list)
                subs_snap   = list(self._subs)

            # ① before 处理链条
            for h in before_list:
                try:
                    h(self, event)
                except Exception:
                    if self._strict:
                        raise
                    # 宽松模式：打印 stderr（生产用 logging 可后续替换，此处保持零依赖）
                    import sys
                    print(f"[EventBus] before handler 异常（event_type={event.event_type}）", file=sys.stderr)

            # ② 自动发布：分发给所有匹配 pattern 的订阅者
            for pattern, h_sub, once in subs_snap:
                if not self._match_topic(pattern, topic):
                    continue
                try:
                    h_sub(topic, event)
                except Exception:
                    if self._strict:
                        raise
                    import sys
                    print(f"[EventBus] subscribe handler 异常（pattern={pattern}）", file=sys.stderr)
                finally:
                    if once:
                        with self._lock:
                            self._subs = [
                                (p, hs, o)
                                for (p, hs, o) in self._subs
                                if not (p == pattern and hs is h_sub and o == once)
                            ]

            # ③ after 处理链条
            for h in after_list:
                try:
                    h(self, event)
                except Exception:
                    if self._strict:
                        raise
                    import sys
                    print(f"[EventBus] after handler 异常（event_type={event.event_type}）", file=sys.stderr)
        finally:
            # 收尾：一定清理事件 map（异常也不泄漏）
            with self._lock:
                self._events.pop(event.event_id, None)


# 模块级便捷函数（统一生成 ID，外部也能直接用）
def generate_id() -> str:
    return EventBus._generate_id()
```

---

## 四、验收标准（22 条，全通过方可进入下一阶段）

### 4.1 基础结构（T1-T6）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T1 | 两个 EventBus 实例互不干扰（非单例） | busA.register_object → busB.list_objects() 返回空；busA 发布事件 busB 订阅者收不到 |
| T2 | generate_id 格式符合 毫秒_UUID4 | 正则匹配 `^\d{13}_[0-9a-f]{32}$`，生成 1000 个无重复；字符串排序与时间戳排序一致 |
| T3 | EventBus._objects 结构严格为 `{oid: [data, meta]}` | register_object 后，get_object_with_meta 返回 [data, dict]；meta 必含 4 个下划线元数据键且值与入参一致 |
| T4 | EventBus._events 结构 = `{eid: Event}` | publish 进行中 handler 内可从 bus._events[eid] 取回；publish 结束后（finally）必已删除 |
| T5 | RLock 并发安全：10 线程并发 register_object + update_object 50 次 | 最终 _objects 数量与预期一致；无 KeyError / RuntimeError；元数据键不丢失 |
| T6 | Event dataclass 字段共 9 个且不含 kind / topic | 反射 `Event.__dataclass_fields__` 集合 == `{event_id, trace_id, user_id, session_id, conversation_id, object_id, event_type, data, timestamp}` |

### 4.2 处理链条（T7-T11）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T7 | register_event 追加模式 | 对 "更新" 调两次：[h1,h2] 再 [h3] → before 链调用顺序 h1→h2→h3；after 同理 |
| T8 | 预置 3 个 debug print 输出正确 | register_object / update_object / trigger_event("完成") 分别触发 → stdout 捕获到 "call idle" / "call 更新" / "call 完成" |
| T9 | before → subscribe_handler → after 严格调用顺序 | 三 handler 各自 append 标记 list → 结果顺序是 [before..., sub_handler..., after...] |
| T10 | strict=True：handler 抛异常 → 中断后续 + 上抛 | h1 抛 → h2 不执行；外部捕获到同样异常 |
| T11 | strict=False：handler 异常 → 只打日志继续执行 | h1 抛 → h2 仍执行；不影响订阅者；finally 清理 _events 正常 |

### 4.3 订阅 / 模式匹配（T12-T18）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T12 | 精确匹配 | `sub("a.b.c.d.e", fn)` + 发布 topic="a.b.c.d.e" → fn 命中；改任意层都不命中 |
| T13 | `*` 单层通配 | `sub("*.*.c.*.e", fn)` → `a.b.c.d.e` 命中；`a.x.y.d.e` 不命中（第 3 层不是 c）；层数不匹配 `a.b.c.d.e.f` 不命中 |
| T14 | `>` 末尾多层通配 | `sub("a.>", fn)` → `a` 命中；`a.b`、`a.b.c.d.e` 全部命中；`z.a` 不命中 |
| T15 | `>` 不在末尾 → ValueError | `subscribe("a.>.c", fn)` 立即抛 ValueError 拒绝订阅 |
| T16 | once=True 一次性订阅 | once=True 的 fn 被调用 1 次后，再次发布同 topic 不再命中；unsubscribe 也能正常手动移除 |
| T17 | subscribe handler 签名为 (topic, event) | handler 被调用时两个参数分别等于发布的 topic/event（同一对象引用非拷贝） |
| T18 | unsubscribe 立即生效 | 发布两次：先 unsub → 第二次 fn 不被调用 |

### 4.4 对象 API 与 publish 流程（T19-T22）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T19 | register_object：自动生成 oid/meta/eid/trace；返回 oid；data 类型任意 | 传入 data=list/str/dict 三种类型，get_object() 返回同一对象引用（或等价值）；meta 四键与入参一致；自动触发 idle publish 并调用链条 handler |
| T20 | update_object：**不修改对象 data**（纯下放 handler） | update_object 前后对 _objects[oid][0] 做 id() 比较 → 完全相同；handler 自己在 before 里改了才算 |
| T21 | trigger_event：不写 _objects，只走 publish 流程 | 对不存在的 object_id 调 trigger_event，_objects 无新增；链条 handler 与 subscribe handler 正常调用 |
| T22 | remove_object：无条件直接删 | remove_object(oid) 后，get_object 返回 None；再次 update_object 抛 KeyError（因为 meta 不存在） |

---

## 五、结论：所有设计点已确认

| 讨论项 | 最终结论（已确认） |
|---|---|
| **两个 map 唯一定义** | `_objects = {oid: [data, meta]}`；`_events = {eid: Event}`；**不再加其他 map** |
| **_objects.meta 固定字段** | `_user_id/_session_id/_conversation_id/_object_id` 四个下划线键；其他字段任意扩展不侵入 data 位置 |
| **data 类型限制** | data 位置 0 可以是**任意类型**（dict/list/str/int...），不强制 dict，无保留键冲突 |
| 5 层主题顺序 | `user . session . conversation . object . event_type` |
| 分隔符 / 通配符 | `.` 分隔；`*` 单层；`>` 仅末尾；`>` 非法位置 ValueError |
| Event 字段数量 | 9 个（event_id/trace_id/user_id/session_id/conversation_id/object_id/event_type/data/timestamp）；**不再拆分 event_type+kind**；前 4 层独立暴露，无需 split |
| ID 格式 | `毫秒_UUID4无横杠`，天然字符串排序 = 时间排序 |
| register_event 策略 | **追加模式，形成处理链条**；无覆盖；启动期静态注册 |
| 链条 Handler 签名 | `Handler = (bus, event) -> None`（需要改仓库所以给 bus） |
| 预置 Handler | `print("call idle")` / `print("call 更新")` / `print("call 完成")` 3 条，debug 用 |
| subscribe / Handler 签名 | `SubHandler = (topic, event) -> None`；与链条 Handler 不同签名；once=True 一次后自动移除 |
| 对象 API | register_object（写 _objects + 自动 idle publish）/ update_object（不写 _objects，纯触发 publish）/ remove_object（无条件删）/ trigger_event（不碰对象纯事件） |
| put_object 是否需要 | **不需要** |
| **框架 vs handler 分工** | **框架尽量少做具体的事情，修改对象数据等实际处理完全下放给 register_event 注册的 handler** |
| publish 对外暴露？ | **不对外暴露**，作为私有方法 `_publish_internal`；对外只有 trigger_event（便捷构造 Event + 内部 publish） |
| strict / once / 异常清理 | strict=True（默认）异常上抛且仍 finally 清理 _events；once=True 支持；全部确认使用 |
| 单例 / 语法糖 | **两者都不要**。EventBus 普通类；无任何 pattern 快捷封装 |
| 线程安全 | 所有内部 map/列表访问用 `threading.RLock` 包裹，_publish_internal 的 finally 必清理 _events |
