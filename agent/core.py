# -*- coding: utf-8 -*-
"""Agent 核心循环（ReAct 风格）。

ReAct 模式：Reasoning + Acting，即「推理 → 行动 → 观察 → 再推理」的循环，
把大模型的推理能力与工具的执行能力结合起来。

本模块实现的循环：
    while 未达到最大步数:
        1. 用系统提示 + 记忆历史构造消息，调用 LLM；
        2. 解析 LLM 的决策（thought / action / action_input）；
        3. 若 action == "answer" -> 输出最终答案，结束；
        4. 否则执行对应工具，把结果写回记忆，进入下一轮。
"""

from . import tools
from .llm import LLMError, RateLimitError
from .prompts import build_system_prompt, build_review_task


class AgentResult(object):
    """一轮审查的结果。"""

    def __init__(self, answer, steps, tool_calls):
        self.answer = answer          # 最终审查报告文本
        self.steps = steps            # 消耗的推理步数
        self.tool_calls = tool_calls  # 实际调用过的工具名列表

    def __repr__(self):
        return "<AgentResult steps=%d tools=%s>" % (self.steps, self.tool_calls)


class CodeReviewAgent(object):
    """代码审查 Agent。"""

    def __init__(self, provider, memory, max_iterations=10, verbose=False):
        self.provider = provider
        self.memory = memory
        self.max_iterations = max_iterations
        self.verbose = verbose

    def run(self, target):
        """对 target（文件或目录）执行一轮完整审查，返回 AgentResult。"""
        system_prompt = build_system_prompt(tools.describe_tools())
        self.memory.add_user(build_review_task(target))

        tool_calls = []

        for step in range(1, self.max_iterations + 1):
            messages = self.memory.to_messages(system_prompt)

            # ---- 调用 LLM，带错误处理 ----
            try:
                decision = self.provider.complete(messages)
            except RateLimitError as e:
                # 限流：给 LLM 反馈，下一轮重试；步数上限保证了不会无限循环
                if self.verbose:
                    print("  [!] 限流：%s，稍后重试" % e)
                self.memory.add_tool("_error", "系统限流，请稍后重试。")
                continue
            except LLMError as e:
                if self.verbose:
                    print("  [!] LLM 调用失败：%s" % e)
                return AgentResult("LLM 调用失败：%s" % e, step, tool_calls)

            action = decision.get("action")
            action_input = decision.get("action_input") or {}

            if self.verbose:
                print("  [step %d] 思考：%s" % (step, decision.get("thought", "")))
                print("  [step %d] 动作：%s" % (step, action))

            # ---- 最终答案 ----
            if action == "answer":
                if isinstance(action_input, dict):
                    answer = action_input.get("answer", "")
                else:
                    answer = str(action_input)
                self.memory.add_assistant(answer)
                self.memory.remember("last_review_target", target)
                return AgentResult(answer, step, tool_calls)

            # ---- 工具调用 ----
            if action in tools.TOOLS:
                try:
                    result = tools.run_tool(action, action_input)
                except Exception as e:
                    # 工具执行出错也要反馈给 LLM，而不是直接崩溃
                    result = "工具执行出错：%s" % e
                tool_calls.append(action)
                self.memory.add_tool(action, result)
            else:
                # 未知动作：反馈给 LLM，让它纠正
                self.memory.add_tool(
                    "_error",
                    "未知动作 %r，可用工具：%s" % (action, ", ".join(tools.TOOLS.keys())),
                )

        # 达到最大步数仍未给出结论
        return AgentResult(
            "（达到最大推理步数 %d，未产出结论）" % self.max_iterations,
            step, tool_calls,
        )
