# -*- coding: utf-8 -*-
"""Prompt 设计模块。

把「代码审查 Agent」用到的所有提示词集中在这里，便于单独打磨 prompt 工程。
作业考察点之一是 "prompt 设计"，因此这里尽量把下面几件事写清楚：
  1. 角色设定（你是谁、要做什么）
  2. 可用工具清单（动态注入，避免和工具实现脱节）
  3. 严格输出格式（JSON 结构化，便于 Agent 解析动作）
  4. 审查关注点与报告格式（对齐评分维度）
"""

import json
import re


def build_system_prompt(tool_descriptions):
    """根据工具清单动态生成系统提示。

    tool_descriptions: 字符串，例如：
        "- read_file(path=文件路径)：读取文本文件并返回带行号的内容\n"
    """
    return (
        "你是一名严谨、专业的高级代码审查员（Code Review Agent）。\n"
        "你的任务是对用户指定的代码文件或项目目录进行审查，找出潜在 Bug、\n"
        "安全隐患、代码坏味道与可维护性问题，并给出可执行的改进建议。\n"
        "\n"
        "## 工作原则\n"
        "1. 不要凭空猜测代码内容，需要查看代码时必须先调用工具获取真实内容；\n"
        "2. 每次只做一步：要么调用一个工具，要么给出最终结论；\n"
        "3. 工具返回结果后，先认真分析，再决定下一步。\n"
        "\n"
        "## 可用工具\n"
        + tool_descriptions +
        "\n"
        "## 输出格式（严格遵守）\n"
        "你每次只能输出一个 JSON 对象，不要输出任何多余文字，格式如下：\n"
        '{"thought": "你的思考过程", "action": "工具名 或 answer", "action_input": {}}\n'
        "\n"
        "- 需要收集信息时：action 填工具名，action_input 填该工具需要的参数（JSON 对象）。\n"
        '- 已经可以下结论时：action 填 "answer"，action_input 填 {"answer": "最终审查报告全文"}。\n'
        "\n"
        "## 审查关注点\n"
        "1. 潜在 Bug：边界条件、空值/None、类型错误、资源未释放、逻辑错误；\n"
        "2. 安全：eval/exec、硬编码密钥、SQL 注入、路径穿越、不安全反序列化；\n"
        "3. 坏味道：重复代码、过长函数、命名不当、魔法数字、过深嵌套；\n"
        "4. 规范性：异常处理是否规范、是否吞异常、可变默认参数等 Python 常见坑。\n"
        "\n"
        "## 报告格式\n"
        "最终答案请用中文，按严重程度分级：\n"
        "【严重】必须修复；【一般】建议修复；【建议】可优化。\n"
        "每条问题请给出：位置（文件:行号）、问题描述、修复建议。\n"
    )


def build_review_task(target):
    """构造发给 Agent 的初始任务描述。"""
    return (
        "请审查以下目标：\n"
        "【目标】" + str(target) + "\n"
        "\n"
        "请先了解目标里有哪些文件、读取代码、做静态分析，"
        "最后输出一份完整的中文审查报告。"
    )


def parse_llm_output(text):
    """把 LLM 输出的文本解析成结构化 dict：{"thought", "action", "action_input"}。

    真实 LLM 的输出可能带代码块包裹、前后废话，这里做宽容解析：
    先尝试直接 JSON，再剥掉 ``` 代码块，最后用首个 { 到末个 } 截取。
    全部失败时，把整段文本当作最终答案兜底。
    """
    if not text:
        return {"thought": "", "action": "answer", "action_input": {"answer": ""}}
    text = text.strip()

    # 1) 去掉 ```json ... ``` 或 ``` ... ``` 包裹
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)

    # 2) 直接解析
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass

    # 3) 截取第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass

    # 4) 兜底：当作最终答案
    return {"thought": "", "action": "answer", "action_input": {"answer": text}}
