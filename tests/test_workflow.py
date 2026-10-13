# -*- coding: utf-8 -*-
"""单元测试：覆盖 Workflow 状态、三个 Agent 节点与整体编排。

运行方式（在项目根目录）：
    python -m unittest discover -s tests -v
或：
    python tests/test_workflow.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.llm import MockProvider
from agent.tools import analyze_source
from workflow.engine import WorkflowEngine
from workflow.nodes import CoderNode, ReviewerNode, FixerNode, NODES
from workflow.state import WorkflowState


def make_factory():
    return lambda: MockProvider()


def build_engine(max_fix_rounds=3):
    f = make_factory()
    return WorkflowEngine(
        CoderNode(f), ReviewerNode(f), FixerNode(f),
        max_fix_rounds=max_fix_rounds,
    )


class TestState(unittest.TestCase):
    def test_shared_state_and_history(self):
        s = WorkflowState("需求A")
        s.record("coder", "start", "编码")
        s.record("coder", "finish", "完成")
        self.assertEqual(len(s.history), 2)
        self.assertEqual(s.history[0]["node"], "coder")
        self.assertEqual(s.critical_count(), 0)


class TestNodes(unittest.TestCase):
    def test_registry_has_three_agents(self):
        for name in ("coder", "reviewer", "fixer"):
            self.assertIn(name, NODES)

    def test_coder_produces_code(self):
        s = WorkflowState("写一个求和的函数")
        CoderNode(make_factory()).run(s)
        self.assertTrue(s.code)
        self.assertIn("def process", s.code)

    def test_reviewer_finds_planted_issues(self):
        s = WorkflowState("写一个求和的函数")
        CoderNode(make_factory()).run(s)
        ReviewerNode(make_factory()).run(s)
        kinds = set(f["kind"] for f in s.findings)
        self.assertIn("hardcoded_secret", kinds)
        self.assertIn("mutable_default", kinds)
        self.assertIn("bare_except", kinds)
        self.assertIn("eq_none", kinds)

    def test_fixer_removes_all_fixable_issues(self):
        s = WorkflowState("写一个求和的函数")
        CoderNode(make_factory()).run(s)
        ReviewerNode(make_factory()).run(s)
        FixerNode(make_factory()).run(s)
        self.assertEqual(analyze_source(s.code), [])


class TestEngine(unittest.TestCase):
    def test_end_to_end_flow(self):
        s = WorkflowState("写一个处理数据的函数")
        build_engine().run(s)
        self.assertEqual(s.status, "done")
        self.assertEqual(s.findings, [])
        self.assertTrue(s.code)
        compile(s.code, "<workflow>", "exec")   # 最终代码必须是合法 Python
        # coder + reviewer + fixer + reviewer，至少 4 条 finish 日志
        self.assertGreaterEqual(len(s.history), 4)

    def test_terminates_within_max_rounds(self):
        s = WorkflowState("任意需求")
        build_engine(max_fix_rounds=2).run(s)
        self.assertIn(s.status, ("done", "error"))
        self.assertLessEqual(s.round, 2)


if __name__ == "__main__":
    unittest.main()
