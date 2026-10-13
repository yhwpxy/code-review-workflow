# -*- coding: utf-8 -*-
"""代码审查 Agent（Code Review Agent）包。

一个用纯 Python 标准库实现的「代码助手 Agent」，演示 Agent 开发的核心能力：
LLM 调用、prompt 设计、工具集成、上下文记忆、错误处理与重试。

模块划分：
    prompts  —— prompt 设计（系统提示 / 任务构造 / 输出解析）
    llm      —— LLM Provider 抽象（Mock 离线实现 + OpenAI 兼容实现）
    tools    —— 工具集（文件读取 / 目录列举 / 语法检查 / AST 静态分析）
    memory   —— 上下文记忆（对话历史 + 会话持久化）
    core     —— Agent 核心循环（输入 → 推理 → 工具调用 → 输出）
    main     —— 命令行入口
    webapp   —— 简易 Web 界面（可选）
"""

__version__ = "1.0.0"
