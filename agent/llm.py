# -*- coding: utf-8 -*-
"""LLM Provider 抽象层。

设计目标：让 Agent 核心循环不关心底层用哪个大模型，方便切换。

统一接口：
    complete(messages: list[dict]) -> dict
        messages = [{"role": "system"|"user"|"assistant"|"tool", "content": "..."}]
        返回已解析的结构化结果 {"thought", "action", "action_input"}。

内置两个实现：
    MockProvider          —— 离线可用，用固定剧本模拟「推理 + 工具调用」，
                             没有 API key 也能完整跑通 Agent 循环（演示/测试用）。
    OpenAICompatProvider  —— 用标准库 urllib 调用任意 OpenAI 兼容接口
                             （OpenAI / 通义千问 DashScope / 各类中转站），
                             带超时、指数退避重试与错误分类。
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

from .prompts import parse_llm_output


# ---------------------------------------------------------------------------
# 异常体系
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """LLM 调用相关的通用错误基类。"""


class RateLimitError(LLMError):
    """触发限流（429）且重试后仍失败。"""


class ProviderError(LLMError):
    """服务端返回错误，或响应格式异常。"""


# ---------------------------------------------------------------------------
# Provider 接口
# ---------------------------------------------------------------------------

class LLMProvider(object):
    """所有 Provider 的基类。"""

    name = "base"

    def complete(self, messages):
        """调用一次 LLM，返回结构化决策。"""
        raise NotImplementedError

    def complete_text(self, messages):
        """调用一次 LLM，返回纯文本（供编码/修复等文本生成节点使用）。"""
        raise NotImplementedError


def _extract_target(messages):
    """从消息历史里解析出【目标】...，供 MockProvider 使用。"""
    for m in messages:
        if m.get("role") == "user":
            mt = re.search(r"【目标】(.+?)(?:\n|$)", m.get("content", ""))
            if mt:
                return mt.group(1).strip()
    return "."


# ---------------------------------------------------------------------------
# Mock 实现：离线跑通整个循环
# ---------------------------------------------------------------------------

class MockProvider(LLMProvider):
    """离线 Mock：用固定剧本模拟 LLM 的推理与工具调用。

    剧本（三步）：read_file -> analyze -> answer。
    第 3 步会把历史里 analyze 工具的返回结果整理成报告，从而完整走通
    「输入 -> 推理 -> 工具调用 -> 输出」的 Agent 循环，无需任何 API key。
    """

    name = "mock"

    def __init__(self):
        self._step = 0

    def complete(self, messages):
        target = _extract_target(messages)
        self._step += 1

        if self._step == 1:
            return {
                "thought": "开始审查前，先读取目标文件的源代码，了解要审什么。",
                "action": "read_file",
                "action_input": {"path": target},
            }
        if self._step == 2:
            return {
                "thought": "已拿到代码，接下来做静态分析（语法检查 + AST 扫描常见问题）。",
                "action": "analyze",
                "action_input": {"path": target},
            }

        # 第 3 步：汇总成报告
        return {
            "thought": "信息收集完毕，汇总成审查报告。",
            "action": "answer",
            "action_input": {"answer": self._compose_report(messages)},
        }

    def _compose_report(self, messages):
        analyze_result = ""
        for m in messages:
            if m.get("role") == "tool" and m.get("name") == "analyze":
                analyze_result = m.get("content", "")
        if analyze_result:
            return (
                "# 代码审查报告（Mock 模式）\n\n"
                + analyze_result
                + "\n\n> 说明：当前未配置真实 LLM API key，本报告由内置静态分析器生成。\n"
                "> 在 config.json 中配置 API key 后，可得到更深入的语义级审查。"
            )
        return "未能完成审查：分析工具未返回结果。"


# ---------------------------------------------------------------------------
# OpenAI 兼容实现：真实 LLM 调用
# ---------------------------------------------------------------------------

class OpenAICompatProvider(LLMProvider):
    """调用 OpenAI 兼容的 Chat Completions 接口。

    兼容 OpenAI、通义千问 DashScope（compatible-mode）以及众多国产中转站。
    使用标准库 urllib，避免第三方依赖；带超时、指数退避重试与错误分类。
    """

    name = "openai_compat"

    def __init__(self, base_url, api_key, model, temperature=0.2,
                 max_tokens=2000, timeout=60, max_retries=3):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries

    def _build_request(self, data):
        url = self.base_url + "/chat/completions"
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + (self.api_key or ""))
        return req

    def _post(self, messages):
        """发送一次 Chat Completions 请求，返回模型原始文本内容。

        把「带超时、指数退避、错误分类」的请求逻辑抽出来，供
        complete（结构化解析）与 complete_text（纯文本）共用。
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        data = json.dumps(payload).encode("utf-8")

        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                req = self._build_request(data)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                    obj = json.loads(body)
                    return obj["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                last_err = e
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:200]
                except Exception:
                    pass
                if e.code == 429:
                    if attempt < self.max_retries:
                        time.sleep(2 ** attempt)  # 指数退避
                        continue
                    raise RateLimitError("触发限流(429)，已重试 %d 次" % self.max_retries)
                if 500 <= e.code < 600 and attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise ProviderError("服务端错误 %d: %s" % (e.code, detail))
            except urllib.error.URLError as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise ProviderError("网络错误: %s" % e.reason)
            except (KeyError, IndexError, ValueError) as e:
                raise ProviderError("响应格式异常: %s" % e)

        raise ProviderError("重试 %d 次后仍失败: %s" % (self.max_retries, last_err))

    def complete(self, messages):
        """结构化决策：解析 LLM 输出为 {"thought","action","action_input"}。"""
        return parse_llm_output(self._post(messages))

    def complete_text(self, messages):
        """纯文本生成：返回 LLM 原始输出（供编码/修复等文本生成节点使用）。"""
        return self._post(messages)


# ---------------------------------------------------------------------------
# 工厂：根据命令行参数与配置创建 Provider
# ---------------------------------------------------------------------------

def create_provider(args, config):
    """根据 args（argparse.Namespace）和 config（dict）创建 Provider。

    优先级：命令行 > 配置文件 > 环境变量 > 默认回退到 Mock。
    """
    config = config or {}
    choice = args.provider or config.get("provider", "mock")

    if args.mock or choice == "mock":
        return MockProvider()

    # 收集所有可能的 key（环境变量兜底）
    env_key = (os.environ.get("OPENAI_API_KEY")
               or os.environ.get("DASHSCOPE_API_KEY")
               or os.environ.get("ANTHROPIC_API_KEY")
               or "")

    if choice in ("openai", "openai_compat"):
        c = config.get("openai", {})
        base_url = c.get("base_url", "https://api.openai.com/v1")
        model = c.get("model", "gpt-4o-mini")
        key = c.get("api_key") or env_key
    elif choice == "dashscope":
        c = config.get("dashscope", {})
        base_url = c.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        model = c.get("model", "qwen-plus")
        key = c.get("api_key") or env_key
    else:
        print("警告：未知 provider %r，回退到 Mock 模式。" % choice)
        return MockProvider()

    if not key:
        print("警告：未配置 API key，回退到 Mock 模式。请在 config.json 或环境变量中设置。")
        return MockProvider()

    model = args.model or model
    return OpenAICompatProvider(
        base_url=base_url,
        api_key=key,
        model=model,
        temperature=float(c.get("temperature", 0.2)),
        max_tokens=int(c.get("max_tokens", 2000)),
    )
