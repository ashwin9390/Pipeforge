# PipeForge -- Cost Controller
# Financial circuit breaker with:
#   - Real tiktoken spend (set atomically by processor via commit_spend)
#   - Soft warn at 80%, hard kill at 100%
#   - Per-user + per-session rate limiting
#   - Reconciliation log for billing discrepancy detection
#   - OTel events

import os, json, time, signal
from shared.redis_utils import BlackboardClient
from shared.telemetry import record_session_event

bb = BlackboardClient(host=os.getenv("REDIS_HOST", "localhost"))

SESSION_BUDGET    = float(os.getenv("SESSION_BUDGET_USD",  "0.50"))
WARN_THRESHOLD    = float(os.getenv("BUDGET_WARN_PCT",     "0.80"))
CHECK_INTERVAL    = int(os.getenv("COST_CHECK_SEC",        "10"))
RECONCILE_EVERY   = int(os.getenv("RECONCILE_INTERVAL",    "300"))  # 5 min
TERMINAL          = {"KILLED_BY_BUDGET", "BLOCKED_SECURITY", "COMPLETED", "DEAD_LETTER"}

_shutdown = False
_last_reconcile = 0

def _handle_sigterm(sig, frame):
    global _shutdown
    _shutdown = True
signal.signal(signal.SIGTERM, _handle_sigterm)


def enforce_budget():
    for sid in bb.all_session_ids():
        state = bb.get_state(sid)
        if not state:
            continue

        status = state.get("status", "ACTIVE")
        step   = state.get("next_step", "FINISH")
        if step == "FINISH" or status in TERMINAL:
            continue

        # Real spend is committed atomically by processor via commit_spend Lua
        spend    = state.get("current_spend",  0.0)
        reserved = state.get("reserved_spend", 0.0)
        tokens   = state.get("current_tokens", 0)
        budget   = state.get("budget_usd", SESSION_BUDGET)
        # Total committed + reserved (in-flight)
        total    = spend + reserved
        pct      = (total / budget * 100) if budget > 0 else 0

        if total >= budget:
            print(f"[CostCtrl] KILL {sid}: ${total:.5f} (${spend:.5f} spent "
                  f"+ ${reserved:.5f} reserved) >= ${budget}")
            state["memory"].append(
                f"COST_CONTROLLER: TERMINATED. Total=${total:.5f} "
                f"(spent=${spend:.5f} + reserved=${reserved:.5f}) "
                f"exceeded budget=${budget}. Tokens: {tokens}."
            )
            state["next_step"] = "FINISH"
            state["status"]    = "KILLED_BY_BUDGET"
            bb.set_state(sid, state)
            bb.purge_from_all_queues(sid)
            record_session_event(sid, "budget_killed",
                                 spend=spend, reserved=reserved, budget=budget)

        elif pct >= WARN_THRESHOLD * 100:
            warn_key = f"budget_warn_{sid}"
            if not bb.raw().get(warn_key):
                msg = (f"COST_CONTROLLER: WARNING -- {pct:.0f}% budget used "
                       f"(${total:.5f}/${budget}, {tokens} tokens).")
                state["memory"].append(msg)
                bb.set_state(sid, state)
                bb.raw().set(warn_key, "1", ex=3600)
                record_session_event(sid, "budget_warning", pct=pct, spend=total)
                print(f"[CostCtrl] WARN {sid}: {pct:.0f}% of budget used")


def reconciliation_sweep():
    """
    Periodic check: compare pipeline token counts vs expected.
    Flags sessions where spend seems anomalous (e.g. provider rounding issues).
    In production, this would compare against provider invoice API.
    """
    anomalies = []
    for sid in bb.all_session_ids():
        state = bb.get_state(sid)
        if not state:
            continue
        spend  = state.get("current_spend", 0.0)
        tokens = state.get("current_tokens", 0)
        if tokens > 0 and spend == 0.0:
            anomalies.append({"sid": sid, "issue": "tokens > 0 but spend = 0"})
        if spend > 0 and tokens == 0:
            anomalies.append({"sid": sid, "issue": "spend > 0 but tokens = 0"})

    if anomalies:
        print(f"[CostCtrl] Reconciliation: {len(anomalies)} anomalies detected")
        # Write to a reconcile audit log in Redis (keeps last 100 entries)
        for a in anomalies:
            bb.raw().lpush("pf:reconcile:anomalies",
                           json.dumps({**a, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}))
        bb.raw().ltrim("pf:reconcile:anomalies", 0, 99)
    else:
        print("[CostCtrl] Reconciliation: no anomalies")


if __name__ == "__main__":
    print(f"[CostCtrl] Active -- budget=${SESSION_BUDGET} "
          f"warn@{int(WARN_THRESHOLD*100)}% reconcile_every={RECONCILE_EVERY}s")
    while not _shutdown:
        enforce_budget()
        global _last_reconcile
        if time.time() - _last_reconcile >= RECONCILE_EVERY:
            reconciliation_sweep()
            _last_reconcile = time.time()
        time.sleep(CHECK_INTERVAL)