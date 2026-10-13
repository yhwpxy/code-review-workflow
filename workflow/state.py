# -*- coding: utf-8 -*-
"""Workflow 共享状态：Agent 之间通信的「数据总线」。

多个 Agent 不直接相互调用，而是通过读写同一个 WorkflowState 对象来
传递信息——这就是「状态共享」。同时 state.history 记录每个节点写下的
执行消息，形成可回放的执行日志（消息传递 + 状态追踪）。

字段契约（谁写、谁读）：
    requirement     用户需求       CoderNode 读
    code            当前代码       CoderNode 写；Reviewer / Fixer 读写
    findings        问题清单       ReviewerNode 写；Fixer / 引擎 读
    review_report   审查报告       ReviewerNode 写
    fix_log         修复说明       FixerNode 写
    round           修复轮数       引擎 写
    status / error  运行状态       引擎 写
    history         执行日志       所有节点 + 引擎 写
"""


class WorkflowState(object):
    def __init__(self, requirement):
        self.requirement = requirement
        self.code = ""
        self.findings = []
        self.review_report = ""
        self.fix_log = []
        self.round = 0
        self.status = "pending"      # pending -> running -> done / error
        self.error = ""
        self.history = []

    # ---- 消息传递 / 执行日志 ----

    def record(self, node, event, detail=""):
        """写入一条执行日志（Agent 间消息传递的载体）。"""
        self.history.append({
            "node": node,
            "event": event,          # start / finish / retry / stop / error
            "detail": detail,
        })

    # ---- 状态查询 ----

    def critical_count(self):
        """统计「严重」级别问题数。"""
        return sum(1 for f in self.findings if f.get("severity") == "严重")

    def to_dict(self):
        """序列化整个状态，便于保存执行结果。"""
        return {
            "requirement": self.requirement,
            "status": self.status,
            "error": self.error,
            "round": self.round,
            "code": self.code,
            "findings": self.findings,
            "review_report": self.review_report,
            "fix_log": self.fix_log,
            "history": self.history,
        }
