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


# ============================================================================
# 测试入口
# ============================================================================
if __name__ == "__main__":
    """验收测试 T1-T12"""
    import os as _os
    import sys as _sys
    _ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    if _ROOT not in _sys.path:
        _sys.path.insert(0, _ROOT)

    import re
    import shutil
    import tempfile

    passed = 0
    failed = 0

    def check(name, cond, msg=""):
        global passed, failed
        if cond:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name}  {msg}")

    # 重置模块状态（每个测试用独立临时目录）
    def reset_state():
        global _root_configured, _config, _component_loggers
        _root_configured = False
        _config = {}
        _component_loggers = {}
        # 清理已配置的 agent logger
        root = logging.getLogger("agent")
        root.handlers.clear()
        root.setLevel(logging.NOTSET)

    # ===== T1-T2: trace_id =====
    print("=== T1-T2: trace_id ===")

    from agent.observability.tracing import generate_trace_id

    ids = [generate_trace_id() for _ in range(1000)]
    pat = re.compile(r"^\d{13}_[0-9a-f]{32}$")
    check("T1a", all(pat.match(i) for i in ids), "正则不匹配")
    check("T1b", len(set(ids)) == 1000, "有重复")
    ts_list = [s.split("_")[0] for s in sorted(ids)]
    check("T2", ts_list == sorted(ts_list), "字符串排序与时间戳排序不一致")

    # ===== T3-T5: 日志格式 =====
    print("=== T3-T5: 日志格式 ===")

    tmpdir = tempfile.mkdtemp()
    reset_state()
    setup_logging(level="DEBUG", log_dir=tmpdir, enable_component_files=False)

    logger = get_logger("test_fmt")
    logger.info("event_published", extra={
        "trace_id": "trace-abc-123",
        "data": {"event_type": "更新"},
    })

    with open(os.path.join(tmpdir, "agent.log"), "r", encoding="utf-8") as f:
        line = f.readline()

    import json as json_mod
    log_obj = json_mod.loads(line)
    check("T3a", "ts" in log_obj and "level" in log_obj and "logger" in log_obj
                and "trace_id" in log_obj and "event" in log_obj, f"字段缺失: {log_obj}")
    check("T3b", log_obj["level"] == "INFO")
    check("T3c", log_obj["logger"] == "agent.test_fmt")
    check("T3d", log_obj["event"] == "event_published")
    check("T4", log_obj["trace_id"] == "trace-abc-123", f"trace_id 不匹配: {log_obj['trace_id']}")

    # T5: 未传 trace_id 时填 null
    logger.info("no_trace_event")
    with open(os.path.join(tmpdir, "agent.log"), "r", encoding="utf-8") as f:
        lines = f.readlines()
    log_obj2 = json_mod.loads(lines[-1])
    check("T5", log_obj2["trace_id"] is None, f"trace_id 应为 null: {log_obj2['trace_id']}")

    # ===== T6-T9: 日志输出目标 =====
    print("=== T6-T9: 日志输出目标 ===")

    tmpdir2 = tempfile.mkdtemp()
    reset_state()
    setup_logging(level="DEBUG", log_dir=tmpdir2, enable_component_files=True,
                  enable_warning_file=True, enable_console=True)

    logger_eb = get_logger("event_bus")
    logger_rt = get_logger("router")

    logger_eb.info("eb_event", extra={"trace_id": "t1"})
    logger_rt.info("rt_event", extra={"trace_id": "t2"})
    logger_eb.warning("eb_warn", extra={"trace_id": "t3"})
    logger_rt.error("rt_error", extra={"trace_id": "t4"})

    # T6: 总文件有所有日志
    with open(os.path.join(tmpdir2, "agent.log"), "r", encoding="utf-8") as f:
        total_lines = f.readlines()
    check("T6", len(total_lines) == 4, f"总文件应有 4 条，实际 {len(total_lines)}")

    # T7: 组件文件只有自己的
    with open(os.path.join(tmpdir2, "event_bus.log"), "r", encoding="utf-8") as f:
        eb_lines = f.readlines()
    eb_events = [json_mod.loads(l)["event"] for l in eb_lines]
    check("T7a", len(eb_lines) == 2, f"event_bus.log 应有 2 条，实际 {len(eb_lines)}")
    check("T7b", "eb_event" in eb_events and "eb_warn" in eb_events)

    with open(os.path.join(tmpdir2, "router.log"), "r", encoding="utf-8") as f:
        rt_lines = f.readlines()
    rt_events = [json_mod.loads(l)["event"] for l in rt_lines]
    check("T7c", len(rt_lines) == 2, f"router.log 应有 2 条，实际 {len(rt_lines)}")
    check("T7d", "rt_event" in rt_events and "rt_error" in rt_events)

    # T8: 告警文件只有 WARNING+
    with open(os.path.join(tmpdir2, "warnings.log"), "r", encoding="utf-8") as f:
        warn_lines = f.readlines()
    warn_levels = [json_mod.loads(l)["level"] for l in warn_lines]
    check("T8a", len(warn_lines) == 2, f"告警文件应有 2 条，实际 {len(warn_lines)}")
    check("T8b", all(l in ("WARNING", "ERROR", "CRITICAL") for l in warn_levels))

    # T9: stdout（用 capsys 不好做，检查 handler 存在即可）
    root_logger = logging.getLogger("agent")
    has_stream = any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
                     for h in root_logger.handlers)
    check("T9", has_stream, "根 logger 应有 stdout handler")

    # ===== T10: 从配置文件读取 =====
    print("=== T10: 从配置文件读取 ===")

    tmpdir3 = tempfile.mkdtemp()
    reset_state()

    # 模拟配置：直接调 setup_logging 验证参数传递
    setup_logging(level="WARNING", log_dir=tmpdir3, enable_component_files=False)
    root_logger3 = logging.getLogger("agent")
    check("T10a", root_logger3.level == logging.WARNING, f"level 应为 WARNING，实际 {root_logger3.level}")
    check("T10b", os.path.exists(os.path.join(tmpdir3, "agent.log")), "总文件应存在")
    check("T10c", os.path.exists(os.path.join(tmpdir3, "warnings.log")), "告警文件应存在")

    # ===== T11: 文件轮转 =====
    print("=== T11: 文件轮转 ===")

    tmpdir4 = tempfile.mkdtemp()
    reset_state()
    setup_logging(level="DEBUG", log_dir=tmpdir4, enable_component_files=False,
                  enable_warning_file=False, enable_console=False, max_bytes=200, backup_count=3)

    logger_r = get_logger("rot_test")
    # 不加组件文件，直接用根 logger
    root_r = logging.getLogger("agent")
    for i in range(50):
        root_r.info(f"event_{i:03d}", extra={"trace_id": f"t{i:03d}"})

    # 检查是否有备份文件
    files = os.listdir(tmpdir4)
    has_backup = any("agent.log." in f for f in files)
    check("T11", has_backup, f"应有轮转备份文件，实际文件: {files}")

    # ===== T12: get_logger 幂等 =====
    print("=== T12: get_logger 幂等 ===")

    tmpdir5 = tempfile.mkdtemp()
    reset_state()
    setup_logging(level="DEBUG", log_dir=tmpdir5, enable_component_files=True)

    l1 = get_logger("idempotent")
    handler_count_1 = len(l1.handlers)
    l2 = get_logger("idempotent")
    handler_count_2 = len(l2.handlers)
    l3 = get_logger("idempotent")
    handler_count_3 = len(l3.handlers)
    check("T12a", l1 is l2 is l3, "应返回同一对象")
    check("T12b", handler_count_1 == handler_count_2 == handler_count_3,
          f"handler 数量应不变: {handler_count_1} / {handler_count_2} / {handler_count_3}")

    # ===== 清理 =====
    reset_state()
    for d in [tmpdir, tmpdir2, tmpdir3, tmpdir4, tmpdir5]:
        shutil.rmtree(d, ignore_errors=True)

    # ===== 汇总 =====
    print(f"\n{'='*40}")
    print(f"总计: {passed} PASS, {failed} FAIL")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)
