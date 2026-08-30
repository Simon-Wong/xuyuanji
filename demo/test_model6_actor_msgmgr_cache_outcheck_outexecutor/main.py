import asyncio
import os
from agents import Agent, Runner, RunConfig, function_tool, set_tracing_disabled,RunResult,TResponseInputItem
from agents import RunItem
from openai_agents_providers import OllamaProvider

from typing import Annotated, Literal,Any
from pydantic import Field,BaseModel
import json

from configuration import UserConfig

from tools import load_tools_from_folder
all_tools,tool_map = load_tools_from_folder()
print("Loaded tools:", [t.name for t in all_tools])


set_tracing_disabled(True)

class ModelStore:
    DEFAULT_MODEL:str= "qwen3:4b"
    DEFAULT_URL:str= "http://192.168.0.119:11434/v1"

    all_models:dict[str,OllamaProvider]
    default_model:str
    default_url:str

    def __init__(self):
        self.default_model = self.DEFAULT_MODEL
        self.default_url = self.DEFAULT_URL
        self.all_models={}
        self._register_model(self.default_model, self.default_url)
    def _make_key(self,model_name: str, base_url: str)->str:
        return f"{model_name}@{base_url}"
    
    def _register_model(self, model_name: str, base_url: str):
        key=self._make_key(model_name, base_url)
        self.all_models[key]=OllamaProvider(model=model_name,base_url=base_url)

    def get_model(self, model_name: str, base_url: str|None)->OllamaProvider:
        if base_url is None:
            base_url=self.default_url
        key=self._make_key(model_name, base_url)
        if key not in self.all_models:
            self._register_model(model_name, base_url)
        return self.all_models.get(key)

global_model_store = ModelStore()

class AgentStore:
    agents:dict[str,Agent]
    def __init__(self):
        self.agents = {}
    
    def register_agent(self, agent:Agent) -> tuple[int,str]:
        item=self.agents.get(agent.name)
        if item is not None:
            return 1,f"{agent.name}已经注册过了。"
        self.agents[agent.name] = agent
        return 0,f"注册 {agent.name}成功。"
    def get_agent(self, agent_name: str)->tuple[int,Agent,str]:
        item=self.agents.get(agent_name)
        if item is None:
            return 1,None,f"{agent_name}未注册。"
        return 0,item,f"获取{agent_name}成功。"
    
global_agent_store = AgentStore()

MsgHis=Annotated[list[TResponseInputItem],Field(description="历史对话")]

class MessageManager:
    all_messages:dict[str,MsgHis]
    def __init__(self):
        self.all_messages = {}

    def _make_key(self,user_id: str,session_id:str)->str:
        return f"{user_id}@{session_id}"
    def get_messages(self, user_id: str,session_id:str)->MsgHis:
        if user_id is None or session_id is None:
            return []

        key=self._make_key(user_id,session_id)
        item=self.all_messages.get(key,[])
        return item
    def append_message(self, user_id: str,session_id:str,msg:MsgHis):
        key=self._make_key(user_id,session_id)
        self.all_messages[key].append(msg)

global_message_manager = MessageManager()


class CacheManager:
    #调试用
    #保存dict[str:str]，并以json的形式保存到磁盘。
    cache:dict[str:str]
    pathfile:str

    def __init__(self):
        self.pathfile="cache.json"
        self.cache={}
        self._load_cache(self.pathfile)

    def _load_cache(self,filepath:str):      
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.rstrip('\n')
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            self.cache.update(obj)
                    except json.JSONDecodeError:
                        # 忽略损坏的行（可记录日志）
                        continue

    def _append_cache(self,q:str,a:str):
        #追加式写入, q和a占一行
        tmp={q:a}
        with open(self.pathfile,"a",encoding="utf-8") as f:
            json.dump(tmp,f,ensure_ascii=False)
            f.write("\n")

    def set(self,q:str,a:str):    
        tmp=self.cache.get(q)
        if tmp is None:
            self.cache[q]=a
            self._append_cache(q,a)
    
    def get(self,q:str)->str:
        tmp=self.cache.get(q)
        if tmp is None:
            return None
        print(f"<cache hit>")
        return tmp
       

env_model=os.getenv("MODEL_NAME", "qwen3:4b")
env_base_url=os.getenv("PROVIDER_URL", "http://192.168.0.119:11434/v1")

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


Role=Annotated[Literal["user", "assistant", "system"],Field(description="消息角色，仅支持 user/assistant/system")]
InputStr=Annotated[str,Field(description="输入的消息内容")]
CheckList=Annotated[list[tuple[int,str,Literal['y','n'],str]],Field(description="检查列表每一个元素表示一个检查项，包含:"
                                                   "一个整数表示序号，"
                                                   "一个字符串表示带检查内容，"
                                                   "一个字符表示审批结果，y表示同意，n表示拒绝。"
                                                   "一个字符串表示做出该审批的理由。")]

from enum import Enum, unique  
class ActorStatus(Enum):
    BAD_PARAM:int=-1

    FINAL_RESULT:int=0

    CHECKLIST:int=1
    NEED_EXECUTE_CHECKLIST:int=2
    CALL_RESULT:int=3

class ActorData:
    status:ActorStatus#表示状态
    result:str#表示结果，用于显示给用户
    checklist:CheckList#检查列表，用于执行工具
    callresults:MsgHis#存储工具调用结果

    def __init__(self,status:ActorStatus,result:str,checklist:CheckList=[]):
        self.status=status
        self.result=result
        self.checklist=checklist
        self.callresults=MsgHis()

    def get_checklist(self)->CheckList:
        return self.checklist

    def check_done(self):
        self.status=ActorStatus.NEED_EXECUTE_CHECKLIST

    def append_callresult(self,msg:TResponseInputItem):
        self.callresults.append(msg)
        self.status=ActorStatus.CALL_RESULT

    def get_callresults(self)->MsgHis:
        return self.callresults

class CallExecutor:
    user_config:UserConfig
    def __init__(self,user_config:UserConfig):
        self.user_config=user_config

    def run(self, data:ActorData) -> ActorData:
        checklist=data.get_checklist()  

        for idx, detail, decision, reason in checklist:
            tc_id=detail[0]
            tc_name=detail[1]
            tc_args=detail[2]

            if decision == 'y':
                #self.state.approve(self.result.interruptions[idx])
                print(f"<{idx}> {detail} 审批：通过")
                #尝试运行多次
                for idx_time in range(self.user_config.max_turns_try_function):
                    tool_func=tool_map.get(tc_name)
                    if tool_func is not None:
                        try:
                            args=json.loads(tc_args)
                            result_data=tool_func(**args)
                            print(f"工具执行结果: {result_data}")
                        except Exception as e:
                            result_data = f"工具执行出错: {e}"
                            print(result_data)

                        data.append_callresult({"call_id": tc_id,
                                            "output": result_data,
                                            "type": "function_call_output"
                                        })
                        break
                    else:
                        print(f"未知工具 {tc_name}，跳过。")
                        data.append_callresult({"call_id": tc_id,
                                            "output": f"错误：未知工具 {tc_name}",
                                            "type": "function_call_output"
                                        })
                        
            else:
                print(f"<{idx}> {detail} 审批：拒绝 原因:{reason}")
                data.append_callresult({"call_id": tc_id,
                                "output": "用户拒绝了该工具调用。请不要再尝试调用该工具。",
                                "type": "function_call_output"
                            })
            
        return data

class Actor:
    agent: Agent
    run_config: RunConfig
    result: RunResult#内部变量
    msghis: MsgHis
    cache:CacheManager
    last_input:InputStr
    debug_need_same_answer:bool
    user_config:UserConfig

    def __init__(self, agent: Agent, run_config: RunConfig,msghis:MsgHis,user_config:UserConfig):
        self.agent = agent
        self.run_config = run_config
        self.state = None
        self.result = None
        self.msghis=msghis
        self.cache=None
        self.last_input=None
        self.debug_need_same_answer=user_config.debug_need_same_answer
        self.user_config=user_config

    def set_msghis(self,msghis:MsgHis):
        self.msghis=msghis
    def get_msghis(self)->MsgHis:
        return self.msghis#self.result.to_input_list()

    def set_debug_need_same_answer(self,flag:bool):
        self.debug_need_same_answer=flag
        if flag==True:
            self.cache=CacheManager()
        else:
            self.cache=None

    def _set_last_input(self,last_input:InputStr):
        self.last_input=last_input
    
    def _get_last_input(self)->InputStr:
        return self.last_input

    def _make_role_input(self,role:Role,input:InputStr)->str:
        if role == "user":
            msg = {"role": "user", "content": input}
        elif role == "assistant":
            msg = {"role": "assistant", "content": input}
        elif role == "system":
            msg = {"role": "system", "content": input}

        return msg

    def _collect_interruptions(self) -> CheckList:
        """从 self.result.interruptions 收集所有待审批项，返回 Checklist"""
        tmplist = []
        print("需要人工授权才能继续。")
        for idx, interruption in enumerate(self.result.interruptions):
            # 尝试获取参数
            arg_value = None
            for attr in ['arguments', 'input', 'tool_input', 'parameters', 'function_arguments']:
                if hasattr(interruption, attr):
                    arg_value = getattr(interruption, attr)
                    break
            tool_name = interruption.tool_name
            tool_param = arg_value
            call_id=interruption.call_id
            detail=(call_id,tool_name,tool_param)
            one = (idx, detail, 'y', "默认允许")
            tmplist.append(one)
        return tmplist

    async def play(self, role: Role = None, input: InputStr = None,actor_data:ActorData=None) -> ActorData:
        #调试用，直接返回缓存结果
        if self.debug_need_same_answer==True:
            if input is not None and role is not None and actor_data is None:
                self._set_last_input(input)

            # 从缓存中获取结果
            cache_result=self.cache.get(input)
            if cache_result is not None:
                #伪造聊天记录
                msg_user = self._make_role_input(role,input)
                self.msghis.append(msg_user)
                msg_assistant=self._make_role_input("assistant",cache_result)
                self.msghis.append(msg_assistant)

                return ActorData(ActorStatus.FINAL_RESULT,
                                 cache_result)

        if role is not None and input is not None and actor_data is None:# 开始新的运行            
            msg = self._make_role_input(role,input)
            self.msghis.append(msg)

            self.result = await Runner.run(self.agent, input=self.msghis, run_config=self.run_config)
            self.msghis = self.result.to_input_list()

            if self.result.interruptions:
                tmplist = self._collect_interruptions()
                return ActorData(ActorStatus.CHECKLIST,
                                 "需要审批的工具调用。请检查并批准或拒绝。",
                                 tmplist)
            
            final_output = self.result.final_output

            if self.debug_need_same_answer==True:
                self.cache.set(self._get_last_input(),final_output)

            return ActorData(ActorStatus.FINAL_RESULT,
                             final_output)

        elif role is None and input is None and actor_data is not None:
            if actor_data.status!=ActorStatus.CALL_RESULT:#错误的流程，不处理
                print(f"错误的流程，不处理。当前actor_data状态为:{actor_data.status},应当由外部处理并修改状态。")
                return actor_data
            
            # 获取函数执行结果后继续运行
            print("\n恢复运行...")
            self.msghis.extend(actor_data.get_callresults())

            self.result = await Runner.run(
                self.agent,
                self.msghis,
                run_config=self.run_config,
            )
            self.msghis = self.result.to_input_list()

            if self.result.interruptions:
                tmplist = self._collect_interruptions()
                return ActorData(ActorStatus.CHECKLIST,
                                 "需要审批的工具调用。请检查并批准或拒绝。",
                                 tmplist)
                        
            final_output = self.result.final_output

            if self.debug_need_same_answer==True:
                self.cache.set(self._get_last_input(),final_output)

            return ActorData(ActorStatus.FINAL_RESULT,
                             final_output,
                             [])

        else:
            return ActorData(ActorStatus.BAD_PARAM,'''错误的参数，仅支持：
                    if checklist is None and role is not None and input is not None:# 开始新的运行            
                    elif role is None and input is None and checklist is not None:# 查看审批结果但不执行函数
                    elif role is None and input is not None and checklist is None:# 获取函数执行结果后继续运行
                                                        ''', [])
    
async def Test1():
    provider = global_model_store.get_model(env_model, env_base_url)
    run_config = RunConfig(model_provider=provider)
    _,agent,_=global_agent_store.get_agent("天气助手2")
    msghis=global_message_manager.get_messages("test_user_1","session_1")
    user_cfg=UserConfig.load(user_id="test_user_1",session_id="session_1",config_file_name="user_config.json")
    call_exe=CallExecutor(user_cfg)

    actor=Actor(agent,run_config,msghis,user_cfg)
    actor_data:ActorData=await actor.play(role="user",input="北京今天天气怎么样？上海今天天气怎么样？")
    while actor_data.status!=ActorStatus.FINAL_RESULT:
        if actor_data.status==ActorStatus.CHECKLIST:#需要外部审批
            #假装外部已经审批
            actor_data.check_done()

        if actor_data.status==ActorStatus.NEED_EXECUTE_CHECKLIST:#需要外部执行
            actor_data=call_exe.run(actor_data)

        if actor_data.status==ActorStatus.CALL_RESULT:#继续运行
            actor_data=await actor.play(actor_data=actor_data)

    print(f"\n助手: {actor_data.result}")

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

    provider = global_model_store.get_model(env_model, env_base_url)
    run_config = RunConfig(model_provider=provider)
    _,agent,_=global_agent_store.get_agent("天气助手")
    msghis=global_message_manager.get_messages("test_user_1","session_1")
    user_cfg=UserConfig.load(user_id="test_user_1",session_id="session_1",config_file_name="user_config.json")
    call_exe=CallExecutor(user_cfg)

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
                actor_data=call_exe.run(actor_data)

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
                actor_data=call_exe.run(actor_data)

            if actor_data.status==ActorStatus.CALL_RESULT:#继续运行
                actor_data=await actor.play(actor_data=actor_data)

        print(f"\n助手: {actor_data.result}")

async def Test3():
    cachemgr=CacheManager(is_debug=True)
    cachemgr.set("q1","a1")
    cachemgr.set("q2","a2")
    cachemgr.set("q3","a3")
    cache=cachemgr.get("q2")
    print(cache)

async def Test4():
    cachemgr=CacheManager(is_debug=True)
    cachemgr.set("q1","a1")
    cachemgr.set("q3","a3")
    cache=cachemgr.get("q2")
    print(cache)


async def main():
    initialize()
    #await Test1()
    await Test2()

    #await Test3()
    #await Test4()

if __name__ == "__main__":
    asyncio.run(main())