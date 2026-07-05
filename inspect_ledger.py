# PipeForge -- Cost Ledger Inspector (CLI)
# Answers: "how did we end up at this situation"
# Usage:
#   python3 inspect_ledger.py <session_id>
#   python3 inspect_ledger.py <session_id> --json

import os, sys, json
from shared.redis_utils import BlackboardClient

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 inspect_ledger.py <session_id> [--json]")
        print("Example: python3 inspect_ledger.py pf_a1b2c3d4")
        sys.exit(1)

    sid = sys.argv[1]
    as_json = "--json" in sys.argv

    bb = BlackboardClient(host=os.getenv("REDIS_HOST", "localhost"))

    ledger  = bb.get_cost_ledger(sid)
    summary = bb.ledger_summary(sid)
    state   = bb.get_state(sid)

    if as_json:
        print(json.dumps({
            "session_id": sid,
            "state": state,
            "summary": summary,
            "ledger": ledger
        }, indent=2, default=str))
        return

    if not ledger:
        print(f"No cost ledger entries found for {sid}")
        print("Either the session does not exist, has expired (24h TTL),")
        print("or no LLM calls have been committed yet.")
        return

    print("=" * 78)
    print(f"  COST LEDGER -- {sid}")
    print("=" * 78)

    if state:
        print(f"  Goal:    {state.get('goal', '?')}")
        print(f"  Status:  {state.get('status', '?')}")
        print(f"  Step:    {state.get('next_step', '?')}")
        print(f"  Budget:  ${state.get('budget_usd', 0.0)}")
    print()

    print(f"  {'TIME':<20}{'NODE':<12}{'PURPOSE':<24}{'TOKENS':>8}{'COST':>12}{'LATENCY':>10}")
    print("  " + "-" * 86)
    for e in ledger:
        err = f"  [ERROR: {e['error'][:30]}]" if e["error"] else ""
        print(f"  {e['wall_time']:<20}{e['node']:<12}{e['call_purpose'][:23]:<24}"
              f"{e['total_tokens']:>8}{('$'+format(e['cost_usd'],'.6f')):>12}"
              f"{(format(e['latency_ms'],'.0f')+'ms'):>10}{err}")

    print("  " + "-" * 86)
    print()
    print(f"  TOTAL CALLS: {summary['total_calls']}")
    print(f"  TOTAL COST:  ${summary['total_cost']:.6f}")
    print()
    print("  BREAKDOWN BY NODE:")
    for node, info in summary["by_node"].items():
        print(f"    {node:<14} {info['calls']:>3} calls   "
              f"${info['cost']:.6f}   {info['tokens']} tokens")
    print()
    print("  BREAKDOWN BY MODEL:")
    for model, info in summary["by_model"].items():
        print(f"    {model:<20} {info['calls']:>3} calls   "
              f"${info['cost']:.6f}   {info['tokens']} tokens")
    print()
    print(f"  First call: {summary['first_call']}")
    print(f"  Last call:  {summary['last_call']}")
    print("=" * 78)

if __name__ == "__main__":
    main()
