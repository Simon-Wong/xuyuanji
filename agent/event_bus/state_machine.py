# /home/thbytwo/testCode/xuyuanji/agent/event_bus/state_machine.py
"""
StateMachine：基于 EventBus 的轻量状态机
=========================================

设计（v3.2）：
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
        audit_log.append(f"[审计-before] event_type={event.event_type} oid={event.object_id}")

    def audit_after(bus, event):
        audit_log.append(f"[审计-after]  event_type={event.event_type} oid={event.object_id}")

    bus.register_event("用户输入", [audit_before], [audit_after])
    bus.register_event("模型返回", [audit_before], [audit_after])

    # ===== 一个状态机，多个对象（规则共享，状态隔离）=====
    sm = StateMachine(bus)
    sm.add_state("idle", initial=True)
    sm.add_state("processing")
    sm.add_state("done")
    sm.add_transition("idle",       "用户输入", "processing")
    sm.add_transition("processing", "模型返回", "done")

    oid_a = bus.register_object("u1", "s1", "c1", data="对象A")
    oid_b = bus.register_object("u1", "s1", "c1", data="对象B")

    sm.attach(oid_a, user_id="u1")
    sm.attach(oid_b, user_id="u1")

    print(f"初始状态: A={sm.get_state(oid_a)}, B={sm.get_state(oid_b)}")

    bus.trigger_event("u1", "s1", "c1", oid_a, "用户输入")
    time.sleep(0.1)
    print(f"A 收到事件后: A={sm.get_state(oid_a)}, B={sm.get_state(oid_b)}")

    bus.trigger_event("u1", "s1", "c1", oid_b, "用户输入")
    time.sleep(0.1)
    print(f"B 收到事件后: A={sm.get_state(oid_a)}, B={sm.get_state(oid_b)}")

    bus.trigger_event("u2", "s1", "c1", oid_a, "模型返回")
    time.sleep(0.1)
    print(f"u2 触发 A: A={sm.get_state(oid_a)}, B={sm.get_state(oid_b)} (不变)")

    bus.trigger_event("u1", "s1", "c1", oid_a, "模型返回")
    time.sleep(0.1)
    print(f"u1 触发 A 模型返回: A={sm.get_state(oid_a)}, B={sm.get_state(oid_b)}")

    print(f"\n审计日志 ({len(audit_log)} 条，所有事件都被记录):")
    for entry in audit_log:
        print(f"  {entry}")

    sm.print_graph()
    bus.shutdown()