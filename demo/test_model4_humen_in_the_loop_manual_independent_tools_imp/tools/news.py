from agents import function_tool

@function_tool
def get_todays_news(topic: str = "科技") -> str:
    """获取今日新闻（模拟）"""
    news_db = {
        "科技": "OpenAI发布全新Agent框架",
        "财经": "美联储宣布降息25个基点",
        "体育": "中国队夺得金牌"
    }
    return news_db.get(topic, f"没有找到关于 {topic} 的新闻。")