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
| once / 异常策略 | once=True 订阅 + handler 异常记日志跳过继续。无 strict 参数，消费线程统一容错。 |
| 队列 + 消费线程 | 构造时启动专用 daemon 线程，`queue.Queue` 实现 FIFO 顺序发布；`shutdown()` 优雅退出，剩余事件丢弃写日志。 |

### 2.2 对象/事件 仓库结构（唯一定义，不再加其他 map）
严格按用户的原话。EventBus 内**只有一个独立 map**（`_objects`）+ 一个 `queue.Queue`：

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
    event_id: str                # 空时 _enqueue 自动生成：毫秒_UUID4
    trace_id: str                # 链路追踪 ID，必填（空则自动生成 UUID4）
    user_id: str                 # 5 层主题第 1 层
    session_id: str              # 5 层主题第 2 层
    conversation_id: str         # 5 层主题第 3 层
    object_id: str               # 5 层主题第 4 层
    event_type: str              # 5 层主题第 5 层（仅此一个类型字段，不再拆分 kind/event_type）
    data: Any = None             # 完整载荷。框架原样透传，由调用方 & handler 自定内容
    timestamp: float = field(default_factory=time.time)
```
> 说明：前 4 层作为独立字段直接暴露，handler 直接取 `event.user_id` 等，**无需 split 字符串消耗 CPU**。匹配 pattern 时由 `_publish` 内部拼一次 topic 字符串用于通配符匹配。

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
def subscribe(self, pattern: str, handler: SubHandler, *, once: bool = False) -> str
def unsubscribe(self, subscription_id: str) -> None
```
- **subscribe 返回 GUID**（`subscription_id`），便于精确移除。允许同一 pattern 注册多个不同 handler，unsubscribe 时按 ID 精确移除。
- **SubHandler 签名**（与链条 handler 区分）：`Callable[[str, Event], None]` → `(topic, event) -> None`。不需要 bus 引用，纯被动通知；需要 bus 时用闭包自行捕获。
- `once=True`：触发匹配一次后**自动从 _subs 移除该 subscription_id**。
- `pattern` 校验：`>` 必须在末尾 → 否则 `ValueError`。
- 内部 `_subs` 从 list 改为 `dict[str, tuple[str, SubHandler, bool]]`，key = subscription_id，value = (pattern, handler, once)。
- 匹配时遍历 _subs 字典，所有匹配 pattern 的 handler **按注册顺序依次调用**（Python 3.7+ dict 保持插入序）。

### 2.8 对象 API（对外核心操作）
```python
# ── 新增对象：写入 _objects → 构造 idle Event → 入队 → 返回 object_id
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
# ⚠️  注意：update_object 本身**不触碰 _objects 的 data**，只构造 Event → 入队。
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

# ── 优雅退出
def shutdown(self) -> None
# 向队列放入 None 哨兵 → 消费线程处理完当前事件后退出 → 剩余事件丢弃写日志

# ── 只读辅助
def get_object(self, object_id: str) -> Any | None   # 返回 _objects[object_id][0]（对象数据本体）
def get_object_with_meta(self, object_id: str) -> tuple[Any, dict] | None  # 返回完整 [data, meta]
def list_objects(self) -> dict[str, Any]             # 返回 {object_id: 对象数据} 的浅拷贝
```

### 2.9 事件入队 + 消费线程分发流程

**对象 API（register_object / update_object / trigger_event）的职责**：
1. 构造 Event 对象（生成 event_id / trace_id）
2. 放入 `queue.Queue`（FIFO 保证顺序）
3. 立即返回调用方（不阻塞）

**消费线程 `_consumer_loop` 的 `_publish` 流程**：

```
publish 生命周期（消费线程内，单线程保证 FIFO）：
  【前置】
    1. 从 event 字段拼出 topic 字符串（用于 pattern 匹配）
    2. 从 _chains[event.event_type] 取 (before_list, after_list)，未注册用空列表
    3. 快照 _subs 字典（避免遍历时修改）

  【阶段 ①：before 处理链条】
    按注册顺序依次调用 before_list 每个 handler(bus, event)
    handler 异常 → 记日志，跳过当前 handler，继续下一个

  【阶段 ②：通知订阅者】
    遍历 _subs 快照：
      若 topic 匹配 pattern：
        调用 sub_handler(topic, event)
        handler 异常 → 记日志，跳过当前 handler，继续下一个
        once=True → 调用完后从 _subs 移除对应 subscription_id

  【阶段 ③：after 处理链条】
    按注册顺序依次调用 after_list 每个 handler(bus, event)
    handler 异常 → 记日志，跳过当前 handler，继续下一个

  【收尾】（try/finally 保证无论是否异常都执行）
    4. 完成（无需额外清理，事件引用由 GC 回收）
```

**关键保证**：
- handler 异常不会中断整个事件队列——记日志跳过，继续后续 handler 和后续事件。
- 调用顺序固定：**先改状态（before 链条）→ 再通知订阅者（拿到最新状态读）→ 再 after（清理/汇总）**。
- 单消费线程保证 FIFO：事件按入队顺序依次处理，不会乱序。
- register_object 触发的 idle 事件、update_object 触发的更新事件、trigger_event 触发的自定义事件，全部走这一套流程。

### 2.10 消费线程生命周期
- **启动**：EventBus 构造时自动启动一个 daemon 线程运行 `_consumer_loop`。
- **退出**：`shutdown()` 向队列放入 `None` 哨兵，消费线程收到后处理完当前事件即退出；队列中剩余事件**丢弃并写日志**。
- **场景**：EventBus 是程序核心组件，shutdown 意味着程序不再继续运行。

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
（构造 Event → 入队 queue.Queue，消费线程 _publish 分发，不对外暴露 publish 函数）

一个内部 map + 一个队列：
    _objects[object_id] = [对象数据, metadata_dict]      （长生命周期）
    _queue               = queue.Queue                   （FIFO 事件队列，消费线程逐个取出 _publish）

消费线程：构造时启动 daemon 线程，queue.Queue 保证 FIFO 顺序；
         shutdown() 放入 None 哨兵优雅退出，剩余事件丢弃写日志。

5 层主题顺序：user_id . session_id . conversation_id . object_id . event_type
通配符：* 单层  /  > 多层末尾
"""
from __future__ import annotations

import queue
import sys
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
# EventBus：普通类，不做单例，3 把独立锁 + queue.Queue 消费线程
# ==============================================================================
class EventBus:
    # ----------------------------------------------------------------------
    # 构造
    # ----------------------------------------------------------------------
    def __init__(self) -> None:
        # 对象仓库（严格按约定，不再加其他 map）
        self._objects: dict[str, list[Any, dict]] = {}

        # 链条字典 + 订阅字典
        self._chains: dict[str, tuple[list[Handler], list[Handler]]] = {}
        self._subs:   dict[str, tuple[str, SubHandler, bool]] = {}

        # 3 把独立锁（无嵌套持有，无死锁风险）
        self._objects_lock = threading.RLock()
        self._chains_lock  = threading.RLock()
        self._subs_lock    = threading.RLock()

        # 事件队列 + 消费线程（queue.Queue 自带线程安全，不需要额外锁）
        self._queue: queue.Queue[Event | None] = queue.Queue()
        self._stopped = False
        self._consumer = threading.Thread(target=self._consumer_loop, daemon=True)
        self._consumer.start()

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
        with self._objects_lock:
            if object_id not in self._objects:
                raise KeyError(f"[EventBus] _objects 中不存在 object_id={object_id}，请先 register_object")
            meta = self._objects[object_id][1]
            return meta["_user_id"], meta["_session_id"], meta["_conversation_id"]

    # ---------- 入队辅助 ----------
    def _enqueue(self, event: Event) -> None:
        """放入队列（调用方立即返回）。"""
        self._queue.put(event)

    # ----------------------------------------------------------------------
    # 公共 API 1/3：register_event（链条，追加模式）
    # ----------------------------------------------------------------------
    def register_event(self, event_type: str, before: list[Handler], after: list[Handler]) -> None:
        with self._chains_lock:
            if event_type not in self._chains:
                self._chains[event_type] = ([], [])
            cur_before, cur_after = self._chains[event_type]
            cur_before.extend(before)
            cur_after.extend(after)

    # ----------------------------------------------------------------------
    # 公共 API 2/3：subscribe / unsubscribe（5 层主题 pattern）
    # ----------------------------------------------------------------------
    def subscribe(self, pattern: str, handler: SubHandler, *, once: bool = False) -> str:
        # 合法性校验：> 只能出现在最后一位
        segs = pattern.split(".")
        for idx, s in enumerate(segs):
            if s == ">" and idx != len(segs) - 1:
                raise ValueError(
                    f"[EventBus] pattern={pattern!r} 非法：通配符 '>' 只能出现在末尾"
                )
        subscription_id = str(uuid.uuid4())
        with self._subs_lock:
            self._subs[subscription_id] = (pattern, handler, once)
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> None:
        with self._subs_lock:
            self._subs.pop(subscription_id, None)

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
        with self._objects_lock:
            self._objects[object_id] = [data, meta]
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
        self._enqueue(event)
        return object_id

    def update_object(
        self,
        object_id: str,
        event_type: str = "更新",
        data: Any = None,
        trace_id: str | None = None,
    ) -> None:
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
        self._enqueue(event)  # ⚠️ update_object 本身不修改 _objects[object_id][0]！完全下放 handler

    def remove_object(self, object_id: str) -> None:
        """直接从 _objects 删除（不校验任何生命周期规则，业务层自行控制时机）。"""
        with self._objects_lock:
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
        self._enqueue(event)

    # ---------- 优雅退出 ----------
    def shutdown(self) -> None:
        """放入 None 哨兵 → 消费线程处理完当前事件后退出 → 剩余事件丢弃写日志。"""
        self._stopped = True
        self._queue.put(None)
        self._consumer.join(timeout=5.0)

    # ---------- 只读辅助 ----------
    def get_object(self, object_id: str) -> Any | None:
        with self._objects_lock:
            if object_id not in self._objects:
                return None
            return self._objects[object_id][0]

    def get_object_with_meta(self, object_id: str) -> tuple[Any, dict] | None:
        with self._objects_lock:
            if object_id not in self._objects:
                return None
            d, m = self._objects[object_id]
            return d, dict(m)  # 浅拷贝，防止外部改 meta

    def list_objects(self) -> dict[str, Any]:
        with self._objects_lock:
            return {oid: entry[0] for oid, entry in self._objects.items()}

    # ----------------------------------------------------------------------
    # 消费线程主循环
    # ----------------------------------------------------------------------
    def _consumer_loop(self) -> None:
        while not self._stopped:
            event = self._queue.get()  # 阻塞等待
            if event is None:           # shutdown 哨兵
                break
            try:
                self._publish(event)
            except Exception:
                print(f"[EventBus] _publish 异常", file=sys.stderr)
        # shutdown：剩余事件丢弃写日志
        dropped = 0
        while True:
            try:
                remaining = self._queue.get_nowait()
                if remaining is not None:
                    dropped += 1
            except queue.Empty:
                break
        if dropped > 0:
            print(f"[EventBus] shutdown: 丢弃 {dropped} 个未处理事件", file=sys.stderr)

    # ----------------------------------------------------------------------
    # 发布流程（消费线程内调用，单线程保证 FIFO）
    # ----------------------------------------------------------------------
    def _publish(self, event: Event) -> None:
        # 拼一次 topic 字符串（用于 pattern 匹配）
        topic = ".".join([
            event.user_id, event.session_id,
            event.conversation_id, event.object_id, event.event_type,
        ])

        # 取链条快照（_chains_lock）
        with self._chains_lock:
            before_list, after_list = self._chains.get(event.event_type, ([], []))
            before_list = list(before_list)
            after_list  = list(after_list)

        # 取订阅快照（_subs_lock）
        with self._subs_lock:
            subs_snap = dict(self._subs)

        # ① before 处理链条
        for h in before_list:
            try:
                h(self, event)
            except Exception:
                print(f"[EventBus] before handler 异常（event_type={event.event_type}）", file=sys.stderr)

        # ② 通知订阅者
        for sub_id, (pattern, h_sub, once) in subs_snap.items():
            if not self._match_topic(pattern, topic):
                continue
            try:
                h_sub(topic, event)
            except Exception:
                print(f"[EventBus] subscribe handler 异常（pattern={pattern}）", file=sys.stderr)
            finally:
                if once:
                    with self._subs_lock:
                        self._subs.pop(sub_id, None)

        # ③ after 处理链条
        for h in after_list:
            try:
                h(self, event)
            except Exception:
                print(f"[EventBus] after handler 异常（event_type={event.event_type}）", file=sys.stderr)


# 模块级便捷函数（统一生成 ID，外部也能直接用）
def generate_id() -> str:
    return EventBus._generate_id()
```

---

## 四、验收标准（23 条，全通过方可进入下一阶段）

### 4.1 基础结构（T1-T6）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T1 | 两个 EventBus 实例互不干扰（非单例） | busA.register_object → busB.list_objects() 返回空；busA 发布事件 busB 订阅者收不到 |
| T2 | generate_id 格式符合 毫秒_UUID4 | 正则匹配 `^\d{13}_[0-9a-f]{32}$`，生成 1000 个无重复；字符串排序与时间戳排序一致 |
| T3 | EventBus._objects 结构严格为 `{oid: [data, meta]}` | register_object 后，get_object_with_meta 返回 [data, dict]；meta 必含 4 个下划线元数据键且值与入参一致 |
| T4 | EventBus._queue 结构 = `queue.Queue` | 事件入队后 _queue 持有引用；_publish 完成后事件出队被 GC 回收 |
| T5 | 3 把独立锁并发安全：10 线程并发 register_object + update_object 50 次 | 最终 _objects 数量与预期一致；无 KeyError / RuntimeError；元数据键不丢失 |
| T6 | Event dataclass 字段共 9 个且不含 kind / topic | 反射 `Event.__dataclass_fields__` 集合 == `{event_id, trace_id, user_id, session_id, conversation_id, object_id, event_type, data, timestamp}` |

### 4.2 处理链条（T7-T11）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T7 | register_event 追加模式 | 对 "更新" 调两次：[h1,h2] 再 [h3] → before 链调用顺序 h1→h2→h3；after 同理 |
| T8 | 预置 3 个 debug print 输出正确 | register_object / update_object / trigger_event("完成") 分别触发 → stdout 捕获到 "call idle" / "call 更新" / "call 完成" |
| T9 | before → subscribe_handler → after 严格调用顺序 | 三 handler 各自 append 标记 list → 结果顺序是 [before..., sub_handler..., after...] |
| T10 | handler 抛异常 → 记日志跳过，不中断后续 | h1 抛 → h2 仍执行；订阅者仍收到事件；后续事件正常处理 |
| T11 | _publish 异常不中断消费线程 | _publish 抛异常 → 消费线程记日志继续处理下一个事件 |

### 4.3 订阅 / 模式匹配（T12-T18）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T12 | 精确匹配 | `sub("a.b.c.d.e", fn)` + 发布 topic="a.b.c.d.e" → fn 命中；改任意层都不命中 |
| T13 | `*` 单层通配 | `sub("*.*.c.*.e", fn)` → `a.b.c.d.e` 命中；`a.x.y.d.e` 不命中（第 3 层不是 c）；层数不匹配 `a.b.c.d.e.f` 不命中 |
| T14 | `>` 末尾多层通配 | `sub("a.>", fn)` → `a.b.c.d.e` 命中；`a.x.y.z.w` 命中；`z.a.b.c.d` 不命中 |
| T15 | `>` 不在末尾 → ValueError | `subscribe("a.>.c", fn)` 立即抛 ValueError 拒绝订阅 |
| T16 | once=True 一次性订阅 | once=True 的 fn 被调用 1 次后，再次发布同 topic 不再命中 |
| T17 | subscribe 返回 GUID，可精确移除 | subscribe 返回 str GUID；unsubscribe(id) 后 fn 不再被调用；同一 pattern 多 handler 可各自独立 unsubscribe |
| T18 | unsubscribe 立即生效 | 发布两次：先 unsub → 第二次 fn 不被调用 |

### 4.4 对象 API 与队列流程（T19-T22）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T19 | register_object：自动生成 oid/meta/eid/trace；返回 oid；data 类型任意 | 传入 data=list/str/dict 三种类型，get_object() 返回同一对象引用（或等价值）；meta 四键与入参一致；自动触发 idle 入队并调用链条 handler |
| T20 | update_object：**不修改对象 data**（纯下放 handler） | update_object 前后对 _objects[oid][0] 做 id() 比较 → 完全相同；handler 自己在 before 里改了才算 |
| T21 | trigger_event：不写 _objects，只走入队流程 | 对不存在的 object_id 调 trigger_event，_objects 无新增；链条 handler 与 subscribe handler 正常调用 |
| T22 | remove_object：无条件直接删 | remove_object(oid) 后，get_object 返回 None；再次 update_object 抛 KeyError（因为 meta 不存在） |

### 4.5 消费线程与 shutdown（T23）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T23 | shutdown 优雅退出 | shutdown() 后消费线程退出；队列中剩余事件被丢弃且写日志 |

---

## 五、结论：所有设计点已确认

| 讨论项 | 最终结论（已确认） |
|---|---|
| **一个 map + 一个队列** | `_objects = {oid: [data, meta]}`；`_queue = queue.Queue`；**不再加其他 map** |
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
| **subscribe 返回 GUID** | subscribe 返回 `subscription_id: str`；unsubscribe 按 ID 精确移除；`_subs` 为 dict 而非 list |
| 对象 API | register_object（写 _objects + 构造 idle Event 入队）/ update_object（不写 _objects，纯构造 Event 入队）/ remove_object（无条件删）/ trigger_event（不碰对象纯事件入队） |
| put_object 是否需要 | **不需要** |
| **框架 vs handler 分工** | **框架尽量少做具体的事情，修改对象数据等实际处理完全下放给 register_event 注册的 handler** |
| publish 对外暴露？ | **不对外暴露**。对象 API 只负责构造 Event + 入队；`_publish` 是消费线程内部方法 |
| **队列 + 消费线程** | `queue.Queue` FIFO 保证事件顺序；构造时启动 daemon 线程运行 `_consumer_loop` |
| **异常策略** | 无 strict 参数；handler 异常统一记 stderr 日志跳过，不中断后续 handler 和后续事件 |
| **shutdown 优雅退出** | `shutdown()` 放 None 哨兵 → 消费线程退出 → 剩余事件丢弃写日志 |
| 单例 / 语法糖 | **两者都不要**。EventBus 普通类；无任何 pattern 快捷封装 |
| 线程安全 | 3 把独立锁 `_objects_lock` / `_chains_lock` / `_subs_lock`，各管各的数据结构，无嵌套持有；`_queue` 自带线程安全；消费线程异常不中断后续事件 |
