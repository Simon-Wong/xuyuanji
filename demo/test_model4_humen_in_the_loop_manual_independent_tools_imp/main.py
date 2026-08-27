import asyncio
import os
import json
from agents import Agent, Runner, RunConfig, function_tool, set_tracing_disabled
from openai_agents_providers import OllamaProvider

# 一键导入所有工具（无需单独 import 每个工具）
from tools import load_tools_from_folder

all_tools = load_tools_from_folder()
print("Loaded tools:", [t.name for t in all_tools])  # 显示工具名称

set_tracing_disabled(True)


async def main():
    provider = OllamaProvider(
        model=os.getenv("MODEL_NAME", "qwen3:4b"),
        base_url=os.getenv("PROVIDER_URL", "http://192.168.0.119:11434/v1")
    )
    run_config = RunConfig(model_provider=provider)

    agent = Agent(
        name="全能助手",
        instructions=(
            "你是一个多功能助手，可以查询天气、新闻、做加减乘除四则运算。\n"
            "根据用户的问题，调用合适的工具。如果问题超出范围，请告知用户。"
            "不能伪造任何结果，不知道或者无法调用工具请直接回复原因。"
        ),
        tools=all_tools,
    )

    # 2. 运行 Agent，并处理可能的中断
    #    注意：我们传入 input，但不传入 session，以保持示例简洁
    user_input = [{"role": "user", "content": "北京今天天气怎么样？"}]
    print(user_input)

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