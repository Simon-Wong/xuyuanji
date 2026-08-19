"""
配置包 - 集中管理所有模块的配置文件与加载逻辑

内容:
- loader.py                  公共配置加载器
- server_main.default.json   Web Server 默认配置 (git 追踪)
- agent.default.json         Agent 默认配置 (git 追踪)
- server_main.json           Web Server 本地覆盖 (.gitignore)
- agent.json                 Agent 本地覆盖 (.gitignore)
"""
from .loader import load_config, get_config, _clear_for_tests
