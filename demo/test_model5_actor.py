import asyncio
import os
from agents import Agent, Runner, RunConfig, function_tool, set_tracing_disabled,RunResult
from openai_agents_providers import OllamaProvider

from typing import Annotated, Literal,Any
from pydantic import Field,BaseModel

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


# 1. 定义需要审批的工具
#    通过 needs_approval=True 标记该工具执行前需要人工批准
@function_tool(needs_approval=True)  # <--- 关键点
def get_weather(city: str) -> str:
    """查询指定城市的当前天气（模拟数据）。"""
    weather_db = {
        "北京": "晴朗，25°C",
        "上海": "多云，28°C",
        "广州": "雷阵雨，32°C",
        "深圳": "晴转多云，30°C"
    }
    return weather_db.get(city, f"抱歉，没有 {city} 的天气数据。")


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
        tools=[get_weather],
    )
    global_agent_store.register_agent(agent)

Role=Annotated[Literal["user", "assistant", "system"],Field(description="消息角色，仅支持 user/assistant/system")]
InputStr=Annotated[str,Field(description="输入的消息内容")]
CheckList=Annotated[list[tuple[int,str,Literal['y','n'],str]],Field(description="检查列表每一个元素表示一个检查项，包含:"
                                                   "一个整数表示序号，"
                                                   "一个字符串表示带检查内容，"
                                                   "一个字符表示审批结果，y表示同意，n表示拒绝。"
                                                   "一个字符串表示做出该审批的理由。")]


class Actor:
    agent:Agent
    run_config:RunConfig
    result:RunResult
    def __init__(self,agent:Agent,run_config:RunConfig):
        self.agent=agent
        self.run_config=run_config
        self.state=None
        self.result=None
    async def play(self,role:Role=None, input: InputStr=None, checklist:CheckList=None) -> tuple[int,str,CheckList]:
        if checklist is None and role is not None and input is not None:
            #开始新的运行
            msg=''
            if role=="user":
                msg = [{"role": "user", "content": input}]
            elif role=="assistant":
                msg = [{"role": "assistant", "content": input}]
            elif role=="system":
                msg = [{"role": "system", "content": input}]

            
            self.result = await Runner.run(self.agent,
                                                input=msg,
                                                run_config=self.run_config)
            
            if self.result.interruptions:
                self.state=self.result.to_state()
                tmplist=[]

                print("需要人工授权才能继续。")
                for idx,interruption in enumerate(self.result.interruptions):
                    # 显示待审批的工具调用详情
                    #print(f"<{idx}>  工具: {interruption.tool_name}")

                    # 动态查找参数属性
                    # 打印所有可用属性以便调试
                    #print(f"  可用属性: {dir(interruption)}")
                    
                    # 尝试常见的属性名
                    arg_value = None
                    for attr in ['arguments', 'input', 'tool_input', 'parameters', 'function_arguments']:
                        if hasattr(interruption, attr):
                            arg_value = getattr(interruption, attr)
                            break
                    
                    # if arg_value is not None:
                    #     print(f"  参数: {arg_value}")
                    # else:
                    #     print("  参数: (无法获取)")

                    detail=f"工具: {interruption.tool_name} 参数: {arg_value}"

                    one=(idx,detail,'y',"默认允许")
                    tmplist.append(one)

                return 1,f"需要审批的工具调用。请检查并批准或拒绝。",tmplist
            final_output=self.result.final_output
            return 0,final_output,[]


        if role is None and input is None and checklist:
            #继续上次因审批而打断的运行
            user_approved = False
            for one in checklist:
                idx=one[0];
                detail=one[1];
                if one[2]=='y':
                    user_approved = True
                else:
                    user_approved = False
                reason=one[3]
                
                if user_approved:
                    # 批准该工具调用
                    self.state.approve(self.result.interruptions[idx])
                    print(f"<{idx}> {detail} 审批：通过")
                else:
                    # 拒绝该工具调用，提供一个原因给模型
                    self.state.reject(self.result.interruptions[idx],reason)
                    print(f"<{idx}> {detail} 审批：拒绝 原因:{reason}")

            print("\n恢复运行...")
            self.result = await Runner.run(
                self.agent,  # 传入原始的顶级 Agent
                self.state,  # 传入更新后的状态
                run_config=self.run_config,
            )

            if self.result.interruptions:
                self.state=self.result.to_state()
                tmplist=[]

                print("需要人工授权才能继续。")
                for idx,interruption in enumerate(self.result.interruptions):
                    # 显示待审批的工具调用详情
                    #print(f"<{idx}>  工具: {interruption.tool_name}")

                    # 动态查找参数属性
                    # 打印所有可用属性以便调试
                    #print(f"  可用属性: {dir(interruption)}")
                    
                    # 尝试常见的属性名
                    arg_value = None
                    for attr in ['arguments', 'input', 'tool_input', 'parameters', 'function_arguments']:
                        if hasattr(interruption, attr):
                            arg_value = getattr(interruption, attr)
                            break
                    
                    # if arg_value is not None:
                    #     print(f"  参数: {arg_value}")
                    # else:
                    #     print("  参数: (无法获取)")

                    detail=f"工具: {interruption.tool_name}\n参数: {arg_value}"

                    one=(idx,detail,'y',"默认允许")
                    tmplist.append(one)

                return 1,f"需要审批的工具调用。请检查并批准或拒绝。",tmplist
            final_output=self.result.final_output
            return 0,final_output,[]

        return -1,f"错误的参数，仅支持：checklist is None and role is not None and input is not None 或者 role is None and input is None and checklist",[]

    
async def main():
    initialize()

    provider = global_model_store.get_model(env_model, env_base_url)
    run_config = RunConfig(model_provider=provider)
    _,agent,_=global_agent_store.get_agent("天气助手")

    actor=Actor(agent,run_config)
    flag,text,checklist=await actor.play(role="user",input="北京今天天气怎么样？")
    while flag==1:
        #处理checklist
        
        flag,text,checklist=await actor.play(checklist=checklist)
    print(f"\n助手: {text}")

if __name__ == "__main__":
    asyncio.run(main())