# 服务器主程序
# 后端框架：FastAPI，提供 WebSocket 端点和 HTTP 静态服务
# ASGI 服务器：Uvicorn，运行 FastAPI 应用，纯 Python
# 通信：WebSocket + JSON，结构清晰，支持流式

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import (
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# ---------------------------------------------------------------------------
# 配置加载（server_main.default.json → server_main.json 覆盖 → 环境变量覆盖）
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DEFAULT_CONFIG_PATH = os.path.join(CONFIG_DIR, "server_main.default.json")
USER_CONFIG_PATH = os.path.join(CONFIG_DIR, "server_main.json")


def _read_json(path: str) -> dict[str, Any]:
    """读取单个 JSON 文件，失败时返回空字典并打印警告。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        import sys
        print(f"[server_main] 读取配置失败 {path}: {exc}", file=sys.stderr)
        return {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并 override 到 base：dict 深合并，其他类型直接覆盖。"""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _load_config(default_path: str, user_path: str) -> dict[str, Any]:
    """先读 default 配置，再用 user 配置递归覆盖。"""
    config = _read_json(default_path)
    config = _deep_merge(config, _read_json(user_path))
    return config


CONFIG = _load_config(DEFAULT_CONFIG_PATH, USER_CONFIG_PATH)

# 日志（使用加载后的配置）
logging.basicConfig(
    level=CONFIG["logging"]["level"],
    format=CONFIG["logging"]["format"],
)
logger = logging.getLogger("server_main")

# 便捷配置变量（环境变量优先级最高，用于容器/CI 场景覆盖）
DEFAULT_HOST = os.getenv("SERVER_HOST", CONFIG["server"]["host"])
DEFAULT_PORT = int(os.getenv("SERVER_PORT", str(CONFIG["server"]["port"])))
RELOAD = bool(os.getenv("SERVER_RELOAD", str(CONFIG["server"]["reload"]).lower()) in ("1", "true", "yes"))
LOG_LEVEL = os.getenv("SERVER_LOG_LEVEL", CONFIG["server"]["log_level"])
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    ",".join(CONFIG["cors"]["allowed_origins"]),
).split(",")
ALLOW_CREDENTIALS = CONFIG["cors"]["allow_credentials"]
ALLOW_METHODS = CONFIG["cors"]["allow_methods"]
ALLOW_HEADERS = CONFIG["cors"]["allow_headers"]
STATIC_DIR = os.path.join(BASE_DIR, CONFIG["paths"]["static_dir"])

logger.info("已加载配置 | default=%s | user=%s | host=%s | port=%d | static=%s",
            DEFAULT_CONFIG_PATH, USER_CONFIG_PATH, DEFAULT_HOST, DEFAULT_PORT, STATIC_DIR)


# ---------------------------------------------------------------------------
# WebSocket 连接管理器
# ---------------------------------------------------------------------------
@dataclass
class ConnectionManager:
    """管理所有活跃的 WebSocket 连接，支持单播、广播、流式发送。"""

    connections: dict[str, WebSocket] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def connect(self, websocket: WebSocket) -> str:
        """接收连接并返回分配的 client_id。"""
        await websocket.accept()
        client_id = uuid.uuid4().hex[:12]
        async with self._lock:
            self.connections[client_id] = websocket
        logger.info("WebSocket 连接已建立 | client_id=%s | 在线=%d",
                    client_id, len(self.connections))
        await self._send_json(
            websocket,
            {"type": "system", "event": "connected", "client_id": client_id},
        )
        return client_id

    async def disconnect(self, client_id: str) -> None:
        async with self._lock:
            ws = self.connections.pop(client_id, None)
        if ws is not None:
            logger.info("WebSocket 连接已关闭 | client_id=%s | 在线=%d",
                        client_id, len(self.connections))

    async def send_to(self, client_id: str, message: dict[str, Any]) -> bool:
        ws = self.connections.get(client_id)
        if ws is None:
            return False
        await self._send_json(ws, message)
        return True

    async def broadcast(self, message: dict[str, Any]) -> int:
        """广播到所有在线客户端，返回成功发送的数量。"""
        count = 0
        async with self._lock:
            targets = list(self.connections.items())
        for cid, ws in targets:
            try:
                await self._send_json(ws, message)
                count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("广播失败，移除 client_id=%s | %s", cid, exc)
                async with self._lock:
                    self.connections.pop(cid, None)
        return count

    async def stream_to(
        self,
        client_id: str,
        chunks: list[str],
        *,
        message_id: str | None = None,
        delay: float = 0.0,
    ) -> bool:
        """流式发送多个文本片段（{type:'stream' ...}），末尾发送 stream_end。"""
        ws = self.connections.get(client_id)
        if ws is None:
            return False
        mid = message_id or uuid.uuid4().hex[:8]
        for idx, chunk in enumerate(chunks):
            await self._send_json(ws, {
                "type": "stream",
                "message_id": mid,
                "index": idx,
                "total": len(chunks),
                "content": chunk,
            })
            if delay:
                await asyncio.sleep(delay)
        await self._send_json(ws, {
            "type": "stream_end",
            "message_id": mid,
            "chunks": len(chunks),
        })
        return True

    @property
    def client_count(self) -> int:
        return len(self.connections)

    @staticmethod
    async def _send_json(ws: WebSocket, payload: dict[str, Any]) -> None:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))


# 全局单例（通过 lifespan 注入到 app.state）
manager = ConnectionManager()


# ---------------------------------------------------------------------------
# 应用生命周期
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：准备共享资源
    start_ts = time.time()
    app.state.manager = manager
    app.state.started_at = start_ts
    os.makedirs(STATIC_DIR, exist_ok=True)
    logger.info("FastAPI 应用启动 | 静态目录=%s | 端口=%d", STATIC_DIR, DEFAULT_PORT)
    yield
    # 关闭：清理连接
    logger.info("FastAPI 应用关闭中 | 当前在线=%d", manager.client_count)
    async with manager._lock:  # noqa: SLF001
        for cid, ws in list(manager.connections.items()):
            try:
                await ws.close(code=status.WS_1001_GOING_AWAY)
            except Exception:  # noqa: BLE001
                pass
        manager.connections.clear()
    logger.info("FastAPI 应用已关闭 | 耗时=%.2fs", time.time() - start_ts)


# ---------------------------------------------------------------------------
# FastAPI 应用实例
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Main Server",
    description="FastAPI + WebSocket + 静态文件 服务骨架",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=ALLOW_METHODS,
    allow_headers=ALLOW_HEADERS,
)


# ---------------------------------------------------------------------------
# HTTP 路由（必须在静态文件挂载之前定义）
# ---------------------------------------------------------------------------
@app.get("/api/health", tags=["system"], summary="健康检查")
async def health_check() -> dict[str, Any]:
    uptime = time.time() - app.state.started_at
    return {
        "status": "ok",
        "uptime_seconds": round(uptime, 2),
        "online_clients": manager.client_count,
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/api/clients", tags=["system"], summary="当前在线客户端列表")
async def list_clients() -> dict[str, Any]:
    async with manager._lock:  # noqa: SLF001
        client_ids = list(manager.connections.keys())
    return {
        "count": len(client_ids),
        "client_ids": client_ids,
    }


@app.post("/api/broadcast", tags=["message"], summary="广播消息到所有在线客户端")
async def broadcast_message(payload: dict[str, Any]) -> dict[str, Any]:
    if "type" not in payload:
        payload["type"] = "broadcast"
    sent = await manager.broadcast(payload)
    return {"ok": True, "sent_count": sent}


@app.post("/api/send/{client_id}", tags=["message"], summary="向指定客户端发送消息")
async def send_to_client(client_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if "type" not in payload:
        payload["type"] = "message"
    ok = await manager.send_to(client_id, payload)
    if not ok:
        raise HTTPException(status_code=404, detail=f"client_id {client_id!r} 不在线")
    return {"ok": True, "client_id": client_id}


# ---------------------------------------------------------------------------
# WebSocket 端点
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket 主端点。

    客户端发送消息格式（JSON）：
      - {"action": "ping"}                              → 回复 pong
      - {"action": "echo", "data": {...}}               → 回显 data
      - {"action": "broadcast", "data": {...}}          → 广播给所有客户端
      - {"action": "send", "to": "<id>", "data": {...}} → 发给指定客户端
      - {"action": "stream_demo", "count": 5}           → 给自己推送流式示例
    """
    client_id = await manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_to(client_id, {
                    "type": "error",
                    "error": "invalid_json",
                    "detail": "消息必须是合法 JSON",
                })
                continue

            action = msg.get("action")

            if action == "ping":
                await manager.send_to(client_id, {
                    "type": "pong",
                    "server_time": time.strftime("%H:%M:%S"),
                })

            elif action == "echo":
                await manager.send_to(client_id, {
                    "type": "echo",
                    "data": msg.get("data"),
                })

            elif action == "broadcast":
                data = msg.get("data", {})
                await manager.broadcast({
                    "type": "broadcast",
                    "from": client_id,
                    "data": data,
                })

            elif action == "send":
                target = msg.get("to")
                data = msg.get("data", {})
                if not target or target not in manager.connections:
                    await manager.send_to(client_id, {
                        "type": "error",
                        "error": "target_not_found",
                        "detail": f"client_id {target!r} 不在线",
                    })
                    continue
                await manager.send_to(target, {
                    "type": "message",
                    "from": client_id,
                    "data": data,
                })

            elif action == "stream_demo":
                count = max(1, min(int(msg.get("count", 5)), 50))
                chunks = [f"[chunk-{i + 1}] Hello from server 🌊" for i in range(count)]
                await manager.stream_to(client_id, chunks, delay=0.1)

            else:
                await manager.send_to(client_id, {
                    "type": "error",
                    "error": "unknown_action",
                    "detail": f"不支持的 action: {action}",
                })

    except WebSocketDisconnect:
        await manager.disconnect(client_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("WebSocket 异常 | client_id=%s", client_id)
        await manager.send_to(client_id, {
            "type": "error",
            "error": "server_error",
            "detail": str(exc),
        })
        await manager.disconnect(client_id)


# ---------------------------------------------------------------------------
# 静态文件服务（最后挂载，避免覆盖 API 路由）
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.isfile(index_path):
        return JSONResponse(
            status_code=404,
            content={"detail": "static/index.html 不存在，可在静态目录下放置前端资源"},
        )
    return FileResponse(index_path)


app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR, check_dir=False),
    name="static",
)


# ---------------------------------------------------------------------------
# 直接运行入口
# ---------------------------------------------------------------------------
def main() -> None:
    uvicorn.run(
        "server_main:app",
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        reload=RELOAD,
        log_level=LOG_LEVEL,
    )


if __name__ == "__main__":
    main()
