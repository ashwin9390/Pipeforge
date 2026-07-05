# Lead Architect: PipeForge
# Role: Chaos Engineering / System Stress-Tester
# WARNING: Only run this during testing. Never in production.

import os, random, time, subprocess, json, redis

r = redis.Redis(host=os.getenv('REDIS_HOST', 'localhost'), port=6379, decode_responses=True)

CHAOS_ACTIONS = ["STALL_SESSION", "BLOAT_MEMORY"]
# "KILL_PROCESSOR" is commented by default -- uncomment only in a Docker environment
# CHAOS_ACTIONS = ["KILL_PROCESSOR", "STALL_SESSION", "BLOAT_MEMORY"]

def chaos_sweep():
    choice = random.choice(CHAOS_ACTIONS)
    sessions = r.keys("pf_*")

    if not sessions:
        print("[Chaos] No active sessions to target.")
        return

    target = random.choice(sessions)
    raw = r.get(target)
    if not raw:
        return

    state = json.loads(raw)

    if choice == "KILL_PROCESSOR":
        print("[Chaos] Terminating a random processor-agent container...")
        subprocess.run(
            ["docker", "compose", "kill", "-s", "SIGKILL", "processor-agent"],
            capture_output=True
        )

    elif choice == "STALL_SESSION":
        print(f"[Chaos] Artificially stalling session: {target}")
        state["last_heartbeat"] = time.time() - 500  # 500s ago -> triggers Sentinel
        r.set(target, json.dumps(state))

    elif choice == "BLOAT_MEMORY":
        print(f"[Chaos] Injecting context bloat into: {target}")
        state["memory"].append("NOISE: " + ("LOREM IPSUM " * 500))
        r.set(target, json.dumps(state))

    print(f"[Chaos] Action '{choice}' applied to {target}")

if __name__ == "__main__":
    print("[Chaos Monkey] Starting -- random fault injection every 30-90s")
    while True:
        wait = random.randint(30, 90)
        print(f"[Chaos] Next fault in {wait}s...")
        time.sleep(wait)
        chaos_sweep()