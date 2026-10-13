# -*- coding: utf-8 -*-
"""上下文记忆：对话历史 + 会话持久化。

记忆是 Agent 的"短期 + 长期"两层：
    history  —— 短期记忆，保存本轮对话的消息序列，构造给 LLM 的上下文；
    facts    —— 长期记忆，简单的键值对，可跨会话保存（如已审查过的文件）。
"""

import json


class Memory(object):
    def __init__(self, max_turns=30):
        self.history = []       # 消息序列：[{"role","content",("name")}, ...]
        self.facts = {}         # 长期记忆：键值对
        self.max_turns = max_turns

    # ---- 写入 ----

    def add(self, role, content, name=None):
        msg = {"role": role, "content": content}
        if name:
            msg["name"] = name
        self.history.append(msg)
        self._trim()

    def add_user(self, text):
        self.add("user", text)

    def add_assistant(self, text):
        self.add("assistant", text)

    def add_tool(self, name, result):
        self.add("tool", result, name=name)

    def remember(self, key, value):
        """写入一条长期记忆。"""
        self.facts[key] = value

    def recall(self, key, default=None):
        """读取一条长期记忆。"""
        return self.facts.get(key, default)

    # ---- 读取 ----

    def _trim(self):
        """限制历史长度，防止上下文无限膨胀。"""
        limit = self.max_turns * 2
        if len(self.history) > limit:
            self.history = self.history[-limit:]

    def to_messages(self, system_prompt):
        """构造发给 LLM 的完整消息列表（系统提示 + 历史）。"""
        msgs = [{"role": "system", "content": system_prompt}]
        msgs.extend(self.history)
        return msgs

    # ---- 持久化 ----

    def to_dict(self):
        return {"history": self.history, "facts": self.facts}

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    def load(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.history = d.get("history", [])
            self.facts = d.get("facts", {})
        except (OSError, ValueError):
            pass
