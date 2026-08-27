from agents import function_tool

@function_tool(needs_approval=True) 
def get_weather(city: str) -> str:
    """查询指定城市的当前天气（模拟数据）。"""
    weather_db = {
        "北京": "晴朗，25°C",
        "上海": "多云，28°C",
        "广州": "雷阵雨，32°C",
        "深圳": "晴转多云，30°C"
    }
    return weather_db.get(city, f"抱歉，没有 {city} 的天气数据。")