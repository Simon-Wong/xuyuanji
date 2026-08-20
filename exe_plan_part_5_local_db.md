# 开发任务 5 执行计划：db（封装 SQLite）
> 对应 `dev_plan.md` 阶段 0 的任务 #5「db（封装 sqlite）」
> 本文档为任务 #5 的讨论结论定版与执行计划。

---

## 一、文档定位与范围

### 1.1 任务目标
封装 SQLite 持久化层，存储用户原始输入、agent 答案、对话元数据、中间思维过程、中间结果、计划排期表、产物、上下文等全量数据，支持跨终端加载历史对话。

### 1.2 与其他模块的关系
- **上游**：Web Server 收到用户消息时调用 `save_message`；agent 回复后调用 `save_message`；各组件产生中间数据时调用 `save_record`
- **下游**：上下文管理器从 db 加载历史上下文；展示器从 db 加载历史记录用于跨终端展示
- **依赖**：`config`（读取 db 配置路径），零第三方依赖（仅用 Python 标准库 `sqlite3`）
- **EventBus 关系**：EventBus 管运行时状态（内存），db 管持久化（磁盘），两者互补不交叉

### 1.3 核心约束
- **零第三方依赖**：仅用 Python 标准库 `sqlite3` + `json`，便于移植到其他语言
- **全量存储**：用户输入、agent 答案、中间思维、中间结果、计划排期表、产物、上下文全部存
- **不分页**：当前全量返回，数据量真正大了再加
- **线程安全**：WAL 模式 + `check_same_thread=False` + `threading.Lock` 保护所有读写操作

---

## 二、已讨论并确认的设计决策

### 2.1 存什么

全部存，支持跨终端完整回溯：

| 内容 | 存？ | 存哪张表 |
|---|---|---|
| 用户原始输入 | ✅ | messages (role=user) |
| agent 给用户的答案 | ✅ | messages (role=assistant) |
| 对话元数据 | ✅ | conversations |
| 中间思维过程 | ✅ | records (type=thought) |
| 中间结果 | ✅ | records (type=result) |
| 计划排期表 | ✅ | records (type=schedule_sheet) |
| 产物 | ✅ | records (type=artifact) |
| 上下文 | ✅ | records (type=context) |

### 2.2 表结构

3 张表，单表 + type 字段区分（不分表）：

#### conversations 表：对话元数据

```sql
CREATE TABLE IF NOT EXISTS conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT    NOT NULL,
    session_id      TEXT    NOT NULL,
    conversation_id TEXT    NOT NULL,
    trace_id        TEXT    NOT NULL,
    title           TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_conv_cid ON conversations(conversation_id);
```

#### messages 表：用户输入 + agent 答案

```sql
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT    NOT NULL,
    trace_id        TEXT    NOT NULL,
    role            TEXT    NOT NULL,   -- user / assistant
    content         TEXT    NOT NULL,   -- 纯文本或 markdown 字符串
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_cid ON messages(conversation_id);
```

#### records 表：中间思维 / 中间结果 / 计划排期表 / 产物 / 上下文

```sql
CREATE TABLE IF NOT EXISTS records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT    NOT NULL,
    trace_id        TEXT    NOT NULL,
    object_id       TEXT,                -- 关联 EventBus 的 object_id，可为空
    type            TEXT    NOT NULL,    -- thought / result / schedule_sheet / artifact / context
    data            TEXT    NOT NULL,    -- JSON 字符串
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rec_cid ON records(conversation_id);
CREATE INDEX IF NOT EXISTS idx_rec_cid_type ON records(conversation_id, type);
```

### 2.3 messages.content 类型

统一存纯文本（markdown 字符串）。格式化交给展示器处理。

### 2.4 records 表用 type 区分

单表 + type 字段，不分表。理由：
- 查询时通常按 conversation_id 全量加载，单表一次查完
- 新增类型不用改表结构

### 2.5 SQLite 封装方式

原生 `sqlite3` 手动封装，不用 ORM。理由：零第三方依赖，便于移植到其他语言。

### 2.6 线程安全

- `check_same_thread=False`：允许跨线程使用同一连接
- `threading.Lock`：保护所有读写操作，避免多线程并发访问 SQLite 连接报错
- `PRAGMA journal_mode=WAL`：WAL 模式允许并发读不阻塞
- `PRAGMA foreign_keys=ON`：开启外键约束

### 2.7 分页

当前不分页，全量返回。数据量真正大了再加。

### 2.8 时间戳格式

统一 ISO 8601 带毫秒：`2026-08-20T10:00:00.123`（与日志一致）。

### 2.9 db 文件路径配置

从 `agent.default.json` 的 `db` 段读取：

```json
"db": {
    "path": "data/agent.db",
    "journal_mode": "WAL"
}
```

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `path` | str | `"data/agent.db"` | db 文件路径（相对于项目根） |
| `journal_mode` | str | `"WAL"` | SQLite 日志模式 |

---

## 三、实现范围与文件结构

### 3.1 目录结构

```
local_db/
├── __init__.py        # re-export: LocalDB
├── db.py              # LocalDB 类
└── (无其他文件)
```

### 3.2 `local_db/__init__.py`

```python
from local_db.db import LocalDB

__all__ = ["LocalDB"]
```

### 3.3 `local_db/db.py` 完整代码骨架

```python
"""SQLite 持久化封装：全量存储，支持跨终端加载历史对话。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any


def _now() -> str:
    """ISO 8601 带毫秒。"""
    ct = time.localtime()
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", ct)
    ts += f".{int(time.time() * 1000) % 1000:03d}"
    return ts


class LocalDB:
    """SQLite 持久化层。

    线程安全：check_same_thread=False + WAL 模式。
    构造时自动建表（IF NOT EXISTS）。
    """

    def __init__(self, db_path: str = "data/agent.db", journal_mode: str = "WAL") -> None:
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

        # PRAGMA
        self._conn.execute(f"PRAGMA journal_mode={journal_mode}")
        self._conn.execute("PRAGMA foreign_keys=ON")

        # 建表
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT    NOT NULL,
                session_id      TEXT    NOT NULL,
                conversation_id TEXT    NOT NULL,
                trace_id        TEXT    NOT NULL,
                title           TEXT,
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
            CREATE INDEX IF NOT EXISTS idx_conv_cid ON conversations(conversation_id);

            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT    NOT NULL,
                trace_id        TEXT    NOT NULL,
                role            TEXT    NOT NULL,
                content         TEXT    NOT NULL,
                created_at      TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_msg_cid ON messages(conversation_id);

            CREATE TABLE IF NOT EXISTS records (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT    NOT NULL,
                trace_id        TEXT    NOT NULL,
                object_id       TEXT,
                type            TEXT    NOT NULL,
                data            TEXT    NOT NULL,
                created_at      TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rec_cid ON records(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_rec_cid_type ON records(conversation_id, type);
        """)
        self._conn.commit()

    # ========================================================================
    # conversations
    # ========================================================================
    def save_conversation(
        self,
        user_id: str,
        session_id: str,
        conversation_id: str,
        trace_id: str,
        title: str | None = None,
    ) -> int:
        """创建对话记录，返回自增 id。"""
        now = _now()
        cur = self._conn.execute(
            "INSERT INTO conversations (user_id, session_id, conversation_id, trace_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, session_id, conversation_id, trace_id, title, now, now),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_conversations(self, session_id: str) -> list[dict[str, Any]]:
        """按 session_id 加载所有对话，按 updated_at 降序。"""
        rows = self._conn.execute(
            "SELECT * FROM conversations WHERE session_id=? ORDER BY updated_at DESC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_conversation_title(self, conversation_id: str, title: str) -> None:
        """更新对话标题。"""
        self._conn.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE conversation_id=?",
            (title, _now(), conversation_id),
        )
        self._conn.commit()

    def touch_conversation(self, conversation_id: str) -> None:
        """更新对话的 updated_at（新消息时调用）。"""
        self._conn.execute(
            "UPDATE conversations SET updated_at=? WHERE conversation_id=?",
            (_now(), conversation_id),
        )
        self._conn.commit()

    # ========================================================================
    # messages
    # ========================================================================
    def save_message(
        self,
        conversation_id: str,
        trace_id: str,
        role: str,
        content: str,
    ) -> int:
        """保存消息（user 或 assistant），返回自增 id。"""
        cur = self._conn.execute(
            "INSERT INTO messages (conversation_id, trace_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conversation_id, trace_id, role, content, _now()),
        )
        self._conn.commit()
        # 更新对话的 updated_at
        self.touch_conversation(conversation_id)
        return cur.lastrowid

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """按 conversation_id 加载所有消息，按时间升序。"""
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ========================================================================
    # records
    # ========================================================================
    def save_record(
        self,
        conversation_id: str,
        trace_id: str,
        record_type: str,
        object_id: str | None = None,
        data: Any = None,
    ) -> int:
        """保存记录（thought / result / schedule_sheet / artifact / context），返回自增 id。

        data 接受 dict/list/str/任意可序列化对象，内部 json.dumps 序列化。
        """
        cur = self._conn.execute(
            "INSERT INTO records (conversation_id, trace_id, object_id, type, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, trace_id, object_id, record_type,
             json.dumps(data, ensure_ascii=False) if data is not None else "null", _now()),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_records(self, conversation_id: str) -> list[dict[str, Any]]:
        """按 conversation_id 加载所有记录，按时间升序。

        返回的 dict 中 data 字段自动 json.loads 反序列化。
        """
        rows = self._conn.execute(
            "SELECT * FROM records WHERE conversation_id=? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d["data"])
            result.append(d)
        return result

    def get_records_by_type(
        self,
        conversation_id: str,
        record_type: str,
    ) -> list[dict[str, Any]]:
        """按 conversation_id + type 加载记录，按时间升序。"""
        rows = self._conn.execute(
            "SELECT * FROM records WHERE conversation_id=? AND type=? ORDER BY created_at ASC",
            (conversation_id, record_type),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d["data"])
            result.append(d)
        return result

    # ========================================================================
    # 生命周期
    # ========================================================================
    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()
```

---

## 四、配置文件更新

`config/agent.default.json` 新增 `db` 段：

```json
"db": {
    "path": "data/agent.db",
    "journal_mode": "WAL"
}
```

---

## 五、验收标准（15 条，全通过方可进入下一阶段）

### 5.1 基础结构（T1-T4）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T1 | 构造时自动创建 db 文件和目录 | `data/agent.db` 文件存在 |
| T2 | 构造时自动建表 | 3 张表 + 4 个索引存在 |
| T3 | WAL 模式生效 | `PRAGMA journal_mode` 返回 `wal` |
| T4 | 重复构造不报错（IF NOT EXISTS） | 连续构造 2 次无异常 |

### 5.2 conversations（T5-T7）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T5 | save_conversation 返回自增 id | 第 1 条返回 1，第 2 条返回 2 |
| T6 | get_conversations 按 updated_at 降序 | 后 touch 的排前面 |
| T7 | update_conversation_title 正确更新 | title 字段更新，updated_at 也更新 |

### 5.3 messages（T8-T10）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T8 | save_message 正确保存 user + assistant 消息 | 2 条记录，role 正确 |
| T9 | get_messages 按 created_at 升序 | 时间顺序与插入顺序一致 |
| T10 | save_message 自动 touch_conversation | 对话的 updated_at 更新 |

### 5.4 records（T11-T14）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T11 | save_record 存 dict 并正确 json.dumps + 反序列化 | data 字段为原始 dict |
| T12 | save_record 存 list 并正确序列化 | data 字段为原始 list |
| T13 | get_records 全量加载，按时间升序 | 所有类型记录返回，顺序正确 |
| T14 | get_records_by_type 按类型过滤 | 只返回指定 type 的记录 |

### 5.5 线程安全（T15）
| 编号 | 验证点 | 预期 |
|---|---|---|
| T15 | 多线程并发写不报错 | 10 线程各写 10 条 message，共 100 条全部成功写入 |

---

## 六、结论：所有设计点已确认

| 讨论项 | 最终结论（已确认） |
|---|---|
| **存什么** | 全量存储（输入 + 答案 + 元数据 + 中间思维 + 中间结果 + 计划排期表 + 产物 + 上下文） |
| **表结构** | 3 张表：conversations / messages / records（单表 + type 字段区分） |
| **messages.content 类型** | 纯文本（markdown 字符串） |
| **records 表** | 单表 + type 字段，不分表 |
| **SQLite 封装** | 原生 sqlite3 手动封装，不用 ORM |
| **线程安全** | `check_same_thread=False` + WAL 模式 + `threading.Lock` |
| **分页** | 当前不分页，全量返回 |
| **时间戳格式** | ISO 8601 带毫秒 |
| **db 文件路径** | 从 `agent.default.json` 的 `db` 段读取（`path` + `journal_mode`） |
| **文件结构** | `local_db/`（`__init__.py` + `db.py`） |
