from agents import Agent

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