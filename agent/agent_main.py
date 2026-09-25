'''
用户-----会话-----------对话--------------------------工作空间
         通信通道       不同的会话可同时打开同一个      同一会话共用同一个工作空间
'''

import sys
from pathlib import Path
ROOT_DIR=Path(__file__).parent.parent
print(ROOT_DIR)
sys.path.append(str(ROOT_DIR))

import asyncio
import os
from typing import Any

from configuration import UserConfig

from model_store import ModelStore
global_model_store = ModelStore()

from agent_store import AgentStore
global_agent_store = AgentStore()

from message_manager import MessageManager
global_message_manager = MessageManager()

from pre_load import all_tools

from event_bus import EventBus,Event
global_event_bus = EventBus()
       

env_model=os.getenv("MODEL_NAME", "qwen3:4b")
env_base_url=os.getenv("PROVIDER_URL", "http://192.168.0.119:11434/v1")

from agents import Agent
def initialize():
    agent = Agent(
        name="天气助手",
        instructions=(
            "你是一个天气预报助手，名字叫小云。\n"
            "当用户询问任何城市的天气时，你可以调用适当工具来获取数据。\n"
            "如果用户问与天气无关的问题，直接回答：'我是天气助手，只回答天气问题。'"
            "不能伪造任何结果，不知道或者无法调用工具请直接回复原因。"),
        tools=all_tools,
    )
    global_agent_store.register_agent(agent)

    agent2 = Agent(
        name="天气助手2",
        instructions=(
            "你是一个天气预报助手，名字叫小云。\n"
            "当用户询问任何城市的天气时，你可以调用适当工具来获取数据。\n"
            "如果用户问与天气无关的问题，直接回答：'我是天气助手，只回答天气问题。'"
            "不能伪造任何结果，不知道或者无法调用工具请直接回复原因。"),
        tools=all_tools,
    )
    global_agent_store.register_agent(agent2)

    agent3 = Agent(
        name="八卦小助手",
        instructions=(
            "你是一个八卦助手，名字叫小8。\n"
            "当用户询问任何八卦问题时，你可以调用适当工具来获取数据。\n"
            "如果用户问与八卦无关的问题，直接回答：'我是八卦助手，只回答八卦问题。"
            "不能伪造任何结果，不知道或者无法调用工具请直接回复原因。"),
        tools=all_tools,
    )
    global_agent_store.register_agent(agent3) 

    agent3 = Agent(
        name="脚本小助手",
        instructions=(
            "你是一个脚本编程助手，名字叫小虫。\n"
            "当用户询问任何脚本编程问题时，你可以调用适当工具来获取数据、编写脚本、执行脚本。\n"
            "如果用户问与脚本编程无关的问题，直接回答：'我是脚本编程助手，只回答脚本编程问题。"
            "不能伪造任何结果，不知道或者无法调用工具请直接回复原因。"),
        tools=all_tools,
    )
    global_agent_store.register_agent(agent3) 


from workspace_stuff import WorkspaceManager
global_workspace_manager = WorkspaceManager()

from user_session_conversation_stuff import UserSessionConversationManager
global_user_session_conversation_manager=UserSessionConversationManager()

from cache_manager import CacheManager

from actor_stuff import ActorManager,ActorStatus,ActorData,Actor
global_actor_manager=ActorManager()
from agents import Agent, RunConfig

async def Test1():
    user_id="test_user_1"
    session_id="session_1"
    global_user_session_conversation_manager.record_user_session(user_id,session_id)#模拟用户登录后注册会话
    cids=global_user_session_conversation_manager.get_conversations_id(user_id)#获取用户的对话列表
    cid=""
    caption=""
    if cids==[]:
        print("用户没有任何对话")
        cid="conversation_1"
        caption="对话1"
        print(f"用户创建对话{cid} {caption}")
    else:
        print(f"用户已有{len(cids)}个对话")
        cid=cids[0]
        print(f"用户选择对话{cid}")
        flag,caption=global_user_session_conversation_manager.get_conversation_caption(user_id,cid)
        if flag==False:
            print(reason_str)
            return
        else:
            print(f"标题为：{caption}")

    flag,reason_str=global_user_session_conversation_manager.record_user_session_conversation(user_id,session_id,cid,caption)#模拟用户打开对话
    if flag==False:
        print(reason_str)

    provider = global_model_store.get_model_provider(env_model, env_base_url)#加载模型
    run_config = RunConfig(model_provider=provider)#加载模型
    _,agent,_=global_agent_store.get_agent("天气助手2")#加载模型

    msghis=global_message_manager.get_messages(user_id,cid)#获取对话历史记录
    user_cfg=UserConfig.load(user_id=user_id,session_id=session_id,config_file_name="user_config.json")#加载用户配置
    workspace=global_workspace_manager.get_workspace(user_cfg,cid)#获取工作空间

    actor=Actor(agent,run_config,msghis,user_cfg)
    actor_data:ActorData=await actor.play(role="user",input="北京今天天气怎么样？上海今天天气怎么样？")
    while actor_data.status!=ActorStatus.FINAL_RESULT:
        if actor_data.status==ActorStatus.CHECKLIST:#需要外部审批
            #假装外部已经审批
            actor_data.check_done()

        if actor_data.status==ActorStatus.NEED_EXECUTE_CHECKLIST:#需要外部执行
            actor_data=workspace.run(actor_data)

        if actor_data.status==ActorStatus.CALL_RESULT:#继续运行
            actor_data=await actor.play(actor_data=actor_data)

    print(f"\n助手: {actor_data.result}")

    closed_cids=global_user_session_conversation_manager.close_session(user_id,session_id)#关闭会话，返回因关闭会话而关闭的所有对话id
    for ccid in closed_cids:
        global_workspace_manager.stop_one(user_id,ccid)#关闭工作空间


async def Test2():
    questions1 = [
        "北京今天天气怎么样？",
        "那 123 加 456 等于多少？",
    ]

    questions2 = [
        "上海今天天气怎么样？",
        "那 123 加 456 等于多少？",
        "那北京呢？"
    ]

    user_id="test_user_1"
    session_id="session_1"
    global_user_session_conversation_manager.record_user_session(user_id,session_id)#模拟用户登录后注册会话
    cids=global_user_session_conversation_manager.get_conversations_id(user_id)#获取用户的对话列表
    cid=""
    caption=""
    if cids==[]:
        print("用户没有任何对话")
        cid="conversation_1"
        caption="对话1"
        print(f"用户创建对话{cid} {caption}")
    else:
        print(f"用户已有{len(cids)}个对话")
        cid=cids[0]
        print(f"用户选择对话{cid}")
        flag,caption=global_user_session_conversation_manager.get_conversation_caption(user_id,cid)
        if flag==False:
            print(reason_str)
            return
        else:
            print(f"标题为：{caption}")

    flag,reason_str=global_user_session_conversation_manager.record_user_session_conversation(user_id,session_id,cid,caption)#模拟用户打开对话
    if flag==False:
        print(reason_str)

    provider = global_model_store.get_model_provider(env_model, env_base_url)
    run_config = RunConfig(model_provider=provider)
    _,agent,_=global_agent_store.get_agent("天气助手")

    msghis=global_message_manager.get_messages(user_id,cid)
    user_cfg=UserConfig.load(user_id=user_id,session_id=session_id,config_file_name="user_config.json")
    workspace=global_workspace_manager.get_workspace(user_cfg,cid)

    actor=Actor(agent,run_config,msghis,user_cfg)
    actor.set_debug_need_same_answer(False)

    print(f"{'='*50}第1组问题{'='*50}")

    for i, question in enumerate(questions1, 1):
        print(f"\n{'='*10} 第 {i} 轮 {'='*10}")
        print(f"用户: {question}")
    
        actor_data:ActorData=await actor.play(role="user",input=question)
        while actor_data.status!=ActorStatus.FINAL_RESULT:
            if actor_data.status==ActorStatus.CHECKLIST:#需要外部审批
                #假装外部已经审批
                actor_data.check_done()

            if actor_data.status==ActorStatus.NEED_EXECUTE_CHECKLIST:#需要外部执行
                actor_data=workspace.run(actor_data)

            if actor_data.status==ActorStatus.CALL_RESULT:#继续运行
                actor_data=await actor.play(actor_data=actor_data)

        print(f"\n助手: {actor_data.result}")

    #模拟外部修改了历史
    print(f"{'='*50}模拟外部修改了历史{'='*50}")
    msghis=actor.get_msghis()
    print(msghis)
    actor.set_msghis(msghis)

    print(f"{'='*50}第2组问题{'='*50}")

    for i, question in enumerate(questions2, 1):
        print(f"\n{'='*10} 第 {i} 轮 {'='*10}")
        print(f"用户: {question}")
    
        actor_data:ActorData=await actor.play(role="user",input=question)
        while actor_data.status!=ActorStatus.FINAL_RESULT:
            if actor_data.status==ActorStatus.CHECKLIST:#需要外部审批
                #假装外部已经审批
                actor_data.check_done()

            if actor_data.status==ActorStatus.NEED_EXECUTE_CHECKLIST:#需要外部执行
                actor_data=workspace.run(actor_data)

            if actor_data.status==ActorStatus.CALL_RESULT:#继续运行
                actor_data=await actor.play(actor_data=actor_data)

        print(f"\n助手: {actor_data.result}")

    closed_cids=global_user_session_conversation_manager.close_session(user_id,session_id)#关闭会话，返回因关闭会话而关闭的所有对话id
    for ccid in closed_cids:
        global_workspace_manager.stop_one(user_id,ccid)#关闭工作空间   

async def Test3():
    cachemgr=CacheManager()
    cachemgr.set("q1","a1")
    cachemgr.set("q2","a2")
    cachemgr.set("q3","a3")
    cache=cachemgr.get("q2")
    print(cache)

async def Test4():
    cachemgr=CacheManager()
    cachemgr.set("q1","a1")
    cachemgr.set("q3","a3")
    cache=cachemgr.get("q2")
    print(cache)

async def Test5():
    user_id="test_user_1"
    session_id="session_1"
    global_user_session_conversation_manager.record_user_session(user_id,session_id)#模拟用户登录后注册会话
    cids=global_user_session_conversation_manager.get_conversations_id(user_id)#获取用户的对话列表
    cid=""
    caption=""
    if cids==[]:
        print("用户没有任何对话")
        cid="conversation_1"
        caption="对话1"
        print(f"用户创建对话{cid} {caption}")
    else:
        print(f"用户已有{len(cids)}个对话")
        cid=cids[0]
        print(f"用户选择对话{cid}")
        flag,caption=global_user_session_conversation_manager.get_conversation_caption(user_id,cid)
        if flag==False:
            print(reason_str)
            return
        else:
            print(f"标题为：{caption}")

    flag,reason_str=global_user_session_conversation_manager.record_user_session_conversation(user_id,session_id,cid,caption)#模拟用户打开对话
    if flag==False:
        print(reason_str)

    provider = global_model_store.get_model_provider(env_model, env_base_url)
    run_config = RunConfig(model_provider=provider)
    _,agent,_=global_agent_store.get_agent("八卦小助手")

    msghis=global_message_manager.get_messages(user_id,cid)
    user_cfg=UserConfig.load(user_id=user_id,session_id=session_id,config_file_name="user_config.json")
    workspace=global_workspace_manager.get_workspace(user_cfg,cid)

    actor=Actor(agent,run_config,msghis,user_cfg)
    actor_data:ActorData=await actor.play(role="user",input="娱乐圈最近有什么大新闻？")
    while actor_data.status!=ActorStatus.FINAL_RESULT:
        if actor_data.status==ActorStatus.CHECKLIST:#需要外部审批
            #假装外部已经审批
            actor_data.check_done()

        if actor_data.status==ActorStatus.NEED_EXECUTE_CHECKLIST:#需要外部执行
            actor_data=workspace.run(actor_data)

        if actor_data.status==ActorStatus.CALL_RESULT:#继续运行
            actor_data=await actor.play(actor_data=actor_data)

    print(f"\n助手: {actor_data.result}")

    closed_cids=global_user_session_conversation_manager.close_session(user_id,session_id)#关闭会话，返回因关闭会话而关闭的所有对话id
    for ccid in closed_cids:
        global_workspace_manager.stop_one(user_id,ccid)#关闭工作空间

async def Test6():
    user_id="test_user_1"
    session_id="session_1"
    global_user_session_conversation_manager.record_user_session(user_id,session_id)#模拟用户登录后注册会话
    cids=global_user_session_conversation_manager.get_conversations_id(user_id)#获取用户的对话列表
    cid=""
    caption=""
    if cids==[]:
        print("用户没有任何对话")
        cid="conversation_1"
        caption="对话1"
        print(f"用户创建对话{cid} {caption}")
    else:
        print(f"用户已有{len(cids)}个对话")
        cid=cids[0]
        print(f"用户选择对话{cid}")
        flag,caption=global_user_session_conversation_manager.get_conversation_caption(user_id,cid)
        if flag==False:
            print(reason_str)
            return
        else:
            print(f"标题为：{caption}")

    flag,reason_str=global_user_session_conversation_manager.record_user_session_conversation(user_id,session_id,cid,caption)#模拟用户打开对话
    if flag==False:
        print(reason_str)

    model_name="moophlo/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest"
    provider = global_model_store.get_model_provider(model_name, env_base_url)
    run_config = RunConfig(model_provider=provider)
    _,agent,_=global_agent_store.get_agent("脚本小助手")
    msghis=global_message_manager.get_messages(user_id,cid)
    user_cfg=UserConfig.load(user_id=user_id,session_id=session_id,config_file_name="user_config.json")
    workspace=global_workspace_manager.get_workspace(user_cfg,cid)

    actor=Actor(agent,run_config,msghis,user_cfg)
    global_actor_manager.add_actor(actor)
    tmpinput="编写一个脚本，获取本机MAC地址。执行这个脚本并告诉我结果\n"+workspace.append_prompt()
    actor_data:ActorData=await actor.play(role="user",input=tmpinput)#开始运行，始终以actor_id为贯穿线索
    actor.set_question(tmpinput)
    while actor_data.status!=ActorStatus.FINAL_RESULT:
        if actor_data.status==ActorStatus.CHECKLIST:#需要外部审批
            #假装外部已经审批
            actor_data.check_done()

        if actor_data.status==ActorStatus.NEED_EXECUTE_CHECKLIST:#需要外部执行
            question=global_actor_manager.get_actor_question(actor_data.get_actor_id())
            actor_data=workspace.run(actor_data,question)

        if actor_data.status==ActorStatus.CALL_RESULT:#继续运行
            if actor_data.get_actor_id()==actor.get_id():#确保是当前actor的运行结果，以继续运行。
                actor_data=await actor.play(actor_data=actor_data)

    print(f"\n助手: {actor_data.result}")

    workspace.save_product()

    closed_cids=global_user_session_conversation_manager.close_session(user_id,session_id)#关闭会话，返回因关闭会话而关闭的所有对话id
    for ccid in closed_cids:
        global_workspace_manager.stop_one(user_id,ccid)#关闭工作空间


from agent.conversation_backend_manager import ConversationBackend,ConversationBackendManager
from dealwith_event_bus import BE_create_conversation_backend,BEH_create_conversation_backend
from dealwith_event_bus import BE_create_actor_data,BEH_create_actor_data
from dealwith_event_bus import BE_checklist,BEH_checklist
from dealwith_event_bus import BE_update_actor_data,BEH_update_actor_data

global_conversation_backend_manager=ConversationBackendManager()

async def Test7():
    from agent.event_bus.state_machine import StateMachine

    user_id = "test_user_1"
    session_id = "session_1"
    global_user_session_conversation_manager.record_user_session(user_id, session_id)
    cids = global_user_session_conversation_manager.get_conversations_id(user_id)

    cid = ""
    caption = ""
    if cids == []:
        print("用户没有任何对话")
        cid = "conversation_1"
        caption = "对话1"
        print(f"用户创建对话{cid} {caption}")
    else:
        print(f"用户已有{len(cids)}个对话")
        cid = cids[0]
        print(f"用户选择对话{cid}")
        flag, caption = global_user_session_conversation_manager.get_conversation_caption(user_id, cid)
        if flag == False:
            print(reason_str)
            return
        else:
            print(f"标题为：{caption}")

    flag, reason_str = global_user_session_conversation_manager.record_user_session_conversation(
        user_id, session_id, cid, caption)
    if flag == False:
        print(reason_str)

    # ==================================================================
    # 初始化 actor、workspace、cb1
    # ==================================================================
    provider = global_model_store.get_model_provider(env_model, env_base_url)
    run_config = RunConfig(model_provider=provider)
    _, agent, _ = global_agent_store.get_agent("八卦小助手")

    msghis = global_message_manager.get_messages(user_id, cid)
    user_cfg = UserConfig.load(user_id=user_id, session_id=session_id, config_file_name="user_config.json")
    workspace = global_workspace_manager.get_workspace(user_cfg, cid)

    actor = Actor(agent, run_config, msghis, user_cfg)

    cb1 = ConversationBackend()
    cb1.user_id = user_id
    cb1.session_id = session_id
    cb1.cid = cid
    cb1.caption = caption
    cb1.base_model = env_model
    cb1.base_url = env_base_url
    cb1.user_cfg = user_cfg
    cb1.workspace = workspace
    cb1.msghis = msghis
    cb1.actors[actor.get_id()] = actor

    # ==================================================================
    # EventBus 注册事件（审计/日志层）
    # ==================================================================
    # global_event_bus.register_event(event_type=BE_create_actor_data,
    #                                 before=[BEH_create_actor_data])
    # global_event_bus.register_event(event_type=BE_checklist,
    #                                 before=[BEH_checklist])
    # global_event_bus.register_event(event_type=BE_update_actor_data,
    #                                 before=[BEH_update_actor_data])

    # ==================================================================
    # StateMachine
    # ==================================================================
    loop = asyncio.get_running_loop()

    # ------------------------------------------------------------------
    # 异步桥接：真正调用 actor.play()
    # ------------------------------------------------------------------
    async def _do_play(oid: str, initial: bool, question: str = ""):
        obj_data = global_event_bus.get_object(oid)
        if initial:
            ad = await actor.play(role="user", input=question)
        else:
            prev_ad = obj_data.get("actor_data")
            ad = await actor.play(actor_data=prev_ad)
        obj_data["actor_data"] = ad

        match ad.status:
            case ActorStatus.CHECKLIST:
                global_event_bus.trigger_event(
                    user_id, session_id, cid, oid, BE_checklist, data=obj_data)
            case ActorStatus.NEED_EXECUTE_CHECKLIST:
                global_event_bus.trigger_event(
                    user_id, session_id, cid, oid, "开始执行", data=obj_data)
            case ActorStatus.FINAL_RESULT:
                global_event_bus.trigger_event(
                    user_id, session_id, cid, oid, "对话结束", data=obj_data)

    # ------------------------------------------------------------------
    # guard：转换前的条件检查
    # ------------------------------------------------------------------
    def _guard_to_checklist(bus, event, obj_data):
        ad = obj_data.get("actor_data")
        return ad is not None and ad.status == ActorStatus.CHECKLIST

    def _guard_to_executing(bus, event, obj_data):
        ad = obj_data.get("actor_data")
        return ad is not None and ad.status == ActorStatus.NEED_EXECUTE_CHECKLIST

    def _guard_to_done(bus, event, obj_data):
        ad = obj_data.get("actor_data")
        return ad is not None and ad.status == ActorStatus.FINAL_RESULT

    # ------------------------------------------------------------------
    # action：绑定在具体的边上
    #   oid 从 event.object_id 获取，question 从 event.data 获取
    # ------------------------------------------------------------------
    def _action_start_first_play(bus, event, obj_data):
        question = event.data.get("question", "") if event.data else ""
        asyncio.run_coroutine_threadsafe(
            _do_play(event.object_id, initial=True, question=question), loop)

    def _action_continue_play(bus, event, obj_data):
        asyncio.run_coroutine_threadsafe(
            _do_play(event.object_id, initial=False), loop)

    # ------------------------------------------------------------------
    # on_enter：进入状态后的通用动作（多条入边共享）
    # ------------------------------------------------------------------
    def _on_enter_checklist(bus, event, obj_data):
        ad = obj_data.get("actor_data")
        ad.check_done()
        obj_data["actor_data"] = ad
        bus.trigger_event(user_id, session_id, cid, event.object_id, "审批完成", data=ad)

    def _on_enter_executing(bus, event, obj_data):
        ad = obj_data.get("actor_data")
        ad = workspace.run(ad)
        obj_data["actor_data"] = ad
        bus.trigger_event(user_id, session_id, cid, event.object_id, "执行完成", data=ad)

    def _on_enter_done(bus, event, obj_data):
        ad = obj_data.get("actor_data")
        print(f"\n助手: {ad.result}")
        print(f"最终状态: {sm.get_state(event.object_id)}")

    # ==================================================================
    # 声明式定义（通过 add_state 传 on_enter，不再捅内部 dict）
    # ==================================================================
    sm = StateMachine(global_event_bus)

    sm.add_state("idle", initial=True)
    sm.add_state("calling_first")
    sm.add_state("calling_continue")
    sm.add_state("checklist",     on_enter=_on_enter_checklist)
    sm.add_state("executing",     on_enter=_on_enter_executing)
    sm.add_state("done",          on_enter=_on_enter_done)

    sm.add_transition("idle",             "对话开始",     "calling_first",
                      action=_action_start_first_play)

    sm.add_transition("calling_first",    BE_checklist,   "checklist",
                      guard=_guard_to_checklist)
    sm.add_transition("calling_first",    "开始执行",     "executing",
                      guard=_guard_to_executing)
    sm.add_transition("calling_first",    "对话结束",     "done",
                      guard=_guard_to_done)

    sm.add_transition("calling_continue", BE_checklist,   "checklist",
                      guard=_guard_to_checklist)
    sm.add_transition("calling_continue", "开始执行",     "executing",
                      guard=_guard_to_executing)
    sm.add_transition("calling_continue", "对话结束",     "done",
                      guard=_guard_to_done)

    sm.add_transition("checklist",        "审批完成",     "calling_continue",
                      action=_action_continue_play)
    sm.add_transition("executing",        "执行完成",     "calling_continue",
                      action=_action_continue_play)

    # ==================================================================
    # 启动：actor 必须先 register_object，因为 StateMachine._make_handler
    #       会在每次事件到来时通过 event.object_id 调用 bus.get_object(oid)
    #       来获取 actor 的数据
    # ==================================================================

    oid = actor.get_id()
    global_event_bus.register_object(user_id=user_id, session_id=session_id,
                                     conversation_id=cid, object_id=oid, data={"actor_data": actor},
                                     trace_id=None, event_type=BE_create_actor_data)
    sm.attach(oid, user_id=user_id, session_id=session_id, conversation_id=cid)

    initial_question = "娱乐圈最近有什么大新闻？"
    global_event_bus.trigger_event(user_id, session_id, cid, oid, "对话开始",
                                   data={"question": initial_question})

    await asyncio.to_thread(input, "\n按回车退出程序...")

    sm.print_graph()

async def main():
    initialize()
    # await Test1()
    # await Test2()

    # await Test3()
    # await Test4()
    # await Test5()
    # await Test6()
    await Test7()

if __name__ == "__main__":
    asyncio.run(main())

'''
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji$  cd /home/thbytwo/testCode/xuyuanji ; /usr/bin/env /home/thbytwo/miniforge3/envs/envXYJ/bin/python /home/thbytwo/.vscode-server/extensions/ms-python.debugpy-2025.18.0/bundled/libs/debugpy/adapter/../../debugpy/launcher 40865 -- /home/thbytwo/testCode/xuyuanji/agent/main.py 
/home/thbytwo/testCode/xuyuanji
Scanning directory: /home/thbytwo/testCode/xuyuanji/agent/tools
Found file: weather.py
Found file: basic_tool.py
Found file: news.py
Found file: calculator.py
Found file: loader.py
Total tools found: 16
Loaded tools: ['get_weather', 'create_dir', 'delete_dir', 'delete_file', 'execute_script', 'get_current_datetime', 'read_file', 'read_file_lines', 'search_file', 'web_search', 'write_file', 'get_todays_news', 'add', 'div', 'multi', 'sub']
用户没有任何对话
用户创建对话conversation_1 对话1
需要人工授权才能继续。
<0> ('call_71if5jks', 'write_file', '{"filename":"/home/thbytwo/testCode/xuyuanji/agent/output_test_user_1_session_1/get_mac.py","content":"# 获取本机MAC地址\\nimport uuid\\n\\ndef get_mac_address():\\n    # 获取本机MAC地址\\n    mac = uuid.getnode()\\n    # 格式化为标准MAC地址格式\\n    mac_hex = \':\'.join([\'{:02x}\'.format((mac \\u003e\\u003e elements) \\u0026 0xff) for elements in range(0, 2*6, 2)][::-1])\\n    return mac_hex\\n\\nif __name__ == \\"__main__\\":\\n    print(\\"本机MAC地址为:\\", get_mac_address())"}') 审批：通过
工具执行结果: 文件写入成功。
<1> ('call_gokftwla', 'execute_script', '{"script_name":"/home/thbytwo/testCode/xuyuanji/agent/output_test_user_1_session_1/get_mac.py"}') 审批：通过
sandbox执行结果: python: can't open file '/home/thbytwo/testCode/xuyuanji/agent/output_test_user_1_session_1/get_mac.py': [Errno 2] No such file or directory

恢复运行...
需要人工授权才能继续。
<0> ('call_uhd3jguo', 'execute_script', '{"script_name":"get_mac.py","script_args":null}') 审批：通过
sandbox执行结果: 本机MAC地址为: bd:f4:d0:42:09:26

恢复运行...

助手: 通过执行脚本，我已经成功获取了本机的MAC地址。

结果如下：
本机MAC地址为: bd:f4:d0:42:09:26

这个MAC地址是计算机网络接口的唯一标识符，用于在网络中识别设备。
(envXYJ) thbytwo@thbytwopower:~/testCode/xuyuanji$ 
'''