# -*- coding: utf-8 -*-
"""Simple Workflow MVP —— 代码审查 Workflow。

Homework 2：搭建一个 2-3 个 Agent 协作的简单 Workflow。

本项目选择「代码审查流程」方向：
    需求 -> 编码 -> 审查 ->（有问题则）修复 -> 再审查 … -> 完成

三个 Agent 节点：
    CoderNode    编码 Agent：把自然语言需求写成代码；
    ReviewerNode 审查 Agent：复用 Homework1 的 CodeReviewAgent 做代码审查；
    FixerNode    修复 Agent：根据审查发现的问题自动修复代码。

包结构：
    state   —— 共享状态（Agent 间通信的数据总线）
    nodes   —— Workflow 节点（Agent）抽象与三个具体实现
    engine  —— Workflow 引擎（顺序执行 + 条件分支 + 循环 + 日志 + 错误恢复）
    main    —— 命令行入口
    webapp  —— 可视化执行界面（可选）
"""

__version__ = "2.0.0"
