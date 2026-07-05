# Lead Architect: PipeForge
# Role: Quality Assurance Agent (Validator Node v3)
# v4: correct choices[0], retry -> queue_processor_retry (separate queue), tiktoken tracking

import os, json, time, signal
from openai import OpenAI
from shared.redis_utils import BlackboardClient
from shared.token_utils import estimate_cost

bb     = BlackboardClient(host=os.getenv("REDIS_HOST", "localhost"))
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL  = os.getenv("PROCESSOR_MODEL", "gpt-4o-mini")
MAX_RETRIES = int(os.getenv("MAX_VALIDATOR_RETRIES", "2"))

_shutdown = False
def _handle_sigterm(sig, frame):
    global _shutdown
    _shutdown = True
signal.signal(signal.SIGTERM, _handle_sigterm)

def run_validator():
    print(f"[Validator v3] Active -- max_retries={MAX_RETRIES}")
    while not _shutdown:
        try:
            sid = bb.blocking_pop("queue_validator", timeout=5)
            if not sid:
                continue

            state = bb.get_state(sid)
            if not state:
                continue

            print(f"[Validator] Reviewing {sid}")
            retry_count = state.get("retry_count", 0)
            context     = "\n".join(state["memory"])

            call_start = time.time()
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a strict quality validator. Evaluate the agent output for:\n"
                            "1. Factual accuracy\n2. Completeness\n3. Logical coherence\n\n"
                            "Respond with EXACTLY one of:\n"
                            "APPROVED: <brief reason>\n"
                            "REJECTED: <specific issue>"
                        )
                    },
                    {"role": "user", "content": f"Goal: {state['goal']}\n\nWork Log:\n{context}"}
                ],
                max_tokens=200
            )
            latency_ms = (time.time() - call_start) * 1000

            verdict   = resp.choices[0].message.content.strip()   # <- fixed: [0]
            inp, out  = resp.usage.prompt_tokens, resp.usage.completion_tokens
            cost      = estimate_cost(inp, out, MODEL)

            state["memory"].append(f"VALIDATOR: {verdict}")
            state["last_heartbeat"] = time.time()

            # commit_spend updates totals atomically AND writes to the cost ledger
            bb.commit_spend(sid, cost, cost, inp, out,
                            node="validator", model=MODEL,
                            call_purpose=f"qa_check_retry_{retry_count}",
                            latency_ms=latency_ms)

            if verdict.startswith("APPROVED") or retry_count >= MAX_RETRIES:
                if retry_count >= MAX_RETRIES and not verdict.startswith("APPROVED"):
                    state["memory"].append(f"VALIDATOR: Max retries ({MAX_RETRIES}) reached. Forcing completion.")
                state["next_step"] = "FINISH"
                state["status"]    = "COMPLETED"
                print(f"[Validator] [OK] Approved {sid}")
            else:
                # Send to SEPARATE retry queue -- won't compete with fresh tasks
                state["retry_count"] = retry_count + 1
                state["next_step"]   = "processor"
                bb.set_state(sid, state)
                bb.safe_push("queue_processor_retry", sid)
                print(f"[Validator] [NO] Rejected {sid} -> queue_processor_retry (retry {retry_count+1})")
                continue

            bb.set_state(sid, state)

        except Exception as e:
            print(f"[Validator] Error: {e}")
            time.sleep(2)

if __name__ == "__main__":
    run_validator()