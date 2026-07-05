# Lead Architect: PipeForge
# Role: Security-Enhanced Supervisor (Pre-Execution Shield)
# Use this as a drop-in replacement for supervisor.py for hardened deployments.

import os, re, json, redis, time

r = redis.Redis(host=os.getenv('REDIS_HOST', 'localhost'), port=6379, decode_responses=True)

# ------------------------------------------------------------
# DENYLIST -- Dangerous Unix & System Commands
# ------------------------------------------------------------
DANGEROUS_PATTERNS = [
    r"rm\s+-rf",            # Recursive force delete
    r"mkfs",                # Format file system
    r"dd\s+if=/dev/zero",   # Overwriting disk with zeros
    r"shutdown",            # System shutdown
    r":\(\){ :\|:& };:",    # Fork bomb
    r"> /dev/sda",          # Writing directly to a physical drive
    r"chmod\s+777",         # Globally executable files (security risk)
    r"wget\s+.*\.sh\s+\|",  # Download-and-pipe-to-bash
    r"curl\s+.*\|\s*bash",  # Curl-and-pipe-to-bash
    r"eval\s+\$\(",         # Eval injection
    r"base64\s+--decode",   # Encoded payload execution
]

def security_scan(goal: str, memory: list) -> tuple[bool, str | None]:
    """
    Scans the task goal and memory for dangerous patterns.
    Returns (is_safe, error_message).
    """
    combined = f"{goal} {json.dumps(memory)}".lower()
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, combined):
            return False, f"SECURITY ALERT: Blocked dangerous pattern '{pattern}'"
    return True, None

def safe_spawn_agent(sid: str, state: dict) -> bool:
    """Validate before routing. Returns True if safe to proceed."""
    is_safe, error = security_scan(state.get("goal", ""), state.get("memory", []))
    if not is_safe:
        print(f"[SHIELD] {error} | Session: {sid}")
        state["status"]    = "BLOCKED_SECURITY"
        state["next_step"] = "FINISH"
        state["memory"].append(f"SUPERVISOR_SHIELD: {error}")
        r.set(sid, json.dumps(state))
        return False
    print(f"[SHIELD] Session {sid} cleared security scan.")
    return True

PIPELINE = ["Collector", "Processor", "Validator", "FINISH"]

def secure_supervisor():
    print("[SecureSupervisor] PipeForge Security Orchestrator Active.")
    while True:
        for sid in r.keys("pf_*"):
            raw = r.get(sid)
            if not raw:
                continue

            state = json.loads(raw)
            step   = state.get("next_step", "FINISH")
            status = state.get("status", "ACTIVE")

            if step == "FINISH" or status in ("KILLED_BY_BUDGET", "BLOCKED_SECURITY"):
                continue

            # Run security guardrail before every routing decision
            if not safe_spawn_agent(sid, state):
                continue

            queue_key = f"queue_{step}"
            if r.lpos(queue_key, sid) is None:
                print(f"[SecureSupervisor] Routing {sid} -> {step}")
                r.lpush(queue_key, sid)

        time.sleep(2)

if __name__ == "__main__":
    secure_supervisor()