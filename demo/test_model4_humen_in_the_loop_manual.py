import asyncio
import os
import json
from agents import Agent, Runner, RunConfig, function_tool, set_tracing_disabled
from openai_agents_providers import OllamaProvider

set_tracing_disabled(True)

def _get_weather_impl(city: str) -> str:
    weather_db = {
        "北京": "晴朗，25°C",
        "上海": "多云，28°C",
        "广州": "雷阵雨，32°C",
        "深圳": "晴转多云，30°C"
    }
    return weather_db.get(city, f"抱歉，没有 {city} 的天气数据。")

@function_tool
def get_weather(city: str) -> str:
    return _get_weather_impl(city)

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
        tool_use_behavior='stop_on_first_tool',  # 阻止 SDK 自动执行工具
    )

    history = [{"role": "user", "content": "北京今天天气怎么样？"}]

    MAX_TURNS = 3
    final_result = None

    for _ in range(MAX_TURNS):
        result = await Runner.run(
            agent,
            input=history,
            run_config=run_config,
        )
        final_result = result

        # 检测工具调用项
        tool_calls = [item for item in result.new_items if getattr(item, 'type', '') == 'tool_call_item']

        if not tool_calls:
            break

        # 更新完整历史（包含模型生成的所有内容）
        history = result.to_input_list()

        # 处理第一个工具调用（通常只有一个）
        tool_call = tool_calls[0]
        tool_name = tool_call.raw_item.name
        tool_args = tool_call.raw_item.arguments
        tool_id = tool_call.raw_item.id

        print(f"\n工具调用请求: {tool_name}")
        print(f"参数: {tool_args}")

        while True:
            choice = input("是否批准执行该工具？(y/n): ").strip().lower()
            if choice in ('y', 'n'):
                break
            print("无效输入，请输入 'y' 或 'n'。")

        if choice == 'y':
            args = json.loads(tool_args)
            if tool_name == "get_weather":
                result_data = _get_weather_impl(**args)
                print(f"工具执行结果: {result_data}")
                # 正确格式：call_id, output, type
                history.append({
                    "call_id": tool_id,
                    "output": result_data,
                    "type": "function_call_output"
                })
            else:
                print(f"未知工具 {tool_name}，跳过。")
                history.append({
                    "call_id": tool_id,
                    "output": f"错误：未知工具 {tool_name}",
                    "type": "function_call_output"
                })
        else:
            # 拒绝：注入明确消息
            history.append({
                "call_id": tool_id,
                "output": "用户拒绝了该工具调用。请直接告知用户无法获取天气信息，不要再尝试调用工具。",
                "type": "function_call_output"
            })

        # 继续循环，让模型基于工具输出生成最终回答

    if final_result:
        print(f"\n最终回答: {final_result.final_output}")

if __name__ == "__main__":
    asyncio.run(main())