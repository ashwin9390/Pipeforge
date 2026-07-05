# PipeForge -- Why Colab Times Out, And What To Use Instead
# Plain ASCII.

=======================================================
WHY COLAB IS HITTING THE 1-HOUR WALL
=======================================================

Your docker-compose.yml runs 8 services that each say "build: .".
Every Colab session starts from a CLEAN DISK. This means every time
you run the notebook:

  1. apt installs Docker from scratch               (~3-5 min)
  2. Pulls redis:7-alpine                            (~1 min)
  3. Pulls redis/redisinsight:latest                 (~2 min)
  4. Pulls amir20/dozzle:latest                      (~1 min)
  5. Pulls jaegertracing/all-in-one (if used)         (~2 min)
  6. Builds YOUR image 8 separate times               (~10-20 min,
                                                        even with
                                                        layer caching
                                                        Colab's disk
                                                        I/O is slow)
  7. Starts 10 containers, waits for healthchecks      (~1-2 min)

That alone is 20-30 minutes BEFORE you have sent a single test task.
Add normal Colab background throttling (it deprioritizes long-running
non-notebook processes like Docker daemons) and crossing 60 minutes
is the expected outcome, not a fluke.

Colab is built for: running Python/ML code in a single process.
Colab is NOT built for: orchestrating a multi-container daemon stack
with healthchecks, persistent state, and background services.

This is not a PipeForge problem. It is a tool-mismatch problem.


=======================================================
THE FIX: STOP USING COLAB FOR THIS. USE ONE OF THESE.
=======================================================

Ranked by setup time, from fastest to slowest.


--- OPTION A (FASTEST -- 5 minutes, your own machine) ---

If you have ANY machine with Docker Desktop installed (Windows, Mac,
Linux), this is by far the easiest path. No cloud, no timeouts, no
rebuild-from-scratch every session.

  unzip pipeforge.zip
  cd pipeforge
  cp .env.example .env
  nano .env   # set OPENAI_API_KEY and UI_API_KEY
  chmod +x launch.sh
  ./launch.sh

Once built the FIRST time, Docker caches the image layers. Every
subsequent ./launch.sh after that takes under 30 seconds, not 20
minutes, because nothing has to be re-downloaded or rebuilt.

This is the option I would actually use if I were you.


--- OPTION B (10 minutes, free, persistent, cloud) ---

GitHub Codespaces. Unlike Colab, Codespaces machines have Docker
PRE-INSTALLED and the disk PERSISTS between sessions if you don't
delete the Codespace. No rebuild-from-scratch every time.

Free tier: 120 core-hours/month for personal accounts (effectively
60 hours on a 2-core machine -- more than enough for testing).

STEPS:
  1. Push pipeforge to a GitHub repo (even a private one)
  2. On the repo page: Code button -> Codespaces tab -> Create
     codespace on main
  3. Wait ~60 seconds for the cloud VM to boot (Docker is already
     installed -- no apt install step needed)
  4. In the Codespaces terminal:
       cp .env.example .env
       nano .env
       chmod +x launch.sh
       ./launch.sh
  5. Codespaces auto-forwards port 3000. Click the "Ports" tab,
     open port 3000 in browser.
  6. IMPORTANT: Stop the Codespace when done (or it auto-stops after
     30 min idle) to conserve your free hours. Your disk + Docker
     images PERSIST for next time -- no rebuild needed.


--- OPTION C (15 minutes, free, no install needed at all) ---

Railway.app free trial ($5 credit, no credit card needed to start).
Genuinely supports docker-compose.yml natively -- push the repo,
Railway reads the compose file and deploys all services.

STEPS:
  1. Push pipeforge to GitHub
  2. Go to railway.app, sign in with GitHub
  3. New Project -> Deploy from GitHub repo -> select pipeforge
  4. Railway detects docker-compose.yml automatically
  5. Add environment variables in the Railway dashboard:
       OPENAI_API_KEY, UI_API_KEY, SESSION_BUDGET_USD
  6. Railway builds and deploys all services, gives you a public URL
  7. The $5 free credit covers several hours of testing easily


--- OPTION D (slowest but cheapest long-term: a real Linux VPS) ---

If you want something that stays up permanently (not just for
testing), see LINUX_SETUP.md in this same folder -- a $4-6/month
DigitalOcean or Hetzner droplet with Docker installed once, and it
just runs. No timeouts, no rebuilds, no session limits, ever.


=======================================================
IF YOU MUST USE COLAB ANYWAY (NOT RECOMMENDED)
=======================================================

If for some reason Colab is your only option, here is how to
minimize the rebuild tax:

  1. Do NOT run the full docker-compose.yml. Run ONLY Redis:

     %%bash
     apt-get install -y redis-server -qq
     redis-server --daemonize yes --port 6379
     redis-cli ping

  2. Skip Docker entirely. Run the Python nodes directly as
     background processes instead of containers:

     %%bash
     cd pipeforge
     pip install -q redis openai tiktoken fastapi uvicorn slowapi
     export REDIS_HOST=localhost
     export OPENAI_API_KEY=sk-...
     nohup python3 collector_node.py > collector.log 2>&1 &
     nohup python3 processor_node.py > processor.log 2>&1 &
     nohup python3 validator_node.py > validator.log 2>&1 &
     nohup python3 sentinel_node.py > sentinel.log 2>&1 &
     nohup python3 cost_controller.py > cost.log 2>&1 &
     nohup python3 supervisor.py > supervisor.log 2>&1 &

  3. This skips Docker build time ENTIRELY (which is most of the
     1-hour problem). No Jaeger, no Dozzle, no Redis Insight UI --
     but the actual pipeline logic runs and you can verify it with
     inspect_ledger.py and by reading the .log files.

  4. This will still die when the Colab session disconnects (max
     12 hours free tier, often less). It is a TEST environment only,
     never a permanent one.


=======================================================
BOTTOM LINE
=======================================================

Colab is good for: testing the core logic with MINIMAL_TEST.md
(just Redis + Python, no Docker) -- this was already designed to
avoid the rebuild problem.

Colab is bad for: the full docker-compose.yml stack. Use Option A
(your own machine) or Option B (Codespaces) instead -- both are
free and do not have the rebuild-from-scratch tax that is causing
your 1-hour timeout.
