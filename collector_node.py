# Lead Architect: PipeForge
# Role: Grounded Data Fetcher (Collector Node v4)
# v4: MCP tool calling for real data, OpenTelemetry spans

import os, json, time, signal, asyncio
from openai import OpenAI
from shared.redis_utils import BlackboardClient
from shared.token_utils import estimate_cost
from shared.telemetry import tracer, span
from shared.mcp_tools import MCPToolRegistry, run_tool_calls

bb     = BlackboardClient(host=os.getenv("REDIS_HOST", "localhost"))
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL  = os.getenv("PROCESSOR_MODEL", "gpt-4o-mini")

mcp = MCPToolRegistry()

_shutdown = False
def _handle_sigterm(sig, frame):
    global _shutdown
    _shutdown = True
signal.signal(signal.SIGTERM, _handle_sigterm)

async def _harvest(sid: str, goal: str) -> tuple[str, int, int]:
    """Fetch ground truth using MCP tools if available, else LLM-only."""
    with tracer.start_as_current_span("pipeforge.collector.fetch") as root_span:
        root_span.set_attribute("session.id", sid)

        tool_schemas = mcp.tool_schemas_for_llm()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a research assistant. Use available tools to fetch real-world "
                    "facts for the task. If no tools are available, list 3 key facts to verify. "
                    "Be brief and factual."
                )
            },
            {"role": "user", "content": f"Task: {goal}"}
        ]

        kwargs = dict(model=MODEL, messages=messages, max_tokens=400)
        if tool_schemas:
            kwargs["tools"] = tool_schemas
            kwargs["tool_choice"] = "auto"

        with span("pipeforge.collector.llm_call", model=MODEL):
            resp = client.chat.completions.create(**kwargs)

        msg = resp.choices[0].message
        inp = resp.usage.prompt_tokens
        out = resp.usage.completion_tokens

        # Handle tool calls if the LLM chose to use them
        if msg.tool_calls:
            messages.append(msg)
            with span("pipeforge.collector.tool_calls", count=len(msg.tool_calls)):
                tool_results = await run_tool_calls(msg.tool_calls, mcp)
            messages.extend(tool_results)

            # Second LLM call to synthesise tool results
            with span("pipeforge.collector.synthesis"):
                resp2 = client.chat.completions.create(
                    model=MODEL, messages=messages, max_tokens=400
                )
            content = resp2.choices[0].message.content or ""
            inp += resp2.usage.prompt_tokens
            out += resp2.usage.completion_tokens
        else:
            content = msg.content or ""

        root_span.set_attribute("tokens.total", inp + out)
        return content, inp, out

def run_collector():
    print("[Collector v4] Discovering MCP tools...")
    try:
        asyncio.run(mcp.discover())
    except Exception as e:
        print(f"[Collector v4] MCP discovery skipped: {e}")
    print(f"[Collector v4] Active | MCP tools: {mcp.available_tools or 'none'}")

    while not _shutdown:
        try:
            sid = bb.blocking_pop("queue_collector_priority", timeout=1) or \
                  bb.blocking_pop("queue_collector", timeout=4)
            if not sid:
                continue

            state = bb.get_state(sid)
            if not state:
                continue

            print(f"[Collector] Processing {sid}")
            call_start = time.time()
            content, inp, out = asyncio.run(_harvest(sid, state["goal"]))
            cost       = estimate_cost(inp, out, MODEL)
            latency_ms = (time.time() - call_start) * 1000

            state["memory"].append(f"COLLECTOR: Ground Truth\n{content}")
            state["next_step"]      = "processor"
            state["last_heartbeat"] = time.time()
            bb.set_state(sid, state)

            # commit_spend updates current_tokens/current_spend atomically
            # AND writes a structured entry to the per-session cost ledger
            bb.commit_spend(sid, cost, cost, inp, out,
                            node="collector", model=MODEL,
                            call_purpose="ground_truth_fetch",
                            latency_ms=latency_ms)

            print(f"[Collector] [OK] {sid} -> processor | cost=${cost:.5f}")

        except Exception as e:
            print(f"[Collector] Error: {e}")
            time.sleep(2)

if __name__ == "__main__":
    run_collector()