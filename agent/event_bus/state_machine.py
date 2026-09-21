# /home/thbytwo/testCode/xuyuanji/agent/event_bus/state_machine.py
"""
StateMachine：基于 EventBus 的轻量状态机
=========================================

设计（v3.4）：
  - 状态存储在 StateMachine 内部的 _object_states: dict[str, str] 中
  - 对象 data 字段保持 Any 类型，完全不污染
  - handler 在构造时创建一次，所有对象共用
  - 通过 bus.subscribe，利用 5 层主题通配符按对象精准匹配
  - register_event before/after 留给审计、日志等横向切面
  - attach 支持 user_id / session_id / conversation_id 控制作用范围
  - 一个 StateMachine 实例可 attach 多个对象，规则共享，状态隔离

处理路径逻辑分离：
  register_event before/after → 审计、日志、预处理（所有对象通用）
  subscribe（5 层主题通配符）→ 状态机（可按 user/session/conversation/object 分层控制）

回调签名：
  GuardCallback  = (bus: EventBus, event: Event, obj_data: Any) -> bool
  StateCallback  = (bus: EventBus, event: Event, obj_data: Any) -> None
"""

from typing import Any, Callable

from bus import EventBus, Event

StateCallback = Callable[[EventBus, Event, Any], None]
GuardCallback = Callable[[EventBus, Event, Any], bool]

class StateMachine:
    def __init__(self, bus: EventBus):
        self.bus = bus
        self._initial_state: str | None = None
        self._states: dict[str, dict] = {}
        self._transitions: dict[str, dict[str, tuple[str, GuardCallback | None, StateCallback | None]]] = {}
        self._object_states: dict[str, str] = {}
        self._subscriptions: dict[str, list[str]] = {}
        self._handler = self._make_handler()

    # ------------------------------------------------------------------
    # 声明式 API
    # ------------------------------------------------------------------
    def add_state(self, name: str, *,
                  initial: bool = False,
                  on_enter: StateCallback | None = None,
                  on_exit: StateCallback | None = None):
        self._states[name] = {"on_enter": on_enter, "on_exit": on_exit}
        if initial:
            self._initial_state = name
        return self

    def add_transition(self, from_state: str, event_type: str, to_state: str,
                       guard: GuardCallback | None = None,
                       action: StateCallback | None = None):
        if from_state not in self._states:
            raise ValueError(f"源状态 {from_state!r} 未声明，请先 add_state")
        if to_state not in self._states:
            raise ValueError(f"目标状态 {to_state!r} 未声明，请先 add_state")
        self._transitions.setdefault(from_state, {})[event_type] = (to_state, guard, action)
        return self

    # ------------------------------------------------------------------
    # 绑定 / 解绑（基于 subscribe，5 层主题精准控制作用范围）
    # ------------------------------------------------------------------
    def attach(self, object_id: str, *,
               user_id: str = "*",
               session_id: str = "*",
               conversation_id: str = "*"):
        """将状态机绑定到指定对象。

        通过 user_id / session_id / conversation_id 控制状态机在 5 层主题的
        哪一层起作用，默认 * 表示匹配任意。

        使用示例：
          sm.attach(oid)                                         # 所有用户的所有事件
          sm.attach(oid, user_id="u1")                           # 只处理 u1 的事件
          sm.attach(oid, user_id="u1", session_id="s1")          # u1.s1 的事件
          sm.attach(oid, user_id="u1", session_id="s1",
                    conversation_id="c1")                        # 精确到具体对话
        """
        if object_id in self._subscriptions:
            return

        if object_id not in self._object_states:
            self._object_states[object_id] = self._initial_state

        current = self._object_states[object_id]
        if current and current in self._states:
            enter_cb = self._states[current].get("on_enter")
            if enter_cb:
                obj_data = self.bus.get_object(object_id)
                enter_cb(self.bus, _make_init_event(object_id), obj_data)

        all_event_types: set[str] = set()
        for trans_map in self._transitions.values():
            all_event_types.update(trans_map.keys())

        sub_ids: list[str] = []
        for et in all_event_types:
            pattern = f"{user_id}.{session_id}.{conversation_id}.{object_id}.{et}"
            sub_id = self.bus.subscribe(pattern, self._handler)#订阅事件。连接状态机和事件总线的关键代码。
            sub_ids.append(sub_id)

        self._subscriptions[object_id] = sub_ids

    def detach(self, object_id: str):
        sub_ids = self._subscriptions.pop(object_id, [])
        for sid in sub_ids:
            self.bus.unsubscribe(sid)
        self._object_states.pop(object_id, None)

    # ------------------------------------------------------------------
    # 核心转移引擎（SubHandler 签名：topic, event）
    # ------------------------------------------------------------------
    def _make_handler(self):
        sm = self

        def handler(topic: str, event: Event):
            oid = event.object_id

            obj_data = sm.bus.get_object(oid)
            if obj_data is None:
                return

            current_state = sm._object_states.get(oid)
            if current_state is None:
                return

            trans_map = sm._transitions.get(current_state, {})
            if event.event_type not in trans_map:
                return

            to_state, guard, action = trans_map[event.event_type]

            # ① guard
            if guard is not None and not guard(sm.bus, event, obj_data):
                return

            # ② action
            if action is not None:
                action(sm.bus, event, obj_data)

            # ③ on_exit
            exit_cb = sm._states.get(current_state, {}).get("on_exit")
            if exit_cb:
                exit_cb(sm.bus, event, obj_data)

            # ④ 状态变更
            sm._object_states[oid] = to_state

            # ⑤ on_enter
            enter_cb = sm._states.get(to_state, {}).get("on_enter")
            if enter_cb:
                enter_cb(sm.bus, event, obj_data)

        return handler

    # ------------------------------------------------------------------
    # 只读辅助
    # ------------------------------------------------------------------
    def get_state(self, object_id: str) -> str | None:
        return self._object_states.get(object_id)

    def list_states(self) -> dict[str, str]:
        return dict(self._object_states)

    def print_graph(self):
        total = sum(len(v) for v in self._transitions.values())
        print(f"\nStateMachine graph ({len(self._states)} states, {total} transitions):")
        for from_s, trans in self._transitions.items():
            for evt, (to_s, guard, _action) in trans.items():
                g = "?" if guard else ""
                init = "●" if from_s == self._initial_state else " "
                print(f"  {init} [{from_s}] --({evt}){g}--> [{to_s}]")

# ------------------------------------------------------------------
# 辅助
# ------------------------------------------------------------------
def _make_init_event(object_id: str) -> Event:
    return Event(
        event_id="", trace_id="", user_id="", session_id="",
        conversation_id="", object_id=object_id,
        event_type="_init", data=None,
    )

if __name__ == "__main__":
    import time

    bus = EventBus()

    # ===== 横向切面：审计日志（register_event before/after，所有对象通用）=====
    audit_log: list[str] = []

    def audit_before(bus, event):
        payload = event.data.get("task_type", "") if event.data else ""
        audit_log.append(f"[审计] event_type={event.event_type} oid={event.object_id} payload={payload}")

    bus.register_event("接收任务", [audit_before], [])
    bus.register_event("开始处理", [audit_before], [])
    bus.register_event("处理完成", [audit_before], [])

    # ===== 状态机（subscribe，一个规则驱动多个对象）=====
    sm = StateMachine(bus)
    sm.add_state("idle", initial=True,
                 on_enter=lambda b, e, d: print(f"  [on_enter idle] oid={e.object_id}"))
    sm.add_state("queued",
                 on_enter=lambda b, e, d: print(f"  [on_enter queued] oid={e.object_id}"))
    sm.add_state("processing",
                 on_enter=lambda b, e, d: print(f"  [on_enter processing] oid={e.object_id}"),
                 on_exit=lambda b, e, d: print(f"  [on_exit processing] 耗时={time.time()-d['started_at']:.2f}s"))
    sm.add_state("completed",
                 on_enter=lambda b, e, d: print(f"  [on_enter completed] oid={e.object_id}"))

    # idle → queued: guard 检查任务优先级（priority <= 0 则拒绝）
    #               action 把事件携带的 task_type、text 写入对象数据
    def guard_has_priority(bus, event, data):
        ok = data.get("priority", 0) > 0
        if not ok:
            print(f"  [guard 拦截] priority={data.get('priority')}，拒绝入队")
        return ok

    sm.add_transition("idle", "接收任务", "queued",
                      guard=guard_has_priority,
                      action=lambda b, e, d: d.update(
                          task_type=e.data.get("task_type"),
                          text=e.data.get("text"),
                          queue_time=time.time()))

    # queued → processing: action 把事件携带的 worker 写入，同时记录开始时间
    sm.add_transition("queued", "开始处理", "processing",
                      action=lambda b, e, d: d.update(
                          worker=e.data.get("worker"),
                          started_at=time.time()))

    # processing → completed: action 把事件携带的 result 写入
    sm.add_transition("processing", "处理完成", "completed",
                      action=lambda b, e, d: d.update(
                          result=e.data.get("result")))

    # ===== 三个对象，三种命运 =====
    # 对象A：高优先级任务，正常走完流程
    # 对象B：零优先级任务，guard 拦截（永远停在 idle）
    # 对象C：admin 专属任务，只响应 u_admin 的事件
    oid_a = bus.register_object("u1", "s1", "c1", data={"name": "高优任务", "priority": 5})
    oid_b = bus.register_object("u1", "s1", "c1", data={"name": "零优先任务", "priority": 0})
    oid_c = bus.register_object("u_admin", "s1", "c1", data={"name": "管理员任务", "priority": 10})

    sm.attach(oid_a, user_id="u1")
    sm.attach(oid_b, user_id="u1")
    sm.attach(oid_c, user_id="u_admin")

    print(f"初始状态: A={sm.get_state(oid_a)}, B={sm.get_state(oid_b)}, C={sm.get_state(oid_c)}")
    print()

    # --- 对象A：正常流程（每个 trigger_event 都带 data）---
    print("=== 对象A（priority=5）：正常流程 ===")
    bus.trigger_event("u1", "s1", "c1", oid_a, "接收任务",
                      data={"task_type": "翻译", "text": "hello"})
    time.sleep(0.05)
    print(f"  → A 状态: {sm.get_state(oid_a)}")
    print(f"     task_type={bus.get_object(oid_a).get('task_type')}, "
          f"text='{bus.get_object(oid_a).get('text')}'")

    bus.trigger_event("u1", "s1", "c1", oid_a, "开始处理",
                      data={"worker": "worker-1"})
    time.sleep(0.05)
    print(f"  → A 状态: {sm.get_state(oid_a)}")
    print(f"     worker={bus.get_object(oid_a).get('worker')}")

    bus.trigger_event("u1", "s1", "c1", oid_a, "处理完成",
                      data={"result": "你好"})
    time.sleep(0.05)
    print(f"  → A 状态: {sm.get_state(oid_a)}")
    print(f"     result='{bus.get_object(oid_a).get('result')}'")
    print()

    # --- 对象B：guard 拦截（event.data 带了数据但 action 不执行，数据不写入）---
    print("=== 对象B（priority=0）：guard 拦截 ===")
    bus.trigger_event("u1", "s1", "c1", oid_b, "接收任务",
                      data={"task_type": "低优翻译", "text": "world"})
    time.sleep(0.05)
    print(f"  → B 状态: {sm.get_state(oid_b)} (仍为 idle)")
    print(f"     task_type={bus.get_object(oid_b).get('task_type')}"
          f" (为 None，guard 拦截后 action 未执行)")
    print()

    # --- 对象C：user_id 过滤 + 完整流程 ---
    print("=== 对象C（只响应 u_admin）：user_id 过滤 ===")
    bus.trigger_event("u1", "s1", "c1", oid_c, "接收任务",
                      data={"task_type": "管理", "text": "admin task"})
    time.sleep(0.05)
    print(f"  → u1 触发 C：状态={sm.get_state(oid_c)} (不变，pattern 不匹配)")

    bus.trigger_event("u_admin", "s1", "c1", oid_c, "接收任务",
                      data={"task_type": "管理", "text": "admin task"})
    time.sleep(0.05)
    print(f"  → u_admin 触发 C：状态={sm.get_state(oid_c)}")

    bus.trigger_event("u_admin", "s1", "c1", oid_c, "开始处理",
                      data={"worker": "admin-worker"})
    time.sleep(0.05)
    bus.trigger_event("u_admin", "s1", "c1", oid_c, "处理完成",
                      data={"result": "管理任务已完成"})
    time.sleep(0.05)
    print(f"  → C 完成：状态={sm.get_state(oid_c)}")
    print(f"     result='{bus.get_object(oid_c).get('result')}'")
    print()

    # --- 汇总 ---
    print(f"=== 最终状态 ===")
    for oid, state in sm.list_states().items():
        d = bus.get_object(oid)
        extra = ""
        if d.get("task_type"):
            extra += f" task_type={d['task_type']}"
        if d.get("text"):
            extra += f" text='{d['text']}'"
        if d.get("result"):
            extra += f" result='{d['result']}'"
        print(f"  {d['name']}: {state}{extra}")

    print(f"\n审计日志 ({len(audit_log)} 条):")
    for entry in audit_log:
        print(f"  {entry}")

    sm.print_graph()
    bus.shutdown()