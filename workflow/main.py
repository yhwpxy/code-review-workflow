# -*- coding: utf-8 -*-
"""命令行入口。

用法示例：
    python run.py "写一个把列表里所有数字相加的函数"    # Mock 离线演示完整 Workflow
    python run.py "..." --verbose                      # 打印每个节点与每一步推理
    python run.py "..." --max-rounds 5                 # 最多修复 5 轮
    python run.py "..." -o result.json                 # 保存执行日志与结果
    python run.py "..." --web                          # 启动可视化界面
    python run.py "..." --config config.json           # 接入真实 LLM
"""

import argparse
import json
import os
import sys

from agent.llm import create_provider
from .engine import WorkflowEngine
from .nodes import CoderNode, ReviewerNode, FixerNode
from .state import WorkflowState


def load_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except ValueError as e:
        print("警告：配置文件解析失败（%s），已忽略。" % e)
        return {}


def build_parser():
    p = argparse.ArgumentParser(
        prog="code-review-workflow",
        description="Simple Workflow MVP：编码 -> 审查 -> 修复 的 3-Agent 协作流程。",
    )
    p.add_argument("requirement", help="用自然语言描述的功能需求")
    p.add_argument("--mock", action="store_true", help="使用离线 Mock（无需 API key，默认）")
    p.add_argument("--config", help="配置文件路径（默认 config.json）")
    p.add_argument("--provider", choices=["mock", "openai", "dashscope"],
                   help="LLM 服务商：mock / openai / dashscope(通义千问)")
    p.add_argument("--model", help="覆盖配置文件里的模型名")
    p.add_argument("--max-rounds", type=int, default=3, help="最大修复轮数（默认 3）")
    p.add_argument("--output", "-o", help="把执行结果（JSON）写入指定文件")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="打印每个节点与每一步推理过程")
    p.add_argument("--web", action="store_true", help="启动可视化界面（默认 127.0.0.1:8001）")
    p.add_argument("--port", type=int, default=8001, help="可视化界面端口")
    return p


def _summarize(state):
    lines = []
    lines.append("=" * 60)
    lines.append("Workflow 执行结果")
    lines.append("=" * 60)
    lines.append("需求：%s" % state.requirement)
    lines.append("状态：%s" % ("成功" if state.status == "done" else state.status))
    if state.error:
        lines.append("错误：%s" % state.error)
    lines.append("修复轮数：%d" % state.round)
    lines.append("最终问题数：%d（严重 %d 个）" % (len(state.findings), state.critical_count()))
    lines.append("")
    lines.append("--- 最终代码 ---")
    lines.append(state.code)
    lines.append("")
    lines.append("--- 审查报告 ---")
    lines.append(state.review_report)
    if state.fix_log:
        lines.append("")
        lines.append("--- 修复记录 ---")
        for s in state.fix_log:
            lines.append("  * " + s)
    lines.append("")
    lines.append("--- 执行日志（消息传递记录） ---")
    for h in state.history:
        lines.append("  [%s] %s %s" % (h["node"], h["event"], h["detail"]))
    return "\n".join(lines)


def main(argv=None):
    args = build_parser().parse_args(argv)

    # 可视化界面模式
    if args.web:
        from .webapp import run_web
        config = load_config(args.config or "config.json")
        provider_factory = lambda: create_provider(args, config)
        run_web(provider_factory, max_rounds=args.max_rounds, port=args.port)
        return 0

    config = load_config(args.config or "config.json")
    provider_factory = lambda: create_provider(args, config)

    state = WorkflowState(args.requirement)
    engine = WorkflowEngine(
        CoderNode(provider_factory, verbose=args.verbose),
        ReviewerNode(provider_factory, verbose=args.verbose),
        FixerNode(provider_factory, verbose=args.verbose),
        max_fix_rounds=args.max_rounds,
        verbose=args.verbose,
    )
    engine.run(state)

    print()
    print(_summarize(state))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        print("\n[已保存执行结果] %s" % args.output)

    return 0 if state.status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
