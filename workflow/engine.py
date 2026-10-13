# -*- coding: utf-8 -*-
"""Workflow 引擎：把多个 Agent 节点编排成一个完整流程。

流程控制（三种都覆盖）：
    1. 顺序执行   编码 -> 审查 ->（修复 -> 审查）*；
    2. 条件分支   审查发现可修复问题才进入「修复」分支，否则直接结束；
    3. 循环       修复后重新审查，直到通过或达到最大修复轮数。

同时负责：
    - 执行日志与状态追踪：每个节点的 start/finish/retry/error 都写入 state.history；
    - 错误处理与恢复：节点抛异常时记录并重试一次，仍失败则终止并标记 error；
    - 死循环防护：修复未改变代码（无进展）时主动停止循环。
"""

from .state import WorkflowState  # noqa: F401  (仅文档引用)


class WorkflowEngine(object):
    def __init__(self, coder, reviewer, fixer, max_fix_rounds=3, verbose=False):
        self.coder = coder
        self.reviewer = reviewer
        self.fixer = fixer
        self.max_fix_rounds = max_fix_rounds
        self.verbose = verbose

    def run(self, state):
        state.status = "running"
        try:
            # 顺序：编码 -> 审查
            self._step(state, self.coder)
            self._step(state, self.reviewer)

            # 循环 + 条件分支：有问题且未超过最大轮数 -> 修复 -> 再审查
            while state.findings and state.round < self.max_fix_rounds:
                state.round += 1
                if self.verbose:
                    print("\n--- 第 %d 轮修复 ---" % state.round)
                fix_out = self._step(state, self.fixer)
                if not fix_out.data.get("changed"):
                    state.record("engine", "stop",
                                 "修复未改变代码，剩余问题需人工/真实 LLM 处理，终止循环")
                    break
                self._step(state, self.reviewer)

            state.status = "done"
        except Exception as e:
            state.status = "error"
            state.error = str(e)
            state.record("engine", "error", str(e))
        return state

    def _step(self, state, node):
        """执行单个节点：记录日志 + 失败重试一次（错误处理与恢复）。"""
        if self.verbose:
            print("\n== [%s] %s ==" % (node.name, node.description))
        state.record(node.name, "start", node.description)
        out = None
        try:
            out = node.run(state)
        except Exception as e:
            state.record(node.name, "retry", "首次执行失败：%s，重试一次" % e)
            if self.verbose:
                print("  [!] 首次执行失败（%s），重试一次" % e)
            try:
                out = node.run(state)
            except Exception as e2:
                raise RuntimeError("节点 %s 执行失败：%s" % (node.name, e2))
        state.record(node.name, "finish", out.message)
        if self.verbose:
            print("  -> %s" % out.message)
        return out
