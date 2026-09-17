from enum import Enum  
import uuid
import datetime

from agents import TResponseInputItem
from defination_types import MsgHis
from agents import Agent, Runner, RunConfig, set_tracing_disabled,RunResult,TResponseInputItem
from defination_types import CheckList,InputStr,Role
from configuration import UserConfig
from cache_manager import CacheManager

set_tracing_disabled(True)

class ActorStatus(Enum):
    BAD_PARAM:int=-1

    FINAL_RESULT:int=0

    CHECKLIST:int=1
    NEED_EXECUTE_CHECKLIST:int=2
    CALL_RESULT:int=3

class ActorData:
    actor_id:str=""
    id:str=""
    status:ActorStatus#表示状态
    result:str#表示结果，用于显示给用户
    checklist:CheckList#检查列表，用于执行工具
    callresults:MsgHis#存储工具调用结果

    def __init__(self,actor_id:str,status:ActorStatus,result:str,checklist:CheckList=[]):
        self.actor_id=actor_id
        self.status=status
        self.result=result
        self.checklist=checklist
        self.callresults=MsgHis()
        self.id=actor_id+"_"+uuid.uuid4().hex

    def get_id(self)->str:
        return self.id
    
    def get_actor_id(self)->str:
        return self.actor_id
    
    def get_checklist(self)->CheckList:
        return self.checklist

    def check_done(self):
        self.status=ActorStatus.NEED_EXECUTE_CHECKLIST

    def append_callresult(self,msg:TResponseInputItem):
        self.callresults.append(msg)
        self.status=ActorStatus.CALL_RESULT

    def get_callresults(self)->MsgHis:
        return self.callresults

class Actor:
    agent: Agent
    run_config: RunConfig
    result: RunResult#内部变量
    msghis: MsgHis
    cache:CacheManager
    last_input:InputStr
    debug_need_same_answer:bool
    user_config:UserConfig
    id:str
    question:str

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
        self.id=user_config.user_id+"_"+datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")+"_"+uuid.uuid4().hex
        self.question=""

    def set_msghis(self,msghis:MsgHis):
        self.msghis=msghis
    def get_msghis(self)->MsgHis:
        return self.msghis#self.result.to_input_list()

    def set_question(self,question:str):
        self.question=question
    
    def get_question(self)->str:
        return self.question

    def get_id(self)->str:
        return self.id

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

                return ActorData(self.id,ActorStatus.FINAL_RESULT,
                                 cache_result)

        if role is not None and input is not None and actor_data is None:# 开始新的运行            
            msg = self._make_role_input(role,input)
            self.msghis.append(msg)

            self.result = await Runner.run(self.agent, input=self.msghis, run_config=self.run_config)
            self.msghis = self.result.to_input_list()

            if self.result.interruptions:
                tmplist = self._collect_interruptions()
                return ActorData(self.id,ActorStatus.CHECKLIST,
                                 "需要审批的工具调用。请检查并批准或拒绝。",
                                 tmplist)
            
            final_output = self.result.final_output

            if self.debug_need_same_answer==True:
                self.cache.set(self._get_last_input(),final_output)

            return ActorData(self.id,ActorStatus.FINAL_RESULT,
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
                return ActorData(self.id,ActorStatus.CHECKLIST,
                                 "需要审批的工具调用。请检查并批准或拒绝。",
                                 tmplist)
                        
            final_output = self.result.final_output

            if self.debug_need_same_answer==True:
                self.cache.set(self._get_last_input(),final_output)

            return ActorData(self.id,ActorStatus.FINAL_RESULT,
                             final_output,
                             [])

        else:
            return ActorData(self.id,ActorStatus.BAD_PARAM,'''错误的参数，仅支持：
                    if checklist is None and role is not None and input is not None:# 开始新的运行            
                    elif role is None and input is None and checklist is not None:# 查看审批结果但不执行函数
                    elif role is None and input is not None and checklist is None:# 获取函数执行结果后继续运行
                                                        ''', [])
class ActorManager:
    def __init__(self):
        self.actors={}
    def add_actor(self,actor:Actor):
        self.actors[actor.get_id()]=actor
    def get_actor(self,actor_id:str)->Actor:
        return self.actors[actor_id]
    def get_actor_question(self,actor_id:str)->str:
        return self.actors[actor_id].get_question()
