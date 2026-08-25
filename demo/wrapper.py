import asyncio
import os
from agents import Agent, Runner, RunConfig, function_tool, SQLiteSession  # 1. 导入 SQLiteSession
from openai_agents_providers import OllamaProvider

# 1. 定义工具：模拟获取天气
@function_tool
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
    # 2. 配置 Ollama 提供者
    provider = OllamaProvider(
        model=os.getenv("MODEL_NAME", "llama3.2:3b"), 
        base_url=os.getenv("PROVIDER_URL", "http://192.168.0.119:11434/v1")
    )
    run_config = RunConfig(model_provider=provider)

    # 3. 创建 Agent
    agent = Agent(
        name="天气助手小云",
        instructions=(
            "你的名字叫小云，是一个亲切、友好的天气预报助手。\n"
            "你的职责是回答用户关于天气的问题。\n"
            "如果用户询问与天气无关的话题（比如数学、历史、娱乐等），"
            "你必须礼貌地拒绝，并引导用户回到天气话题。\n"
            "回答时要保持简洁、清晰，并主动询问用户需要哪个城市的天气。"
        ),
        tools=[get_weather],
    )

    # 4. 创建一个会话实例 (关键改动)
    #    使用 SQLiteSession 并指定一个会话 ID，用于在多次运行间保持对话历史
    session = SQLiteSession("weather_chat_session")

    # --- 第一轮：正常天气查询 ---
    print("=" * 40 + " 第 1 轮 " + "=" * 40)
    user_input_1 = "北京今天天气怎么样？"
    print(f"用户: {user_input_1}")

    # 通过 session=session 参数传入会话
    result_1 = await Runner.run(
        agent,
        user_input_1,
        run_config=run_config,
        session=session  # <--- 关键改动：传入 session
    )
    print(f"助手: {result_1.final_output}\n")

    # --- 第二轮：无关话题（考验 instructions 的约束力） ---
    print("=" * 40 + " 第 2 轮 " + "=" * 40)
    user_input_2 = "那 123 加 456 等于多少？"
    print(f"用户: {user_input_2}")

    # 再次使用同一个 session 实例
    result_2 = await Runner.run(
        agent,
        user_input_2,
        run_config=run_config,
        session=session  # <--- 再次传入同一个 session
    )
    print(f"助手: {result_2.final_output}\n")

    # --- 第三轮：再次询问天气（利用历史记忆） ---
    print("=" * 40 + " 第 3 轮 " + "=" * 40)
    user_input_3 = "那上海呢？"
    print(f"用户: {user_input_3}")

    result_3 = await Runner.run(
        agent,
        user_input_3,
        run_config=run_config,
        session=session  # <--- 第三次传入同一个 session
    )
    print(f"助手: {result_3.final_output}")

if __name__ == "__main__":
    asyncio.run(main())