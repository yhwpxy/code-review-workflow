# -*- coding: utf-8 -*-
"""工具集：Agent 用来获取真实代码信息的能力。

工具（Tool）是 Agent 区别于普通聊天机器人的关键——它让模型能真正读到文件、
看到目录、做静态分析，而不是凭空猜测。

统一接口：
    run_tool(name, args: dict) -> str   # 返回一段给模型看的文本结果
并通过 describe_tools() 把工具清单注入系统提示。
"""

import ast
import os
import re

MAX_READ_CHARS = 12000


def _read_text(path):
    """按多种编码尝试读取文本文件。"""
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

def tool_list_directory(args):
    """列出目录树（限制深度、忽略常见无关目录）。"""
    path = args.get("path", ".")
    depth = int(args.get("depth", 2) or 2)
    if not os.path.exists(path):
        return "错误：路径不存在：%s" % path
    if os.path.isfile(path):
        return "这是一个文件：%s" % path

    ignored = {"__pycache__", "node_modules", ".git", ".venv", "venv", ".idea"}
    lines = []
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in ignored and not d.startswith("."))
        rel = os.path.relpath(root, path)
        cur_depth = 0 if rel == "." else rel.count(os.sep) + 1
        if cur_depth >= depth:
            dirs[:] = []
            continue
        for d in dirs:
            lines.append("[目录] " + (os.path.join(rel, d) if rel != "." else d))
        for f in sorted(files):
            lines.append("[文件] " + (os.path.join(rel, f) if rel != "." else f))
        if len(lines) > 500:
            lines.append("...（内容过多，已截断）")
            break
    return "\n".join(lines) or "（空目录）"


def tool_read_file(args):
    """读取文本文件并返回带行号的内容。"""
    path = args.get("path", "")
    if not path:
        return "错误：缺少参数 path"
    if not os.path.exists(path):
        return "错误：文件不存在：%s" % path
    if os.path.isdir(path):
        return "错误：%s 是目录，请改用 list_directory。" % path

    text = _read_text(path)
    truncated = False
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS]
        truncated = True

    lines = text.splitlines()
    numbered = "\n".join("%5d| %s" % (i + 1, ln) for i, ln in enumerate(lines))
    head = "文件 %s（共 %d 行）：\n" % (path, len(lines))
    if truncated:
        head += "（内容过长，仅显示前 %d 字符）\n" % MAX_READ_CHARS
    return head + numbered


def tool_check_syntax(args):
    """用 compile 检查 Python 文件语法。"""
    path = args.get("path", "")
    if not path:
        return "错误：缺少参数 path"
    if not os.path.exists(path):
        return "错误：文件不存在：%s" % path
    src = _read_text(path)
    try:
        compile(src, path, "exec")
        return "语法检查通过：%s" % path
    except SyntaxError as e:
        return "语法错误：%s 第 %d 行：%s" % (path, e.lineno or 0, e.msg)


# ---------------------------------------------------------------------------
# 静态分析器（规则引擎，供 analyze 工具使用）
# ---------------------------------------------------------------------------

def _is_none_node(node):
    """判断 AST 节点是否为 None 字面量，兼容 Python 3.6/3.7 与 3.8+。"""
    if hasattr(ast, "Constant") and isinstance(node, ast.Constant):
        return node.value is None
    if hasattr(ast, "NameConstant") and isinstance(node, ast.NameConstant):
        return node.value is None
    return isinstance(node, ast.Name) and node.id == "None"


def _analyze_source(source, path="<unknown>"):
    """对一段源码做静态分析，返回问题列表（每条含 severity/line/title/detail/suggestion）。"""
    findings = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [{
            "severity": "严重", "line": e.lineno or 0,
            "kind": "syntax_error",
            "title": "语法错误", "detail": str(e.msg),
            "suggestion": "先修复语法错误",
        }]

    # 1) 收集 import 的根名，近似判断未使用导入
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = (a.asname or a.name).split(".")[0]
                imported[root] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != "*":
                    imported[a.asname or a.name] = node.lineno

    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)

    for name, line in imported.items():
        if name not in used:
            findings.append({
                "severity": "建议", "line": line, "kind": "unused_import",
                "title": "可能未使用的导入",
                "detail": "导入了 %r 但代码中未发现引用" % name,
                "suggestion": "删除多余导入，保持代码整洁",
            })

    # 2) 逐节点检查常见问题
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
                findings.append({
                    "severity": "严重", "line": node.lineno, "kind": "eval_exec",
                    "title": "使用 %s 执行动态代码" % node.func.id,
                    "detail": "eval/exec 可被注入恶意代码，存在安全风险",
                    "suggestion": "改用安全的白名单解析（如 ast.literal_eval）",
                })

        elif isinstance(node, ast.FunctionDef):
            defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d]
            for d in defaults:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    findings.append({
                        "severity": "一般", "line": d.lineno, "kind": "mutable_default",
                        "title": "函数 %r 使用了可变默认参数" % node.name,
                        "detail": "默认参数在定义时只求值一次，会被多次调用共享，造成隐蔽 bug",
                        "suggestion": "改为默认 None，函数体内部再初始化",
                    })
            if len(node.body) > 40:
                findings.append({
                    "severity": "建议", "line": node.lineno, "kind": "long_function",
                    "title": "函数 %r 过长（%d 条语句）" % (node.name, len(node.body)),
                    "detail": "过长函数难以理解与测试",
                    "suggestion": "拆分成多个职责单一的小函数",
                })

        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                findings.append({
                    "severity": "一般", "line": node.lineno, "kind": "bare_except",
                    "title": "裸 except 捕获了所有异常",
                    "detail": "会吞掉 KeyboardInterrupt、SystemExit 等，掩盖真实错误",
                    "suggestion": "捕获具体异常类型，并记录日志",
                })
            body = [b for b in node.body if not isinstance(b, ast.Pass)]
            if not body:
                findings.append({
                    "severity": "一般", "line": node.lineno, "kind": "swallowed_exception",
                    "title": "except 分支吞掉了异常",
                    "detail": "异常被静默忽略，问题难以排查",
                    "suggestion": "至少记录日志或重新抛出",
                })

        elif isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, ast.Eq) and _is_none_node(comp):
                    findings.append({
                        "severity": "建议", "line": node.lineno, "kind": "eq_none",
                        "title": "使用 == 与 None 比较",
                        "detail": "应使用 is None 判断空值，语义更准确",
                        "suggestion": "改写为 is None / is not None",
                    })

    # 3) 硬编码密钥/口令（字符串常量正则，逐行扫描）
    secret_re = re.compile(
        r"(password|passwd|secret|api_key|apikey|token|access_key|private_key)\s*=\s*[\"'][^\"']+[\"']",
        re.IGNORECASE,
    )
    for i, line in enumerate(source.splitlines(), 1):
        if secret_re.search(line):
            findings.append({
                "severity": "严重", "line": i, "kind": "hardcoded_secret",
                "title": "疑似硬编码密钥/口令",
                "detail": "在源码里发现了明文密钥或密码",
                "suggestion": "改为从环境变量或配置文件读取，且不要提交到仓库",
            })

    order = {"严重": 0, "一般": 1, "建议": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), f["line"]))
    return findings


def _format_findings(path, findings):
    if not findings:
        return "未发现明显问题：%s" % path
    lines = ["静态分析结果（%s，共 %d 条问题）：" % (path, len(findings))]
    for f in findings:
        lines.append("- [%s] 第 %d 行 %s：%s（建议：%s）" % (
            f["severity"], f["line"], f["title"], f["detail"], f["suggestion"]))
    return "\n".join(lines)


def analyze_source(source, path="<code>"):
    """对一段源码做静态分析，返回结构化问题列表。

    与 tool_analyze 不同：直接接受源码字符串而非文件路径，返回原始
    findings 列表（每条含 kind/severity/line/title/detail/suggestion），
    供 Workflow 的审查节点复用——修复节点据此做规则化修复。
    """
    return _analyze_source(source, path)


def tool_analyze(args):
    """对指定 Python 文件/目录做静态分析（语法 + 常见问题）。"""
    path = args.get("path", "")
    if not path:
        return "错误：缺少参数 path"
    if not os.path.exists(path):
        return "错误：文件不存在：%s" % path

    if os.path.isfile(path):
        src = _read_text(path)
        return _format_findings(path, _analyze_source(src, path))

    # 目录：收集 .py 文件逐个分析（限制数量）
    files = []
    ignored = {"__pycache__", "node_modules", ".git", ".venv", "venv"}
    for root, dirs, fs in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in ignored and not d.startswith("."))
        for f in sorted(fs):
            if f.endswith(".py"):
                files.append(os.path.join(root, f))
        if len(files) > 50:
            break
    if not files:
        return "目录 %s 下没有 Python 文件。" % path

    out = []
    for fp in files[:50]:
        src = _read_text(fp)
        out.append(_format_findings(fp, _analyze_source(src, fp)))
    return "\n\n".join(out)


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

TOOLS = {
    "list_directory": {
        "description": "列出目录下的文件与子目录，用于了解项目结构",
        "parameters": {"path": "目录路径", "depth": "最大层级（默认 2）"},
        "func": tool_list_directory,
    },
    "read_file": {
        "description": "读取文本文件并返回带行号的内容",
        "parameters": {"path": "文件路径"},
        "func": tool_read_file,
    },
    "check_syntax": {
        "description": "检查 Python 文件语法是否正确",
        "parameters": {"path": "文件路径"},
        "func": tool_check_syntax,
    },
    "analyze": {
        "description": "对 Python 文件/目录做静态分析，找出潜在 Bug、安全隐患与坏味道",
        "parameters": {"path": "文件或目录路径"},
        "func": tool_analyze,
    },
}


def describe_tools():
    """生成工具清单文本，注入系统提示。"""
    parts = []
    for name, spec in TOOLS.items():
        params = ", ".join("%s=%s" % (k, v) for k, v in spec["parameters"].items())
        parts.append("- %s(%s)：%s" % (name, params, spec["description"]))
    return "\n".join(parts)


def run_tool(name, args):
    """执行工具，返回文本结果。"""
    if name not in TOOLS:
        raise ValueError("未知工具：%s" % name)
    if not isinstance(args, dict):
        args = {}
    return TOOLS[name]["func"](args)
