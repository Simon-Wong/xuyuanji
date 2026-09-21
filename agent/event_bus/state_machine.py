# /home/thbytwo/testCode/xuyuanji/agent/event_bus/state_machine.py
"""
StateMachine：基于 EventBus 的轻量状态机
=========================================

设计：
  - 状态存储在 bus._objects[object_id][0] 中（对象 data 字段），key 默认为 "_state"
  - add_state 定义状态及其 on_enter / on_exit 回调
  - add_transition 定义 (当前状态, 事件类型) → 目标状态 的转换
  - guard 条件守卫：返回 False 则阻止转换
  - action 转换动作：状态变更前执行

回调签名（统一）：
  GuardCallback  = (bus: EventBus, event: Event, ctx: dict) -> bool
  StateCallback  = (bus: EventBus, event: Event, ctx: dict) -> None

ctx 是对象的 data 字段（可读写），current_state 可通过 ctx["_state"] 获取。

线程安全：
  单消费者线程顺序处理事件，handler 不会并发执行，无需额外加锁。

使用示例：
  sm = StateMachine(bus)
  sm.add_state("idle", initial=True)
  sm.add_state("processing", on_enter=log_enter)
  sm.add_state("done")
  sm.add_transition("idle", "用户输入", "processing",
                    guard=check_ready,
                    action=before_process)
  sm.add_transition("processing", "模型返回", "done")
  sm.add_transition("processing", "超时", "idle")

  sm.attach(oid)  # 绑到对象上

  # 之后 bus.update_object(oid, "用户输入") 自动触发状态转换
"""

from typing import Any, Callable

from bus import EventBus, Event

StateCallback = Callable[[EventBus, Event, dict], None]
GuardCallback = Callable[[EventBus, Event, dict], bool]

class StateMachine:
    def __init__(self, bus: EventBus, state_key: str = "_state"):
        self.bus = bus
        self.state_key = state_key
        self._initial_state: str | None = None
        self._states: dict[str, dict] = {}
        self._transitions: dict[str, dict[str, tuple[str, GuardCallback | None, StateCallback | None]]] = {}
        self._registered_event_types: set[str] = set()
        self._attached_objects: set[str] = set()

    # ------------------------------------------------------------------
    # 声明式 API
    # ------------------------------------------------------------------
    def add_state(self, name: str, *,
                  initial: bool = False,
                  on_enter: StateCallback | None = None,
                  on_exit: StateCallback | None = None):
        """声明一个状态。initial=True 表示该状态机的初始状态。"""
        self._states[name] = {"on_enter": on_enter, "on_exit": on_exit}
        if initial:
            self._initial_state = name
        return self

    def add_transition(self, from_state: str, event_type: str, to_state: str,
                       guard: GuardCallback | None = None,
                       action: StateCallback | None = None):
        """声明转换：(from_state, event_type) → to_state。"""
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
        """将状态机绑定到指定对象：注册 handler + 设置初始状态。"""
        if object_id in self._attached_objects:
            return

        obj_data = self.bus.get_object(object_id)
        if obj_data is None:
            raise KeyError(f"对象 {object_id!r} 未在 EventBus 中注册")
        if not isinstance(obj_data, dict):
            raise TypeError(f"对象 {object_id!r} 的 data 不是 dict，无法存储状态")

        # 设置初始状态
        if self.state_key not in obj_data:
            obj_data[self.state_key] = self._initial_state

        # 触发初始状态的 on_enter
        current = obj_data[self.state_key]
        if current and current in self._states:
            enter_cb = self._states[current].get("on_enter")
            if enter_cb:
                enter_cb(self.bus, _make_init_event(object_id), obj_data)

        # 收集所有涉及的事件类型，逐一注册 handler
        all_event_types: set[str] = set()
        for trans_map in self._transitions.values():
            all_event_types.update(trans_map.keys())

        for et in all_event_types:
            if et not in self._registered_event_types:
                self.bus.register_event(et, [self._make_handler()])
                self._registered_event_types.add(et)

        self._attached_objects.add(object_id)

    def detach(self, object_id: str):
        """解除绑定（不注销 event_type 级别的 handler，因为可能被其他对象共用）。"""
        self._attached_objects.discard(object_id)

    # ------------------------------------------------------------------
    # 内部：构造 handler + 核心转移逻辑
    # ------------------------------------------------------------------
    def _make_handler(self):
        """返回一个闭包 handler，内部根据当前状态查表转移。"""
        state_machine = self  # 避免 self 被闭包覆盖

        def handler(bus: EventBus, event: Event):
            oid = event.object_id
            if oid not in state_machine._attached_objects:
                return  # 不是本状态机管辖的对象，跳过

            obj_data = bus.get_object(oid)
            if obj_data is None or not isinstance(obj_data, dict):
                return

            current_state = obj_data.get(state_machine.state_key)
            if current_state is None:
                return

            trans_map = state_machine._transitions.get(current_state, {})
            if event.event_type not in trans_map:
                return  # 当前状态下不处理此事件类型

            to_state, guard, action = trans_map[event.event_type]

            # ① guard 检查
            if guard is not None and not guard(bus, event, obj_data):
                return

            # ② 执行转换动作（在状态真正变更前）
            if action is not None:
                action(bus, event, obj_data)

            # ③ 退出当前状态
            exit_cb = state_machine._states.get(current_state, {}).get("on_exit")
            if exit_cb:
                exit_cb(bus, event, obj_data)

            # ④ 状态变更
            obj_data[state_machine.state_key] = to_state

            # ⑤ 进入新状态
            enter_cb = state_machine._states.get(to_state, {}).get("on_enter")
            if enter_cb:
                enter_cb(bus, event, obj_data)

        return handler

    # ------------------------------------------------------------------
    # 只读辅助
    # ------------------------------------------------------------------
    def get_state(self, object_id: str) -> str | None:
        """查询对象当前状态。"""
        obj_data = self.bus.get_object(object_id)
        if obj_data is None or not isinstance(obj_data, dict):
            return None
        return obj_data.get(self.state_key)

    def print_graph(self):
        """打印状态转移图（ASCII）。"""
        print(f"\nStateMachine graph ({len(self._states)} states, {sum(len(v) for v in self._transitions.values())} transitions):")
        for from_s, trans in self._transitions.items():
            for evt, (to_s, guard, _action) in trans.items():
                g = "?" if guard else ""
                init = "●" if from_s == self._initial_state else " "
                print(f"  {init} [{from_s}] --({evt}){g}--> [{to_s}]")

# ------------------------------------------------------------------
# 辅助：构造 attach 时触发 on_enter 的虚拟事件
# ------------------------------------------------------------------
def _make_init_event(object_id: str) -> Event:
    from bus import Event as _Event
    return _Event(
        event_id="",
        trace_id="",
        user_id="",
        session_id="",
        conversation_id="",
        object_id=object_id,
        event_type="_init",
        data=None,
    )


if __name__ == "__main__":
    #from agent.event_bus.bus import EventBus
    #from agent.event_bus.state_machine import StateMachine

    bus = EventBus()

    # 1. 定义状态机
    sm = StateMachine(bus)

    sm.add_state("idle", initial=True)
    sm.add_state("processing")
    sm.add_state("done")

    sm.add_transition("idle",       "用户输入", "processing",
                    guard=lambda b, e, ctx: bool(ctx.get("user_text")),
                    action=lambda b, e, ctx: print(f"开始处理: {ctx['user_text']}"))

    sm.add_transition("processing", "模型返回", "done",
                    action=lambda b, e, ctx: ctx.update(answer=e.data.get("answer")))

    sm.add_transition("processing", "超时", "idle")

    # 2. 创建对象并绑状态机
    oid = bus.register_object("u1", "s1", "c1", data={"user_text": ""})
    sm.attach(oid)

    # 3. 业务触发
    bus.update_object(oid, "用户输入")        # idle → processing（guard 失败，user_text 为空，不转移）
    # 设置好数据后再试
    obj = bus.get_object(oid)
    obj["user_text"] = "你好"
    bus.update_object(oid, "用户输入")        # idle → processing ✓

    print(sm.get_state(oid))                  # "processing"

    bus.trigger_event("u1", "s1", "c1", oid, "模型返回", data={"answer": "你好呀"})
    # processing → done，obj["answer"] 被写入

    print(sm.get_state(oid))                  # "done"
    sm.print_graph()