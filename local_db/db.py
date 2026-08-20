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
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO conversations (user_id, session_id, conversation_id, trace_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, session_id, conversation_id, trace_id, title, now, now),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_conversations(self, session_id: str) -> list[dict[str, Any]]:
        """按 session_id 加载所有对话，按 updated_at 降序。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM conversations WHERE session_id=? ORDER BY updated_at DESC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_conversation_title(self, conversation_id: str, title: str) -> None:
        """更新对话标题。"""
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE conversation_id=?",
                (title, _now(), conversation_id),
            )
            self._conn.commit()

    def touch_conversation(self, conversation_id: str) -> None:
        """更新对话的 updated_at（新消息时调用）。"""
        with self._lock:
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
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages (conversation_id, trace_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, trace_id, role, content, now),
            )
            # 更新对话的 updated_at
            self._conn.execute(
                "UPDATE conversations SET updated_at=? WHERE conversation_id=?",
                (now, conversation_id),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """按 conversation_id 加载所有消息，按时间升序。"""
        with self._lock:
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
        with self._lock:
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
        with self._lock:
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
        with self._lock:
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


# ============================================================================
# 测试入口
# ============================================================================
if __name__ == "__main__":
    """验收测试 T1-T15"""
    import os as _os
    import sys as _sys
    import shutil
    import tempfile
    import threading

    _ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _ROOT not in _sys.path:
        _sys.path.insert(0, _ROOT)

    from local_db.db import LocalDB

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

    tmpdir = tempfile.mkdtemp()
    db_path = _os.path.join(tmpdir, "test.db")

    # ===== T1-T4: 基础结构 =====
    print("=== T1-T4: 基础结构 ===")

    db = LocalDB(db_path=db_path)

    # T1: db 文件存在
    check("T1", _os.path.exists(db_path), "db 文件不存在")

    # T2: 3 张表 + 4 个索引存在
    tables = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {r[0] for r in tables}
    check("T2a", "conversations" in table_names, f"缺少 conversations 表: {table_names}")
    check("T2b", "messages" in table_names, f"缺少 messages 表: {table_names}")
    check("T2c", "records" in table_names, f"缺少 records 表: {table_names}")

    indexes = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    index_names = {r[0] for r in indexes}
    check("T2d", "idx_conv_session" in index_names)
    check("T2e", "idx_conv_cid" in index_names)
    check("T2f", "idx_msg_cid" in index_names)
    check("T2g", "idx_rec_cid" in index_names)
    check("T2h", "idx_rec_cid_type" in index_names)

    # T3: WAL 模式
    mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
    check("T3", mode.lower() == "wal", f"journal_mode={mode}")

    # T4: 重复构造不报错
    db2 = LocalDB(db_path=db_path)
    check("T4", True, "重复构造报错")
    db2.close()

    # ===== T5-T7: conversations =====
    print("=== T5-T7: conversations ===")

    # 先清空表（前面 T4 创建了第二个连接，关掉后继续用第一个）
    db._conn.execute("DELETE FROM conversations")
    db._conn.execute("DELETE FROM messages")
    db._conn.execute("DELETE FROM records")
    db._conn.execute("DELETE FROM sqlite_sequence")
    db._conn.commit()

    cid1 = db.save_conversation("u1", "s1", "conv-1", "trace-1", title="对话1")
    cid2 = db.save_conversation("u1", "s1", "conv-2", "trace-2", title="对话2")
    check("T5", cid1 == 1 and cid2 == 2, f"自增 id 应为 1,2，实际 {cid1},{cid2}")

    # T6: get_conversations 按 updated_at 降序
    # conv-2 后创建，应该排前面
    convs = db.get_conversations("s1")
    check("T6", len(convs) == 2 and convs[0]["conversation_id"] == "conv-2",
          f"排序错误: {[c['conversation_id'] for c in convs]}")

    # T7: update_conversation_title
    db.update_conversation_title("conv-1", "新标题")
    convs2 = db.get_conversations("s1")
    conv1 = [c for c in convs2 if c["conversation_id"] == "conv-1"][0]
    check("T7", conv1["title"] == "新标题", f"title={conv1['title']}")

    # ===== T8-T10: messages =====
    print("=== T8-T10: messages ===")

    # 记录 touch 前的 updated_at
    conv_before = [c for c in db.get_conversations("s1") if c["conversation_id"] == "conv-1"][0]
    old_updated = conv_before["updated_at"]

    # sleep 10ms 确保 updated_at 变化
    import time as _time
    _time.sleep(0.02)

    mid1 = db.save_message("conv-1", "trace-1", "user", "你好")
    _time.sleep(0.02)
    mid2 = db.save_message("conv-1", "trace-1", "assistant", "你好！有什么可以帮你的？")

    # T8: role 正确
    msgs = db.get_messages("conv-1")
    check("T8a", len(msgs) == 2, f"应有 2 条消息，实际 {len(msgs)}")
    check("T8b", msgs[0]["role"] == "user" and msgs[1]["role"] == "assistant",
          f"role 不匹配: {[m['role'] for m in msgs]}")

    # T9: 按 created_at 升序
    check("T9", msgs[0]["created_at"] <= msgs[1]["created_at"],
          f"时间顺序错误: {msgs[0]['created_at']} vs {msgs[1]['created_at']}")

    # T10: save_message 自动 touch_conversation
    conv_after = [c for c in db.get_conversations("s1") if c["conversation_id"] == "conv-1"][0]
    check("T10", conv_after["updated_at"] > old_updated,
          f"updated_at 未更新: {old_updated} -> {conv_after['updated_at']}")

    # ===== T11-T14: records =====
    print("=== T11-T14: records ===")

    # T11: 存 dict
    db.save_record("conv-1", "trace-1", "thought", object_id="obj-1",
                   data={"text": "思考中...", "step": 1})
    records = db.get_records("conv-1")
    thought = [r for r in records if r["type"] == "thought"][0]
    check("T11", thought["data"] == {"text": "思考中...", "step": 1},
          f"data 不匹配: {thought['data']}")

    # T12: 存 list
    db.save_record("conv-1", "trace-1", "result", object_id="obj-1",
                   data=[1, 2, 3, "四"])
    records = db.get_records("conv-1")
    result_rec = [r for r in records if r["type"] == "result"][0]
    check("T12", result_rec["data"] == [1, 2, 3, "四"],
          f"data 不匹配: {result_rec['data']}")

    # T13: get_records 全量加载
    db.save_record("conv-1", "trace-1", "artifact", data={"file": "output.txt"})
    db.save_record("conv-1", "trace-1", "context", data="压缩后的上下文")
    all_records = db.get_records("conv-1")
    check("T13a", len(all_records) == 4, f"应有 4 条记录，实际 {len(all_records)}")
    types = [r["type"] for r in all_records]
    check("T13b", types == ["thought", "result", "artifact", "context"],
          f"类型顺序错误: {types}")

    # T14: get_records_by_type
    thoughts = db.get_records_by_type("conv-1", "thought")
    check("T14a", len(thoughts) == 1 and thoughts[0]["type"] == "thought")
    artifacts = db.get_records_by_type("conv-1", "artifact")
    check("T14b", len(artifacts) == 1 and artifacts[0]["type"] == "artifact")
    check("T14c", artifacts[0]["data"] == {"file": "output.txt"})

    # ===== T15: 线程安全 =====
    print("=== T15: 线程安全 ===")

    # 用独立 db 文件测试并发
    db_path2 = _os.path.join(tmpdir, "thread_test.db")
    db3 = LocalDB(db_path=db_path2)
    db3.save_conversation("u2", "s2", "conv-t", "trace-t")

    errors = []
    def worker(tid):
        try:
            for i in range(10):
                db3.save_message("conv-t", f"trace-t-{tid}", "user", f"msg-{tid}-{i}")
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_msgs = len(db3.get_messages("conv-t"))
    check("T15a", len(errors) == 0, f"线程报错: {errors}")
    check("T15b", total_msgs == 100, f"应有 100 条消息，实际 {total_msgs}")

    db3.close()

    # ===== 清理 =====
    db.close()
    shutil.rmtree(tmpdir, ignore_errors=True)

    # ===== 汇总 =====
    print(f"\n{'='*40}")
    print(f"总计: {passed} PASS, {failed} FAIL")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
        _sys.exit(1)
