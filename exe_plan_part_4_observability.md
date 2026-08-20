# 开发任务 4 执行计划：可观测性 (Observability)
> 对应 `dev_plan.md` 阶段 0 的任务 #4「可观测性」
> 本文档为任务 #4 的讨论结论定版与执行计划，与 exe_plan_part_1 / part_3 风格保持一致。

---

## 一、文档定位与范围

### 1.1 任务目标
实现 Agent 架构的**可观测性基础设施**，包含两大职责：
1. **trace_id 管理**：用户输入新对话时生成唯一 trace_id，后续该对话内所有组件、事件、任务显式传递同一个 trace_id
2. **结构化日志**：单行 JSON 格式，三层输出（总文件 + 组件文件 + 告警文件 + stdout）

### 1.2 与其他模块的关系
- **上游**：Web Server 收到用户请求创建新对话时调用 `generate_trace_id()` 生成 trace_id
- **下游**：所有组件（EventBus、路由器、调度器等）通过 `get_logger(组件名)` 获取 logger，日志中携带 trace_id
- **依赖**：`config`（读取 agent 配置的 logging 段），不依赖 EventBus
- **EventBus 关系**：EventBus 的 Event 已有 `trace_id` 字段，消费线程内 handler 从 `event.trace_id` 取值显式传参，不用 contextvar

### 1.3 核心约束
- **不用 contextvar**：trace_id 全程显式传参，避免隐式依赖，便于未来跨语言移植
- **零第三方依赖**：仅用 Python 标准库（`logging`、`json`、`uuid`、`time`）
- **不侵入 EventBus**：EventBus 预置的 3 个 debug print handler 暂不改动

---

## 二、已讨论并确认的设计决策

### 2.1 trace_id

| 项 | 结论 |
|---|---|
| 生成时机 | 用户输入新对话时生成 |
| 格式 | `毫秒_UUID4无横杠`（与 EventBus 的 event_id 同格式） |
| 传递方式 | **全程显式传参**，不用 contextvar，不隐式传递 |
| 管理模块 | `agent/observability/tracing.py`，暴露 `generate_trace_id()` 函数 |

### 2.2 日志格式

单行 JSON。每条日志输出为一行合法 JSON：

```json
{"ts":"2026-08-20T10:00:00.123","level":"INFO","logger":"agent.event_bus","trace_id":"abc-123","event":"event_published","data":{"event_type":"更新"}}
```

字段说明：

| 字段 | 来源 | 说明 |
|---|---|---|
| `ts` | Formatter 自动生成 | ISO 8601 带毫秒 |
| `level` | logging 自动 | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `logger` | logging 自动 | logger 名称（如 `agent.event_bus`） |
| `trace_id` | `extra` 传入 | 追踪 ID，串联同一对话全链路 |
| `event` | `extra` 传入 | 事件名（如 `event_published`、`route_selected`） |
| `data` | `extra` 传入（可选） | 附加数据 dict |

调用方式：

```python
from agent.observability import get_logger

logger = get_logger("event_bus")
logger.info("event_published", extra={
    "trace_id": "1234_abcd...",
    "event": "event_published",
    "data": {"event_type": "更新", "object_id": "obj-xxx"}
})
```

**Formatter 实现要点**：
- 继承 `logging.Formatter`，重写 `format()` 方法
- 从 `record.__dict__` 提取 `trace_id` / `event` / `data`，组装为 JSON dict
- `extra` 中未传 `trace_id` 时填 `null`（避免 KeyError）
- `data` 未传时不输出该字段
- `ensure_ascii=False`（中文不转义）

### 2.3 日志输出目标

三层输出 + stdout：

| 输出目标 | 文件 | 级别范围 | 说明 |
|---|---|---|---|
| 总文件 | `logs/agent.log` | DEBUG+（受全局 level 控制） | 所有组件所有级别，便于按 trace_id 串联全链路 |
| 组件文件 | `logs/{component}.log` | DEBUG+（受全局 level 控制） | 仅该组件日志，便于快速定位排查 |
| 告警文件 | `logs/warnings.log` | WARNING+ | 所有组件的严重问题集中 |
| 控制台 | stdout | DEBUG+（受全局 level 控制） | 开发调试实时查看 |

**实现方式**：利用 Python logging 的**层级传播**（propagate）。observability 模块是通用基础设施，不感知任何具体组件。各组件自己调 `get_logger(组件名)` 获取 logger，observability 模块内部无任何组件名硬编码。

```
agent (根 logger，setup_logging 一次性配置)
  ├── handler → logs/agent.log         (DEBUG+, 所有组件，受全局 level)
  ├── handler → logs/warnings.log       (WARNING+, 所有组件)
  └── handler → stdout                 (DEBUG+, 受全局 level)

agent.{任意组件名} (子 logger, propagate=True)
  └── handler → logs/{组件名}.log       (DEBUG+, 仅自己)
```

任意组件调 `get_logger("xxx")` 后，该 logger 的日志会：
1. 写入自己的组件文件 `logs/xxx.log`
2. 向上冒泡到根 logger → 写入总文件 + 告警文件（如级别达标）+ stdout

### 2.4 配置文件

`config/agent.default.json` 的 `logging` 段扩展为：

```json
"logging": {
    "level": "INFO",
    "trace_enabled": true,
    "log_dir": "logs",
    "enable_total_file": true,
    "enable_component_files": true,
    "enable_warning_file": true,
    "enable_console": true,
    "max_bytes": 10485760,
    "backup_count": 5
}
```

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `level` | str | `"INFO"` | 全局日志级别 |
| `trace_enabled` | bool | `true` | 预留开关（当前 trace_id 始终生成） |
| `log_dir` | str | `"logs"` | 日志目录（相对于项目根） |
| `enable_total_file` | bool | `true` | 是否写总文件 `agent.log` |
| `enable_component_files` | bool | `true` | 是否按组件分文件 |
| `enable_warning_file` | bool | `true` | 是否写告警文件 `warnings.log` |
| `enable_console` | bool | `true` | 是否输出到 stdout |
| `max_bytes` | int | `10485760` | 单文件最大字节数（10MB），触发轮转 |
| `backup_count` | int | `5` | 保留的轮转备份文件数 |

文件轮转使用 `logging.handlers.RotatingFileHandler`。

### 2.5 Logger 命名规范

- 统一前缀 `agent.`
- 组件名取目录名（如 `agent.event_bus`、`agent.router`、`agent.scheduler`）
- 调用方通过 `get_logger("event_bus")` 获取，内部自动拼接为 `agent.event_bus`

### 2.6 get_logger 行为

```python
def get_logger(component: str) -> logging.Logger:
    """获取组件 logger，首次调用时自动创建组件文件 handler。"""
```

- **首次调用**某组件名时：
  1. 创建 `agent.{component}` logger
  2. 如果 `enable_component_files=True`，添加 `RotatingFileHandler` → `logs/{component}.log`
  3. 设置 `propagate=True`（默认），日志冒泡到 `agent` 根 logger
  4. 缓存已创建的 logger，避免重复添加 handler
- **根 logger** `agent` 在 `setup_logging()` 时一次性配置好总文件 + 告警文件 + stdout 三个 handler
- **重复调用**同一组件名：直接返回缓存的 logger，不重复添加 handler

### 2.7 模块初始化流程

```
程序启动
  ↓
load_config("agent", ...)          ← 加载配置
  ↓
setup_logging()                    ← 配置根 logger（总文件 + 告警 + stdout）
  ↓
各组件 import 时调 get_logger("xxx")  ← 按需创建组件 logger + 组件文件
```

`setup_logging()` 只需调一次（程序入口处）。`get_logger()` 可多次调用，内部幂等。

---

## 三、实现范围与文件结构

### 3.1 目录结构

```
agent/
├── observability/
│   ├── __init__.py        # re-export: get_logger, setup_logging, generate_trace_id
│   ├── tracing.py         # generate_trace_id()
│   ├── logging_setup.py   # setup_logging() + get_logger() + JsonFormatter
│   └── (无其他文件)
```

### 3.2 `agent/observability/__init__.py`

```python
from agent.observability.tracing import generate_trace_id
from agent.observability.logging_setup import get_logger, setup_logging

__all__ = ["generate_trace_id", "get_logger", "setup_logging"]
```

### 3.3 `agent/observability/tracing.py`

```python
"""trace_id 生成与管理。"""
from __future__ import annotations

import time
import uuid


def generate_trace_id() -> str:
    """生成 trace_id：毫秒时间戳_UUID4无横杠。
    
    格式与 EventBus 的 event_id 一致，字符串排序 = 时间先后序。
    在用户输入新对话时调用，后续该对话内所有组件显式传递同一个 trace_id。
    """
    ts_ms = str(int(time.time() * 1000))
    hex32 = uuid.uuid4().hex
    return f"{ts_ms}_{hex32}"
```

### 3.4 `agent/observability/logging_setup.py`

```python
"""结构化日志配置：单行 JSON + 三层输出（总文件 + 组件文件 + 告警文件 + stdout）。"""
from __future__ import annotations

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Any


# ============================================================================
# JsonFormatter：每条日志输出为一行合法 JSON
# ============================================================================
class JsonFormatter(logging.Formatter):
    """将 LogRecord 格式化为单行 JSON。

    从 extra 提取 trace_id / event / data，组装为 JSON dict。
    未传入的字段填 null 或省略。
    """

    def format(self, record: logging.LogRecord) -> str:
        # 手动构造 ISO 8601 带毫秒，time.strftime 不支持 %f
        import time as _time
        ct = _time.localtime(record.created)
        ts = _time.strftime("%Y-%m-%dT%H:%M:%S", ct)
        ts += f".{int(record.msecs):03d}"

        log_dict: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", None),
            "event": record.getMessage(),
        }
        # data 字段可选
        data = getattr(record, "data", None)
        if data is not None:
            log_dict["data"] = data
        return json.dumps(log_dict, ensure_ascii=False)


# ============================================================================
# 模块级状态
# ============================================================================
_root_configured = False
_component_loggers: dict[str, logging.Logger] = {}

# 配置缓存（setup_logging 时填入）
_config: dict[str, Any] = {}


# ============================================================================
# setup_logging：配置根 logger（程序入口调一次）
# ============================================================================
def setup_logging(
    *,
    level: str = "INFO",
    log_dir: str = "logs",
    enable_total_file: bool = True,
    enable_component_files: bool = True,
    enable_warning_file: bool = True,
    enable_console: bool = True,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """配置根 logger agent，添加总文件 + 告警文件 + stdout handler。

    从 agent 配置的 logging 段读取参数。仅调一次。
    """
    global _root_configured, _config

    if _root_configured:
        return

    _config = {
        "level": level,
        "log_dir": log_dir,
        "enable_total_file": enable_total_file,
        "enable_component_files": enable_component_files,
        "enable_warning_file": enable_warning_file,
        "enable_console": enable_console,
        "max_bytes": max_bytes,
        "backup_count": backup_count,
    }

    os.makedirs(log_dir, exist_ok=True)

    root = logging.getLogger("agent")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False  # 不冒泡到 Python root logger

    formatter = JsonFormatter()

    # 总文件 handler
    if enable_total_file:
        h = RotatingFileHandler(
            os.path.join(log_dir, "agent.log"),
            maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
        )
        h.setLevel(getattr(logging, level.upper(), logging.INFO))
        h.setFormatter(formatter)
        root.addHandler(h)

    # 告警文件 handler（WARNING+）
    if enable_warning_file:
        h = RotatingFileHandler(
            os.path.join(log_dir, "warnings.log"),
            maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
        )
        h.setLevel(logging.WARNING)
        h.setFormatter(formatter)
        root.addHandler(h)

    # stdout handler
    if enable_console:
        h = logging.StreamHandler(sys.stdout)
        h.setLevel(getattr(logging, level.upper(), logging.INFO))
        h.setFormatter(formatter)
        root.addHandler(h)

    _root_configured = True


# ============================================================================
# get_logger：获取组件 logger（首次调用自动创建组件文件 handler）
# ============================================================================
def get_logger(component: str) -> logging.Logger:
    """获取 agent.{component} logger。

    首次调用时创建组件文件 handler（如启用），后续调用直接返回缓存。
    propagate=True，日志自动冒泡到根 logger 写入总文件 + 告警 + stdout。
    """
    logger_name = f"agent.{component}"

    if logger_name in _component_loggers:
        return _component_loggers[logger_name]

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)  # 子 logger 不限级别，由 handler 和根 logger 控制

    # 添加组件文件 handler
    if _config.get("enable_component_files", True) and _config.get("log_dir"):
        formatter = JsonFormatter()
        h = RotatingFileHandler(
            os.path.join(_config["log_dir"], f"{component}.log"),
            maxBytes=_config.get("max_bytes", 10 * 1024 * 1024),
            backupCount=_config.get("backup_count", 5),
            encoding="utf-8",
        )
        h.setLevel(logging.DEBUG)
        h.setFormatter(formatter)
        logger.addHandler(h)

    logger.propagate = True  # 冒泡到 agent 根 logger
    _component_loggers[logger_name] = logger
    return logger


# ============================================================================
# 便捷封装：从 config 加载并 setup
# ============================================================================
def setup_from_config(module: str = "agent") -> None:
    """从 ConfigLoader 读取 logging 配置并调用 setup_logging。

    前提：已 load_config(module, ...) 加载过 agent 配置。
    """
    try:
        from config import get_config
        cfg = get_config(module, "logging")
        setup_logging(
            level=cfg.get("level", "INFO"),
            log_dir=cfg.get("log_dir", "logs"),
            enable_total_file=cfg.get("enable_total_file", True),
            enable_component_files=cfg.get("enable_component_files", True),
            enable_warning_file=cfg.get("enable_warning_file", True),
            enable_console=cfg.get("enable_console", True),
            max_bytes=cfg.get("max_bytes", 10 * 1024 * 1024),
            backup_count=cfg.get("backup_count", 5),
        )
    except Exception:
        # 配置未加载时用默认值
        setup_logging()
```

---

## 四、验收标准（12 条，全通过方可进入下一阶段）

### 4.1 trace_id（T1-T2）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T1 | generate_trace_id 格式符合 毫秒_UUID4 | 正则匹配 `^\d{13}_[0-9a-f]{32}$`，生成 1000 个无重复 |
| T2 | 字符串排序与时间戳排序一致 | 1000 个 ID 按字符串排序后，时间戳部分递增 |

### 4.2 日志格式（T3-T5）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T3 | 每条日志为单行合法 JSON | `json.loads()` 解析成功；包含 ts / level / logger / trace_id / event 字段 |
| T4 | extra 传 trace_id 时正确输出 | 日志 JSON 中 trace_id 等于传入值 |
| T5 | extra 未传 trace_id 时填 null | 日志 JSON 中 trace_id 为 null，不报 KeyError |

### 4.3 日志输出目标（T6-T9）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T6 | 总文件 logs/agent.log 写入所有组件日志 | event_bus + router 两个组件各发一条 → agent.log 有 2 条 |
| T7 | 组件文件 logs/{component}.log 仅写自己 | event_bus.log 只有 event_bus 的日志；router.log 只有 router 的 |
| T8 | 告警文件 logs/warnings.log 仅写 WARNING+ | WARNING 和 ERROR 写入；INFO / DEBUG 不写入 |
| T9 | stdout 输出日志 | 控制台捕获到 JSON 格式日志 |

### 4.4 配置与轮转（T10-T12）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T10 | 从 agent.default.json 读取 logging 配置 | setup_from_config() 后日志级别 / 目录等与配置文件一致 |
| T11 | 文件轮转：超过 max_bytes 时生成备份 | 写入超过 max_bytes 的日志后，生成 agent.log.1 等备份文件 |
| T12 | get_logger 幂等：重复调用不重复添加 handler | 调 3 次 get_logger("event_bus") → logger.handlers 长度不变 |

---

## 五、结论：所有设计点已确认

| 讨论项 | 最终结论（已确认） |
|---|---|
| **trace_id 生成时机** | 用户输入新对话时生成 |
| **trace_id 格式** | `毫秒_UUID4无横杠`（与 EventBus 的 event_id 同格式） |
| **trace_id 传递方式** | 全程显式传参，不用 contextvar |
| **tracing.py** | 保留，放 `generate_trace_id()` |
| **日志格式** | 单行 JSON |
| **日志输出目标** | 三层：总文件 `agent.log` + 组件文件 `{component}.log` + 告警文件 `warnings.log` + stdout |
| **日志层级传播** | 子 logger `propagate=True`，冒泡到根 logger 写入总文件 + 告警 + stdout |
| **配置来源** | 从 `agent.default.json` 的 `logging` 段读取（level / log_dir / 各开关 / 轮转参数） |
| **文件轮转** | `RotatingFileHandler`，max_bytes + backup_count 可配 |
| **Logger 命名** | 统一前缀 `agent.`，组件名取目录名 |
| **get_logger 行为** | 首次创建组件文件 handler + 缓存；重复调用幂等 |
| **EventBus debug print** | 暂不改动 |
| **contextvar** | 不用 |
| **文件结构** | `agent/observability/`（tracing.py + logging_setup.py + \_\_init\_\_.py） |
