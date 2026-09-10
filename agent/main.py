'''
用户-----会话-----------对话--------------------------工作空间
         通信通道       不同的会话可同时打开同一个      同一会话共用同一个工作空间
'''

import sys
from pathlib import Path
ROOT_DIR=Path(__file__).parent.parent
print(ROOT_DIR)
sys.path.append(str(ROOT_DIR))
import copy
import asyncio
import os
from agents import Agent, Runner, RunConfig, function_tool, set_tracing_disabled,RunResult,TResponseInputItem
from agents import RunItem
from openai_agents_providers import OllamaProvider

from typing import Annotated, Literal,Any
from pydantic import Field,BaseModel
import json

from agent.configuration import UserConfig

from sandboxex import PersistentScriptSandbox

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

    def _make_key(self,user_id: str,conversation_id:str)->str:
        return f"{user_id}@{conversation_id}"
    def get_messages(self, user_id: str,conversation_id:str)->MsgHis:
        if user_id is None or conversation_id is None:
            return []

        key=self._make_key(user_id,conversation_id)
        item=self.all_messages.get(key,[])
        return item
    def append_message(self, user_id: str,conversation_id:str,msg:MsgHis):
        key=self._make_key(user_id,conversation_id)
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

class Sandboxex:
    sandbox_type:str=""
    sandbox_script_type:str=""
    sandbox:PersistentScriptSandbox

    save_dir:str#调试用
    host_scripts_dir:str#调试用
    host_data_dir:str=""#调试用

    def __init__(self,sandbox_type: str,sandbox_script_type: str,save_dir: str):
        self.sandbox_type=sandbox_type

        if self.sandbox_type == "PersistentScriptSandbox":
            self.sandbox=PersistentScriptSandbox(output_dir=save_dir)
            self.sandbox_script_type=sandbox_script_type

        else:
            self.sandbox=None
            print(f"不支持的sandbox_type {sandbox_type}")

    def start(self,host_scripts_dir: str,host_data_dir: str = None,ro_volumes: list[tuple[str, str]] = None)->tuple[bool,str]:
        flag:bool =False
        if self.sandbox is None:
            return False,f"错误：不支持的sandbox_type： {self.sandbox_type}"

        if self.sandbox_script_type == "python":
            flag= self.sandbox.start_python_script(host_scripts_dir=host_scripts_dir,
                                                   host_data_dir=host_data_dir,
                                                   ro_volumes=ro_volumes)
            self.host_scripts_dir=host_scripts_dir
            self.host_data_dir=host_data_dir

        elif self.sandbox_script_type == "bash":
            flag = self.sandbox.start_bash_script(host_scripts_dir=host_scripts_dir,
                                                  ro_volumes=ro_volumes)
            self.host_scripts_dir=host_scripts_dir
        
        if flag:
            return True,""
        elif not flag:
            return False,f"错误：启动{self.sandbox_script_type}类型的PersistentScriptSandbox沙箱失败"

        return False,f"错误：不支持的sandbox_script_type {self.sandbox_script_type},仅支持python和bash"

    def stop(self):
        if self.sandbox is None:
            return f"错误：不支持的sandbox_type {self.sandbox_type}"
        self.sandbox.cleanup()

    def run(self, name:str, args:str) -> str:
        if self.sandbox is None:
            return f"错误：不支持的sandbox_type {self.sandbox_type}"
        
        if self.sandbox_script_type == "python":
            flag, outputstr, errstr = self.sandbox.run_python_script(name, args)
        elif self.sandbox_script_type == "bash":
            flag, outputstr, errstr = self.sandbox.run_bash_script(name, args)
        else:
            return f"错误：不支持的sandbox_script_type {self.sandbox_script_type},仅支持python和bash"
        
        if flag == 0:
            return outputstr
        
        return errstr

class CallExecutor:
    max_turns_try_function:int
    use_sandbox:bool=False
    sandbox:Sandboxex
    need_init_flag:bool=False
    sandbox_type:str
    sandbox_script_type:str
    save_dir:str
    host_scripts_dir:str

    def __init__(self,user_config:UserConfig):
        self.max_turns_try_function=user_config.max_turns_try_function
        if user_config.use_sandbox == True:
            self.use_sandbox=True
            self.sandbox=None
            self.need_init_flag=True

            self.sandbox_type=user_config.sandbox_type
            self.sandbox_script_type=user_config.sandbox_script_type
            self.save_dir=user_config.save_dir

            self.host_scripts_dir=user_config.output_dir

    def append_prompt(self)->str:
        '''
        补充提示词
        '''
        pm=""
        if self.use_sandbox:
            pm=f'''
启用了沙箱，允许运行{self.sandbox_script_type}类型的脚本。
脚本存放目录: {self.host_scripts_dir}，
数据目录: {self.save_dir}，
沙箱结果保存目录: {self.save_dir}'''
        else:
            pm=f'''
未启用沙箱，不允许运行脚本。
''' 
        return pm

    def stop(self):
        if self.sandbox is not None:
            self.sandbox.stop()

    def run_in_sandbox(self,tc_id:str,tc_name: str,tc_args: str,data:ActorData) -> ActorData:
        if self.need_init_flag:
            self.need_init_flag=False
            self.sandbox=Sandboxex(sandbox_type=self.sandbox_type,
                                   sandbox_script_type=self.sandbox_script_type,
                                   save_dir=self.save_dir)
            self.sandbox.start(host_scripts_dir=self.host_scripts_dir,
                               host_data_dir=self.save_dir)

        if self.sandbox is None:
            data.append_callresult({"call_id": tc_id,
                            "output": "用户没有权限使用沙箱执行脚本。请不要再尝试调用该工具。",
                            "type": "function_call_output"
                        })
            return data
        try:
            args=json.loads(tc_args)
            script_name=args["script_name"]
            script_args=args["script_args"]
            result_data=self.sandbox.run(name=script_name, args=script_args)
            print(f"sandbox执行结果: {result_data}")
        except Exception as e:
            result_data = f"sandbox执行出错: {e}"
            print(result_data)

        data.append_callresult({"call_id": tc_id,
                            "output": result_data,
                            "type": "function_call_output"
                        })   
        
        return data

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
                for idx_time in range(self.max_turns_try_function):#todo:考虑是否需要尝试多次
                    tool_func=tool_map.get(tc_name)
                    if tc_name=="execute_script":
                        #执行脚本需要在沙箱中运行
                        data=self.run_in_sandbox(tc_id,tc_name,tc_args,data)#这里在内部形成了结果字符串
                        break#执行脚本后，跳出循环。无法保证脚本具有幂等性。

                    elif tool_func is not None:
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

class Product:

    data:dict[str, list[tuple[str, str]]]=[]

    question:str
    type:str
    product:str
    def __init__(self,question:str,type:str,product:str):
        self.question=question
        self.type=type
        self.product=product


class WorkSpace:
    work_dir:str
    output_dir:str
    save_dir:str
    call_executor:CallExecutor
    use_sandbox:bool=False
    enable_sandbox:bool=False
    session_id:str
    product:dict[str, list[tuple[str, str]]]=[]

    def __init__(self,user_config:UserConfig):
        self.session_id=user_config.session_id
        self.use_sandbox=user_config.use_sandbox
        self.work_dir=os.getcwd()
        if user_config.work_dir != "default_work_dir":
            if os.path.exists(user_config.work_dir) and os.path.isdir(user_config.work_dir):
                self.work_dir=user_config.work_dir
            else:
                print(f"错误：工作目录 {user_config.work_dir} 不存在或不是目录。使用当前目录 {self.work_dir}")
                self.work_dir=os.getcwd()


        program_dir=os.path.dirname(os.path.abspath(__file__))
        self.output_dir=os.path.join(program_dir,"output_"+user_config.user_id+"_"+user_config.session_id)
        if user_config.output_dir != "default_output_dir":
            if os.path.exists(user_config.output_dir) and os.path.isdir(user_config.output_dir) and os.access(user_config.output_dir, os.W_OK):
                self.output_dir=user_config.output_dir
            else:
                print(f"错误：输出目录 {user_config.output_dir} 不存在或不是目录或不可写。使用默认规则生成目录 {self.output_dir}")
                os.makedirs(self.output_dir, exist_ok=True)
        else:
            os.makedirs(self.output_dir, exist_ok=True)

        self.save_dir=os.path.join(program_dir,"save_"+user_config.user_id)      
        if user_config.save_dir != "default_save_dir":
            if os.path.exists(user_config.save_dir) and os.path.isdir(user_config.save_dir) and os.access(user_config.save_dir, os.W_OK):
                self.save_dir=user_config.save_dir
            else:
                print(f"错误：保存目录 {user_config.save_dir} 不存在或不是目录或不可写。使用默认规则生成目录 {self.save_dir}")
                os.makedirs(self.save_dir, exist_ok=True)
        else:
            os.makedirs(self.save_dir, exist_ok=True)

        user_config_real:UserConfig=copy.deepcopy(user_config)
        user_config_real.work_dir=self.work_dir
        user_config_real.output_dir=self.output_dir
        user_config_real.save_dir=self.save_dir
        self.call_executor=CallExecutor(user_config_real)

    def run(self, data:ActorData) -> ActorData:
        return self.call_executor.run(data)

    def make_product(self,question:str,type:str,product:str)->str:
        # 生成产物
        if question not in self.product:
            self.product[question]=[]
        self.product[question].append((type,product))

    def save_product(self,data:ActorData,product:str)->None:
        # 保存产物
        pass
    

    def stop(self):
        self.call_executor.stop()


    def show(self):
        print(f"工作目录: {self.work_dir}")
        print(f"输出目录: {self.output_dir}")
        print(f"保存目录: {self.save_dir}")
        print(f"是否启用沙箱: {self.call_executor.use_sandbox}")
        print(f"沙箱是否可用: {self.call_executor.sandbox is not None}")

    def append_prompt(self)->str:
        '''
        补充提示词
        '''
        pm=self.call_executor.append_prompt()
        return pm

class WorkspaceManager:
    workspaces:dict[str, dict[str, WorkSpace]]={}
    flag:bool=True
    def get_workspace(self,user_config: UserConfig,conversation_id:str)->WorkSpace:
        if self.flag==False:
            return None

        user_id=user_config.user_id
        
        if user_id not in self.workspaces:
            self.workspaces[user_id]={}
        if conversation_id not in self.workspaces[user_id]:
            self.workspaces[user_id][conversation_id]=WorkSpace(user_config)

        return self.workspaces[user_id][conversation_id]
    
    def stop_one(self,user_id:str,conversation_id:str):
        if user_id not in self.workspaces.keys():
            return
        if conversation_id not in self.workspaces[user_id].keys():
            return
        self.workspaces[user_id][conversation_id].stop()
        self.workspaces[user_id].pop(conversation_id)

    def stop_all(self):
        self.flag=False

        for user_id in self.workspaces.keys():
            for conversation_id in self.workspaces[user_id].keys():
                self.workspaces[user_id][conversation_id].stop()

        self.workspaces={}


global_workspace_manager = WorkspaceManager()

class Conversation:
    id:str
    ref_count:int=0
    flag:bool=True
    caption:str=""

    def __init__(self,conversation_id:str,caption:str|None=None):
        self.id=conversation_id
        self.ref_count=0
        self.flag=True
        if caption is not None:
            self.caption=caption
        else:
            self.caption="无标题"

    def reference(self)->None:
        self.ref_count+=1
        self.flag=True

    def release(self)->None:
        self.ref_count-=1
        if self.ref_count<=0:
            self.flag=False

    def is_opened(self)->bool:
        return self.flag 

class UserSessionConversationManager:
    '''
    记录用户会话打开了哪些对话
    当用户关闭会话时，需要对该对话的计数减一，便于其他组件释放资源
    '''
    conversations:dict[str, Conversation]={}#对话id到对话
    uid2cids:dict[str,list[str]]={}#用户id到多个对话id
    uid2sid2cids:dict[str,dict[str,list[str]]]={}#用户id到会话id到多个对话id

    def __init__(self):
        self.conversations={}
        self.uid2cids={}
        self.uid2sid2cids={}

    def record_user_session(self,user_id:str,session_id:str):
        '''
        记录用户会话，初始化对应的对话列表容器
        若该用户会话已存在，则保留原有对话列表，不做覆盖
        '''
        user_session_map = self.uid2sid2cids.setdefault(user_id, {})
        user_session_map.setdefault(session_id, [])

    def record_user_session_conversation(self,user_id:str,session_id:str,conversation_id:str,conversation_caption:str|None=None)->tuple[bool,str]:
        '''
        记录用户在会话里打开了对话
        '''
        #self.record_user_session(user_id, session_id)#这个由外部保证

        cids=self.uid2sid2cids[user_id][session_id]
        if conversation_id in cids:#会话内的对话不能重复打开
            return False,"对话已打开"
        
        tc=self.conversations.get(conversation_id)
        if tc is None:
            tc=Conversation(conversation_id,conversation_caption)
            self.conversations[conversation_id]=tc
            self.uid2cids.setdefault(user_id,[]).append(conversation_id)

        tc.reference()

        self.uid2sid2cids[user_id][session_id].append(conversation_id)
        return True,""

    def get_conversations_id(self,userid:str)->list[str]:
        '''
        获取用户拥有的对话id
        '''
        return self.uid2cids.get(userid,[])
    
    def get_conversation_caption(self,user_id:str,conversation_id:str)->tuple[bool,str]:
        '''
        获取对话标题
        '''

        cids=self.uid2cids.get(user_id,[])
        if conversation_id not in cids:
            return False,"用户不存在此对话"

        conversation=self.conversations.get(conversation_id)
        if conversation is None:
            return False,"对话不存在"
        
        return True,conversation.caption

    def remove_conversation(self,user_id:str,session_id:str,conversation_id:str)->str:
        '''
        移除对话
        '''
        if conversation_id in self.conversations:
            self.conversations.pop(conversation_id)

        cids=self.uid2cids[user_id]
        if conversation_id in cids:
            cids.remove(conversation_id)

        cids2=self.uid2sid2cids[user_id][session_id]
        if conversation_id in cids2:
            cids2.remove(conversation_id)

        return conversation_id
    
    def close_session(self,user_id:str,session_id:str)->list[str]:
        '''
        关闭会话，返回因关闭会话而关闭的所有对话id
        '''
        tmp=[]

        cids=self.uid2sid2cids[user_id].pop(session_id)
        for cid_id in cids:
            c=self.conversations[cid_id]
            c.release()
            if c.is_opened()==False:
                tmp.append(cid_id)

        return tmp

    def close_conversation(self,user_id:str,session_id:str,conversation_id:str)->tuple[bool,str]:
        '''
        关闭对话
        如果对话正在使用，返回False,"对话正在使用"
        如果对话未被使用，返回True,"对话id"
        如果对话不存在，返回False,"对话不存在"
        '''
        cids=self.uid2sid2cids[user_id][session_id]
        if conversation_id not in cids:
            return False,"对话不存在"
        
        cids.remove(conversation_id)

        c=self.conversations[conversation_id]
        c.release()
        if c.is_opened()==False:
            return True,conversation_id
        else:
            return False,"对话正在使用"

global_user_session_conversation_manager=UserSessionConversationManager()

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

    provider = global_model_store.get_model(env_model, env_base_url)#加载模型
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

    provider = global_model_store.get_model(env_model, env_base_url)
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
    provider = global_model_store.get_model(env_model, env_base_url)
    run_config = RunConfig(model_provider=provider)
    _,agent,_=global_agent_store.get_agent("八卦小助手")
    msghis=global_message_manager.get_messages("test_user_1","conversation_1")
    user_cfg=UserConfig.load(user_id="test_user_1",session_id="session_1",config_file_name="user_config.json")
    workspace=WorkSpace(user_cfg)

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

async def Test6():
    provider = global_model_store.get_model(env_model, env_base_url)
    run_config = RunConfig(model_provider=provider)
    _,agent,_=global_agent_store.get_agent("脚本小助手")
    msghis=global_message_manager.get_messages("test_user_1","conversation_1")
    user_cfg=UserConfig.load(user_id="test_user_1",session_id="session_1",config_file_name="user_config.json")
    workspace=WorkSpace(user_cfg)

    actor=Actor(agent,run_config,msghis,user_cfg)
    tmpinput="编写一个脚本，获取本机MAC地址并打印出来。执行这个脚本并告诉我结果\n"+workspace.append_prompt()
    actor_data:ActorData=await actor.play(role="user",input=tmpinput)
    while actor_data.status!=ActorStatus.FINAL_RESULT:
        if actor_data.status==ActorStatus.CHECKLIST:#需要外部审批
            #假装外部已经审批
            actor_data.check_done()

        if actor_data.status==ActorStatus.NEED_EXECUTE_CHECKLIST:#需要外部执行
            actor_data=workspace.run(actor_data)

        if actor_data.status==ActorStatus.CALL_RESULT:#继续运行
            actor_data=await actor.play(actor_data=actor_data)

    print(f"\n助手: {actor_data.result}")


async def main():
    initialize()
    #await Test1()
    await Test2()

    #await Test3()
    #await Test4()
    #await Test5()
    #await Test6()

if __name__ == "__main__":
    asyncio.run(main())