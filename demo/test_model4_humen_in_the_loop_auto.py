import asyncio
import os
from agents import Agent, Runner, RunConfig, function_tool, set_tracing_disabled
from openai_agents_providers import OllamaProvider

set_tracing_disabled(True)

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

async def main():
    provider = OllamaProvider(
        model=os.getenv("MODEL_NAME", "qwen3:4b"),
        base_url=os.getenv("PROVIDER_URL", "http://192.168.0.119:11434/v1")
    )
    run_config = RunConfig(model_provider=provider)

    agent = Agent(
        name="天气助手",
        instructions=(
            "你是一个天气预报助手，名字叫小云。\n"
            "当用户询问任何城市的天气时，你可以调用适当工具来获取数据。\n"
            "如果用户问与天气无关的问题，直接回答：'我是天气助手，只回答天气问题。'"
            "不能伪造任何结果，不知道或者无法调用工具请直接回复原因。"),
        tools=[get_weather],
    )

    # 2. 运行 Agent，并处理可能的中断
    #    注意：我们传入 input，但不传入 session，以保持示例简洁
    user_input = [{"role": "user", "content": "北京今天天气怎么样？"}]
    result = await Runner.run(
        agent,
        input=user_input,
        run_config=run_config,
    )

    # 3. 检查是否有待审批的工具调用
    if result.interruptions:
        state=result.to_state()

        print("需要人工授权才能继续。")
        for idx,interruption in enumerate(result.interruptions):
            # 显示待审批的工具调用详情
            print(f"<{idx}>  工具: {interruption.tool_name}")

            # 动态查找参数属性
            # 打印所有可用属性以便调试
            #print(f"  可用属性: {dir(interruption)}")
            
            # 尝试常见的属性名
            arg_value = None
            for attr in ['arguments', 'input', 'tool_input', 'parameters', 'function_arguments']:
                if hasattr(interruption, attr):
                    arg_value = getattr(interruption, attr)
                    break
            
            if arg_value is not None:
                print(f"  参数: {arg_value}")
            else:
                print("  参数: (无法获取)")
            
            # 4. 等待人工决策
            #    这里可以等待用户在终端输入 'y' 或 'n'
            user_approved = False
            while True:
                user_input = input("请输入 'y' 或 'n'：")
                if user_input == "y":
                    user_approved = True
                elif user_input == "n":
                    user_approved = False
                else:
                    print("无效的输入。请重新输入。")
                if user_input in ["y", "n"]:
                    break
                
            if user_approved:
                # 批准该工具调用
                state.approve(result.interruptions[idx])
                print("已批准。")
            else:
                # 拒绝该工具调用，可以提供一个消息给模型
                state.reject(result.interruptions[idx], "用户拒绝了该工具调用。")
                print("已拒绝。")

        # 5. 使用更新后的状态恢复运行
        print("\n恢复运行...")
        result = await Runner.run(
            agent,  # 传入原始的顶级 Agent
            state,  # 传入更新后的状态
            run_config=run_config,
        )

    # 6. 输出最终结果
    print(f"\n助手: {result.final_output}")

if __name__ == "__main__":
    asyncio.run(main())