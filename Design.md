# 代码审查 Workflow —— 设计文档（Design.md）

## 1. 总体架构

本作业在 Homework1「代码审查 Agent」之上，新增一层 **Workflow 编排层**，
把单个 Agent 升级为 **多 Agent 协作的流水线**。

```
┌──────────────────────────────────────────────────────────────┐
│                       用户（CLI / Web）                        │
└──────────────────────────────┬───────────────────────────────┘
                               │ 自然语言需求
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                   WorkflowEngine（workflow/engine.py）         │
│          顺序执行 + 条件分支 + 循环 + 日志 + 错误恢复            │
│                                                              │
│   ┌────────────┐   ┌────────────┐   ┌────────────┐          │
│   │ CoderNode  │──▶│ReviewerNode│──▶│ FixerNode  │──┐       │
│   │ 编码 Agent  │   │ 审查 Agent  │   │ 修复 Agent  │  │       │
│   └────────────┘   └─────┬──────┘   └────────────┘  │ 循环   │
│                          │ 复用 agent/ 包             └───────┘
│                          ▼                                    │
│              ┌─────────────────────────┐                     │
│              │ agent/（复用 HW1）        │                     │
│              │ core + prompts + llm    │                     │
│              │ tools + memory          │                     │
│              └─────────────────────────┘                     │
│                          │                                    │
│                          ▼                                    │
│              ┌─────────────────────────┐                     │
│              │  WorkflowState（state.py）│◀── 所有节点读写      │
│              │  共享状态 = 数据总线       │                     │
│              └─────────────────────────┘                     │
└──────────────────────────────────────────────────────────────┘
```

分层职责：

| 模块 | 职责 |
|------|------|
| `workflow/state.py` | 共享状态：Agent 间通信的「数据总线」+ 执行日志 |
| `workflow/nodes.py` | Workflow 节点抽象 + 编码/审查/修复三个 Agent |
| `workflow/engine.py` | 编排引擎：顺序 + 条件分支 + 循环 + 错误恢复 |
| `workflow/main.py` / `webapp.py` | 交互层：命令行 / 可视化界面 |
| `agent/*` | **复用 HW1** 的代码审查 Agent，作为审查节点 |

## 2. Workflow 编排（流程控制）

```
开始
  │
  ├─ [顺序] CoderNode     需求 -> 代码
  │
  ├─ [顺序] ReviewerNode  代码 -> 问题清单 + 报告
  │
  ├─ [条件分支] 发现可修复问题？
  │       ├─ 否 ──────────────────────► 结束（输出代码 + 报告 + 日志）
  │       └─ 是，且轮数 < max_rounds ──► [循环] FixerNode 修复
  │                                          │
  │                                          ▼
  │                                    ReviewerNode 再审查 ──► 回到条件分支
  │
  └─ 达到最大轮数 / 修复无进展 ──► 结束（防死循环）
```

三种流程控制都覆盖：

1. **顺序执行**（必须项）：编码 → 审查 →（修复 → 审查）的固定先后顺序；
2. **条件分支**（推荐项）：审查发现「可修复问题」才进入修复分支，否则直接结束；
3. **循环**（推荐项）：修复后重新审查，直到通过或达到 `max_fix_rounds`。

**终止性保证**：循环受三重约束——① 最大修复轮数；② 修复无进展（代码未变）即停；
③ Mock 修复器是确定性的、收敛的（详见 3.3）。

## 3. 关键设计决策

### 3.1 用「共享状态」实现 Agent 间通信

多个 Agent 不直接调用彼此，而是读写同一个 `WorkflowState` 对象（**状态共享**），
每个字段有明确的「谁写、谁读」契约：

| 字段 | 写入方 | 读取方 |
|------|--------|--------|
| `requirement` | 用户 | CoderNode |
| `code` | Coder / Fixer | Reviewer / Fixer |
| `findings` | Reviewer | Fixer / Engine |
| `review_report` | Reviewer | 输出 |
| `fix_log` | Fixer | 输出 |
| `history` | 所有节点 | 输出（日志） |

同时 `state.record(node, event, detail)` 让每个节点写入一条消息到 `history`，
形成可回放的**执行日志**（**消息传递**）。两种机制互补：
状态共享传递「数据」，消息日志传递「过程」。

### 3.2 节点抽象 + 注册表（可扩展性）

```python
class WorkflowNode(object):
    name = "node"
    description = ""
    def run(self, state): raise NotImplementedError

@register
class CoderNode(WorkflowNode): ...
```

- 统一接口 `run(state) -> NodeOutput`，引擎与具体节点解耦；
- `NODES` 注册表按名字管理节点，新增 Agent = 继承 + 实现 + `@register`，
  引擎无需改动（**开闭原则**）；
- 引擎通过构造函数注入 `coder/reviewer/fixer` 三个节点，替换任一节点即改流程。

### 3.3 Mock 模式的「确定性收敛」设计

为了让离线演示真正跑通「编码 → 审查 → 修复 → 再审查 → 通过」闭环：

- **CoderNode（Mock）**：返回一段「带 5 处缺陷」的模板代码（硬编码密钥、可变默认
  参数、裸 except、`== None`、未使用导入），缺陷类型覆盖严重/一般/建议三档；
- **ReviewerNode**：复用 HW1 的静态分析器 `analyze_source` 得到**结构化问题清单**
  （每条带 `kind` 字段），同时跑完整 `CodeReviewAgent`（ReAct 循环）产出报告；
- **FixerNode（Mock）**：按 `kind` 应用**确定性规则修复**（详见 `_FIXERS`），
  一轮即可修掉全部 5 处问题；
- **再审查**：修复后重新分析，0 问题 → 循环结束。

这样整个流程可离线复现、结果可预期，测试也能严格断言收敛。

### 3.4 复用 HW1 的 Agent（审查节点）

审查节点直接复用 Homework1 的 `agent/` 包：

- `ReviewerNode._run_review_agent()` 调用 `CodeReviewAgent.run()`，把代码写入临时
  文件后走完整的 **ReAct 循环**（read_file → analyze → answer）；
- 复用 `MockProvider`（离线）、`OpenAICompatProvider`（真实 LLM）、`Memory`、
  `tools` 静态分析器，全部未改语义。

对 HW1 代码的**最小改动**（均向后兼容）：

| 文件 | 改动 | 目的 |
|------|------|------|
| `agent/tools.py` | 每条 finding 增加 `kind` 字段；新增公开函数 `analyze_source` | 让修复节点能按类型做规则化修复 |
| `agent/llm.py` | 新增 `complete_text()`；把请求逻辑抽成 `_post()` | 编码/修复节点需要「纯文本生成」而非结构化决策 |

其余模块（`core/prompts/memory`）原样复用。

### 3.5 错误处理与恢复

- **节点级**：`engine._step()` 捕获节点异常，记录 `retry` 并重试一次，仍失败则
  抛 `RuntimeError`，由引擎把状态置为 `error` 并记录日志（不崩溃、不静默）；
- **循环级**：修复未产生变化（`changed=False`）→ 判定「无进展」，主动终止循环，
  防止死循环；`max_fix_rounds` 兜底；
- **LLM 级**：复用 HW1 的指数退避、错误分类（429/5xx/网络错误重试）。

## 4. Prompt 设计（编码 / 修复节点）

- **编码节点**：系统提示限定「只输出代码本身」，`complete_text` 拿原始文本，
  再用 `_strip_fences` 剥掉可能的 ``` 代码块；
- **修复节点**：把「原始代码 + 结构化问题清单」拼进 prompt，要求输出修复后完整代码；
- **审查节点**：完全复用 HW1 的 prompt（角色设定 + 工具清单 + JSON 协议 + 报告格式）。

## 5. 扩展点（未来可做）

- **声明式编排**：把流程定义成 DAG/JSON，引擎按图执行（支持并行分支）；
- **并行执行**：多个独立子流程并发跑（如多文件同时审查）；
- **可视化编排界面**：拖拽节点连线、实时状态高亮（当前 Web 仅做执行可视化）；
- **更多节点**：测试生成 Agent、文档 Agent、`git diff` 审查节点等；
- **跨文件分析**：审查节点支持多文件调用图。

## 6. 技术栈

- 语言：Python（3.6+）
- 依赖：**零第三方库**，仅标准库（`ast` / `urllib` / `json` / `http.server` / `unittest` / `tempfile`）
- 复用：Homework1 的 `agent` 包（代码审查 Agent）
- LLM 服务商：可插拔（OpenAI / 通义千问 DashScope / 任意 OpenAI 兼容接口），离线 Mock 兜底
