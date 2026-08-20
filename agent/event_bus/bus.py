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
    _queue               = queue.Queue                   （FIFO 事件队列）

消费线程：构造时启动 daemon 线程，queue.Queue 保证 FIFO 顺序；
         shutdown() 放入 None 哨兵优雅退出，剩余事件丢弃写日志。

线程安全：3 把独立锁，各管各的数据结构，无嵌套持有，无死锁风险。
    _objects_lock  → _objects
    _chains_lock   → _chains
    _subs_lock     → _subs
    _queue 自带线程安全，不需要额外锁。

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


if __name__ == "__main__":
    """验收测试 T1-T23"""
    import io
    import re
    import contextlib
    from dataclasses import fields as dc_fields

    passed = 0
    failed = 0

    def check(name, cond, msg=""):
        global passed, failed
        if cond:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name}  {msg}")

    def wait_for(fn, timeout=2.0, interval=0.01):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if fn():
                return True
            time.sleep(interval)
        return False

    # ===== 4.1 基础结构 T1-T6 =====
    print("=== 4.1 基础结构 ===")

    # T1: 两个实例互不干扰
    busA = EventBus()
    busB = EventBus()
    busA.register_object("u1", "s1", "c1", data={"a": 1})
    check("T1a", busB.list_objects() == {}, "busB 应为空")
    recvB = []
    busB.subscribe("*.s1.c1.*.*", lambda t, e: recvB.append(e))
    busA.update_object(list(busA.list_objects().keys())[0])
    time.sleep(0.1)
    check("T1b", len(recvB) == 0, "busB 不应收到 busA 的事件")
    busA.shutdown()
    busB.shutdown()

    # T2: generate_id 格式
    ids = [generate_id() for _ in range(1000)]
    pat = re.compile(r"^\d{13}_[0-9a-f]{32}$")
    check("T2a", all(pat.match(i) for i in ids), "正则不匹配")
    check("T2b", len(set(ids)) == 1000, "有重复")
    ts_list = [s.split("_")[0] for s in sorted(ids)]
    check("T2c", ts_list == sorted(ts_list), "字符串排序与时间戳排序不一致")

    # T3: _objects 结构
    bus = EventBus()
    oid = bus.register_object("u1", "s1", "c1", data={"key": "val"})
    result = bus.get_object_with_meta(oid)
    check("T3a", result is not None)
    data_back, meta_back = result
    check("T3b", data_back == {"key": "val"})
    check("T3c", isinstance(meta_back, dict))
    check("T3d", set(meta_back.keys()) == {"_user_id", "_session_id", "_conversation_id", "_object_id"})
    check("T3e", meta_back["_user_id"] == "u1" and meta_back["_session_id"] == "s1"
                and meta_back["_conversation_id"] == "c1" and meta_back["_object_id"] == oid)
    bus.shutdown()

    # T4: _queue 结构
    bus4 = EventBus()
    called_t4 = []
    bus4.register_event("test_t4", [lambda b, e: called_t4.append(e.event_id)], [])
    bus4.trigger_event("u", "s", "c", "o", "test_t4")
    assert wait_for(lambda: len(called_t4) == 1), "T4 handler 未被调用"
    check("T4a", bus4._queue.qsize() == 0, "dispatch 完成后队列应为空")
    bus4.shutdown()

    # T5: 3把独立锁并发安全
    bus5 = EventBus()
    errors = []
    def worker(tid):
        try:
            for i in range(50):
                oid5 = bus5.register_object(f"u{tid}", f"s{tid}", f"c{tid}", data={"t": tid, "i": i})
                bus5.update_object(oid5, "更新", data={"updated": True})
        except Exception as e:
            errors.append(str(e))
    threads5 = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
    for t in threads5: t.start()
    for t in threads5: t.join()
    wait_for(lambda: bus5._queue.qsize() == 0, timeout=5.0)
    check("T5a", len(errors) == 0, f"并发错误: {errors[:3]}")
    check("T5b", len(bus5.list_objects()) == 500, f"对象数量: {len(bus5.list_objects())}")
    s_oid = list(bus5.list_objects().keys())[0]
    _, s_meta = bus5.get_object_with_meta(s_oid)
    check("T5c", "_user_id" in s_meta and "_session_id" in s_meta)
    bus5.shutdown()

    # T6: Event dataclass 字段
    fns = {f.name for f in dc_fields(Event)}
    check("T6", fns == {"event_id", "trace_id", "user_id", "session_id",
                         "conversation_id", "object_id", "event_type", "data", "timestamp"})

    # ===== 4.2 处理链条 T7-T11 =====
    print("=== 4.2 处理链条 ===")

    # T7: register_event 追加模式
    bus7 = EventBus()
    order7 = []
    bus7.register_event("更新", [lambda b, e: order7.append("h1"), lambda b, e: order7.append("h2")],
                         [lambda b, e: order7.append("a1")])
    bus7.register_event("更新", [lambda b, e: order7.append("h3")], [lambda b, e: order7.append("a3")])
    bus7.trigger_event("u", "s", "c", "o", "更新")
    assert wait_for(lambda: "a3" in order7)
    check("T7a", [order7.index(x) for x in ["h1", "h2", "h3"]] == sorted([order7.index(x) for x in ["h1", "h2", "h3"]]))
    check("T7b", [order7.index(x) for x in ["a1", "a3"]] == sorted([order7.index(x) for x in ["a1", "a3"]]))
    bus7.shutdown()

    # T8: 预置 debug print
    bus8 = EventBus()
    buf8 = io.StringIO()
    with contextlib.redirect_stdout(buf8):
        bus8.trigger_event("u", "s", "c", "o", "idle")
        bus8.trigger_event("u", "s", "c", "o", "更新")
        bus8.trigger_event("u", "s", "c", "o", "完成")
        time.sleep(0.2)
    out8 = buf8.getvalue()
    check("T8a", "call idle" in out8)
    check("T8b", "call 更新" in out8)
    check("T8c", "call 完成" in out8)
    bus8.shutdown()

    # T9: before → subscribe → after
    bus9 = EventBus()
    order9 = []
    bus9.register_event("test_order", [lambda b, e: order9.append("before")], [lambda b, e: order9.append("after")])
    bus9.subscribe("u.s.c.o.test_order", lambda t, e: order9.append("sub"))
    bus9.trigger_event("u", "s", "c", "o", "test_order")
    assert wait_for(lambda: "after" in order9)
    check("T9", order9 == ["before", "sub", "after"], f"顺序: {order9}")
    bus9.shutdown()

    # T10: handler 抛异常 → 记日志跳过
    bus10 = EventBus()
    called10 = []
    def throw_h(b, e):
        called10.append("throw")
        raise RuntimeError("test")
    bus10.register_event("test_exc", [throw_h, lambda b, e: called10.append("after")], [])
    recv10 = []
    bus10.subscribe("u.s.c.o.test_exc", lambda t, e: recv10.append(e))
    with contextlib.redirect_stderr(io.StringIO()):
        bus10.trigger_event("u", "s", "c", "o", "test_exc")
        assert wait_for(lambda: len(called10) == 2)
    check("T10a", called10 == ["throw", "after"])
    check("T10b", len(recv10) == 1)
    bus10.shutdown()

    # T11: _publish 异常不中断消费线程
    bus11 = EventBus()
    eid11 = []
    def throw_disp(b, e):
        eid11.append(e.event_id)
        raise RuntimeError("dispatch")
    bus11.register_event("test_dispatch_exc", [throw_disp], [])
    bus11.trigger_event("u", "s", "c", "o", "test_dispatch_exc")
    assert wait_for(lambda: len(eid11) == 1)
    called11 = []
    bus11.register_event("test_after_fault", [lambda b, e: called11.append("ok")], [])
    bus11.trigger_event("u", "s", "c", "o", "test_after_fault")
    assert wait_for(lambda: len(called11) == 1)
    check("T11", len(called11) == 1, "消费线程应继续处理后续事件")
    bus11.shutdown()

    # ===== 4.3 订阅 / 模式匹配 T12-T18 =====
    print("=== 4.3 订阅 / 模式匹配 ===")

    # T12: 精确匹配
    bus12 = EventBus()
    hits12 = []
    bus12.subscribe("a.b.c.d.e", lambda t, e: hits12.append(t))
    bus12.trigger_event("a", "b", "c", "d", "e")
    assert wait_for(lambda: len(hits12) == 1)
    check("T12a", len(hits12) == 1)
    hits12.clear()
    bus12.trigger_event("a", "b", "x", "d", "e")
    time.sleep(0.1)
    check("T12b", len(hits12) == 0)
    bus12.shutdown()

    # T13: * 单层通配
    bus13 = EventBus()
    hits13 = []
    bus13.subscribe("*.*.c.*.e", lambda t, e: hits13.append(t))
    bus13.trigger_event("a", "b", "c", "d", "e")
    assert wait_for(lambda: len(hits13) == 1)
    check("T13a", len(hits13) == 1)
    hits13.clear()
    bus13.trigger_event("a", "x", "y", "d", "e")
    time.sleep(0.1)
    check("T13b", len(hits13) == 0)
    bus13.shutdown()

    # T14: > 末尾多层通配
    bus14 = EventBus()
    hits14 = []
    bus14.subscribe("a.>", lambda t, e: hits14.append(t))
    bus14.trigger_event("a", "b", "c", "d", "e")
    assert wait_for(lambda: len(hits14) == 1)
    check("T14a", len(hits14) == 1)
    hits14.clear()
    bus14.trigger_event("a", "x", "y", "z", "w")
    assert wait_for(lambda: len(hits14) == 1)
    check("T14b", len(hits14) == 1)
    hits14.clear()
    bus14.register_object("a", "x", "y", "z")
    assert wait_for(lambda: len(hits14) == 1)
    check("T14c", len(hits14) == 1)
    hits14.clear()
    bus14.trigger_event("z", "a", "b", "c", "d")
    time.sleep(0.1)
    check("T14d", len(hits14) == 0)
    bus14.shutdown()

    # T15: > 不在末尾 → ValueError
    bus15 = EventBus()
    val_err = False
    try:
        bus15.subscribe("a.>.c", lambda t, e: None)
    except ValueError:
        val_err = True
    check("T15", val_err)
    bus15.shutdown()

    # T16: once=True
    bus16 = EventBus()
    cnt16 = [0]
    def once_h16(t, e): cnt16[0] += 1
    bus16.subscribe("u.s.c.o.once_test", once_h16, once=True)
    bus16.trigger_event("u", "s", "c", "o", "once_test")
    assert wait_for(lambda: cnt16[0] == 1)
    bus16.trigger_event("u", "s", "c", "o", "once_test")
    time.sleep(0.1)
    check("T16", cnt16[0] == 1)
    bus16.shutdown()

    # T17: subscribe 返回 GUID，可精确移除
    bus17 = EventBus()
    cnt17a = [0]
    cnt17b = [0]
    def h17a(t, e): cnt17a[0] += 1
    def h17b(t, e): cnt17b[0] += 1
    sid_a = bus17.subscribe("u.s.c.o.guid_test", h17a)
    sid_b = bus17.subscribe("u.s.c.o.guid_test", h17b)
    check("T17a", isinstance(sid_a, str) and isinstance(sid_b, str))
    check("T17b", sid_a != sid_b)
    bus17.trigger_event("u", "s", "c", "o", "guid_test")
    assert wait_for(lambda: cnt17a[0] == 1 and cnt17b[0] == 1)
    bus17.unsubscribe(sid_a)
    bus17.trigger_event("u", "s", "c", "o", "guid_test")
    assert wait_for(lambda: cnt17b[0] == 2)
    check("T17c", cnt17a[0] == 1)
    check("T17d", cnt17b[0] == 2)
    bus17.shutdown()

    # T18: unsubscribe 立即生效
    bus18 = EventBus()
    cnt18 = [0]
    def h18(t, e): cnt18[0] += 1
    sid18 = bus18.subscribe("u.s.c.o.unsub_test", h18)
    bus18.trigger_event("u", "s", "c", "o", "unsub_test")
    assert wait_for(lambda: cnt18[0] == 1)
    check("T18a", cnt18[0] == 1)
    bus18.unsubscribe(sid18)
    bus18.trigger_event("u", "s", "c", "o", "unsub_test")
    time.sleep(0.1)
    check("T18b", cnt18[0] == 1)
    bus18.shutdown()

    # ===== 4.4 对象 API 与队列流程 T19-T22 =====
    print("=== 4.4 对象 API 与队列流程 ===")

    # T19: register_object 各种 data 类型 + 自动 idle
    bus19 = EventBus()
    oid_d = bus19.register_object("u", "s", "c", data={"k": "v"})
    check("T19a", bus19.get_object(oid_d) == {"k": "v"})
    oid_l = bus19.register_object("u", "s", "c", data=[1, 2, 3])
    check("T19b", bus19.get_object(oid_l) == [1, 2, 3])
    oid_s = bus19.register_object("u", "s", "c", data="hello")
    check("T19c", bus19.get_object(oid_s) == "hello")
    _, meta19 = bus19.get_object_with_meta(oid_d)
    check("T19d", meta19["_user_id"] == "u" and meta19["_session_id"] == "s"
                and meta19["_conversation_id"] == "c" and meta19["_object_id"] == oid_d)
    buf19 = io.StringIO()
    with contextlib.redirect_stdout(buf19):
        bus19.register_object("u", "s", "c", data="test_idle")
        time.sleep(0.1)
    check("T19e", "call idle" in buf19.getvalue())
    trace19 = []
    bus19.register_event("idle", [lambda b, e: trace19.append(e.trace_id)], [])
    bus19.register_object("u", "s", "c", data="x", trace_id=None)
    assert wait_for(lambda: len(trace19) > 0)
    check("T19f", trace19[-1] != "")
    bus19.shutdown()

    # T20: update_object 不修改对象 data
    bus20 = EventBus()
    oid20 = bus20.register_object("u", "s", "c", data={"original": True})
    id_before = id(bus20.get_object(oid20))
    bus20.update_object(oid20, "更新", data={"new": True})
    time.sleep(0.1)
    id_after = id(bus20.get_object(oid20))
    check("T20", id_before == id_after)
    bus20.shutdown()

    # T21: trigger_event 不写 _objects
    bus21 = EventBus()
    cnt_before = len(bus21.list_objects())
    bus21.trigger_event("u", "s", "c", "nonexistent", "custom")
    time.sleep(0.1)
    check("T21a", len(bus21.list_objects()) == cnt_before)
    bus21b = EventBus()
    recv21 = []
    bus21b.subscribe("u.s.c.*.*", lambda t, e: recv21.append(t))
    bus21b.trigger_event("u", "s", "c", "any", "any_event")
    assert wait_for(lambda: len(recv21) == 1)
    check("T21b", len(recv21) == 1)
    bus21.shutdown()
    bus21b.shutdown()

    # T22: remove_object 无条件删
    bus22 = EventBus()
    oid22 = bus22.register_object("u", "s", "c", data={"del": True})
    check("T22a", bus22.get_object(oid22) is not None)
    bus22.remove_object(oid22)
    check("T22b", bus22.get_object(oid22) is None)
    ke = False
    try:
        bus22.update_object(oid22)
    except KeyError:
        ke = True
    check("T22c", ke)
    bus22.shutdown()

    # ===== 4.5 消费线程与 shutdown T23 =====
    print("=== 4.5 消费线程与 shutdown ===")

    # T23: shutdown 优雅退出
    bus23 = EventBus()
    for i in range(10):
        bus23.trigger_event("u", "s", "c", "o", "更新")
    with contextlib.redirect_stderr(io.StringIO()):
        bus23.shutdown()
    check("T23a", not bus23._consumer.is_alive())
    check("T23b", bus23._stopped)

    # ===== 汇总 =====
    print(f"\n{'='*40}")
    print(f"总计: {passed} PASS, {failed} FAIL")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)
