#  Contributing to PipeForge (USAP)

Thanks for your interest in the **PipeForge**! This project builds the fastest, most resilient Distributed Agent Factory in the 2026 AI ecosystem.

##  The PipeForge Standard

All contributions must adhere to these four pillars:

1. **Ephemerality:** Agents must "Read -> Act -> Update -> Die." No long-running state inside a container.
2. **Blackboard First:** All memory must be committed to the Redis Blackboard before a state transition.
3. **Heartbeat Compliance:** Every worker loop must update `last_heartbeat` to remain visible to the Sentinel Node.
4. **Financial Awareness:** All LLM calls must be loggable by the Cost Controller.

##  Adding a New Specialized Agent

1. **Fork** the repo and create a branch: `feat/your-agent-name`
2. **Copy** `processor_node.py` as your template
3. **Define** a unique Redis queue: e.g. `queue_coder`
4. **Register** your service in `docker-compose.yml` with a `replicas` count
5. **Handle** OpenAI API errors gracefully so the Sentinel can re-queue
6. **Update** the `PIPELINE` list in `supervisor.py` if your agent is part of the main chain

##  Testing Requirements

Run the full suite before submitting a PR:

```bash
pytest tests/
```

Your code must pass Stall Recovery and Budget Violation tests.

## [NOTE] Reporting Bugs

Include:
- Screenshot of Command Centre (Port 3000)
- JSON state from Redis Insight (Port 5540)
- Raw logs from Dozzle (Port 8080)

**Lead Architect:** PipeForge  
**License:** Apache 2.0