# PipeForge -- Linux Setup & Google Colab Guide
# Plain ASCII. No special characters.

=======================================================
PART 1: RUNNING ON A LINUX MACHINE (Ubuntu 22.04/24.04)
=======================================================

STEP 1 -- Install Docker (official method, not apt docker.io)
-------------------------------------------------------------
# Remove old versions first
sudo apt remove docker docker-engine docker.io containerd runc 2>/dev/null

# Install dependencies
sudo apt update
sudo apt install -y ca-certificates curl

# Add Docker GPG key
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
     -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) \
  signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${VERSION_CODENAME}") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine + Compose
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
     docker-buildx-plugin docker-compose-plugin

# Allow running Docker without sudo (log out and back in after this)
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
docker compose version


STEP 2 -- Install Python 3.11 and Git
--------------------------------------
sudo apt install -y python3.11 python3-pip git unzip


STEP 3 -- Download and extract PipeForge
-----------------------------------------
# Option A: If you have the zip file
unzip pipeforge.zip
cd pipeforge

# Option B: If you push to GitHub first
git clone https://github.com/YOUR_USERNAME/pipeforge.git
cd pipeforge


STEP 4 -- Configure environment
---------------------------------
cp .env.example .env
nano .env

# Set these two required values:
#   OPENAI_API_KEY=sk-your-key-here
#   UI_API_KEY=any-strong-secret-you-choose


STEP 5 -- Launch PipeForge
----------------------------
chmod +x launch.sh
./launch.sh

# You will see output like:
# [YES]  PIPEFORGE AGENT FACTORY IS LIVE
# [UI]   Command Center  ->  http://localhost:3000?api_key=YOUR_KEY
# Traces ->  http://localhost:16686
# Logs   ->  http://localhost:8080
# Redis  ->  http://localhost:5540


STEP 6 -- Open in browser
---------------------------
# On local machine:
#   Open http://localhost:3000?api_key=YOUR_KEY

# On a remote server (VPS/cloud):
#   Use SSH tunnel from your laptop:
ssh -L 3000:localhost:3000 \
    -L 16686:localhost:16686 \
    -L 8080:localhost:8080 \
    -L 5540:localhost:5540 \
    user@YOUR_SERVER_IP

# Then open http://localhost:3000?api_key=YOUR_KEY on your laptop browser


STEP 7 -- Run tests (optional)
--------------------------------
cd pipeforge
pip3 install tiktoken redis openai fastapi slowapi python-jose passlib --break-system-packages
python3 tests/test_pipeforge.py


STEP 8 -- Scale workers
-------------------------
docker compose up -d --scale processor-agent=10


STEP 9 -- Stop everything
---------------------------
docker compose down


=======================================================
PART 2: RUNNING ON GOOGLE COLAB (Free GPU/CPU instance)
=======================================================

Google Colab runs Ubuntu under the hood. You can run PipeForge
using a technique called "localtunnel" to expose ports publicly.

IMPORTANT: Colab sessions reset after ~12 hours (free tier).
Redis data is lost on reset. Use Colab Pro for longer sessions.
For production use, always prefer a proper Linux VPS.


--- COLAB NOTEBOOK CELLS (copy each block into a new cell) ---


CELL 1 -- Install system dependencies
---------------------------------------
%%bash
# Install Docker inside Colab
apt-get update -qq
apt-get install -y docker.io docker-compose curl unzip python3-pip -qq

# Start Docker daemon
service docker start
sleep 3
docker --version


CELL 2 -- Upload and extract PipeForge
----------------------------------------
# Run this cell, then use the file upload button to upload pipeforge.zip
from google.colab import files
uploaded = files.upload()  # Upload pipeforge.zip here


CELL 3 -- Extract and configure
---------------------------------
%%bash
unzip -q pipeforge.zip
cd pipeforge

# Create .env file
cat > .env << 'ENVEOF'
OPENAI_API_KEY=sk-YOUR-KEY-HERE
UI_API_KEY=colab-test-secret-123
SESSION_BUDGET_USD=0.10
WORKER_MODEL=gpt-4o-mini
ENABLE_LLM_SECURITY_SCAN=false
ENVEOF

echo "Config done"
cat .env


CELL 4 -- Launch PipeForge
-----------------------------
%%bash
cd pipeforge

# Docker Compose v2 syntax in Colab
docker compose up -d --scale processor-agent=2

sleep 8
docker compose ps


CELL 5 -- Expose port with ngrok (free tunnel)
------------------------------------------------
# Install pyngrok
!pip install pyngrok -q

from pyngrok import ngrok

# Expose the Command Center on port 3000
# Sign up free at https://ngrok.com and get your auth token
ngrok.set_auth_token("YOUR_NGROK_AUTH_TOKEN")

tunnel = ngrok.connect(3000)
print("PipeForge Command Center URL:")
print(tunnel.public_url + "?api_key=colab-test-secret-123")

# Also expose Jaeger traces (optional)
jaeger_tunnel = ngrok.connect(16686)
print("\nJaeger Traces URL:")
print(jaeger_tunnel.public_url)


CELL 6 -- Run a test task via API
-----------------------------------
import requests

BASE_URL = tunnel.public_url  # from Cell 5
API_KEY  = "colab-test-secret-123"

# Launch a task
resp = requests.post(
    f"{BASE_URL}/trigger?api_key={API_KEY}",
    data={"goal": "Summarise the key trends in AI agents in 2026", "priority": "normal"}
)
print("Status:", resp.status_code)
print("Response:", resp.text[:300])


CELL 7 -- Monitor logs
------------------------
%%bash
cd pipeforge
docker compose logs --tail=30 processor-agent


CELL 8 -- Run benchmark
-------------------------
%%bash
cd pipeforge
pip install tiktoken redis openai -q
python3 bench_pipeforge.py --mode redis


CELL 9 -- Stop everything
---------------------------
%%bash
cd pipeforge
docker compose down
echo "PipeForge stopped"


=======================================================
PART 3: MINIMUM MACHINE REQUIREMENTS
=======================================================

For development / testing:
  RAM:   4 GB minimum (8 GB recommended)
  CPU:   2 cores minimum
  Disk:  5 GB free
  OS:    Ubuntu 20.04 / 22.04 / 24.04
  Port:  3000, 8080, 5540, 16686 must be open (or SSH tunneled)

For production (100+ concurrent tasks):
  RAM:   16 GB
  CPU:   8 cores
  Disk:  20 GB
  Redis: Run with persistence (AOF enabled) and a replica
  Workers: Scale to processor-agent=10 or more


=======================================================
PART 4: COMMON ERRORS AND FIXES
=======================================================

ERROR: "permission denied while trying to connect to Docker"
  FIX:  sudo usermod -aG docker $USER && newgrp docker

ERROR: "port 3000 already in use"
  FIX:  sudo lsof -i :3000  -- find what is using it
        sudo kill -9 <PID>

ERROR: "Cannot connect to Redis"
  FIX:  docker compose ps  -- check blackboard container is healthy
        docker compose restart blackboard

ERROR: "OPENAI_API_KEY not set"
  FIX:  nano .env  -- make sure the key is on its own line with no spaces

ERROR: Colab session disconnected
  FIX:  Restart runtime, run all cells again from Cell 1.
        Redis state is lost on Colab reset -- tasks start fresh.

ERROR: "No space left on device" in Colab
  FIX:  Runtime -> Factory reset runtime -- clears disk
        Or use: docker system prune -af

=======================================================
