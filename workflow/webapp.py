# -*- coding: utf-8 -*-
"""可视化 Workflow 执行界面。

标准库 http.server 实现，浏览器打开 http://127.0.0.1:8001：
    - 输入需求 -> 点击执行 -> 后台跑完整 Workflow；
    - 前端把「编码 -> 审查 -> 修复 -> 再审查」渲染成一条流水线，
      并展示最终代码、审查报告、修复记录与执行日志（状态追踪可视化）。
"""

import json
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer

from .engine import WorkflowEngine
from .nodes import CoderNode, ReviewerNode, FixerNode
from .state import WorkflowState


class _SafeHTTPServer(HTTPServer):
    """解决 Windows 中文主机名下 socket.getfqdn 的编码崩溃问题（同 HW1）。"""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = self.server_address[0] or "localhost"
        self.server_port = self.server_address[1]


HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>代码审查 Workflow</title>
<style>
  body { font-family: "Microsoft YaHei", sans-serif; background: #1e1f26; color: #eee;
         max-width: 960px; margin: 40px auto; padding: 0 20px; }
  h1 { font-size: 22px; }
  h2 { font-size: 15px; margin: 22px 0 8px; color: #b9c2ff; }
  .muted { color: #9aa0b0; font-size: 13px; }
  textarea { width: 100%; box-sizing: border-box; background: #2a2c36; color: #eee;
         border: 1px solid #3a3d4a; border-radius: 8px; padding: 10px;
         font-family: Consolas, monospace; font-size: 13px; height: 64px; resize: vertical; }
  label { display: block; margin: 12px 0 6px; font-size: 14px; }
  button { background: #5f8cff; color: #fff; border: none; padding: 10px 22px;
           border-radius: 8px; font-size: 15px; cursor: pointer; margin-top: 12px; }
  button:hover { background: #4a74e0; }
  button:disabled { background: #555; cursor: wait; }
  pre { background: #2a2c36; border: 1px solid #3a3d4a; border-radius: 8px;
        padding: 14px; white-space: pre-wrap; word-break: break-word; font-size: 13px;
        line-height: 1.6; max-height: 320px; overflow: auto; }
  .pipeline { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 10px 0; }
  .node { background: #2a2c36; border: 1px solid #3a3d4a; border-radius: 8px;
          padding: 8px 14px; font-size: 14px; }
  .node.ok { border-color: #3f9d5a; background: #1f2b23; }
  .node.err { border-color: #d05b5b; background: #2b1f1f; }
  .arrow { color: #9aa0b0; font-size: 16px; }
  .badge { font-size: 12px; padding: 1px 7px; border-radius: 10px; margin-left: 6px; }
  .badge.severe { background: #d05b5b; color: #fff; }
  .badge.ok { background: #3f9d5a; color: #fff; }
</style>
</head>
<body>
<h1>🔗 代码审查 Workflow（编码 → 审查 → 修复）</h1>
<p class="muted">输入一个自然语言需求，三个 Agent 协作完成：编码 → 审查 →（发现问题则）修复 → 再审查。</p>

<label>功能需求</label>
<textarea id="req">写一个把列表里所有数字相加的函数</textarea>
<button id="run" onclick="run()">执行 Workflow</button>
<p id="status" class="muted"></p>

<div id="result" style="display:none">
  <h2>① 流程执行（流水线）</h2>
  <div id="pipeline" class="pipeline"></div>

  <h2>② 最终代码</h2>
  <pre id="code"></pre>

  <h2>③ 审查报告</h2>
  <pre id="report"></pre>

  <h2>④ 修复记录 / 执行日志</h2>
  <pre id="log"></pre>
</div>

<script>
const NODE_LABEL = { coder: '编码 Agent', reviewer: '审查 Agent', fixer: '修复 Agent', engine: '引擎' };

function esc(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function run() {
  const req = document.getElementById('req').value;
  const btn = document.getElementById('run');
  const status = document.getElementById('status');
  btn.disabled = true;
  status.textContent = '执行中…';
  document.getElementById('result').style.display = 'none';
  try {
    const resp = await fetch('/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ requirement: req })
    });
    const s = await resp.json();
    render(s);
    status.textContent = '完成（状态 ' + s.status + '，修复 ' + s.round + ' 轮）';
  } catch (e) {
    status.textContent = '出错：' + e;
  } finally {
    btn.disabled = false;
  }
}

function render(s) {
  document.getElementById('result').style.display = 'block';
  // 流水线：按 history 里的 finish/error 事件渲染节点
  const pipe = document.getElementById('pipeline');
  pipe.innerHTML = '';
  const steps = (s.history || []).filter(h => h.event === 'finish' || h.event === 'error' || h.event === 'stop');
  steps.forEach((h, i) => {
    if (i > 0) {
      const a = document.createElement('span');
      a.className = 'arrow';
      a.textContent = '→';
      pipe.appendChild(a);
    }
    const d = document.createElement('div');
    d.className = 'node ' + (h.event === 'error' ? 'err' : 'ok');
    d.textContent = NODE_LABEL[h.node] || h.node;
    pipe.appendChild(d);
  });

  document.getElementById('code').textContent = s.code || '(无)';

  const report = (s.review_report || '') + (s.error ? '\\n\\n错误：' + s.error : '');
  document.getElementById('report').textContent = report;

  let log = (s.fix_log || []).map(x => '修复：' + x).join('\\n');
  if (log) log += '\\n\\n';
  log += (s.history || []).map(h => '[' + h.node + '] ' + h.event + ' ' + (h.detail || '')).join('\\n');
  document.getElementById('log').textContent = log;
}
</script>
</body>
</html>
"""


def _run_workflow(provider_factory, max_rounds, requirement):
    state = WorkflowState(requirement)
    engine = WorkflowEngine(
        CoderNode(provider_factory),
        ReviewerNode(provider_factory),
        FixerNode(provider_factory),
        max_fix_rounds=max_rounds,
    )
    engine.run(state)
    return state.to_dict()


def make_handler(provider_factory, max_rounds):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(HTML.encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path != "/run":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", "replace")
            try:
                data = json.loads(body or "{}")
            except ValueError:
                data = {}
            requirement = (data.get("requirement") or "").strip() or "未命名的需求"
            result = _run_workflow(provider_factory, max_rounds, requirement)
            resp = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        def log_message(self, *args):
            pass  # 关闭默认访问日志

    return Handler


def run_web(provider_factory, max_rounds=3, port=8001):
    server = _SafeHTTPServer(("127.0.0.1", port), make_handler(provider_factory, max_rounds))
    print("可视化界面已启动：http://127.0.0.1:%d （Ctrl+C 退出）" % port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        server.server_close()
