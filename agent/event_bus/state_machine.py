# /home/thbytwo/testCode/xuyuanji/agent/event_bus/state_machine.py
"""
StateMachine：基于 EventBus 的轻量状态机
=========================================

设计（v2）：
  - 状态存储在 StateMachine 内部的 _object_states: dict[str, str] 中
  - 对象 data 字段保持 Any 类型，完全不污染
  - handler 注册到 bus.register_event，通过事件管道自动驱动

回调签名：
  GuardCallback  = (bus: EventBus, event: Event, obj_data: Any) -> bool
  StateCallback  = (bus: EventBus, event: Event, obj_data: Any) -> None

bus → 事件管道，负责分发
状态机 → 挂在管道上的处理器，维护状态转移逻辑
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
        self._registered_event_types: set[str] = set()
        self._attached_objects: set[str] = set()
        self._object_states: dict[str, str] = {}

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
    # 绑定 / 解绑
    # ------------------------------------------------------------------
    def attach(self, object_id: str):
        if object_id in self._attached_objects:
            return

        # 初始化状态（不碰对象 data）
        if object_id not in self._object_states:
            self._object_states[object_id] = self._initial_state

        # 触发初始状态的 on_enter
        current = self._object_states[object_id]
        if current and current in self._states:
            enter_cb = self._states[current].get("on_enter")
            if enter_cb:
                obj_data = self.bus.get_object(object_id)
                enter_cb(self.bus, _make_init_event(object_id), obj_data)

        # 注册 handler 到 EventBus
        all_event_types: set[str] = set()
        for trans_map in self._transitions.values():
            all_event_types.update(trans_map.keys())

        for et in all_event_types:
            if et not in self._registered_event_types:
                self.bus.register_event(et, [self._make_handler()])
                self._registered_event_types.add(et)

        self._attached_objects.add(object_id)

    def detach(self, object_id: str):
        self._attached_objects.discard(object_id)
        self._object_states.pop(object_id, None)

    # ------------------------------------------------------------------
    # 核心转移引擎
    # ------------------------------------------------------------------
    def _make_handler(self):
        sm = self

        def handler(bus: EventBus, event: Event):
            oid = event.object_id
            if oid not in sm._attached_objects:
                return

            obj_data = bus.get_object(oid)
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
            if guard is not None and not guard(bus, event, obj_data):
                return

            # ② action
            if action is not None:
                action(bus, event, obj_data)

            # ③ on_exit
            exit_cb = sm._states.get(current_state, {}).get("on_exit")
            if exit_cb:
                exit_cb(bus, event, obj_data)

            # ④ 状态变更（在自己的表里改，不碰 data）
            sm._object_states[oid] = to_state

            # ⑤ on_enter
            enter_cb = sm._states.get(to_state, {}).get("on_enter")
            if enter_cb:
                enter_cb(bus, event, obj_data)

        return handler

    # ------------------------------------------------------------------
    # 只读辅助
    # ------------------------------------------------------------------
    def get_state(self, object_id: str) -> str | None:
        return self._object_states.get(object_id)

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
    bus = EventBus()

    sm = StateMachine(bus)
    sm.add_state("idle", initial=True)
    sm.add_state("processing")
    sm.add_state("done")
    sm.add_transition("idle",       "用户输入", "processing")
    sm.add_transition("processing", "模型返回", "done")
    sm.add_transition("processing", "超时",     "idle")

    # data 可以是任何类型，不再强制 dict
    oid1 = bus.register_object("u1", "s1", "c1", data="任意字符串")
    oid2 = bus.register_object("u2", "s2", "c2", data=[1, 2, 3])

    sm.attach(oid1)
    sm.attach(oid2)

    print(sm.get_state(oid1))  # idle
    print(sm.get_state(oid2))  # idle

    bus.update_object(oid1, "用户输入")

    import time
    time.sleep(0.1)
    print(sm.get_state(oid1))  # processing

    sm.print_graph()
    bus.shutdown()