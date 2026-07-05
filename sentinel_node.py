# PipeForge -- Sentinel Node
# Self-healing heartbeat monitor with:
#   - Multi-level health checks (heartbeat + milestone progress)
#   - Configurable grace periods per task type
#   - Jitter on requeues (prevents thundering herd)
#   - Requeue cap before dead-letter
#   - Exponential backoff

import os, json, time, signal, random
from shared.redis_utils import BlackboardClient
from shared.telemetry import record_session_event

bb = BlackboardClient(host=os.getenv("REDIS_HOST", "localhost"))

STALL_THRESHOLD   = int(os.getenv("WATCHDOG_STALL_SEC",  "60"))
SWEEP_INTERVAL    = int(os.getenv("WATCHDOG_SWEEP_SEC",  "15"))
MAX_REQUEUE_COUNT = int(os.getenv("MAX_REQUEUE_COUNT",   "5"))   # before dead-letter
JITTER_MAX_SEC    = int(os.getenv("REQUEUE_JITTER_SEC",  "10"))  # max jitter on requeue
TERMINAL          = {"KILLED_BY_BUDGET", "BLOCKED_SECURITY", "COMPLETED", "DEAD_LETTER"}

# Grace period multipliers per step (some steps legitimately take longer)
STEP_GRACE_MULTIPLIERS = {
    "collector": 1.0,   # Grounding is usually fast
    "processor": 2.0,   # LLM calls can take longer
    "validator": 1.5,
}

_shutdown = False
def _handle_sigterm(sig, frame):
    global _shutdown
    _shutdown = True
signal.signal(signal.SIGTERM, _handle_sigterm)


def _effective_threshold(step: str) -> float:
    multiplier = STEP_GRACE_MULTIPLIERS.get(step, 1.0)
    return STALL_THRESHOLD * multiplier


def sentinel_sweep():
    recovered = stalled = dlq_sent = 0

    for sid in bb.all_session_ids():
        state = bb.get_state(sid)
        if not state:
            continue

        step   = state.get("next_step", "FINISH")
        status = state.get("status", "ACTIVE")

        if step == "FINISH" or status in TERMINAL:
            continue

        last_hb  = state.get("last_heartbeat", 0)
        elapsed  = time.time() - last_hb
        threshold = _effective_threshold(step)

        if elapsed <= threshold:
            continue

        stalled += 1
        requeue_count = state.get("sentinel_requeue_count", 0)

        if requeue_count >= MAX_REQUEUE_COUNT:
            print(f"[Sentinel] {sid} hit requeue cap ({requeue_count}) -> DLQ")
            bb.send_to_dead_letter(
                sid,
                f"Sentinel: max requeues ({MAX_REQUEUE_COUNT}) reached on step '{step}'"
            )
            record_session_event(sid, "dead_letter", reason="max_requeues")
            dlq_sent += 1
            continue

        # Exponential backoff jitter: waits longer between successive requeues
        backoff = min(2 ** requeue_count, 60)
        jitter  = random.uniform(0, JITTER_MAX_SEC)
        wait    = backoff + jitter

        print(f"[Sentinel] {sid} stalled {int(elapsed)}s on '{step}' "
              f"(requeue #{requeue_count+1}, backoff={wait:.1f}s)")

        # Update state before requeue
        state["sentinel_requeue_count"] = requeue_count + 1
        state["memory"].append(
            f"SENTINEL: Stall detected ({int(elapsed)}s on '{step}'). "
            f"Requeue #{requeue_count+1} after {wait:.1f}s backoff."
        )
        bb.set_state(sid, state)

        time.sleep(wait)  # Apply backoff before requeue

        ok = bb.safe_requeue(sid, step)
        if ok:
            recovered += 1
            record_session_event(sid, "sentinel_requeue",
                                 elapsed=int(elapsed), attempt=requeue_count+1)
        else:
            print(f"[Sentinel] {sid} already recovered or terminated -- skip")

    if stalled:
        print(f"[Sentinel] Sweep: {stalled} stalled, {recovered} recovered, {dlq_sent} -> DLQ")
    else:
        print("[Sentinel] All sessions healthy.")


if __name__ == "__main__":
    print(f"[Sentinel] Active -- stall={STALL_THRESHOLD}s sweep={SWEEP_INTERVAL}s "
          f"max_requeues={MAX_REQUEUE_COUNT}")
    while not _shutdown:
        sentinel_sweep()
        time.sleep(SWEEP_INTERVAL)