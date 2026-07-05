# Lead Architect: PipeForge
# Role: Command Center (FastAPI + HTMX)
# v3 Changes: API key auth, rate limiting, priority queues, richer status UI

import os, json, uuid, time
from fastapi import FastAPI, Form, Request, Depends
from fastapi.responses import HTMLResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from shared.redis_utils import BlackboardClient
from shared.auth import verify_api_key

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="PipeForge Command Center v3")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

bb = BlackboardClient(host=os.getenv("REDIS_HOST", "localhost"))

STATUS_COLORS = {
    "ACTIVE":           ("border-yellow-500", "text-yellow-400"),
    "COMPLETED":        ("border-green-500",  "text-green-400"),
    "KILLED_BY_BUDGET": ("border-red-500",    "text-red-400"),
    "BLOCKED_SECURITY": ("border-red-700",    "text-red-300"),
    "INTERRUPTED":      ("border-orange-500", "text-orange-400"),
}

def render_card(sid, data, api_key=""):
    status  = data.get("status", "ACTIVE")
    step    = data.get("next_step", "?")
    spend   = data.get("current_spend", 0.0)
    tokens  = data.get("current_tokens", 0)
    budget  = data.get("budget_usd", float(os.getenv("SESSION_BUDGET_USD", "0.50")))
    pct     = min(int((spend / budget) * 100), 100) if budget > 0 else 0
    border, badge_color = STATUS_COLORS.get(status, ("border-gray-600", "text-gray-400"))
    is_active = status == "ACTIVE"
    poll   = "every 3s" if is_active else "none"
    label  = f"ACTIVE -> {step}" if is_active else status.replace("_", " ")
    bar_color = "bg-green-500" if pct < 70 else "bg-yellow-500" if pct < 90 else "bg-red-500"
    logs = "<br>".join(data.get("memory", [])[-8:])
    return f"""
    <div hx-get="/status/{sid}?api_key={api_key}" hx-trigger="{poll}" hx-swap="outerHTML"
         class="bg-gray-900 border-l-4 {border} rounded-xl p-5 space-y-3">
      <div class="flex justify-between items-center">
        <code class="text-indigo-300 text-xs">{sid}</code>
        <div class="flex gap-4 text-xs">
          <span class="text-gray-400 font-mono">${spend:.5f} / ${budget} &nbsp;|&nbsp; {tokens} tokens</span>
          <span class="{badge_color} font-bold">{label}</span>
        </div>
      </div>
      <p class="text-gray-500 text-xs truncate">Goal: {data.get("goal","")}</p>
      <div class="w-full bg-gray-800 rounded-full h-1.5">
        <div class="{bar_color} h-1.5 rounded-full" style="width:{pct}%"></div>
      </div>
      <div class="text-xs font-mono text-green-400 bg-black/40 p-3 rounded-lg max-h-36 overflow-y-auto">
        {logs or '<span class="text-gray-600">Waiting...</span>'}
      </div>
      <button hx-get="/ledger/{sid}?api_key={api_key}"
              hx-target="#ledger-{sid}" hx-swap="innerHTML"
              class="text-xs text-indigo-400 hover:text-indigo-300 underline">
        View cost ledger (per-call breakdown)
      </button>
      <div id="ledger-{sid}"></div>
    </div>"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <title>PipeForge | Command Center v3</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
</head>
<body class="bg-gray-950 text-white min-h-screen font-sans">
<div class="max-w-5xl mx-auto px-6 py-10 space-y-8">
  <header class="bg-gray-900 border border-gray-700 rounded-2xl p-8 shadow-2xl">
    <div class="flex justify-between items-start">
      <div>
        <h1 class="text-3xl font-bold text-indigo-400">&#x1F6E1;&#xFE0F; PipeForge v3</h1>
        <p class="text-gray-500 text-sm mt-1">USAP Architecture &nbsp;.&nbsp; Auth + Rate Limited + tiktoken</p>
      </div>
      <div class="text-right text-xs text-gray-600 space-y-1">
        <div><a href="http://localhost:8080" target="_blank" class="hover:text-indigo-400">Dozzle :8080</a></div>
        <div><a href="http://localhost:5540" target="_blank" class="hover:text-indigo-400">Redis Insight :5540</a></div>
      </div>
    </div>
    <form hx-post="/trigger?api_key=API_KEY_PLACEHOLDER"
          hx-swap="afterbegin" hx-target="#sessions" class="flex gap-3 mt-6">
      <select name="priority"
        class="bg-gray-800 border border-gray-600 rounded-lg px-3 text-sm text-gray-300 outline-none">
        <option value="normal">Normal</option>
        <option value="high">&#x26A1; High Priority</option>
      </select>
      <input type="text" name="goal" required
        class="flex-1 p-3 rounded-lg bg-gray-800 border border-gray-600 focus:border-indigo-400 outline-none text-sm"
        placeholder="Enter agent goal...">
      <button type="submit"
        class="bg-indigo-600 hover:bg-indigo-500 px-8 py-3 rounded-lg font-bold text-sm">LAUNCH</button>
    </form>
  </header>
  <div hx-get="/stats?api_key=API_KEY_PLACEHOLDER" hx-trigger="every 5s" hx-swap="outerHTML" id="statsbar">
    <div class="grid grid-cols-4 gap-4 text-center text-xs">
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4"><p class="text-yellow-400 text-2xl font-bold">-</p><p class="text-gray-500 mt-1">Active</p></div>
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4"><p class="text-green-400 text-2xl font-bold">-</p><p class="text-gray-500 mt-1">Completed</p></div>
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4"><p class="text-red-400 text-2xl font-bold">-</p><p class="text-gray-500 mt-1">Killed</p></div>
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4"><p class="text-indigo-400 text-2xl font-bold">-</p><p class="text-gray-500 mt-1">Spend $</p></div>
    </div>
  </div>
  <div id="sessions" class="space-y-3"></div>
</div>
<footer class="text-center text-gray-700 text-xs py-8 uppercase tracking-widest">
  &copy; 2026 PIPEFORGE . USAP v3 . Apache 2.0
</footer>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    key = request.query_params.get("api_key", "")
    return HTML_TEMPLATE.replace("API_KEY_PLACEHOLDER", key)

@app.post("/trigger", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def trigger(request: Request, goal: str = Form(...), priority: str = Form("normal"),
                  _auth=Depends(verify_api_key)):
    sid = f"pf_{uuid.uuid4().hex[:8]}"
    api_key = request.query_params.get("api_key", "")
    state = {
        "goal": goal, "memory": ["Initialized by PipeForge UI"],
        "next_step": "collector", "last_heartbeat": time.time(),
        "current_spend": 0.0, "current_tokens": 0,
        "budget_usd": float(os.getenv("SESSION_BUDGET_USD", "0.50")),
        "status": "ACTIVE", "priority": priority, "retry_count": 0,
    }
    bb.set_state(sid, state)
    queue = "queue_collector_priority" if priority == "high" else "queue_collector"
    bb.safe_push(queue, sid)
    return render_card(sid, state, api_key)

@app.get("/status/{sid}", response_class=HTMLResponse)
async def status_route(sid: str, request: Request, _auth=Depends(verify_api_key)):
    api_key = request.query_params.get("api_key", "")
    data = bb.get_state(sid)
    if not data:
        return f'<div class="bg-gray-900 border border-gray-800 rounded-xl p-4 text-gray-600 text-xs">Session <code>{sid}</code> archived.</div>'
    return render_card(sid, data, api_key)

@app.get("/ledger/{sid}", response_class=HTMLResponse)
async def ledger_route(sid: str, _auth=Depends(verify_api_key)):
    """
    Full per-call cost audit trail for a session.
    Answers: 'which call/node actually drove the spend, and when'.
    """
    ledger  = bb.get_cost_ledger(sid)
    summary = bb.ledger_summary(sid)

    if not ledger:
        return '<div class="text-gray-500 text-xs p-4">No cost ledger entries for this session.</div>'

    rows = ""
    for e in ledger:
        err_badge = (f'<span class="text-red-400">[{e["error"][:40]}]</span>'
                    if e["error"] else "")
        rows += f"""
        <tr class="border-b border-gray-800">
          <td class="py-1 px-2 text-gray-500">{e['wall_time']}</td>
          <td class="py-1 px-2 text-indigo-300">{e['node']}</td>
          <td class="py-1 px-2 text-gray-400">{e['model']}</td>
          <td class="py-1 px-2 text-gray-400">{e['call_purpose']}</td>
          <td class="py-1 px-2 text-right">{e['total_tokens']}</td>
          <td class="py-1 px-2 text-right text-green-400">${e['cost_usd']:.6f}</td>
          <td class="py-1 px-2 text-right text-gray-500">{e['latency_ms']:.0f}ms</td>
          <td class="py-1 px-2">{err_badge}</td>
        </tr>"""

    node_summary = "".join(
        f'<span class="mr-4">{node}: {info["calls"]} calls, ${info["cost"]:.5f}</span>'
        for node, info in summary["by_node"].items()
    )

    return f"""
    <div class="bg-gray-900 border border-gray-800 rounded-xl p-4 text-xs">
      <div class="flex justify-between mb-3">
        <code class="text-indigo-300">{sid}</code>
        <span class="text-gray-400">Total: {summary['total_calls']} calls / ${summary['total_cost']:.6f}</span>
      </div>
      <div class="text-gray-500 mb-3">{node_summary}</div>
      <table class="w-full text-xs">
        <thead>
          <tr class="text-gray-600 border-b border-gray-700">
            <th class="text-left py-1 px-2">Time</th>
            <th class="text-left py-1 px-2">Node</th>
            <th class="text-left py-1 px-2">Model</th>
            <th class="text-left py-1 px-2">Purpose</th>
            <th class="text-right py-1 px-2">Tokens</th>
            <th class="text-right py-1 px-2">Cost</th>
            <th class="text-right py-1 px-2">Latency</th>
            <th class="text-left py-1 px-2">Error</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""

@app.get("/stats", response_class=HTMLResponse)
async def stats(_auth=Depends(verify_api_key)):
    active = completed = killed = 0
    total_spend = 0.0
    for sid in bb.all_session_ids():
        s = bb.get_state(sid)
        if not s: continue
        st = s.get("status", "ACTIVE")
        if st == "ACTIVE": active += 1
        elif st == "COMPLETED": completed += 1
        elif st in ("KILLED_BY_BUDGET", "BLOCKED_SECURITY"): killed += 1
        total_spend += s.get("current_spend", 0.0)
    return f"""
    <div hx-get="/stats" hx-trigger="every 5s" hx-swap="outerHTML" id="statsbar"
         class="grid grid-cols-4 gap-4 text-center text-xs">
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4"><p class="text-yellow-400 text-2xl font-bold">{active}</p><p class="text-gray-500 mt-1">Active</p></div>
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4"><p class="text-green-400 text-2xl font-bold">{completed}</p><p class="text-gray-500 mt-1">Completed</p></div>
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4"><p class="text-red-400 text-2xl font-bold">{killed}</p><p class="text-gray-500 mt-1">Killed</p></div>
      <div class="bg-gray-900 border border-gray-800 rounded-xl p-4"><p class="text-indigo-400 text-2xl font-bold">${total_spend:.4f}</p><p class="text-gray-500 mt-1">Spend $</p></div>
    </div>"""