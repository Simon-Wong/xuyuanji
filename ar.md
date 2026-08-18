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

## 4. 核心数据流

### 4.1 简单路径

```
用户输入
  ↓
上下文管理器 → 专用上下文
  ↓
路由器（模型选择器判定为简单）
  ↓
模型封装 → 沙箱 → 产物
  ↓
核验器（轻量核验）
  ↓
汇报器 → 展示器 → Web Server → 用户
```

### 4.2 复杂路径

```
用户输入
  ↓
上下文管理器 → 专用上下文
  ↓
路由器（模型规划器生成计划排期表）
  ↓
调度器 → 任务组 → 沙箱
  ↓
（子任务：路由器 → 模型封装 → 产物）
  ↓
核验器（深度核验）
  ↓
汇报器 → 展示器 → Web Server → 用户
```

### 4.3 实时进度流

```
任务组内部执行
  ↓
产生思维过程 / 中间结果
  ↓
展示器（实时收集）
  ↓
Web Server → 用户（流式推送）
```

---

## 5. 设计决策记录

| 决策 | 结论 | 理由 |
|------|------|------|
| 命名"鉴别器"→"路由器" | 采用 | 更准确描述其路由分发职责 |
| 命名"模型调度器"→"调度器" | 采用 | 调度的是任务而非模型 |
| 命名"工具管理器"→"能力管理器" | 采用 | 管理的不只是工具，还有 MCP 和 RAG |
| Skill 放在哪里 | 独立的技能管理器 | Skill 是复合能力，与原子 Tool 分层管理 |
| 计划排期表作为枢纽 | 调度/核验/汇报/展示均依赖 | 统一追踪线索 |

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