#!/usr/bin/env python3
# Lead Architect: PipeForge
# Real Performance Benchmark (v4)
# -------------------------------------------------------------------------
# This benchmark uses REAL OpenAI API calls and REAL Redis handoffs.
# It measures actual latency, token spend, and recovery time.
# Results are saved to benchmark_results.json and printed as a table.
#
# Usage:
#   python bench_ashwin.py [--tasks 10] [--workers 3] [--goal "..."]
#   python bench_ashwin.py --mode swarm --tasks 50
#   python bench_ashwin.py --mode recovery   # tests Sentinel timing
# -------------------------------------------------------------------------

import os, sys, json, time, uuid, argparse, statistics, asyncio
from datetime import datetime
from openai import OpenAI
import redis as redis_lib

# -- Config ----------------------------------------------------------------
REDIS_HOST  = os.getenv("REDIS_HOST", "localhost")
MODEL       = os.getenv("PROCESSOR_MODEL", "gpt-4o-mini")
BUDGET      = float(os.getenv("SESSION_BUDGET_USD", "0.50"))

r = redis_lib.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SAMPLE_GOALS = [
    "Summarise the key trends in generative AI for 2026 in 3 bullet points.",
    "List 3 advantages of Redis over PostgreSQL for real-time state management.",
    "Explain ephemeral agent architecture in 2 sentences.",
    "What are the top 3 failure modes in multi-agent LLM systems?",
    "Describe the Blackboard pattern for distributed AI coordination.",
]


# -- Helpers ---------------------------------------------------------------
def inject_session(goal: str, priority: str = "normal") -> str:
    sid = f"pf_bench_{uuid.uuid4().hex[:8]}"
    state = {
        "goal":           goal,
        "memory":         ["Initialized by Benchmark"],
        "next_step":      "Collector",
        "last_heartbeat": time.time(),
        "current_spend":  0.0,
        "current_tokens": 0,
        "budget_usd":     BUDGET,
        "status":         "ACTIVE",
        "priority":       priority,
        "retry_count":    0,
        "_bench_start":   time.time(),
    }
    r.set(sid, json.dumps(state), ex=3600)
    queue = "queue_collector_priority" if priority == "high" else "queue_collector"
    r.lpush(queue, sid)
    return sid

def wait_for_completion(sid: str, timeout: int = 120) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = r.get(sid)
        if not raw:
            return None
        state = json.loads(raw)
        status = state.get("status", "ACTIVE")
        if status in ("COMPLETED", "KILLED_BY_BUDGET", "BLOCKED_SECURITY") \
           or state.get("next_step") == "FINISH":
            return state
        time.sleep(0.5)
    return None

def real_llm_latency(goal: str) -> tuple[float, int, int]:
    """Measure actual LLM round-trip time for a single call."""
    start = time.perf_counter()
    resp  = client.chat.completions.create(
        model=MODEL,
        messages=[{"role":"user","content":goal}],
        max_tokens=200
    )
    elapsed = time.perf_counter() - start
    return elapsed, resp.usage.prompt_tokens, resp.usage.completion_tokens

def redis_handoff_latency(payload_kb: int = 64) -> float:
    """Measure Redis set+get round-trip for a payload of given size."""
    key     = f"bench_handoff_{uuid.uuid4().hex[:6]}"
    payload = "x" * (payload_kb * 1024)
    start   = time.perf_counter()
    r.set(key, payload, ex=60)
    r.get(key)
    elapsed = time.perf_counter() - start
    r.delete(key)
    return elapsed


# -- Benchmark modes -------------------------------------------------------
def bench_llm_latency(runs: int = 5):
    """Measure real LLM latency across multiple calls."""
    print(f"\n{'-'*55}")
    print(f"  BENCHMARK 1: Real LLM Latency  (model={MODEL}, n={runs})")
    print(f"{'-'*55}")
    latencies, inp_tokens, out_tokens = [], [], []
    for i in range(runs):
        goal = SAMPLE_GOALS[i % len(SAMPLE_GOALS)]
        lat, inp, out = real_llm_latency(goal)
        latencies.append(lat)
        inp_tokens.append(inp)
        out_tokens.append(out)
        print(f"  Run {i+1}: {lat:.2f}s | {inp+out} tokens")
        time.sleep(0.5)  # Rate limit safety

    avg_lat  = statistics.mean(latencies)
    avg_tok  = statistics.mean([i+o for i,o in zip(inp_tokens,out_tokens)])
    p95_lat  = sorted(latencies)[int(len(latencies)*0.95)]
    print(f"\n  Avg Latency : {avg_lat:.2f}s")
    print(f"  P95 Latency : {p95_lat:.2f}s")
    print(f"  Avg Tokens  : {avg_tok:.0f} per call")
    return {"avg_latency_sec": round(avg_lat,3), "p95_latency_sec": round(p95_lat,3),
            "avg_tokens": round(avg_tok,1)}

def bench_redis_handoff(runs: int = 20, payload_kb: int = 64):
    """Measure Redis blackboard handoff speed."""
    print(f"\n{'-'*55}")
    print(f"  BENCHMARK 2: Redis Handoff Latency  (payload={payload_kb}KB, n={runs})")
    print(f"{'-'*55}")
    latencies = [redis_handoff_latency(payload_kb) for _ in range(runs)]
    avg_ms = statistics.mean(latencies) * 1000
    p99_ms = sorted(latencies)[int(len(latencies)*0.99)] * 1000
    print(f"  Avg Handoff : {avg_ms:.3f}ms")
    print(f"  P99 Handoff : {p99_ms:.3f}ms")
    return {"avg_handoff_ms": round(avg_ms,4), "p99_handoff_ms": round(p99_ms,4),
            "payload_kb": payload_kb}

def bench_pipeline_e2e(tasks: int = 5, timeout: int = 180):
    """
    Inject real tasks into the running factory and measure end-to-end time,
    actual token spend, and completion rate.
    Requires: factory running (docker compose up).
    """
    print(f"\n{'-'*55}")
    print(f"  BENCHMARK 3: End-to-End Pipeline  (tasks={tasks})")
    print(f"  Requires factory running: docker compose up -d")
    print(f"{'-'*55}")

    # Check Redis is reachable
    try:
        r.ping()
    except Exception:
        print("  [WARN]  Redis not reachable -- skipping E2E benchmark.")
        print("  Start factory with: docker compose up -d")
        return {}

    session_ids = []
    start_time  = time.time()

    # Inject tasks
    for i in range(tasks):
        goal = SAMPLE_GOALS[i % len(SAMPLE_GOALS)]
        sid  = inject_session(goal)
        session_ids.append((sid, goal, time.time()))
        print(f"  ^ Injected {sid}")
        time.sleep(0.2)

    # Wait and collect results
    results = []
    for sid, goal, inject_time in session_ids:
        state = wait_for_completion(sid, timeout=timeout)
        if state:
            elapsed = time.time() - inject_time
            tokens  = state.get("current_tokens", 0)
            spend   = state.get("current_spend", 0.0)
            status  = state.get("status", "UNKNOWN")
            results.append({
                "sid": sid, "status": status,
                "latency_sec": round(elapsed, 2),
                "tokens": tokens, "spend_usd": spend,
            })
            marker = "[OK]" if status == "COMPLETED" else "[NO]"
            print(f"  {marker} {sid} -- {status} | {elapsed:.1f}s | {tokens} tok | ${spend:.5f}")
        else:
            print(f"  [NO] {sid} -- TIMEOUT after {timeout}s")
            results.append({"sid": sid, "status": "TIMEOUT", "latency_sec": timeout})
        r.delete(sid)

    if not results:
        return {}

    completed  = [x for x in results if x["status"] == "COMPLETED"]
    rate       = len(completed) / len(results) * 100
    avg_lat    = statistics.mean(x["latency_sec"] for x in results)
    total_tok  = sum(x.get("tokens",0) for x in completed)
    total_cost = sum(x.get("spend_usd",0) for x in completed)

    print(f"\n  Completion Rate : {rate:.0f}% ({len(completed)}/{len(results)})")
    print(f"  Avg E2E Latency : {avg_lat:.1f}s")
    print(f"  Total Tokens    : {total_tok}")
    print(f"  Total Cost      : ${total_cost:.5f}")
    print(f"  Cost / Task     : ${total_cost/max(len(completed),1):.5f}")

    return {"completion_rate_pct": round(rate,1), "avg_e2e_latency_sec": round(avg_lat,2),
            "total_tokens": total_tok, "total_cost_usd": round(total_cost,5),
            "tasks": len(results), "completed": len(completed)}

def bench_swarm(count: int = 50):
    """Inject many tasks concurrently and measure injection throughput."""
    print(f"\n{'-'*55}")
    print(f"  BENCHMARK 4: Swarm Injection  (n={count})")
    print(f"{'-'*55}")

    try:
        r.ping()
    except Exception:
        print("  [WARN]  Redis not reachable -- skipping swarm benchmark.")
        return {}

    sids = []
    start = time.perf_counter()
    for i in range(count):
        goal = SAMPLE_GOALS[i % len(SAMPLE_GOALS)]
        sid  = inject_session(goal)
        sids.append(sid)
    elapsed = time.perf_counter() - start

    throughput = count / elapsed
    avg_ms     = (elapsed / count) * 1000
    print(f"  Injected {count} tasks in {elapsed:.3f}s")
    print(f"  Throughput  : {throughput:.0f} tasks/sec")
    print(f"  Avg Inject  : {avg_ms:.2f}ms per task")

    # Cleanup
    for sid in sids:
        r.delete(sid)

    return {"tasks": count, "elapsed_sec": round(elapsed,3),
            "throughput_tasks_per_sec": round(throughput,1),
            "avg_inject_ms": round(avg_ms,2)}

def bench_recovery():
    """Simulate a stalled session and measure Sentinel recovery time."""
    print(f"\n{'-'*55}")
    print(f"  BENCHMARK 5: Sentinel Recovery Timing")
    print(f"{'-'*55}")

    try:
        r.ping()
    except Exception:
        print("  [WARN]  Redis not reachable -- skipping recovery benchmark.")
        return {}

    sid = f"pf_bench_stall_{uuid.uuid4().hex[:6]}"
    state = {
        "goal": "Stall recovery benchmark",
        "memory": ["Initialized"],
        "next_step": "Processor",
        "last_heartbeat": time.time() - 500,   # 500s ago -- triggers Sentinel
        "status": "ACTIVE",
        "current_spend": 0.0,
        "current_tokens": 0,
    }
    r.set(sid, json.dumps(state), ex=3600)
    r.delete("queue_processor")

    print(f"  Injected stalled session: {sid}")
    print(f"  Waiting for Sentinel (up to 30s)...")

    start = time.time()
    deadline = start + 30
    recovered = False
    while time.time() < deadline:
        queue_len = r.llen("queue_processor")
        if queue_len > 0 and r.lpos("queue_processor", sid) is not None:
            recovery_time = time.time() - start
            recovered = True
            break
        time.sleep(0.5)

    r.delete(sid)
    r.delete("queue_processor")

    if recovered:
        print(f"  [OK] Recovery detected in {recovery_time:.1f}s")
        return {"recovery_time_sec": round(recovery_time, 2), "success": True}
    else:
        print(f"  [NO] Recovery not detected within 30s")
        print(f"    (Is sentinel_node running?)")
        return {"recovery_time_sec": None, "success": False}


# -- Main ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="PipeForge Real Benchmark v4")
    parser.add_argument("--mode",    choices=["full","llm","redis","e2e","swarm","recovery"],
                        default="full")
    parser.add_argument("--tasks",   type=int, default=5)
    parser.add_argument("--runs",    type=int, default=5)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print(f"  PIPEFORGE -- Real Performance Benchmark v4")
    print(f"  Model: {MODEL}  |  Redis: {REDIS_HOST}")
    print(f"  Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}")

    all_results = {"timestamp": datetime.now().isoformat(), "model": MODEL}

    if args.mode in ("full","llm"):
        all_results["llm_latency"]    = bench_llm_latency(args.runs)
    if args.mode in ("full","redis"):
        all_results["redis_handoff"]  = bench_redis_handoff()
    if args.mode in ("full","e2e"):
        all_results["e2e_pipeline"]   = bench_pipeline_e2e(args.tasks, args.timeout)
    if args.mode in ("full","swarm"):
        all_results["swarm"]          = bench_swarm(args.tasks * 10)
    if args.mode in ("full","recovery"):
        all_results["sentinel_recovery"] = bench_recovery()

    # Save results
    out_path = "benchmark_results.json"
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)

    print(f"\n{'='*55}")
    print(f"  Results saved to {out_path}")
    print(f"{'='*55}\n")

if __name__ == "__main__":
    main()