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
