import asyncio
import os
from agents import Agent, Runner, RunConfig, function_tool, set_tracing_disabled
from openai_agents_providers import OllamaProvider

# 屏蔽 SDK 的 trace 警告
set_tracing_disabled(True)

# ========== 开关设置 ==========
SHOW_THINK = False   # True 时打印包含 <think> 的原始输出，False 时只显示最终回答

def clean_output(text: str) -> str:
    """移除 </think> 及其之前的内容，并清理多余空白。"""
    if not text:
        return text
    idx = text.find("</think>")
    if idx != -1:
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

    # ---------- 手动管理历史 ----------
    message_history = []  # 存储所有历史消息（格式为 InputItem 列表）

    questions = [
        "北京今天天气怎么样？",
        "那 123 加 456 等于多少？",
        #"那上海呢？"
    ]

    for i, question in enumerate(questions, 1):
        print(f"\n{'='*40} 第 {i} 轮 {'='*40}")
        print(f"用户: {question}")

        # 1. 构建本轮完整的输入列表：历史 + 当前用户消息
        #    注意：当前用户消息需要包装成符合 InputItem 的字典
        current_input = message_history + [{"role": "user", "content": question}]

        # 2. 调用 Runner.run()，使用 input 参数传入完整消息序列
        result = await Runner.run(
            agent,
            input=current_input,   # 传入完整的消息列表
            run_config=run_config,
        )

        # 3. 更新历史：使用 to_input_list() 获取本轮完整交互（包含工具调用、助手回复等）
        message_history = result.to_input_list()

        # 4. 输出最终答案（按开关决定是否清理）
        raw_output = result.final_output
        if SHOW_THINK:
            display = raw_output
        else:
            display = clean_output(raw_output)
            if not display:
                display = raw_output

        print(f"助手: {display}")

    print("\n所有历史消息：")
    print(message_history)

if __name__ == "__main__":
    asyncio.run(main())