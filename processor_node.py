# PipeForge -- Processor Node
# LLM reasoning engine with:
#   - Atomic pre-call budget reservation (prevents overshoot)
#   - Idempotent step advancement
#   - Dead-letter queue after max retries
#   - Per-task timeout
#   - SIGTERM graceful shutdown

import os, json, time, signal, threading
from openai import OpenAI
from shared.redis_utils import BlackboardClient
from shared.token_utils import count_memory_tokens, count_tokens, estimate_cost
from shared.telemetry import tracer, record_session_event

bb     = BlackboardClient(host=os.getenv("REDIS_HOST", "localhost"))
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL  = os.getenv("WORKER_MODEL", "gpt-4o-mini")

MAX_MEMORY_TOKENS  = int(os.getenv("MAX_MEMORY_TOKENS",  "6000"))
MAX_TOOL_ROUNDS    = int(os.getenv("MAX_TOOL_ROUNDS",    "5"))
TASK_TIMEOUT_SEC   = int(os.getenv("TASK_TIMEOUT_SEC",   "120"))  # per-task hard timeout
MAX_RETRIES_DLQ    = int(os.getenv("MAX_RETRIES_DLQ",    "5"))    # before dead-letter

_shutdown = False
def _handle_sigterm(sig, frame):
    global _shutdown
    _shutdown = True
    print("[Processor] SIGTERM -- will exit after current task.")
signal.signal(signal.SIGTERM, _handle_sigterm)


def compress_memory(memory: list) -> list:
    tokens = count_memory_tokens(memory, MODEL)
    if tokens <= MAX_MEMORY_TOKENS:
        return memory
    try:
        combined = "\n".join(memory[:-3])
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user",
                       "content": f"Summarise this agent memory in 5 bullet points:\n\n{combined[:4000]}"}],
            max_tokens=300
        )
        summary = resp.choices[0].message.content
        return [f"[COMPRESSED HISTORY]\n{summary}"] + memory[-3:]
    except Exception:
        return memory[-5:]


def _llm_call_with_reservation(sid: str, messages: list,
                                state: dict,
                                call_purpose: str = "main_reasoning") -> tuple[str, int, int, float]:
    """
    Pre-reserve estimated budget, make LLM call, commit actual spend.
    Raises BudgetExceededError if reservation fails.
    Returns (content, input_tokens, output_tokens, actual_cost).
    """
    budget = state.get("budget_usd", float(os.getenv("SESSION_BUDGET_USD", "0.50")))

    # Estimate cost of this call before making it
    prompt_text  = " ".join(m.get("content", "") for m in messages)
    est_inp_tok  = count_tokens(prompt_text, MODEL)
    est_out_tok  = int(est_inp_tok * 0.4)   # conservative output estimate
    est_cost     = estimate_cost(est_inp_tok, est_out_tok, MODEL)

    # Atomic reservation -- abort if it would exceed budget
    reserved = bb.reserve_budget(sid, est_cost, budget)
    if not reserved:
        current = state.get("current_spend", 0.0)
        reserved_amt = state.get("reserved_spend", 0.0)
        raise BudgetExceededError(
            f"Pre-call reservation failed: current=${current:.5f} + "
            f"reserved=${reserved_amt:.5f} + estimate=${est_cost:.5f} > budget=${budget}"
        )

    call_start = time.time()
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=600
        )
        content      = resp.choices[0].message.content
        inp_tok      = resp.usage.prompt_tokens
        out_tok      = resp.usage.completion_tokens
        actual_cost  = estimate_cost(inp_tok, out_tok, MODEL)
        latency_ms   = (time.time() - call_start) * 1000

        # Commit actual spend, release reservation, write ledger entry
        bb.commit_spend(sid, actual_cost, est_cost, inp_tok, out_tok,
                        node="processor", model=MODEL,
                        call_purpose=call_purpose, latency_ms=latency_ms)
        return content, inp_tok, out_tok, actual_cost

    except BudgetExceededError:
        raise
    except Exception as e:
        latency_ms = (time.time() - call_start) * 1000
        # Release reservation on failure -- still log to ledger with error
        bb.commit_spend(sid, 0.0, est_cost, 0, 0,
                        node="processor", model=MODEL,
                        call_purpose=call_purpose, latency_ms=latency_ms,
                        error=str(e))
        raise e


class BudgetExceededError(Exception):
    pass



def _run_task(sid: str, state: dict):
    """Process a single session. Runs inside a timeout thread."""
    print(f"[Processor] Processing {sid}")

    # Idempotency: skip if already processed by another worker
    idem_key = f"processor:{sid}:{state.get('retry_count', 0)}"
    if not bb.set_idempotency_key(idem_key, "processing"):
        print(f"[Processor] {sid} already being processed -- skipping (idempotent)")
        return

    try:
        state["memory"] = compress_memory(state["memory"])
        messages = [
            {"role": "system",
             "content": "You are an expert reasoning agent. Be factual, concise, structured."},
            {"role": "user",
             "content": f"Task: {state['goal']}\n\nContext:\n" + "\n".join(state["memory"])}
        ]

        content, inp, out, cost = _llm_call_with_reservation(sid, messages, state)

        # Re-read state (may have been updated by cost_controller during call)
        state = bb.get_state(sid)
        if not state or state.get("status") in ("KILLED_BY_BUDGET", "BLOCKED_SECURITY"):
            print(f"[Processor] {sid} killed during LLM call -- discarding result")
            return

        state["memory"].append(f"PROCESSOR: {content}")

        # Idempotent step advance: collector->processor is already done,
        # now advance processor->validator atomically
        advanced = bb.advance_step(sid, "processor", "validator")
        if not advanced:
            print(f"[Processor] {sid} step already advanced -- skipping write")
            return

        state["next_step"] = "validator"
        bb.set_state(sid, state)
        print(f"[Processor] [OK] {sid} -> validator | cost=${cost:.5f} tokens={inp+out}")

    except BudgetExceededError as e:
        print(f"[Processor] Budget exceeded pre-call for {sid}: {e}")
        state = bb.get_state(sid) or state
        state["next_step"] = "FINISH"
        state["status"]    = "KILLED_BY_BUDGET"
        state["memory"].append(f"PROCESSOR: {e}")
        bb.set_state(sid, state)
        bb.purge_from_all_queues(sid)
        record_session_event(sid, "budget_killed_precall")

    except Exception as e:
        print(f"[Processor] Error on {sid}: {e}")
        state = bb.get_state(sid) or state
        retry = state.get("processor_errors", 0) + 1
        state["processor_errors"] = retry

        if retry >= MAX_RETRIES_DLQ:
            print(f"[Processor] {sid} hit DLQ threshold ({retry} errors)")
            bb.send_to_dead_letter(sid, f"Processor error after {retry} attempts: {e}")
            record_session_event(sid, "dead_letter", reason=str(e))
        else:
            state["memory"].append(f"PROCESSOR: Error (attempt {retry}): {e}")
            bb.set_state(sid, state)


def run_processor():
    print(f"[Processor] Active -- model={MODEL} timeout={TASK_TIMEOUT_SEC}s")
    while not _shutdown:
        try:
            sid = (bb.blocking_pop("queue_processor", timeout=2) or
                   bb.blocking_pop("queue_processor_retry", timeout=1))
            if not sid:
                continue

            state = bb.get_state(sid)
            if not state:
                continue
            if state.get("status") in ("KILLED_BY_BUDGET", "BLOCKED_SECURITY",
                                        "COMPLETED", "DEAD_LETTER"):
                continue

            # Per-task timeout using a thread
            task_thread = threading.Thread(
                target=_run_task, args=(sid, state), daemon=True
            )
            task_thread.start()
            task_thread.join(timeout=TASK_TIMEOUT_SEC)

            if task_thread.is_alive():
                # Task timed out
                print(f"[Processor] TIMEOUT on {sid} after {TASK_TIMEOUT_SEC}s")
                state = bb.get_state(sid) or state
                state["memory"].append(
                    f"PROCESSOR: Task timed out after {TASK_TIMEOUT_SEC}s."
                )
                timeout_count = state.get("timeout_count", 0) + 1
                state["timeout_count"] = timeout_count
                if timeout_count >= 3:
                    bb.send_to_dead_letter(sid, f"Timed out {timeout_count} times")
                    record_session_event(sid, "dead_letter", reason="repeated_timeout")
                else:
                    # Exponential backoff before requeue
                    backoff = min(2 ** timeout_count, 60)
                    state["memory"].append(f"PROCESSOR: Backoff {backoff}s before retry.")
                    bb.set_state(sid, state)
                    time.sleep(backoff)
                    bb.safe_push("queue_processor_retry", sid)
                    record_session_event(sid, "timeout_requeue", backoff=backoff)

        except Exception as e:
            print(f"[Processor] Loop error: {e}")
            time.sleep(2)


if __name__ == "__main__":
    run_processor()