import asyncio
import os
import re
import logging
from agents import Agent, Runner, RunConfig, function_tool, SQLiteSession,set_tracing_disabled
from openai_agents_providers import OllamaProvider

# 屏蔽 SDK 的 trace 警告
set_tracing_disabled(True)

# ========== 开关设置 ==========
SHOW_THINK = False   # True 时打印包含 <think> 的原始输出，False 时只显示最终回答

def clean_output(text: str) -> str:
    """移除 </think> 及其之前的内容，并清理多余空白。"""
    if not text:
        return text
    # 查找第一个 </think> 的位置
    idx = text.find("</think>")
    if idx != -1:
        # 取 </think> 之后的部分
        cleaned = text[idx + len("</think>"):].strip()
    else:
        cleaned = text
    return cleaned

@function_tool
def get_weather(city: str) -> str:
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
        name="天气助手小云",
        instructions=(
            "你是一个天气预报助手，名字叫小云。\n"
            "当用户询问任何城市的天气时，你必须调用适当工具来获取数据。\n"
            "如果用户问与天气无关的问题，直接回答：'我是天气助手，只回答天气问题。'"
        ),
        tools=[get_weather],
    )

    session = SQLiteSession("weather_chat_session")

    questions = [
        "北京今天天气怎么样？",
        "那 123 加 456 等于多少？",
       # "那上海呢？"
    ]

    for i, question in enumerate(questions, 1):
        print(f"\n{'='*40} 第 {i} 轮 {'='*40}")
        print(f"用户: {question}")
        result = await Runner.run(
            agent,
            question,
            run_config=run_config,
            session=session
        )
        raw_output = result.final_output

        # 根据开关决定打印内容
        if SHOW_THINK:
            display = raw_output
        else:
            display = clean_output(raw_output)
            # 如果清理后为空（可能只有思考内容），回退到原始输出
            if not display:
                display = raw_output

        print(f"助手: {display}")


if __name__ == "__main__":
    asyncio.run(main())