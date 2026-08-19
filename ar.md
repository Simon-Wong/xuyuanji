# Agent 架构设计文档 (Architecture)

## 1. 概述

本文档描述"许愿机"项目 Agent 核心的架构设计。Agent 是系统的核心引擎，负责理解用户意图、规划任务、调度执行、核验结果并向用户交付。

---

## 2. 架构全景图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              用户 → web server                              │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                       上下文管理器（压缩/过滤/分类 → 专用上下文）               │
│                                        ↓                                    │
│              路由器（模型选择器 + 模型规划器）                                 │
│                ↓简单路径              ↓复杂路径                               │
│          模型封装 → 沙箱(简单)    计划排期表 → 调度器 → 任务组 → 沙箱(复杂)      │
│                ↓                        ↓              ↓                    │
│              产物─────────────────────────────────────────────────────────   │
│                                        ↓                                    │
│                                   核验器                                     │
│                                        ↓                                    │
│           展示器 ←──────── 汇报器 ←── 核验结果                                │
│                ↑                                                            │
│                └──────────── 任务组实时进度（思维/中间结果）                   │
│                                        ↓                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
                              web server → 用户
```

### 能力调用层次

```
┌──────────────────────────────────────────┐
│            模型封装 (Model Wrapper)       │
│        function calling 接口             │
└──────────────────┬───────────────────────┘
                   ↓
┌──────────────────────────────────────────┐
│          技能管理器 (Skill Manager)       │
│    复合技能：Prompt + 编排多个能力        │
└──────────────────┬───────────────────────┘
                   ↓ 调用
┌──────────────────────────────────────────┐
│       能力管理器 (Capability Manager)     │
│                                          │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
│  │  Tools  │  │   MCP   │  │   RAG   │  │
│  │ 本地函数 │  │ 外部服务 │  │ 检索源  │  │
│  │read_file│  │ calc    │  │ docs    │  │
│  │write_file│ │ weather │  │ codebase│  │
│  │ search  │  │ ...     │  │ wiki    │  │
│  └─────────┘  └─────────┘  └─────────┘  │
│                                          │
│  统一注册 / 发现 / 路由 / 生命周期管理    │
└──────────────────────────────────────────┘
```

---

## 3. 组件说明

### 3.1 上下文管理器 (Context Manager)

**职责**：
- 接收用户原始输入，维护原始上下文
- 对上下文进行压缩，减少 token 消耗
- 过滤噪声（如语音输入中的口头禅、冗余信息）
- 按任务分类导出相关上下文，生成"专用上下文"
- 持久化到 SQLite 数据库，支持历史回溯

**数据流**：
- 输入：用户消息、历史对话
- 输出：专用上下文（供给路由器/任务组）

---

### 3.2 路由器 (Router)

**职责**：
- 接收专用上下文，判断任务复杂度
- **简单问题**：直接调用模型选择器选模型 → 模型封装执行 → 产物
- **复杂任务**：调用模型规划器生成"计划排期表" → 调度器执行

**内部结构**：
- **模型选择器**：为简单问题选择合适的模型，直接派给模型封装
- **模型规划器**：用专用上下文生成"计划排期表"，拆解复杂任务

**分流判定**：
- 规则判定 + 模型自判相结合
- 单轮问答、翻译、检索类走简单路径
- 需要多步骤推理、多工具协作的走复杂路径

---

### 3.3 模型封装 (Model Wrapper)

**职责**：
- 封装不同模型的调用细节，提供统一接口
- 支持流式输出、函数调用
- 切换模型无需修改业务代码

**数据流**：
- 输入：专用上下文 + 指令
- 输出：模型执行结果 → 产物

---

### 3.4 工作区 / 沙箱 (Workspace / Sandbox)

**职责**：
- 提供受限的执行环境，防止 Agent 操作泄露到主系统
- 管理执行过程中的临时文件、产物
- 两种场景：
  - **简单路径沙箱**：挂载能力管理器、接收产物
  - **复杂路径沙箱**：挂载计划排期表、子计划排期表、产物、能力管理器

**沙箱内结构**：
```
工作区/沙箱：
  ├─ 技能管理器（复合技能编排）
  ├─ 能力管理器
  │    ├─ Tools（本地函数）
  │    ├─ MCP（外部服务，可增删）
  │    └─ RAG（检索源）
  ├─ 产物
  └─ 计划排期表（复杂路径）
```

**生命周期**：创建 → 挂载资源 → 执行任务 → 收集产物 → 归档/清理

---

### 3.5 技能管理器 (Skill Manager)

**职责**：
- 管理复合技能（Skill）：由 Prompt 模板 + 多个能力编排组成
- 接收模型封装的 function call 请求，执行多步骤技能逻辑
- 调用能力管理器获取所需能力

**与能力管理器的区别**：
- 技能 = 复合能力（多步骤、可调用多个 Tool/MCP/RAG）
- 能力 = 原子操作（单次调用）

**数据结构（建议）**：
```python
@dataclass
class Skill:
    skill_id: str
    name: str                        # 函数名，如 "code_review"
    description: str                # 给模型看的描述
    parameters: dict                 # 参数 schema（OpenAI function 格式）
    prompt_template: str            # 技能的 prompt 模板
    required_tools: list[str]       # 依赖的 Tool 列表
    execution_steps: list[str]     # 执行步骤描述
```

---

### 3.6 能力管理器 (Capability Manager)

**职责**：
- 统一管理三类异构能力，对上层屏蔽差异
- 统一注册 / 发现 / 路由 / 生命周期管理

**三类能力**：

| 类型 | 说明 | 示例 |
|------|------|------|
| Tools | 本地 Python 函数，无状态 | `read_file`、`write_file`、`search` |
| MCP | 外部 MCP 服务，需连接管理 | `calculator`、`weather` |
| RAG | 检索源，需索引管理 | 文档库、代码库、Wiki |

**统一接口（建议）**：
```python
class CapabilityManager:
    """统一能力注册中心"""

    def register_tool(self, name: str, func: Callable): ...
    def register_mcp(self, name: str, server: MCPServer): ...
    def register_rag(self, name: str, retriever: RAGRetriever): ...

    def call(self, name: str, **kwargs) -> Any: ...
    def get_schemas(self) -> list[dict]: ...  # 生成 function schema 给模型封装
    def start(self): ...   # 启动 MCP 连接、加载 RAG 索引
    def stop(self): ...    # 关闭连接、释放资源
```

---

### 3.7 计划排期表 (Schedule Sheet)

**职责**：
- 由模型规划器生成，描述复杂任务的执行步骤
- 作为调度器、核验器、汇报器、展示器的公共枢纽

**数据结构（建议）**：
```python
@dataclass
class ScheduleSheet:
    schedule_id: str
    tasks: list[Task]
    status: ScheduleStatus  # PENDING / RUNNING / COMPLETED / VERIFIED

@dataclass
class Task:
    task_id: str
    parent_task_id: str | None       # 父任务ID，顶层为 None
    sub_schedule_id: str | None      # 关联的子计划排期表
    description: str
    status: TaskStatus               # PENDING / RUNNING / COMPLETED / FAILED
    input_context: str               # 专用上下文
    assigned_model: str              # 分配的模型
```

---

### 3.8 调度器 (Dispatcher)

**职责**：
- 根据计划排期表启动任务组
- 向任务组内派发任务
- 管理子计划排期表的挂接关系（便于追踪）

**数据流**：
- 输入：计划排期表
- 输出：启动的任务组实例

---

### 3.9 任务组 (Task Group)

**职责**：
- 执行被分配的任务
- 内部包含独立的：
  - 上下文管理器（管理子任务的上下文）
  - 路由器（判断子任务的复杂度）
  - 模型封装（执行具体任务）
- 支持互相独立或协作讨论的执行模式

**特点**：
- 递归嵌套：任务组内的任务可以再创建子计划排期表，让调度器进一步启动子任务组
- 有亲缘关系的任务和任务组公用同一个沙箱环境

---

### 3.10 产物 (Artifact)

**职责**：
- 记录单个任务的执行结果
- 描述具体的结果内容、使用的工具、生成的文件等

**特点**：
- 简单路径产物和复杂路径产物统一格式
- 存储在沙箱内，可被核验器检查

---

### 3.11 核验器 (Verifier)

**职责**：
- 核验所有产物与用户要求、计划排期表的一致性（无论来自简单路径还是复杂路径）
- 核验计划排期表和子计划排期表的合理性
- 两种核验模式：
  - **轻量核验**：格式合规性、安全性、基本正确性（简单路径产物）
  - **深度核验**：格式 + 内容 + 逻辑 + 与计划排期表的一致性（复杂路径产物）

**核验不通过处理**：
- 产物问题 → 任务组重新执行
- 计划问题 → 路由器重新规划
- 多次重试后仍不通过 → 任务失败，记录失败原因
---

### 3.12 汇报器 (Reporter)

**职责**：
- 按计划排期表组织相关联的产物
- 将分散的产物整合成完整的任务报告

**数据流**：
- 输入：核验通过的产物 + 计划排期表
- 输出：结构化的任务汇报

---

### 3.13 展示器 (Presenter)

**职责**：
- 按计划排期表收集全过程信息：
  - 中间思维过程（来自任务组实时反馈）
  - 中间结果（来自任务组实时反馈）
  - 最终结果（来自汇报器）
- 格式化输出，对接 Web Server 的 WebSocket 推送

**数据源**：
1. 任务组实时进度（思维流、中间状态）—— 辅助
2. 汇报器最终结果 —— 主要

**输出格式**（与 WebSocket 协议对齐）：
```json
{
    "type": "task_progress",
    "schedule_id": "...",
    "task_id": "...",
    "phase": "thinking | intermediate | final",
    "content": "..."
}
```

---

## 4. 事件驱动架构

### 4.1 核心思想

Agent 采用事件驱动架构（EDA），通过事件总线连接各组件。只有需要等待外部响应的组件拥有独立执行线程，其余组件均为被动触发器。

### 4.2 组件执行模型

**拥有独立执行线程的组件**：
- 模型封装（简单路径）：等待模型 API 返回
- 任务组（复杂路径）：内部执行链路，使用线程池管理子任务

**被动触发的组件**（事件订阅者）：
- 上下文管理器：订阅用户输入事件
- 路由器：订阅"专用上下文就绪"事件
- 调度器：订阅"计划排期表就绪"事件（被动接收，主动创建任务组实例）
- 核验器：订阅"产物就绪"事件
- 汇报器：订阅"核验通过"事件
- 展示器：订阅"汇报完成"/"任务进度"事件

### 4.3 事件总线

```
┌─────────────────────────────────────────────────────────────────┐
│                        事件总线 (Event Bus)                      │
│                                                                 │
│  事件类型：                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ UserInput    │  │ ContextReady │  │ ScheduleReady│           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ ArtifactReady│  │ Verified     │  │ ReportReady │           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
│  ┌──────────────┐  ┌──────────────┐                              │
│  │ WaitingUser  │  │ UserResponse │                              │
│  └──────────────┘  └──────────────┘                              │
└─────────────────────────────────────────────────────────────────┘
```

### 4.4 核心交互流

```
1. 用户输入 → 发布 UserInput 事件
2. 上下文管理器订阅 → 处理 → 发布 ContextReady 事件
3. 路由器订阅 → 判断复杂度 → 
   - 简单：发布 ExecuteSimple 事件 → 模型封装(有线程)执行 → 发布 ArtifactReady
   - 复杂：生成计划排期表 → 发布 ScheduleReady 事件
4. 调度器订阅 ScheduleReady → 创建任务组实例(分配线程) → 任务组执行
5. 任务组执行完成 → 发布 ArtifactReady
6. 核验器订阅 → 核验 → 
   - 通过：发布 Verified 事件
   - 需用户确认：保存状态，发布 WaitingUser
7. 用户反馈 → 发布 UserResponse 事件 → 核验器/任务组恢复执行
8. 汇报器订阅 Verified → 生成报告 → 发布 ReportReady
9. 展示器订阅 ReportReady + 任务进度事件 → 推送 Web Server → 用户
```

### 4.5 任务组状态机

任务组使用状态机管理生命周期，支持暂停等待用户反馈后恢复执行：

```
IDLE ──(启动)──→ RUNNING ──(需要用户)──→ WAITING_USER
                  ↑                      │
                  │                      │(用户反馈)
                  └──────────────────────┘
                  │
                  └──(完成)──→ COMPLETED
                  │
                  └──(失败)──→ FAILED
```

状态说明：
- **IDLE**：初始状态，等待调度器分配
- **RUNNING**：执行中，使用线程池并发处理子任务
- **WAITING_USER**：暂停执行，保存状态，等待用户反馈
- **COMPLETED**：所有子任务完成
- **FAILED**：执行失败

### 4.6 核验并发策略

同一个计划排期表内的多个产物**可并行核验**，无需顺序保证。只要每个任务结果核验通过，整个计划排期表即视为核验通过。

### 4.7 用户交互协议

#### Agent → Web Server（事件发布）
```json
{
    "type": "agent_event",
    "trace_id": "trace-xxx",
    "event": "schedule_created | task_started | task_completed | artifact_ready | verified | waiting_user_input | report_ready",
    "data": {
        "schedule_id": "...",
        "task_id": "...",
        "content": "..."
    }
}
```

#### Web Server → Agent（事件订阅）
```json
{
    "type": "user_action",
    "trace_id": "trace-xxx",
    "action": "confirm_schedule | provide_info | cancel | modify_requirement",
    "data": {
        "target_id": "...",
        "content": "..."
    }
}
```

### 4.8 可观测性

每个请求携带唯一 `trace_id`，贯穿整个事件链路：

```python
@dataclass
class Task:
    task_id: str
    trace_id: str              # 追踪 ID
    started_at: float | None
    finished_at: float | None
    ...
```

各组件记录结构化日志，包含 `trace_id`、`schedule_id`、耗时等信息，便于调试和监控。

---

## 5. 设计决策记录

| 决策 | 结论 | 理由 |
|------|------|------|
| 命名"鉴别器"→"路由器" | 采用 | 更准确描述其路由分发职责 |
| 命名"模型调度器"→"调度器" | 采用 | 调度的是任务而非模型 |
| 命名"工具管理器"→"能力管理器" | 采用 | 管理的不只是工具，还有 MCP 和 RAG |
| Skill 放在哪里 | 独立的技能管理器 | Skill 是复合能力，与原子 Tool 分层管理 |
| 架构模式 | 事件驱动架构（EDA） | 解耦组件，只有阻塞操作（模型调用）占用线程 |
| 计划排期表作为枢纽 | 调度/核验/汇报/展示均依赖 | 统一追踪线索 |
| 核验并发 | 同一计划排期表内可并行核验 | 按任务分割，每个结果独立核验即可 |
| 用户交互 | 核验器被动触发，支持暂停/恢复 | 通过事件总线传递用户反馈 |
| 上下文隔离 | 任务组上下文不并入顶层 | 任务完成后总结为产物，供演化机学习 |
| 成本控制 | 配置文件设置预算，注入计划排期表 | 限制 token/深度/工具调用次数 |
| 路由器误判恢复 | 用户可通过 flag 强制路由 | 允许用户覆盖自动判定 |

---

## 6. 实现优先级

| 优先级 | 模块 | 依赖 |
|--------|------|------|
| P0 | 核心数据结构（计划排期表、任务、产物） | 无 |
| P1 | 上下文管理器 | 核心数据结构 |
| P1 | 路由器（模型选择器 + 模型规划器） | 核心数据结构 |
| P2 | 模型封装 | 已存在 |
| P2 | 能力管理器（Tools + MCP + RAG） | 核心数据结构 |
| P2 | 技能管理器 | 能力管理器 |
| P2 | 沙箱 | 核心数据结构 |
| P3 | 调度器 + 任务组 | 路由器、模型封装、沙箱、能力管理器 |
| P3 | 核验器 | 调度器、任务组 |
| P4 | 汇报器 + 展示器 | 核验器 |

---

## 7. 技术验证基础

### 7.1 mcpex — MCP 热插拔

**仓库**：`https://github.com/Simon-Wong/mcpex`

**对应架构组件**：能力管理器 → MCP

**核心实现**：
- 使用 `@mcp.tool()` 装饰器自动注册工具函数
- watchdog 监控 `tools/` 目录，实现运行时热加载
- 支持 stdio 和 SSE 两种传输协议

**关键代码**：
```python
# 热加载机制
observer = Observer()
observer.schedule(ToolFileEventHandler(), path=tools_dir)

def safe_import_tool(file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # 自动执行 @mcp.tool() 注册
```

### 7.2 skillsex — Skill 编排

**仓库**：`https://github.com/Simon-Wong/skillsex`

**对应架构组件**：技能管理器

**核心实现**：
- 基于 `SKILL.md` 规范定义技能（名称、描述、指令、依赖工具）
- `SkillRouter` 根据用户输入匹配技能
- 激活技能后注入专用 system prompt
- 通过 function calling 编排工具调用，支持多轮推理

**关键代码**：
```python
# 技能激活与执行
skill = self.router.match(user_input)  # 匹配技能
self._activate(skill)  # 注入系统 prompt

# 带工具的推理循环
for _ in range(5):
    resp = self.client.chat.completions.create(tools=tools)
    if not msg.tool_calls:
        return msg.content
    result = self.router.call_tool(...)  # 执行工具调用
```

### 7.3 sandboxex — 沙箱模式

**仓库**：`https://github.com/Simon-Wong/sandboxex`

**对应架构组件**：工作区/沙箱

**四种沙箱模式**：

| 模式 | 适用场景 | 实现方式 |
|------|---------|---------|
| 批量执行 | 短平快任务 | `docker run` + 多命令串联 |
| 长驻容器 | Agent 多轮对话 | `docker run -d` + 多次 `docker exec` |
| HTTP 服务 | 代码执行沙箱 | 容器内启动 HTTP 服务接收代码片段 |
| stdin 管道 | 低开销交互式执行 | `docker exec -i python -i` 保持解释器进程 |

**关键代码**：
```python
def run_command(cmd, timeout=300):
    """执行系统命令，返回 (返回码, 标准输出, 标准错误)"""
    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return result.returncode, result.stdout, result.stderr
```

### 7.4 与架构集成建议

```
能力管理器（MCP 部分）：
  直接复用 mcpex 的 MCPServer + watchdog 热加载机制

技能管理器：
  参考 skillsex 的 SKILL.md 规范 + SkillRouter 匹配逻辑
  迁移为与能力管理器对接的版本

沙箱：
  参考 sandboxex 的 Docker 容器管理
  优先实现"长驻容器"模式（Agent 多轮对话主要场景）
```

---

## 8. 附录

### 8.1 成本控制配置

在计划排期表中增加预算字段：

```python
@dataclass
class ScheduleSheet:
    ...
    max_depth: int = 3          # 最大递归深度
    max_tokens: int = 100_000   # token 预算
    max_tool_calls: int = 50    # 工具调用上限
```

### 8.2 用户交互 flag

用户可在输入中附加特殊 flag 来强制路由：

```python
# 强制简单路径（跳过规划）
"#simple 帮我翻译这段话"

# 强制复杂路径（走完整规划流程）
"#complex 帮我重构整个项目的模块结构"
```

### 8.3 上下文演化机

任务组完成后，将上下文总结为产物复制到专用目录，为后续"演化机"提供学习进化的经验：

```
任务组完成
  ↓
上下文总结为经验产物
  ↓
复制到经验库目录
  ↓
演化机读取经验，用于后续优化
```

### 8.4 模型输出缓存机制

#### 8.4.1 设计目的

解决 LLM 不确定性导致的调试困难："同一问题，调试多次，模型输出每次都不一样"。仅用于开发和维护目的，不面向终端用户。

#### 8.4.2 核心思路

```
开发模式（缓存启用）：
  模型请求 → 缓存命中？ → 是 → 返回缓存结果（确定性）
                          → 否 → 调用模型 → 存入缓存 → 返回

生产模式（缓存禁用）：
  模型请求 → 直接调用模型（不经过缓存）
```

#### 8.4.3 缓存 Key 设计

缓存 Key 需精确匹配所有影响输出的参数：

```python
def make_cache_key(model: str, messages: list, tools: list | None, **kwargs) -> str:
    import hashlib
    import json
    key_data = {
        "cache_version": "v1",  # 版本号，变更时旧缓存全部失效
        "model": model,
        "messages": messages,
        "tools": tools,
        **kwargs
    }
    serialized = json.dumps(key_data, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()
```

#### 8.4.4 缓存存储结构

```python
@dataclass
class CacheEntry:
    key: str
    model: str
    request: dict           # 原始请求
    response: dict          # 完整响应（含 tool_calls、usage 等）
    created_at: float
    expires_at: float | None  # None 表示永不过期
```

缓存目录结构：
```
.cache/
├── responses/
│   ├── {hash_key_1}.json    # 缓存的响应数据
│   └── {hash_key_2}.json
├── index.json                # 索引：key → 文件路径映射
└── config.json              # 缓存配置
```

#### 8.4.5 与模型封装集成

```python
class ModelWrapper:
    def __init__(self, ..., cache_enabled: bool = False, cache_dir: str = ".cache"):
        ...
        self.cache_enabled = cache_enabled
        self.cache = ResponseCache(cache_dir) if cache_enabled else None

    def chat(self, messages, tools=None, **kwargs):
        # 仅缓存纯对话请求（无 tools），工具调用结果可能变化
        if self.cache and tools is None:
            key = self.cache.make_key(self.model, messages, tools, **kwargs)
            if cached := self.cache.get(key):
                return cached["response"]

        # 正常调用模型
        response = self.client.chat.completions.create(...)

        if self.cache and tools is None:
            self.cache.set(key, {
                "model": self.model,
                "request": {"messages": messages, **kwargs},
                "response": response.model_dump(),
                "created_at": time.time(),
            })

        return response
```

#### 8.4.6 调试命令

```bash
# 启用缓存模式运行
python main.py --debug --cache

# 清除所有缓存
python main.py --debug --cache-clear

# 指定缓存目录
python main.py --debug --cache --cache-dir ./my-cache
```

#### 8.4.7 录制与回放模式（增强）

除简单缓存外，支持完整执行过程的录制与回放：

**录制模式**：
```python
agent.run(user_input, record_mode=True)
# 生成 recording.json：
# {
#   "user_input": "...",
#   "steps": [
#     {"event": "model_call", "request": ..., "response": ...},
#     {"event": "tool_call", "tool": "read_file", "args": ..., "result": ...},
#     {"event": "model_call", "request": ..., "response": ...}
#   ]
# }
```

**回放模式**：
```python
agent.replay(recording_file="debug/recording_001.json")
# 所有模型调用和工具调用都返回录制时的结果
```

**应用场景**：

| 场景 | 模式 | 说明 |
|------|------|------|
| 模型输出调试 | 缓存模式 | 固定模型输出，排查业务逻辑 |
| 完整流程调试 | 回放模式 | 固定模型+工具结果，复现完整场景 |
| 回归测试 | 回放模式 | 确保代码修改不改变执行结果 |
| 性能分析 | 回放模式 | 多次回放同一录制，测量耗时 |

#### 8.4.8 注意事项

1. **仅开发使用**：生产环境必须确保 `cache_enabled = False`
2. **仅缓存纯对话**：工具调用请求不缓存，因工具结果可能随时变化
3. **敏感信息保护**：缓存中可能包含用户数据和 API Key，需确保缓存目录的访问权限
4. **缓存版本管理**：当 prompt 模板、模型版本变更时，通过 `cache_version` 字段使旧缓存失效
