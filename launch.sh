#!/bin/bash
# PipeForge v4 -- One-Click Deploy
# Starts: Redis, Jaeger, Dozzle, Redis Insight, all agent nodes
set -e

if [ ! -f ".env" ]; then
  echo "[NO]  .env not found."
  echo "    Run: cp .env.example .env  then fill in your keys."
  exit 1
fi

export $(grep -v '^#' .env | xargs)

if [ -z "$OPENAI_API_KEY" ]; then
  echo "[NO]  OPENAI_API_KEY missing in .env"
  exit 1
fi

# Generate UI key if not set
if [ -z "$UI_API_KEY" ]; then
  export UI_API_KEY=$(openssl rand -hex 24 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(24))")
  echo "UI_API_KEY=${UI_API_KEY}" >> .env
  echo "[WARN]   Generated UI_API_KEY and saved to .env"
fi

echo "[STOP]  Stopping previous instances..."
docker compose down -v --remove-orphans 2>/dev/null || true

echo "[BUILD]  Building images..."
docker compose build --quiet

echo "[LAUNCH]  Launching all services (3 workers)..."
docker compose up -d --scale processor-agent=3

echo ""
echo "[WAIT]  Waiting for health checks..."
sleep 8
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "=============================================================="
echo "  [YES]  PIPEFORGE AGENT FACTORY v4 IS LIVE"
echo "=============================================================="
echo "  [UI]   Command Center  ->  http://localhost:3000?api_key=${UI_API_KEY}"
echo "  [Traces]  Jaeger Traces   ->  http://localhost:16686"
echo "  [Logs]  Dozzle Logs     ->  http://localhost:8080"
echo "  [DB]  Redis Insight   ->  http://localhost:5540"
echo "=============================================================="
echo ""
echo "  Run benchmarks:  python bench_pipeforge.py --mode full"
echo "  Run tests:       python tests/test_pipeforge_factory.py"
echo "  Scale workers:   docker compose up -d --scale processor-agent=10"
echo "  Stop factory:    docker compose down"
echo ""