from toolkit import mcp

@mcp.tool()
def get_weather(city: str) -> dict:
    """查询指定城市的实时天气"""
    weather_data = {
        "北京": {"temperature": "26°C", "condition": "晴"},
        "上海": {"temperature": "30°C", "condition": "多云"},
    }
    return weather_data.get(city, {"temperature": "未知", "condition": "未知"})