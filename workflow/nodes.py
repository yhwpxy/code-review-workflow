# -*- coding: utf-8 -*-
"""Workflow 节点（Agent）。

把「代码审查流程」拆成 3 个可独立替换的 Agent 节点：

    编码 Agent（CoderNode）    ：需求 -> 代码
    审查 Agent（ReviewerNode） ：代码 -> 问题清单 + 审查报告（复用 HW1 的 CodeReviewAgent）
    修复 Agent（FixerNode）    ：代码 + 问题清单 -> 修复后的代码

统一接口：
    node.run(state: WorkflowState) -> NodeOutput
每个节点只通过共享的 WorkflowState 读写数据，实现 Agent 间通信。

新增一个 Agent：继承 WorkflowNode、实现 run()、用 @register 注册即可，
引擎无需改动（开闭原则）。
"""

import os
import re
import tempfile

from agent.core import CodeReviewAgent
from agent.llm import MockProvider
from agent.memory import Memory
from agent.tools import analyze_source


# ---------------------------------------------------------------------------
# 节点输出与抽象基类
# ---------------------------------------------------------------------------

class NodeOutput(object):
    """一个节点执行后的返回结果。"""

    def __init__(self, ok, message, data=None):
        self.ok = ok                    # 节点本身是否成功执行
        self.message = message          # 人类可读的执行小结
        self.data = data or {}          # 附加数据（如 fixer 的 changed / fixes）


class WorkflowNode(object):
    """所有 Workflow 节点的基类。"""

    name = "node"
    description = ""

    def run(self, state):
        raise NotImplementedError


NODES = {}


def register(cls):
    """把节点类注册到 NODES 注册表，便于按名字查找 / 扩展。"""
    NODES[cls.name] = cls
    return cls


# ---------------------------------------------------------------------------
# 编码节点（CoderNode）
# ---------------------------------------------------------------------------

# Mock 模式下返回的「带缺陷」示例代码。
# 刻意包含 5 处问题（严重/一般/建议各若干），用于演示「审查 -> 修复 -> 再审查」闭环。
_MOCK_CODE_BODY = '''import math

API_KEY = "sk-1234567890abcdef"


def process(data, items=[]):
    if data == None:
        return None
    items.append(data)
    total = 0
    for x in items:
        total = total + x
    return total


def load(path):
    try:
        with open(path) as f:
            return f.read()
    except:
        return ""
'''

_CODER_SYSTEM = (
    "你是一名 Python 程序员。根据用户需求写一段简洁、可直接运行的 Python 代码。"
    "只输出代码本身，不要任何解释或 Markdown 代码块。"
)


def _strip_fences(text):
    """去掉 LLM 可能包在外面的 ``` 代码块。"""
    text = (text or "").strip()
    m = re.search(r"```(?:python)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


@register
class CoderNode(WorkflowNode):
    name = "coder"
    description = "编码 Agent：需求 -> 代码"

    def __init__(self, provider_factory, verbose=False):
        self.provider_factory = provider_factory
        self.mock = isinstance(provider_factory(), MockProvider)
        self.verbose = verbose

    def run(self, state):
        if self.mock:
            code = "# 需求：%s\n" % state.requirement + _MOCK_CODE_BODY
        else:
            provider = self.provider_factory()
            messages = [
                {"role": "system", "content": _CODER_SYSTEM},
                {"role": "user", "content": "需求：%s" % state.requirement},
            ]
            code = _strip_fences(provider.complete_text(messages))
        state.code = code
        n_lines = len(code.splitlines())
        return NodeOutput(True, "编码完成，产出 %d 行代码" % n_lines)


# ---------------------------------------------------------------------------
# 审查节点（ReviewerNode）：复用 HW1 的 CodeReviewAgent
# ---------------------------------------------------------------------------

@register
class ReviewerNode(WorkflowNode):
    name = "reviewer"
    description = "审查 Agent：代码 -> 问题清单 + 审查报告（复用 HW1 的 CodeReviewAgent）"

    def __init__(self, provider_factory, verbose=False):
        self.provider_factory = provider_factory
        self.verbose = verbose

    def run(self, state):
        # 1) 确定性静态分析：得到结构化问题清单（供 Fixer 与循环判定使用）
        state.findings = analyze_source(state.code, "<workflow-generated-code>")

        # 2) 复用 HW1 的 CodeReviewAgent 跑完整 ReAct 循环，得到审查报告
        state.review_report = self._run_review_agent(state.code)

        n = len(state.findings)
        if n == 0:
            msg = "审查通过：未发现问题"
        else:
            msg = "审查完成：发现 %d 个问题（严重 %d 个）" % (n, state.critical_count())
        return NodeOutput(True, msg, {"findings": state.findings})

    def _run_review_agent(self, code):
        """把代码写入临时文件，交给 HW1 的 CodeReviewAgent 审查。"""
        fd, tmp = tempfile.mkstemp(suffix=".py", prefix="wf_review_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        try:
            provider = self.provider_factory()  # 每轮新建，保证 Mock 的步数重置
            agent = CodeReviewAgent(provider, Memory(), verbose=self.verbose)
            return agent.run(tmp).answer
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 修复节点（FixerNode）
# ---------------------------------------------------------------------------

# 修复规则：kind -> 处理器。处理器就地修改 lines，返回本次修复说明列表。
# 按「行号从大到小」处理，避免删除/插入行导致后续行号错位。


def _fix_unused_import(lines, line, f):
    lines[line - 1] = None
    return ["删除未使用的导入（第 %d 行）" % line]


def _fix_eq_none(lines, line, f):
    lines[line - 1] = re.sub(r"==\s*None\b", "is None", lines[line - 1])
    lines[line - 1] = re.sub(r"!=\s*None\b", "is not None", lines[line - 1])
    return ["把 == None 改为 is None（第 %d 行）" % line]


def _fix_bare_except(lines, line, f):
    lines[line - 1] = re.sub(r"except\s*:", "except Exception:", lines[line - 1])
    return ["把裸 except 改为 except Exception（第 %d 行）" % line]


def _fix_hardcoded_secret(lines, line, f):
    lines[line - 1] = re.sub(
        r'=\s*["\'][^"\']*["\']',
        '= None  # TODO: 从环境变量读取',
        lines[line - 1], count=1)
    return ["移除硬编码密钥（第 %d 行）" % line]


def _fix_mutable_default(lines, line, f):
    src = lines[line - 1]
    m = re.search(r"(\b[A-Za-z_]\w*\b)\s*=\s*(\[\s*\]|\{\s*\})", src)
    if not m:
        return ["可变默认参数无法自动修复（第 %d 行），需人工处理" % line]
    arg = m.group(1)
    src = src[:m.start()] + arg + "=None" + src[m.end():]
    lines[line - 1] = src
    indent = re.match(r"[ \t]*", src).group(0) + "    "
    lines.insert(line, indent + "if %s is None:" % arg)
    lines.insert(line + 1, indent + "    " + "%s = []" % arg)
    return ["修复可变默认参数 %s（第 %d 行）" % (arg, line)]


_FIXERS = {
    "unused_import": _fix_unused_import,
    "eq_none": _fix_eq_none,
    "bare_except": _fix_bare_except,
    "hardcoded_secret": _fix_hardcoded_secret,
    "mutable_default": _fix_mutable_default,
    # 其它 kind（eval_exec / swallowed_exception / long_function / syntax_error）
    # 默认不自动修复，交由真实 LLM 或人工处理——这是「best-effort 修复」的边界。
}

_FIXER_SYSTEM = (
    "你是一名 Python 程序员。根据审查发现的问题清单修复代码，"
    "只输出修复后的完整代码，不要任何解释或 Markdown 代码块。"
)


@register
class FixerNode(WorkflowNode):
    name = "fixer"
    description = "修复 Agent：代码 + 问题清单 -> 修复后的代码"

    def __init__(self, provider_factory, verbose=False):
        self.provider_factory = provider_factory
        self.mock = isinstance(provider_factory(), MockProvider)
        self.verbose = verbose

    def run(self, state):
        if not state.findings:
            return NodeOutput(True, "没有需要修复的问题",
                              {"changed": False, "fixes": []})

        if self.mock:
            new_code, fixes = self._fix_by_rules(state.code, state.findings)
        else:
            new_code = self._fix_by_llm(state.code, state.findings)
            fixes = ["由真实 LLM 生成修复"]

        changed = new_code != state.code
        state.code = new_code
        state.fix_log = fixes
        return NodeOutput(True, "已修复 %d 处问题" % len(fixes),
                          {"changed": changed, "fixes": fixes})

    def _fix_by_rules(self, code, findings):
        """规则化修复：按问题 kind 应用确定性改写。"""
        lines = code.split("\n")
        log = []
        # 从下往上处理，避免删除/插入行导致行号错位
        for f in sorted(findings, key=lambda x: x.get("line", 0), reverse=True):
            kind = f.get("kind")
            line = f.get("line", 0)
            if not kind or not line or kind not in _FIXERS:
                continue
            if line - 1 >= len(lines) or lines[line - 1] is None:
                continue
            log.extend(_FIXERS[kind](lines, line, f))
        new_code = "\n".join(l for l in lines if l is not None)
        return new_code, log

    def _fix_by_llm(self, code, findings):
        """真实 LLM：把代码 + 问题清单交给模型生成修复。"""
        provider = self.provider_factory()
        text = "\n".join("- [%s] 第 %d 行 %s：%s（建议：%s）" % (
            f["severity"], f["line"], f["title"], f["detail"], f["suggestion"])
            for f in findings)
        messages = [
            {"role": "system", "content": _FIXER_SYSTEM},
            {"role": "user", "content":
                "原始代码：\n```\n%s\n```\n\n发现的问题：\n%s" % (code, text)},
        ]
        return _strip_fences(provider.complete_text(messages))
