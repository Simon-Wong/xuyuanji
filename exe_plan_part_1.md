# 开发任务 1：配置文件（Execution Plan Part 1）

## 1.1 任务目标

搭建配置加载的骨架，支持各模块独立加载配置，最终统一存入全局字典。配置字段随开发推进逐步补充，**无需在本阶段定义所有字段**。

---

## 1.2 设计决策（讨论结论）

### 决策 1：配置文件清单与存放位置

| 配置文件 | 默认配置文件 | 负责模块 | 配置分组（示例） |
|----------|-------------|---------|-----------------|
| `server_main.json` | `server_main.default.json` | `main_body/` | server / cors / paths / logging |
| `agent.json` | `agent.default.json` | `agent/` | model / cache / sandbox / budget / logging |

**统一存放位置**：所有配置文件（default + user + loader）都放在项目根目录的 **`config/`** 下。原 `main_body/config/` 下的 `server_main.default.json` 迁移到项目根 `config/`。

```
项目根/
├── config/
│   ├── __init__.py              # 让 config 成为一个包
│   ├── loader.py                # 公共 loader（原放在 agent/config 或 common，现确定放 config/）
│   ├── server_main.default.json # server_main 默认配置（git 追踪）
│   ├── server_main.json         # server_main 本地覆盖（.gitignore）
│   ├── agent.default.json       # agent 默认配置（git 追踪）
│   └── agent.json               # agent 本地覆盖（.gitignore）
├── main_body/
│   └── server_main.py           # 调用 config.loader.load_config
└── agent/
    └── ...                      # 调用 config.loader.load_config
```

**说明**：
- default 文件纳入版本管理（git 追踪）
- user 文件（`server_main.json`、`agent.json`）用于开发者本地覆盖，建议 `.gitignore`
- 字段随开发推进逐步填充，初期字段可留空或占位
- 好处：所有配置文件集中在一个目录，部署和维护清晰

---

### 决策 2：load_config API（A+B 结合风格）

各模块通过统一接口加载自己的配置：

```python
# 参数：
#   module: 模块名，用于注册到全局字典的 key
#   default_path: 默认配置文件路径
#   user_path: 用户配置文件路径（可不存在，不存在则跳过覆盖）
#   env_prefix: 可选，环境变量前缀（暂未实现自动覆盖，后续按 server_main 方式补充）
cfg = load_config(
    module="agent",
    default_path="config/agent.default.json",
    user_path="config/agent.json",
)
# 返回 dict，如:
# {
#     "model": {"default": "qwen-plus", ...},
#     "cache": {"enabled": False, ...},
#     "sandbox": {...},
#     "budget": {...},
#     "logging": {...}
# }
```

---

### 决策 3：全局配置字典 + 读写锁

- **存储位置**：配置 loader 模块内部的模块级单例字典 `_GLOBAL_CONFIG`
- **保护**：使用 `threading.RLock`（读写锁）封装
- **字典格式**：按 module 分组，每组内部按配置分组（与 JSON 文件结构一致）

```python
_GLOBAL_CONFIG = {
    "server_main": {
        "server": {"host": "0.0.0.0", "port": 8000},
        "cors": {...},
        "logging": {...},
    },
    "agent": {
        "model": {"default": "qwen-plus", ...},
        "cache": {"enabled": False, ...},
        ...
    },
}
```

**对外封装接口**：

```python
# 写入（load_config 内部调用）
_store_config(module: str, config: dict)

# 读取（各模块使用）
get_config(module: str, key_path: str | None = None) -> Any
# 示例：
#   get_config("agent")                        # 返回 agent 全部配置 dict
#   get_config("agent", "model.default")        # 返回 "qwen-plus"
#   get_config("agent", "model")["default"]    # 等价写法
```

**读取安全**：
- 启动时一次性 `load_config` 写入，运行期只读
- 若未来需要热更新，锁机制已预留

---

### 决策 4：三层覆盖规则（与 server_main 一致）

覆盖优先级（从低到高）：

```
1. default.json  ———  文件缺省时读不到也不报错（返回 {}）
   ↓ 深合并（递归 dict 合并，叶子字段覆盖）
2. user.json     ———  文件不存在则跳过
   ↓ 字段级 os.getenv 覆盖（暂不统一做，各模块按实际需要自行 os.getenv）
3. 环境变量      ———  最高优先级（用于容器/CI 场景）
```

**说明**：
- **深合并逻辑**：直接复用 server_main.py 现有的 `_read_json` 和 `_deep_merge` 实现（放到公共 loader 模块，两边都调用）
- **环境变量覆盖**：参考现有 server_main.py 第 79-83 行的**字段级手动写法**：
  ```python
  # server_main.py 现有写法（保持一致）
  DEFAULT_HOST = os.getenv("SERVER_HOST", CONFIG["server"]["host"])
  DEFAULT_PORT = int(os.getenv("SERVER_PORT", str(CONFIG["server"]["port"])))
  ```
  不做"自动枚举所有环境变量并合并"的黑魔法——避免调试困难，明确可控。
- **安全提醒**：任何真实 API Key / 密钥 **仅通过环境变量传入**，default.json 只保留占位说明，**不得写入用户真实密钥到任何被 git 追踪的文件**，日志中不得打印明文密钥。

---

### 决策 6：其他已确认细节

| 讨论项 | 结论 |
|--------|------|
| **loader 文件位置** | 独立为公共包：项目根 `config/loader.py`（`config/` 目录加 `__init__.py` 成为 Python 包），server_main 和 agent 都通过 `from config.loader import load_config, get_config` 调用 |
| **get_config 找不到字段** | **开发阶段抛异常**（`KeyError` + 清晰错误消息），显式暴露配置遗漏，避免缺字段静默 bug |
| **配置文件存放位置** | 全部集中在项目根 `config/` 目录下，不分模块存放。server_main 和 agent 的配置文件并列放置 |
| **配置热更新** | 初期不支持，重启生效 |

---

## 1.3 交付物

### 1.3.1 公共 loader 模块

**文件位置**：`config/loader.py`（项目根目录下，`config/` 为 Python 包，需配 `__init__.py`）

**导入方式**：
```python
from config.loader import load_config, get_config

# main_body/server_main.py 调用
server_cfg = load_config(
    module="server_main",
    default_path="config/server_main.default.json",
    user_path="config/server_main.json",
)

# agent/ 内各模块调用
agent_cfg = load_config(
    module="agent",
    default_path="config/agent.default.json",
    user_path="config/agent.json",
)
```

**文件内容骨架**：

```python
"""
统一配置加载器
三层优先级：default.json → user.json（深合并）→ 环境变量（字段级 os.getenv）
开发阶段：get_config 找不到字段抛 KeyError
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

# ---------------------------------------------------------------------------
# 全局配置字典 + 读写锁
# ---------------------------------------------------------------------------
_GLOBAL_CONFIG: dict[str, dict[str, Any]] = {}
_GLOBAL_LOCK = threading.RLock()


def _read_json(path: str) -> dict[str, Any]:
    """读取单个 JSON 文件，失败时返回空字典并打印警告。
    复用 server_main.py 现有的 _read_json 实现。
    """
    ...


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并 override 到 base：dict 深合并，其他类型直接覆盖。
    复用 server_main.py 现有的 _deep_merge 实现。
    """
    ...


def _store_config(module: str, config: dict[str, Any]) -> None:
    """将已合并的模块配置存入全局字典（内部加锁）。"""
    with _GLOBAL_LOCK:
        _GLOBAL_CONFIG[module] = config


def load_config(
    module: str,
    default_path: str,
    user_path: str,
) -> dict[str, Any]:
    """
    加载某个模块的配置，合并 default → user，并写入全局字典。
    返回合并后的配置 dict。
    """
    config = _read_json(default_path)
    config = _deep_merge(config, _read_json(user_path))
    _store_config(module, config)
    return config


def get_config(module: str, key_path: str | None = None) -> Any:
    """
    从全局字典读取配置。
    - 只传 module: 返回该模块全部 dict
    - 传 key_path (如 "model.default"): 返回点路径对应的叶子值
    - **开发阶段**：找不到任何路径节点抛 KeyError + 清晰提示
    """
    with _GLOBAL_LOCK:
        cfg = _GLOBAL_CONFIG.get(module)
    if cfg is None:
        raise KeyError(
            f"[config] 模块 '{module}' 尚未加载配置，请先调用 load_config(module=...)"
        )
    if key_path is None:
        return cfg
    # 点路径查找："model.default" → cfg["model"]["default"]
    cur: Any = cfg
    trace = []
    for part in key_path.split("."):
        trace.append(part)
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(
                f"[config] 模块 '{module}' 找不到配置键 '{' .'.join(trace)}'，"
                f"完整请求路径 '{key_path}'，当前模块配置可用分组: {list(cfg.keys())}"
            )
    return cur
```

---

### 1.3.2 占位配置文件

**`config/agent.default.json`（骨架，字段随开发补，放项目根 config/ 下）**

```json
{
  "model": {
    "default": "qwen-plus",
    "planner": "qwen-plus",
    "selector": "qwen-turbo",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key_env": "DASHSCOPE_API_KEY"
  },
  "cache": {
    "enabled": false,
    "dir": ".cache",
    "version": "v1"
  },
  "sandbox": {
    "image": "python:3.12-slim",
    "max_containers": 5,
    "timeout": 600
  },
  "budget": {
    "max_depth": 3,
    "max_tokens": 100000,
    "max_tool_calls": 50
  },
  "logging": {
    "level": "INFO",
    "trace_enabled": true
  }
}
```

**`config/agent.json`（可不存在，存在则覆盖 default，.gitignore 忽略，放项目根 config/ 下）**

```json
{
  "model": {
    "default": "llama3:8b"
  },
  "cache": {
    "enabled": true
  }
}
```

---

### 1.3.3 重构现有 server_main.py + 迁移配置文件

1. **迁移配置文件**：将 `main_body/config/server_main.default.json`（以及可能存在的 `server_main.json`）移动到项目根 `config/` 目录下。原目录删除（或留空）。
2. **代码迁移**：将现有的 `_read_json`、`_deep_merge` 从 `main_body/server_main.py` **迁移到 `config/loader.py`**，作为公共实现。
3. **server_main.py 改为调用公共 loader**：
   ```python
   from config.loader import load_config, get_config

   CONFIG = load_config(
       module="server_main",
       default_path="config/server_main.default.json",
       user_path="config/server_main.json",
   )
   # 环境变量覆盖保持原有方式（字段级 os.getenv）
   DEFAULT_HOST = os.getenv("SERVER_HOST", get_config("server_main", "server.host"))
   DEFAULT_PORT = int(os.getenv("SERVER_PORT", str(get_config("server_main", "server.port"))))
   ```

目的：
- 消除重复实现（单一真源）
- server_main 和 agent 共用同一套深合并逻辑，避免两边各写各的导致行为不一致

---

## 1.4 验收标准

| 序号 | 验证点 | 结果 |
|------|--------|------|
| 1 | 只有 default.json（无 user.json）→ 配置值为 default 的值 | |
| 2 | user.json 覆盖部分字段 → 合并结果正确（深合并，dict 不丢未覆盖字段） | |
| 3 | `get_config("agent", "model.default")` → 返回正确值 | |
| 4 | `get_config("agent", "not.exist.path")` → **开发阶段抛 KeyError**，含清晰错误消息 | |
| 5 | 加载完后全局字典同时含有 `server_main` 和 `agent` 两组配置 | |
| 6 | 配置文件不存在不崩溃（返回 {}） | |
| 7 | 配置内容不包含任何真实 API Key（只有占位符） | |
| 8 | **server_main.py 重构后行为与重构前完全一致**：启动 host/port、cors、path、logging 行为不变（不引入回归） | |
| 9 | 配置文件物理位置正确：`config/loader.py`、`config/server_main.default.json`、`config/agent.default.json` 都在项目根 config/ | |

---

## 1.5 结论：讨论充分，可进入实现阶段

所有设计点已确认：

| 讨论项 | 结论（已确认） |
|--------|--------------|
| 配置文件清单 | 2 组：server_main + agent |
| loader API | 风格 A+B 结合：`load_config(module, default_path, user_path)` |
| 全局字典 | 模块级单例 `_GLOBAL_CONFIG` + `threading.RLock`，按 module 分组 |
| 三层覆盖 | default.json → user.json（深合并）→ 环境变量（字段级 os.getenv） |
| **loader 文件位置** | **独立公共包：`config/loader.py`**（项目根 config/ 下，加 `__init__.py` 成 Python 包） |
| **get_config 找不到字段** | **开发阶段抛 KeyError**，带清晰错误消息 |
| **配置文件存放位置** | **全部集中在项目根 `config/`** 下，server_main 配置文件同步迁移 |
| 配置热更新 | 初期不支持，重启生效 |
| API Key 安全 | 仅通过环境变量传入，default 文件不留真实值，日志不打印明文 |

下一步：按 1.3 交付物开始实现。
