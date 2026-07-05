# Lead Architect: PipeForge
# Role: Lifecycle Router / Orchestrator (Supervisor v4)
# v4: OTel spans on every routing decision

import os, json, time
from shared.redis_utils import BlackboardClient
from shared.security import full_security_scan
from shared.telemetry import tracer, record_session_event

bb = BlackboardClient(host=os.getenv("REDIS_HOST", "localhost"))
PIPELINE = ["collector", "processor", "validator", "FINISH"]
TERMINAL = {"KILLED_BY_BUDGET", "BLOCKED_SECURITY", "COMPLETED"}

def supervisor():
    print("[Supervisor v4] Orchestrator Active")
    while True:
        with tracer.start_as_current_span("pipeforge.supervisor.sweep") as sweep_span:
            routed = 0
            for sid in bb.all_session_ids():
                state = bb.get_state(sid)
                if not state:
                    continue
                step   = state.get("next_step", "FINISH")
                status = state.get("status", "ACTIVE")
                if step == "FINISH" or status in TERMINAL:
                    continue

                use_llm = os.getenv("ENABLE_LLM_SECURITY_SCAN", "true").lower() == "true"
                is_safe, reason = full_security_scan(
                    state.get("goal", ""), state.get("memory", []), use_llm=use_llm
                )
                if not is_safe:
                    print(f"[SHIELD] Blocked {sid}: {reason}")
                    state["status"]    = "BLOCKED_SECURITY"
                    state["next_step"] = "FINISH"
                    state["memory"].append(f"SUPERVISOR_SHIELD: {reason}")
                    bb.set_state(sid, state)
                    record_session_event(sid, "security_block", reason=reason)
                    continue

                pushed = bb.safe_push(f"queue_{step}", sid)
                if pushed:
                    print(f"[Supervisor] {sid} -> {step}")
                    routed += 1

            sweep_span.set_attribute("sessions.routed", routed)
        time.sleep(2)

if __name__ == "__main__":
    supervisor()