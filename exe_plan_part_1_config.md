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

### 决策 3：全局配置存储（单例内部字典 + 读写锁）

- **存储位置**：`ConfigLoader` 单例对象的内部属性 `_config_store`（不是模块级独立变量）
- **保护**：`ConfigLoader` 实例持有 `threading.RLock`（`_store_lock`），每次读写都加锁
- **字典格式**：按 module 分组，每组内部按配置分组（与 JSON 文件结构一致）

```python
# ConfigLoader 单例内部（等价于下面这个结构）
_config_store = {
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
# 写入（load_config 内部自动调用，外部一般不直接调）
loader._store(module: str, config: dict)

# 读取（各模块使用，显式参数、不用点分隔字符串）
get_config(module: str, group: str | None = None, key: str | None = None) -> Any
# 三种形式：
#   get_config("agent")                          # 返回整个 agent 配置 dict
#   get_config("agent", "model")                 # 返回 model 分组 dict
#   get_config("agent", "model", "default")      # 返回叶子字段 "qwen-plus"
#
# 约束：group=None 时 key 必须也为 None（不能跳过 group 直接指定 key）
```

**读取安全**：
- 启动时一次性 `load_config` 写入，运行期只读
- group 不存在、key 不存在、参数非法 → 开发阶段抛 `KeyError`
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
- **深合并逻辑**：统一放在 `config/loader.py` 里作为公共实现（`ConfigLoader._read_json`、`ConfigLoader._deep_merge`），**server_main.py 不再自己私有持有**这两个函数，调用方统一 import loader 使用；
- **环境变量覆盖**：参考现有 server_main.py 的**字段级手动写法**：
  ```python
  # server_main.py 写法（保持一致）
  DEFAULT_HOST = os.getenv("SERVER_HOST", get_config("server_main", "server", "host"))
  DEFAULT_PORT = int(os.getenv("SERVER_PORT", str(get_config("server_main", "server", "port"))))
  ```
  不做"自动枚举所有环境变量并合并"的黑魔法——避免调试困难，明确可控。
- **安全提醒**：任何真实 API Key / 密钥 **仅通过环境变量传入**，default.json 只保留占位说明，**不得写入用户真实密钥到任何被 git 追踪的文件**，日志中不得打印明文密钥。

---

### 决策 6：其他已确认细节

| 讨论项 | 结论 |
|--------|------|
| **loader 文件位置** | 独立为公共包：项目根 `config/loader.py`（`config/` 目录加 `__init__.py` 成为 Python 包）。`__init__.py` 已 re-export 所有公共 API，因此 **两种 import 形式等价**：<br>• `from config.loader import load_config, get_config`（显式子模块导入，跳转最准）<br>• `from config import load_config, get_config`（直接从包顶层导入，更短）<br>server_main.py 当前使用第 ② 种。 |
| **Python 版本基线** | **Python 3.11**，可以直接使用：<br>• `X \| Y` 联合类型（PEP 604，无需 `from __future__ import annotations`）<br>• `list[T]` / `dict[K,V]` 内置泛型（无需 `List/Dict`）<br>• `typing.Self`（无需 `typing_extensions`）<br>• `from __future__ import annotations` 仅 loader.py 保留（避免循环 forward 引用报错，其他文件不必写） |
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

**文件内容骨架（与真实 `config/loader.py` 对齐，Python 3.11+）**：

```python
"""
统一配置加载器（ConfigLoader 单例工具类）

三层优先级（从低到高）:
    default.json -> user.json (深合并) -> 环境变量 (字段级 os.getenv, 各模块自行处理)

开发阶段: 找不到配置时抛 KeyError + 清晰错误消息

使用方式一（推荐，便捷函数，内部自动用单例）:
    from config.loader import load_config, get_config

    load_config("agent", default_path="config/agent.default.json",
                           user_path="config/agent.json")

    get_config("agent")                 # 返回整个 agent 配置 dict
    get_config("agent", "model")        # 返回 model 分组 dict
    get_config("agent", "model", "default")  # 返回叶子值，如 "qwen-plus"

使用方式二（显式获取单例，面向对象风格）:
    from config.loader import ConfigLoader
    loader = ConfigLoader.instance()
    loader.load_config("agent", ...)
    loader.get_config("agent", "model", "default")
"""
from __future__ import annotations

import json
import threading
import sys
from typing import Any, Self


class ConfigLoader:
    """配置加载工具类（单例模式）。

    职责:
        1. 读取 JSON 配置文件（容错，读不到返回 {}）
        2. 递归深合并 default + user 配置（不修改原始入参）
        3. 以模块名为 key，线程安全地存放到内部全局字典
        4. 支持显式 group/key 三级读取，开发阶段缺字段抛 KeyError

    单例实现: __new__ 控制实例只创建一次，线程安全（_instance_lock）。
    """

    _instance: ConfigLoader | None = None
    _instance_lock = threading.Lock()     # 控制单例初始化的锁

    # ------------------------------------------------------------------
    # 单例
    # ------------------------------------------------------------------
    def __new__(cls) -> Self:
        # double-check locking
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._config_store: dict[str, dict[str, Any]] = {}
                    inst._store_lock = threading.RLock()
                    cls._instance = inst
        return cls._instance

    @classmethod
    def instance(cls) -> Self:
        """显式获取单例入口（与 ConfigLoader() 等价）。"""
        return cls()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _read_json(path: str) -> dict[str, Any]:
        """读取单个 JSON 文件，失败时返回空字典并打印警告。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[config.loader] 读取配置失败 {path}: {exc}", file=sys.stderr)
            return {}

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """递归合并 override 到 base 并返回合并结果（不修改入参）。

        规则: 两边同 key 都是 dict 时递归合并，其他情况 override 覆盖。
        """
        merged = {k: (v if not isinstance(v, dict) else dict(v)) for k, v in base.items()}
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = ConfigLoader._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _store(self, module: str, config: dict[str, Any]) -> None:
        with self._store_lock:
            self._config_store[module] = config

    def _fetch(self, module: str) -> dict[str, Any] | None:
        with self._store_lock:
            return self._config_store.get(module)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def load_config(
        self,
        module: str,
        default_path: str,
        user_path: str,
    ) -> dict[str, Any]:
        """加载某个模块的配置并注册到内部字典。

        加载顺序（低优先级 -> 高优先级）:
            1. default_path 的 JSON 内容
            2. user_path 的 JSON 内容（递归深合并）
            3. 环境变量（字段级 os.getenv，由调用方按需在各自逻辑里实现）

        Args:
            module:       模块名，用作字典分组 key
            default_path: 默认配置文件路径（git 追踪）
            user_path:    用户覆盖文件路径，不存在则跳过

        Returns:
            合并后的配置 dict（已存入内部 store）
        """
        default_cfg = self._read_json(default_path)
        user_cfg    = self._read_json(user_path)
        merged = self._deep_merge(default_cfg, user_cfg)
        self._store(module, merged)
        return merged

    def get_config(
        self,
        module: str,
        group: str | None = None,
        key: str | None = None,
    ) -> Any:
        """从内部字典读取配置。

        三种调用形式（显式参数、不用点分隔字符串）:

            1) get_config("agent")
               -> 返回整个 agent 模块的配置 dict，例如
                  {"model": {...}, "cache": {...}, ...}

            2) get_config("agent", "model")
               -> 返回 model 分组的 dict，例如
                  {"default": "qwen-plus", "planner": ..., ...}

            3) get_config("agent", "model", "default")
               -> 返回叶子字段值，例如 "qwen-plus"

        开发阶段安全策略:
            - 模块未被 load_config 注册过  -> KeyError
            - group 不存在                -> KeyError
            - key 有值但在 group 下不存在 -> KeyError
            - 仅有 group 但 value 不是 dict -> KeyError

        Args:
            module: 模块名（对应 load_config 时传入的 module）
            group:  一级分组名，如 "model" / "server" / "logging"。None 表示返回整个模块。
            key:    分组下的具体字段名。None 且 group 有值时返回分组 dict。
                    group=None 时 key 必须也为 None（不能跳过 group 直接指定 key）。

        Returns:
            配置值

        Raises:
            KeyError: 任何层级未命中，或参数组合非法
        """
        # group=None 时 key 也必须为 None
        if group is None and key is not None:
            raise KeyError(
                f"[config] get_config(module='{module}', group=None, key={key!r}) 参数非法: "
                f"不指定 group 时不能单独指定 key，请显式传入 group"
            )

        cfg = self._fetch(module)
        if cfg is None:
            raise KeyError(
                f"[config] 模块 '{module}' 尚未加载配置，请先调用 "
                f"ConfigLoader.instance().load_config(module='{module}', default_path=..., user_path=...)"
            )

        # 形式 1: 仅 module -> 返回整个模块 dict
        if group is None:
            return cfg

        # 形式 2/3: 取 group
        if not isinstance(cfg, dict) or group not in cfg:
            available = list(cfg.keys()) if isinstance(cfg, dict) else f"<not a dict: {type(cfg).__name__}>"
            raise KeyError(
                f"[config] 模块 '{module}' 找不到分组 group='{group}'。"
                f" 可用分组: {available}"
            )
        group_value = cfg[group]

        # 形式 2: 仅有 group -> 返回分组 dict
        if key is None:
            return group_value

        # 形式 3: group + key -> 返回叶子字段
        if not isinstance(group_value, dict):
            raise KeyError(
                f"[config] 模块 '{module}' 的分组 '{group}' 不是字典，"
                f"无法按字段 key='{key}' 取值。该分组实际类型: {type(group_value).__name__}"
            )
        if key not in group_value:
            available = list(group_value.keys())
            raise KeyError(
                f"[config] 模块 '{module}' 分组 '{group}' 找不到字段 key='{key}'。"
                f" 该分组下可用字段: {available}"
            )
        return group_value[key]

    def clear_all(self) -> None:
        """**仅供测试使用**: 清空内部存储，避免测试间状态污染。"""
        with self._store_lock:
            self._config_store.clear()


# ---------------------------------------------------------------------------
# 模块级便捷函数（与类签名完全一致，调用方零改动成本）
# ---------------------------------------------------------------------------
def load_config(module: str, default_path: str, user_path: str) -> dict[str, Any]:
    """便捷函数，等价于 ConfigLoader.instance().load_config(...)"""
    return ConfigLoader.instance().load_config(module, default_path, user_path)


def get_config(
    module: str,
    group: str | None = None,
    key: str | None = None,
) -> Any:
    """便捷函数，等价于 ConfigLoader.instance().get_config(module, group, key)"""
    return ConfigLoader.instance().get_config(module, group, key)


def _clear_for_tests() -> None:
    """**仅供测试使用**: 清空单例内部存储，等价于 ConfigLoader.instance().clear_all()"""
    ConfigLoader.instance().clear_all()
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

1. **迁移配置文件**：将 `main_body/config/server_main.default.json`（以及可能存在的 `server_main.json`）移动到项目根 `config/` 目录下。原目录保留（或删除）。
2. **代码迁移**：`_read_json`、`_deep_merge` 不再放在 `main_body/server_main.py` 私有持有，**统一放在 `config/loader.py` 的 `ConfigLoader` 类里**（作为 `@staticmethod`），所有模块共用同一实现。
3. **server_main.py 开头添加 sys.path 注入（关键步骤，不能漏）**：
   - 直接 `python main_body/server_main.py` 运行时，Python 把 `main_body/` 放进 `sys.path[0]`，会**找不到项目根下的 `config/` 包**。
   - 解决方式：在 `from config import ...` 之前，先用 `Path(__file__).parent.parent` 算出项目根，插入到 `sys.path`。
4. **server_main.py 改为调用公共 loader**：

   ```python
   #基于python 3.11

   import os
   import sys
   from pathlib import Path

   # ① 计算项目根并注入 sys.path（解决 "python main_body/server_main.py" 时
   #    "ModuleNotFoundError: No module named 'config'" 问题）
   ROOT_DIR = Path(__file__).parent.parent
   sys.path.append(str(ROOT_DIR))

   # ② 从 config 顶层直接 import（因为 config/__init__.py 已 re-export 公共 API）
   from config import load_config, get_config

   # ③ 计算绝对路径拼接（避免相对路径依赖 CWD）
   BASE_DIR = os.path.dirname(os.path.abspath(__file__))
   PROJECT_ROOT = os.path.dirname(BASE_DIR)
   DEFAULT_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "server_main.default.json")
   USER_CONFIG_PATH    = os.path.join(PROJECT_ROOT, "config", "server_main.json")

   CONFIG = load_config(
       module="server_main",
       default_path=DEFAULT_CONFIG_PATH,
       user_path=USER_CONFIG_PATH,
   )
   # ④ 环境变量覆盖保持原有方式（字段级 os.getenv + 新 get_config 三级参数）
   DEFAULT_HOST = os.getenv("SERVER_HOST", get_config("server_main", "server", "host"))
   DEFAULT_PORT = int(os.getenv("SERVER_PORT", str(get_config("server_main", "server", "port"))))
   ```

目的：
- 消除重复实现（单一真源：所有合并逻辑都在 loader.py）
- server_main 和 agent 共用同一套深合并逻辑，避免两边各写各的导致行为不一致
- sys.path 注入确保"直接脚本启动" / "IDE 启动" / "uvicorn 启动" 都能稳定找到 `config` 包

---

## 1.4 验收标准

| 序号 | 验证点 | 结果 |
|------|--------|------|
| 1 | 只有 default.json（无 user.json）→ 配置值为 default 的值 | |
| 2 | user.json 覆盖部分字段 → 合并结果正确（深合并，dict 不丢未覆盖字段） | |
| 3 | 三种调用形式全通过：<br>• `get_config("agent")` → 返回整个 agent 模块 dict<br>• `get_config("agent", "model")` → 返回 model 分组 dict<br>• `get_config("agent", "model", "default")` → 返回 "qwen-plus" | |
| 4 | 未命中都抛 `KeyError`（含清晰错误消息）：<br>• 模块不存在<br>• group 不存在<br>• key 在 group 下不存在<br>• 参数非法：`group=None, key="x"` | |
| 5 | 加载完后全局字典同时含有 `server_main` 和 `agent` 两组配置 | |
| 6 | 配置文件不存在不崩溃（返回 {}） | |
| 7 | 配置内容不包含任何真实 API Key（只有占位符） | |
| 8 | **server_main.py 重构后行为与重构前完全一致**：启动 host/port、cors、path、logging 行为不变（不引入回归） | |
| 9 | 配置文件物理位置正确：`config/loader.py`、`config/server_main.default.json`、`config/agent.default.json` 都在项目根 config/ | |

---

## 1.5 结论：讨论充分，可进入实现阶段

所有设计点已确认（与真实代码对齐）：

| 讨论项 | 结论（已确认） |
|--------|--------------|
| **Python 版本基线** | **Python 3.11**，直接使用 `X \| Y`、内置泛型 `list[T]/dict[K,V]`、`typing.Self`，无需 `from __future__ import annotations`（仅 loader.py 保留以避免循环 forward 引用问题） |
| 配置文件清单 | 2 组：server_main + agent |
| loader API | 风格 A+B 结合：`load_config(module, default_path, user_path)` |
| get_config 签名 | 显式参数、不用点分隔字符串：`get_config(module, group=None, key=None)`<br>三种形式：返回整个模块 / 返回分组 / 返回叶子字段；group=None 时 key 必须也为 None |
| 单例实现 | `ConfigLoader` 工具类 + DCL 双重检查锁定（线程安全），内部持有 `_config_store: dict[str, dict]` + `_store_lock: RLock`，单例身份锁是 `_instance_lock` |
| 返回类型标注 | `__new__` 和 `instance()` 都用 `typing.Self`（Python 3.11 新增） |
| 三层覆盖 | default.json → user.json（深合并）→ 环境变量（字段级 os.getenv） |
| **loader 文件位置** | **独立公共包：`config/loader.py`**（项目根 config/ 下，加 `__init__.py` 成 Python 包）。<br>两种 import 等价：<br>• `from config.loader import load_config, get_config`<br>• `from config import load_config, get_config`（server_main 当前采用） |
| **server_main 导入前置步骤** | 在 `from config import ...` 之前，必须先 **`sys.path.append(str(Path(__file__).parent.parent))`**（或等价的项目根注入），否则直接 `python main_body/server_main.py` 会 `ModuleNotFoundError: No module named 'config'` |
| **get_config 错误处理** | **开发阶段抛 KeyError**，含清晰错误消息：模块不存在 / group 不存在 / key 不存在 / 参数非法（group=None 时指定了 key） |
| **配置文件存放位置** | **全部集中在项目根 `config/`** 下，server_main 配置文件同步迁移 |
| 配置热更新 | 初期不支持，重启生效 |
| API Key 安全 | 仅通过环境变量传入，default 文件不留真实值，日志不打印明文 |

下一步：按 1.3 交付物开始实现。
