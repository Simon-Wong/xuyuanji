# 开发计划文档 (Development Plan)

## 1. 现状评估

### 1.1 已有代码

| 模块 | 路径 | 状态 |
|------|------|------|
| Web Server | `main_body/server_main.py` | 已完成，FastAPI + WebSocket |
| 配置加载 | `main_body/config/` | 已完成，三层优先级加载 |
| MCP 原型 | `mcpex/` | 已验证，热插拔 + watchdog |
| Skill 原型 | `skillsex/`（外部仓库） | 已验证，SKILL.md + SkillRouter |
| 沙箱原型 | `sandboxex/`（外部仓库） | 已验证，四种 Docker 模式 |
| Agent 目录 | `agent/` | 空目录，待实现 |

### 1.2 待实现模块总览

根据 ar.md 架构设计，需要实现 13 个核心组件 + 事件总线 + 缓存机制，共 15 个开发单元。

---

## 2. 开发优先级（线性实现顺序）

按实现先后顺序排列，每完成一项再进入下一项。

### 优先级总览

```
【阶段 0: 基础设施层】（必须最先完成）
  1. 配置文件                     →  拆分多个配置文件，三层覆盖规则
  2. 核心数据结构
      ├─ 会话/对话/回合（Session/Conversation/Turn）→ 支持多端同步
      ├─ 计划排期表 ScheduleSheet
      ├─ 任务 Task / 任务组 TaskGroup
      ├─ 双状态机：任务状态机 + 任务组状态机
      ├─ 产物 Artifact（JSON 格式，便于读写）
      └─ 事件 Event / EventType
  3. 订阅发布（事件机制）          →  EventBus，组件间解耦通信
  4. 可观测性                     →  trace_id + 结构化日志，早点做，后续调试受益
  5. db（封装 sqlite）            →  上下文持久化、会话/对话状态保存
       ↓
【阶段 1: 入口链路】
  6. 上下文管理器
      ├─ 原始上下文
      ├─ 专用上下文
      ├─ 过滤
      ├─ 保存到 db
      └─ 从 db 加载
       ↓
【阶段 2: 简单路径闭环】（验证架构，快速得到一个能用的最小版本）
  7. 模型封装                     →  含缓存机制！解决 LLM 调试不确定性
  8. 路由器 - 模型选择器          →  先做简单路径部分
  9. 核验器                       →  先做轻量核验（简单路径）
  10. 汇报器
  11. 展示器
  12. debug_cli.py               →  命令行模拟输入，永久保留，便于 agent 独立调试
  → 里程碑 M1: 简单路径跑通，输入问题 → 返回回答
       ↓
【阶段 3: 能力层】
  13. 沙箱（工作区）               →  Docker 容器执行环境，任务组依赖它
  14. 能力管理器 - skill（本地函数）
  15. 能力管理器 - mcp
  16. 能力管理器 - rag
      ├─ 定义 RAGRetriever 抽象接口（Protocol），便于切换实现
      └─ SimpleLocalRAG 临时实现（纯关键词检索，零依赖，先跑通）
  17. 技能管理器
  → 里程碑 M2: 能力层就绪，模型可调用工具和技能
       ↓
【阶段 4: 复杂路径闭环】
  18. 路由器 - 模型规划器          →  生成计划排期表
  19. 计划排期表管理逻辑           →  结构已在 #2 定义，这里是增删改查/追踪
  20. 调度器
  21. 线程池                       →  任务组内并发执行
  22. 任务组
      ├─ 任务（Task 状态机）
      └─ 任务组（TaskGroup 状态机）
  → 里程碑 M3: 复杂路径跑通，多步骤任务可规划执行
       ↓
【阶段 5: 集成收尾】
  23. web server 集成              →  接入现有 main_body/server_main.py
                                      与 debug_cli.py 共用同一事件总线
  24. 用户交互协议                 →  暂停/恢复/确认/补充信息
  → 里程碑 M4: 端到端可用，用户通过浏览器访问
       ↓
【阶段 6: 增强功能（可选，按需补充）】
  25. 成本控制                     →  max_depth/max_tokens/max_tool_calls
  26. 上下文演化机
      ├─ 1. 喂养 RAG → 总结新 Skill、改进已有 Skill
      └─ 2. 扩充 MCP → 新增工具、优化 prompt、补充资料（利用热插拔）
  27. 录制/回放模式                →  完整执行录制与确定性回放
  28. RAG 完整实现                 →  FAISS / ChromaDB 向量库，替换 SimpleLocalRAG
  → 里程碑 M5: 生产就绪
```

### 选择理由

1. **可观测性提前到 #4**：trace_id 和日志规范早点建立，后续所有组件的调试都受益
2. **会话/对话数据结构放到阶段 0 #2**：多端同步设计提前卡位，后续不用大改
3. **双状态机放到阶段 0 #2**：任务状态机 + 任务组状态机，粒度区分更合理
4. **简单路径先闭环（#7-#12）**：完成后通过 debug_cli 即可验证架构
5. **debug_cli（#12）永久保留**：agent 与 Web Server 解耦，独立调试方便
6. **沙箱（#13）在任务组（#22）前面**：任务组的执行依赖沙箱环境
7. **RAG 抽象接口（#16）先行**：先定义 Protocol，上 SimpleLocalRAG 临时方案，阶段 6 再换
8. **产物统一用 JSON 格式**：便于写文件和读取
9. **上下文演化机（#26）目标明确**：不仅学习，还能自我扩充 Skill 和 MCP
10. **Web Server 和 CLI 共用事件总线**：Presenter 输出 → 同时支持终端打印和 WebSocket 推送

---

## 3. 各阶段详细任务

### 阶段 0: 基础设施层

#### 0.1 配置文件（#1）

**目标**：Agent 配置拆分为多个独立文件，三层覆盖

**任务清单**：

- [ ] 创建 `config/agent.default.json`（默认配置）
- [ ] 创建 `config/agent.json`（用户覆盖，可 gitignore）
- [ ] 实现配置加载逻辑：`default → user → 环境变量`
- [ ] 实现深合并（与 server_main 一致）

**配置结构**：

```json
// config/agent.default.json
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

**文件结构**：
```
agent/
├── config/
│   ├── __init__.py
│   └── loader.py          # 三层覆盖配置加载
```

**验证标准**：
- 无 agent.json 时使用 default
- agent.json 覆盖 default 中对应字段
- 环境变量优先级最高

---

#### 0.2 核心数据结构（#2）

**目标**：定义所有组件的公共数据类型

**任务清单**：

- [ ] 定义会话 / 对话 / 回合
  - Session: session_id, user_id, active_conversation_id, created_at
  - Conversation: conversation_id, session_id, status(ACTIVE/ENDED/PAUSED), turns[], trace_id
  - Turn: turn_id, conversation_id, user_input, schedule_id, artifact_ids[]
- [ ] 定义 `ScheduleSheet`（计划排期表）
  - schedule_id, trace_id, tasks, status
  - 成本控制：max_depth, max_tokens, max_tool_calls
- [ ] 定义 `Task` + 任务状态机
  - task_id, trace_id, parent_task_id, sub_schedule_id, description, status, input_context, assigned_model, started_at, finished_at
  - 状态：PENDING → RUNNING → COMPLETED / FAILED
- [ ] 定义 `TaskGroup` + 任务组状态机
  - group_id, schedule_id, tasks, status
  - 状态：IDLE → RUNNING → WAITING_USER → RUNNING → COMPLETED / FAILED
- [ ] 定义 `Artifact`（产物，JSON 格式）
  - artifact_id, task_id, content, files, tools_used, created_at
- [ ] 定义枚举类型
  - ScheduleStatus: PENDING / RUNNING / WAITING_USER / COMPLETED / VERIFIED / FAILED
  - TaskStatus: PENDING / RUNNING / COMPLETED / FAILED
  - GroupStatus: IDLE / RUNNING / WAITING_USER / COMPLETED / FAILED
  - ConversationStatus: ACTIVE / ENDED / PAUSED
- [ ] 定义 `Event`（事件）
  - event_type, trace_id, session_id, conversation_id, schedule_id, task_id, data, timestamp

**双状态机说明**：

```
任务状态机（单个 Task）：
  PENDING ──(开始)──→ RUNNING ──(完成)──→ COMPLETED
                          │
                          └──(失败)──→ FAILED

任务组状态机（TaskGroup）：
  IDLE ──(启动)──→ RUNNING ──(需用户)──→ WAITING_USER
                    ↑                      │
                    │(恢复)                 │(用户反馈)
                    └──────────────────────┘
                    │
                    └──(完成)──→ COMPLETED
                    │
                    └──(失败)──→ FAILED
```

**文件结构**：
```
agent/
├── __init__.py
├── models/
│   ├── __init__.py
│   ├── enums.py              # 所有枚举定义
│   ├── session.py            # Session / Conversation / Turn
│   ├── schedule.py           # ScheduleSheet / Task
│   ├── task_group.py         # TaskGroup + 双状态机
│   ├── artifact.py           # Artifact
│   └── event.py              # Event / EventType
```

**验证标准**：
- 所有数据结构可被正确序列化/反序列化（JSON）
- 枚举值与 ar.md 定义一致
- 双状态机可正确流转

---

#### 0.3 订阅发布 - 事件机制（#3）

**目标**：实现组件间解耦通信

**任务清单**：

- [ ] 定义所有事件类型常量
  - UserInput, ContextReady, ExecuteSimple, ScheduleReady, ScheduleApproved, ScheduleRejected
  - ArtifactReady, Verified, VerificationFailed, WaitingUser, UserResponse
  - ReportReady, TaskProgress, ConversationPaused, ConversationResumed
- [ ] 实现 `EventBus` 类
  - `publish(event)` 发布事件
  - `subscribe(event_type, handler)` 订阅事件
  - `unsubscribe(event_type, handler)` 取消订阅
- [ ] 支持同步和异步处理（被动触发组件同步，任务组异步）
- [ ] 事件按 trace_id 日志记录

**文件结构**：
```
agent/
├── core/
│   ├── __init__.py
│   ├── event_bus.py          # EventBus 实现
│   └── event_types.py        # 事件类型常量
```

**验证标准**：
- 发布一个事件，多个订阅者都能收到
- 事件按 trace_id 记录到日志

---

#### 0.4 可观测性（#4）

**目标**：trace_id 贯穿全链路，结构化日志

**任务清单**：

- [ ] 实现 trace_id 生成（UUID）
- [ ] 实现全局日志配置（logging 封装）
- [ ] 统一日志格式：时间 + 级别 + logger名 + trace_id + 事件 + 数据
- [ ] contextvar 传递 trace_id，无需手动传参

**日志格式**：

```python
import logging
logger = logging.getLogger(f"agent.{component_name}")

logger.info(
    "event_published",
    extra={
        "trace_id": trace_id,
        "event_type": event_type,
        "schedule_id": schedule_id,
    }
)
```

**文件结构**：
```
agent/
├── core/
│   ├── logging_setup.py      # 日志配置
│   └── tracing.py            # trace_id contextvar 管理
```

**验证标准**：
- 每轮对话有唯一 trace_id
- 同一 trace_id 的所有事件日志可串联

---

#### 0.5 db（封装 sqlite）（#5）

**目标**：SQLite 持久化层，封装增删改查

**任务清单**：

- [ ] 封装 SQLite 连接管理
- [ ] 建表脚本
  - sessions 表
  - conversations 表
  - turns 表
  - contexts 表
  - schedules 表（JSON 字段存储 schedule 快照）
  - artifacts 表
- [ ] 实现 CRUD 接口：insert / update / select / delete

**文件结构**：
```
agent/
├── store/
│   ├── __init__.py
│   ├── connection.py         # SQLite 连接管理
│   └── schema.py             # 建表 + CRUD 接口
```

**验证标准**：
- 重启后可恢复历史会话/对话/上下文/产物
- SQL 注入防护（使用参数化查询）

---

### 阶段 1: 入口链路

#### 1.1 上下文管理器（#6）

**目标**：接收用户输入，生成专用上下文

**任务清单**：

- [ ] 实现 `ContextManager` 类
  - `ingest(user_input, conversation_id)` 接收原始输入
  - `filter(messages)` 过滤噪声（口头禅、冗余）
  - `compress(messages)` 压缩上下文（减少 token）
  - `export()` 生成专用上下文
  - `persist()` 持久化到 SQLite（stage 0 #5 封装好的接口）
  - `load(conversation_id)` 从 db 恢复历史上下文
- [ ] 订阅 `UserInput` 事件，处理完成后发布 `ContextReady` 事件

**文件结构**：
```
agent/
├── context_manager/
│   ├── __init__.py
│   ├── manager.py             # ContextManager 主类
│   ├── filter.py              # 噪声过滤
│   └── compressor.py          # 上下文压缩
```

**验证标准**：
- 输入 "你好" → 生成专用上下文 → 发布 ContextReady 事件
- 持久化后重启可恢复历史上下文

---

### 阶段 2: 简单路径闭环

#### 2.1 模型封装（含缓存机制）（#7）

**目标**：封装模型调用，支持缓存调试

**任务清单**：

- [ ] 实现 `ModelWrapper` 类
  - `chat(messages, tools=None, **kwargs)` 同步调用
  - `stream(messages, **kwargs)` 流式输出（Iterator[str]）
  - `chat_with_usage(messages, **kwargs)` 带 token 用量 (str, usage)
- [ ] 集成缓存机制（ar.md 8.4）
  - `ResponseCache` 类：make_key, get, set, clear
  - 仅缓存纯对话请求（无 tools）
  - 通过 `cache_enabled` 开关控制
  - `cache_version` 字段，版本变更旧缓存失效
- [ ] 支持模型切换（构造函数传参）
- [ ] 订阅 `ExecuteSimple` 事件，执行完成后发布 `ArtifactReady` 事件

**文件结构**：
```
agent/
├── model_wrapper/
│   ├── __init__.py
│   ├── wrapper.py             # ModelWrapper 主类
│   └── cache.py               # ResponseCache 缓存实现
```

**验证标准**：
- 调用 chat() 返回正确响应
- 开启缓存后，相同输入两次调用返回相同结果
- 关闭缓存后，相同输入两次调用可能返回不同结果

---

#### 2.2 路由器 - 模型选择器（#8）

**目标**：判断任务复杂度，简单任务直接派给模型封装

**任务清单**：

- [ ] 实现 `Router` 类
  - `route(context)` 判断复杂度，返回路由结果
- [ ] 实现 `ModelSelector`（模型选择器）
  - 规则判定 + 模型自判
  - 支持 `#simple` / `#complex` flag 覆盖自动判定
- [ ] 订阅 `ContextReady` 事件
  - 简单 → 发布 `ExecuteSimple` 事件
  - 复杂 → （阶段 4 #18 实现）先留桩，输出日志

**文件结构**：
```
agent/
├── router/
│   ├── __init__.py
│   ├── router.py              # Router 主类
│   ├── selector.py            # ModelSelector 模型选择器
│   └── planner.py             # ModelPlanner 模型规划器（阶段 4 #18）
```

**验证标准**：
- 输入 "帮我翻译这段话" → 路由到简单路径
- 输入 "帮我重构整个项目" → 路由到复杂路径（日志桩）
- 输入 "#simple 复杂任务描述" → 强制走简单路径

---

#### 2.3 核验器（#9）

**目标**：先实现轻量核验（简单路径产物）

**任务清单**：

- [ ] 实现 `Verifier` 类
  - `verify_artifact_lightweight(artifact, task)` 轻量核验
    - 格式合规性
    - 安全性（无敏感信息泄露）
    - 基本正确性（非空、格式正确）
- [ ] 核验结果处理
  - 通过 → 发布 `Verified` 事件
  - 不通过 → 发布 `VerificationFailed` 事件
  - 需用户确认 → 保存状态，发布 `WaitingUser` 事件
- [ ] 订阅 `ArtifactReady` 事件

**文件结构**：
```
agent/
├── verifier/
│   ├── __init__.py
│   ├── verifier.py            # Verifier 主类
│   └── lightweight.py         # 轻量核验实现
```

**验证标准**：
- 简单路径合法产物 → 轻量核验通过 → 发布 Verified
- 简单路径格式错误产物 → 核验失败 → 发布 VerificationFailed

---

#### 2.4 汇报器（#10）

**目标**：组织产物，生成任务报告

**任务清单**：

- [ ] 实现 `Reporter` 类
  - `collect_artifacts(schedule_id | conversation_id)` 收集关联产物
  - `organize(artifacts)` 整合
  - `generate_report()` 生成结构化报告
- [ ] 订阅 `Verified` 事件
- [ ] 处理完成后发布 `ReportReady` 事件

**文件结构**：
```
agent/
├── reporter/
│   ├── __init__.py
│   └── reporter.py            # Reporter 主类
```

**验证标准**：
- 收到 Verified 事件 → 收集关联产物 → 生成结构化报告
- 报告包含每个任务的结果和总结

---

#### 2.5 展示器（#11）

**目标**：收集全过程信息，格式化输出

**任务清单**：

- [ ] 实现 `Presenter` 类
  - `format_final(report)` 格式化最终结果
  - `format_progress(event)` 格式化中间进度
  - 输出格式适配 CLI 和 WebSocket（统一结构）
- [ ] 订阅事件
  - `ReportReady` 事件 → 格式化最终结果
  - `TaskProgress` 事件 → 格式化中间状态
- [ ] 输出格式与 WebSocket 协议对齐（ar.md 4.7）
  - phase: thinking / intermediate / final

**文件结构**：
```
agent/
├── presenter/
│   ├── __init__.py
│   └── presenter.py           # Presenter 主类
```

**验证标准**：
- 汇报器完成后 → 输出 final 结果
- 格式与协议一致（type/schedule_id/task_id/phase/content）

---

#### 2.6 debug_cli.py（#12）

**目标**：命令行模拟输入，永久保留，agent 独立调试

**任务清单**：

- [ ] 实现 `agent/debug_cli.py`
  - 单次运行：`python agent/debug_cli.py --query "问题"`
  - 交互式：`python agent/debug_cli.py --interactive --session-id "xxx"`
  - 调试命令：`/status`（查看状态）、`/cache on|off`、`/clear-cache`、`/exit`
  - 回放录屏：`python agent/debug_cli.py --replay xxx.json`
- [ ] 与 Web Server 共用同一事件总线
  - CLI：stdin → 发布 UserInput 事件
  - Web Server：WebSocket → 发布 UserInput 事件
- [ ] Presenter 输出 → 同时支持终端打印和 WebSocket 推送

**关键接口**：

```python
# debug_cli.py 与 server_main.py 共用的 Agent 入口
class AgentApp:
    def __init__(self, event_bus):
        self.event_bus = event_bus

    def handle_user_input(self, user_input: str, session_id: str, conversation_id: str | None = None):
        """CLI 和 Web Server 都调用这个接口"""
        self.event_bus.publish(UserInput(...))
```

**文件结构**：
```
agent/
├── debug_cli.py              # CLI 调试入口（永久保留）
├── app.py                    # AgentApp：agent 主体逻辑，CLI 和 Web Server 共用
```

**验证标准**：
- CLI 输入问题 → 简单路径执行 → 终端输出回答
- CLI 支持会话管理（新开/继续/暂停对话）
- `/cache on` 后相同问题两次回答一致
- 录屏功能可正常录制和回放

---

### 阶段 3: 能力层

#### 3.1 沙箱（工作区）（#13）

**目标**：提供受限的 Docker 执行环境

**任务清单**：

- [ ] 实现 `Sandbox` 类
  - `create(image, mounts)` 创建容器
  - `exec(command)` 在容器内执行命令
  - `copy_out(src, dst)` 从容器复制文件到宿主机
  - `destroy()` 销毁容器
- [ ] 优先实现"长驻容器"模式（Agent 多轮对话主要场景）
  - `docker run -d` 后台运行
  - 多次 `docker exec` 注入命令
- [ ] 实现"批量执行"模式
  - `docker run` + 多命令串联，执行完自动销毁
- [ ] 资源管理
  - 并发容器数量上限（配置文件读取）
  - 容器超时自动回收

**文件结构**：
```
agent/
├── sandbox/
│   ├── __init__.py
│   ├── sandbox.py             # Sandbox 主类
│   ├── docker_manager.py      # Docker 容器管理
│   └── resource_pool.py       # 资源池（并发上限、超时回收）
```

**验证标准**：
- 创建容器 → exec("echo hello") → 返回 "hello"
- 创建容器 → 写入文件 → copy_out → 宿主机读到文件
- 销毁容器 → 再次 exec → 报错

---

#### 3.2 能力管理器 - skill（本地函数）（#14）

**目标**：管理本地 Python 函数（原子工具）

**任务清单**：

- [ ] 实现本地工具注册机制
  - `CapabilityManager.register_tool(name, func)`
  - 基础工具：read_file, write_file, search, run_shell 等
- [ ] 生成 function schema（用于模型 function calling）

**文件结构**：
```
agent/
├── capability/
│   ├── __init__.py
│   ├── manager.py             # CapabilityManager 主类
│   └── tool_registry.py       # 本地 Tools 注册
```

**验证标准**：
- 注册一个本地函数 → get_schemas() 返回正确的 function schema
- call("read_file", path="xxx") → 返回文件内容

---

#### 3.3 能力管理器 - mcp（#15）

**目标**：迁移 mcpex 的 MCP 热加载机制

**任务清单**：

- [ ] 实现 MCP 服务注册机制
  - `CapabilityManager.register_mcp(name, server_config)`
- [ ] 迁移 mcpex 热加载
  - watchdog 监控 `agent/mcp_tools/` 目录
  - 新增文件自动注册工具
  - 修改/删除同步卸载
- [ ] 与本地工具统一接口
  - call() 不区分来源（skill/mcp）

**文件结构**：
```
agent/
├── capability/
│   └── mcp_registry.py        # MCP 连接管理（复用 mcpex）
├── mcp_tools/                 # 运行时热加载目录
│   ├── calculator.py
│   └── weather.py
```

**验证标准**：
- 注册 MCP 服务 → call() 能调用到 MCP 工具
- 新增 mcp_tools/*.py 文件 → 自动热加载

---

#### 3.4 能力管理器 - rag（#16）

**目标**：定义 RAG 抽象接口，先上临时实现

**任务清单**：

- [ ] 定义 `RAGRetriever` 抽象接口（Protocol）
  - `index_documents(docs: list[Document])`
  - `retrieve(query: str, top_k: int = 5) -> list[Document]`
  - `health_check() -> bool`
- [ ] 实现 `SimpleLocalRAG`（临时替代，先跑通）
  - 纯关键词检索（TF-IDF 或简单分词），Python 标准库即可
  - 零外部依赖
- [ ] 注册接口
  - `CapabilityManager.register_rag(name, retriever)`
  - get_schemas() 把 RAG 注册为可被 function calling 调用的工具

**文件结构**：
```
agent/
├── capability/
│   ├── rag_protocol.py        # RAGRetriever Protocol（抽象接口）
│   ├── rag_registry.py        # RAG 注册管理
│   └── simple_local_rag.py    # SimpleLocalRAG 临时实现
```

**验证标准**：
- SimpleLocalRAG 能索引和检索文档
- 新 RAG 实现只需满足 Protocol 即可无缝切换

---

#### 3.5 技能管理器（#17）

**目标**：管理复合技能，编排多个能力

**任务清单**：

- [ ] 实现 `SkillManager` 类
  - `load_skills(skills_dir)` 从目录加载 SKILL.md
  - `match(user_input)` 匹配技能
  - `execute(skill_name, args, capability_manager)` 执行技能
- [ ] 迁移 skillsex 的 SKILL.md 规范解析
  - metadata（name, description, parameters）
  - instruction_body
  - required_tools
- [ ] 与能力管理器对接
  - 技能执行时调用 capability_manager 获取工具
  - 无匹配 Skill 时返回 None（模型自主组合能力）

**文件结构**：
```
agent/
├── skill_manager/
│   ├── __init__.py
│   ├── manager.py             # SkillManager 主类
│   ├── parser.py              # SKILL.md 解析器
│   └── executor.py            # 技能执行器
├── skills/                    # 技能存放目录
│   └── hello-demo/
│       └── SKILL.md
```

**验证标准**：
- 放入 hello-demo SKILL.md → 成功加载
- 匹配到技能 → 注入专用 prompt → 执行多步骤推理
- 无匹配技能 → 返回 None（模型自主模式）

---

### 阶段 4: 复杂路径闭环

#### 4.1 路由器 - 模型规划器（#18）

**目标**：为复杂任务生成计划排期表

**任务清单**：

- [ ] 实现 `ModelPlanner`
  - 用专用上下文调用模型，输出结构化 JSON
  - 解析为 ScheduleSheet 数据结构
  - 支持子计划排期表嵌套
- [ ] 路由器集成
  - 复杂任务 → 调用 ModelPlanner → 发布 `ScheduleReady` 事件
- [ ] 事前核验（调用核验器）
  - 通过 → 发布 `ScheduleApproved`
  - 不通过 → 发布 `ScheduleRejected`（路由器重规划）

**文件结构**：
```
agent/router/
└── planner.py                 # ModelPlanner 实现
```

**验证标准**：
- 输入 "分析项目代码质量并优化" → 生成多任务 ScheduleSheet
- 计划排期表任务依赖关系合理
- 事前核验通过后发布 ScheduleApproved

---

#### 4.2 计划排期表管理逻辑（#19）

**目标**：ScheduleSheet 的增删改查、状态追踪、子计划挂接

**任务清单**：

- [ ] 实现 `ScheduleManager`
  - `create(sheet)` 创建排期表
  - `update(schedule_id, changes)` 更新
  - `get(schedule_id)` 查询
  - `link_sub_schedule(parent_task_id, sub_schedule_id)` 挂接子排期表
  - `track_progress(schedule_id)` 汇总进度
- [ ] 持久化到 db（JSON 字段快照）

**文件结构**：
```
agent/
├── scheduler/
│   ├── __init__.py
│   └── schedule_manager.py    # 排期表增删改查追踪
```

**验证标准**：
- 创建排期表 → 更新任务状态 → 进度汇总正确
- 子排期表挂接关系可追溯

---

#### 4.3 调度器（#20）

**目标**：根据计划排期表创建并管理任务组

**任务清单**：

- [ ] 实现 `Dispatcher` 类
  - `dispatch(schedule_sheet)` 解析排期表，创建任务组
  - `create_task_group(tasks, sandbox)` 创建 TaskGroup 并分配线程
  - `cancel(schedule_id)` 级联取消任务组和子任务组
  - 子计划排期表挂接关系管理
- [ ] 订阅 `ScheduleApproved` 事件
- [ ] 任务组完成后收集所有产物，发布 `ArtifactReady`

**文件结构**：
```
agent/
├── dispatcher/
│   ├── __init__.py
│   ├── dispatcher.py          # Dispatcher 主类
│   └── tracker.py             # 子计划排期表追踪
```

**验证标准**：
- ScheduleApproved → 创建 TaskGroup 实例
- 任务组启动后 GroupStatus = RUNNING
- cancel() → 任务组 FAILED，沙箱销毁

---

#### 4.4 线程池（#21）

**目标**：任务组内并发执行子任务

**任务清单**：

- [ ] 实现 `ThreadPool` 类（封装 concurrent.futures.ThreadPoolExecutor）
  - `submit(task, fn)` 提交任务
  - `wait_all()` 等待所有任务完成
  - 支持依赖调度（有依赖的任务等前置任务完成再提交）
- [ ] 任务与线程绑定，Task 状态机流转

**文件结构**：
```
agent/task_group/
└── thread_pool.py             # 线程池 + 依赖调度
```

**验证标准**：
- 无依赖的 A、B 任务并发执行
- 有依赖的 C 任务等待 A 完成后才执行

---

#### 4.5 任务组（#22）

**目标**：执行被分配的任务，支持递归嵌套 + 双状态机

**任务清单**：

- [ ] 实现 `TaskGroup` 类
  - 任务组状态机：IDLE → RUNNING → WAITING_USER → RUNNING → COMPLETED/FAILED
  - 内部任务状态机：每个 Task 独立状态流转
  - `start()` 启动线程池执行
  - `pause()` 暂停，保存状态
  - `resume(user_response)` 恢复执行
  - `cancel()` 取消
- [ ] 内部组件实例化
  - 独立 ContextManager（子任务上下文）
  - 独立 Router（判断子任务复杂度）
  - 独立 ModelWrapper
- [ ] 递归嵌套支持
  - 子任务生成子计划排期表 → 事件通知调度器创建子任务组
- [ ] 亲缘任务共享沙箱
- [ ] 实时进度发布（TaskProgress 事件给展示器）

**文件结构**：
```
agent/task_group/
├── __init__.py
├── group.py                   # TaskGroup 主类
└── state_machine.py           # 双状态机实现（Task / TaskGroup）
```

**验证标准**：
- 启动任务组 → 并发独立任务，Task 状态各自流转
- 有依赖任务等前置完成
- 暂停后从断点恢复
- 子任务生成子排期表 → 调度器创建子任务组

---

### 阶段 5: 集成收尾

#### 5.1 web server 集成（#23）

**目标**：Agent 接入现有 Web Server

**任务清单**：

- [ ] 在 [server_main.py](file:///home/thbytwo/testCode/xuyuanji/main_body/server_main.py) 中集成 AgentApp
  - WebSocket 消息 → AgentApp.handle_user_input() → 发布 UserInput
  - Agent 事件（Presenter 输出）→ WebSocket 推送到浏览器
- [ ] 复用事件总线（debug_cli.py 和 Web Server 共用同一个 EventBus 实例）
- [ ] WebSocket 协议对齐（ar.md 4.7）
  - Agent → Web Server: agent_event 格式
  - Web Server → Agent: user_action 格式
- [ ] Agent 作为 FastAPI lifespan 内启动的服务

**文件结构**：
```
agent/
├── app.py                     # AgentApp（CLI 和 Web Server 共用）
main_body/
└── server_main.py             # 集成 AgentApp
```

**验证标准**：
- 用户通过 WebSocket 发送消息 → Agent 收到 UserInput 事件
- Agent 发布进度事件 → 用户通过 WebSocket 实时收到
- CLI 调试和 Web 访问互不干扰

---

#### 5.2 用户交互协议（#24）

**目标**：暂停/恢复/确认/补充信息完整链路

**任务清单**：

- [ ] 核验器保存等待状态到 DB（状态 WAITING_USER）
- [ ] 通过展示器 → Web Server 推送给用户待确认信息（含 target_id）
- [ ] 用户通过 target_id 发送反馈 → Web Server → 发布 UserResponse 事件
- [ ] 核验器/任务组收到 UserResponse → 恢复执行
- [ ] 用户中途修改需求
  - 调度器暂停任务组
  - 模型规划器根据当前计划排期表 + 新需求修改 → 新计划
  - 保留未变更任务，删除变更任务

**文件结构**：
```
agent/
└── interaction/               # 用户交互协议实现
    ├── __init__.py
    └── handler.py
```

**验证标准**：
- 核验器等待用户确认 → 用户通过 ID 确认 → 恢复
- 用户中途"改需求" → 调度器暂停 → 规划器出新计划 → 继续执行

---

### 阶段 6: 增强功能

#### 6.1 成本控制（#25）

**任务清单**：

- [ ] 配置文件读取预算参数（max_depth/max_tokens/max_tool_calls）
- [ ] 注入 ScheduleSheet 预算字段
- [ ] 运行时监控
  - Token 用量统计（每轮模型调用累加）
  - 递归深度（子排期表嵌套层数）
  - 工具调用次数
- [ ] 超预算 → 触发核验失败 + 汇报用户

---

#### 6.2 上下文演化机（#26）

**目标**：从执行经验中自我优化

**任务清单**：

- [ ] 任务组完成后总结上下文为经验产物（JSON 格式）
- [ ] 复制到经验库目录（如 `data/experience/`）
- [ ] 演化机读取经验两个方向：
  1. **喂养 RAG** → 总结新 Skill、改进已有 Skill（生成新 SKILL.md 草稿，人工审核后自动生效）
  2. **扩充 MCP** → 新增工具、优化 prompt、补充资料（利用 MCP 热插拔自动加载）
- [ ] 经验可信度阈值机制（自动上线需高分）

**文件结构**：
```
agent/
├── evolution/
│   ├── __init__.py
│   ├── extractor.py           # 从任务上下文提取经验
│   └── optimizer.py           # 生成 Skill/MCP 优化建议
data/
└── experience/                # 经验库目录
```

---

#### 6.3 录制/回放模式（#27）

**任务清单**：

- [ ] 录制模式：agent.run(..., record_mode=True)
  - 所有模型调用 + 工具调用都写入 recording.json
  - 输出目录：`debug/recordings/`
- [ ] 回放模式：agent.replay(recording_file)
  - 所有模型调用和工具调用返回录制时的结果
  - 用于回归测试（保证代码修改不改变执行结果）
- [ ] debug_cli.py 集成：`--record` / `--replay` 开关

---

#### 6.4 RAG 完整实现（#28）

**目标**：替换 SimpleLocalRAG，上真正向量库

**任务清单**：

- [ ] 实现 `FAISSRAG` 或 `ChromaDBRAG`（任选其一先做）
  - 对接嵌入模型
  - 索引构建/持久化
  - 相似度检索
- [ ] 切换实现：`register_rag("codebase", FAISSRAG())`
- [ ] 文档库/代码库/Wiki 多检索源管理
- [ ] 索引更新：MCP 热插拔工具新增的资料自动触发增量索引

**文件结构**：
```
agent/capability/
├── faiss_rag.py               # FAISS 实现（或 ChromaDBRAG）
└── simple_local_rag.py        # 保留，便于无依赖环境调试
```

---

## 4. 里程碑定义

| 里程碑 | 完成标志 | 对应阶段 | 项号 |
|--------|---------|---------|------|
| M0: 基础就绪 | 配置 + 数据结构(会话对话双状态机) + 事件总线 + 可观测性 + DB 可用 | 阶段 0 | #1-#5 |
| M1: 简单路径闭环 | CLI 输入 → 上下文 → 模型 → 轻量核验 → 汇报 → 展示 → 终端输出 | 阶段 2 | #7-#12 |
| M2: 能力层就绪 | 沙箱 + 能力管理器(skill/mcp/SimpleLocalRAG) + 技能管理器可用 | 阶段 3 | #13-#17 |
| M3: 复杂路径闭环 | 规划 → 调度 → 任务组(线程池+双状态机) → 核验 → 产物 | 阶段 4 | #18-#22 |
| M4: 端到端可用 | Web Server 集成 + 用户交互协议（暂停/恢复/确认/改需求） | 阶段 5 | #23-#24 |
| M5: 生产就绪 | 成本控制 + 演化机 + 录制回放 + RAG 完整实现(FAISS/ChromaDB) | 阶段 6 | #25-#28 |

---

## 5. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 事件总线调试困难 | 组件间事件链路复杂 | #4 可观测性先行：trace_id + 结构化日志 |
| 模型规划器输出不稳定 | 计划排期表质量不可控 | #7 模型封装集成缓存，固定输出后调优 prompt |
| 沙箱 Docker 环境依赖 | 开发环境可能没 Docker | #13 前确认 Docker 可用，提供 Sandbox Mock 模式 |
| 任务组递归深度失控 | 资源耗尽 | #25 成本控制 max_depth 限制 |
| 会话多端同步 | 状态竞态 | 会话锁机制，一个对话只能一个 writer |
| SimpleLocalRAG 检索效果差 | 临时方案效果有限 | #28 上 FAISS/ChromaDB，同时保留简单实现兜底 |
| 演化机错误经验放大 | 自动上线坏技能/MCP | 经验可信度阈值 + 人工审核关卡 |

---

## 6. 开发约定

### 6.1 目录结构

```
agent/
├── __init__.py
├── app.py                     # AgentApp：CLI 和 Web Server 共用
├── debug_cli.py               # 命令行调试入口（永久保留）
│
├── config/                    # 阶段 0: 配置加载
│   └── loader.py
│
├── models/                    # 阶段 0: 核心数据结构
│   ├── enums.py
│   ├── session.py             # Session/Conversation/Turn
│   ├── schedule.py            # ScheduleSheet/Task
│   ├── task_group.py          # TaskGroup + 双状态机
│   ├── artifact.py
│   └── event.py
│
├── core/                      # 阶段 0: 基础设施
│   ├── event_bus.py
│   ├── event_types.py
│   ├── logging_setup.py
│   └── tracing.py             # trace_id contextvar
│
├── store/                     # 阶段 0: DB 封装
│   ├── connection.py
│   └── schema.py
│
├── context_manager/           # 阶段 1
│   ├── manager.py
│   ├── filter.py
│   └── compressor.py
│
├── model_wrapper/             # 阶段 2
│   ├── wrapper.py
│   └── cache.py
│
├── router/                    # 阶段 2 + 阶段 4
│   ├── router.py
│   ├── selector.py
│   └── planner.py
│
├── verifier/                  # 阶段 2
│   ├── verifier.py
│   ├── lightweight.py
│   └── deep.py                # 阶段 4 补充深度核验
│
├── reporter/                  # 阶段 2
│   └── reporter.py
│
├── presenter/                 # 阶段 2
│   └── presenter.py
│
├── capability/                # 阶段 3
│   ├── manager.py
│   ├── tool_registry.py
│   ├── mcp_registry.py
│   ├── rag_protocol.py        # RAG 抽象接口
│   ├── rag_registry.py
│   ├── simple_local_rag.py    # 临时实现
│   └── faiss_rag.py           # 阶段 6 补充
│
├── skill_manager/             # 阶段 3
│   ├── manager.py
│   ├── parser.py
│   └── executor.py
│
├── sandbox/                   # 阶段 3
│   ├── sandbox.py
│   ├── docker_manager.py
│   └── resource_pool.py
│
├── scheduler/                 # 阶段 4
│   └── schedule_manager.py
│
├── dispatcher/                # 阶段 4
│   ├── dispatcher.py
│   └── tracker.py
│
├── task_group/                # 阶段 4
│   ├── group.py
│   ├── state_machine.py       # 双状态机
│   └── thread_pool.py
│
├── interaction/               # 阶段 5
│   └── handler.py
│
├── evolution/                 # 阶段 6
│   ├── extractor.py
│   └── optimizer.py
│
├── mcp_tools/                 # MCP 热加载目录
├── skills/                    # Skill 目录
└── cache/                     # 缓存目录
```

### 6.2 日志规范

所有组件使用统一的结构化日志：

```python
import logging
logger = logging.getLogger(f"agent.{component_name}")

logger.info(
    "event_published",
    extra={
        "trace_id": trace_id,
        "event_type": event_type,
        "schedule_id": schedule_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
)
```

### 6.3 调试约定

1. 开发阶段默认**开启模型缓存**（`--cache`），保证同一问题可反复复现
2. 每次功能开发先通过 `debug_cli.py` 验证，再接入 Web Server
3. 关键路径录制录屏，作为回归测试素材
4. 所有数据结构必须可 JSON 序列化（存储 + 跨进程传递需要）
