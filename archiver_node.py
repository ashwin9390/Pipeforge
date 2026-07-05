# Lead Architect: PipeForge
# Role: Cold Storage & RAM Optimization (Archiver v3)
# v3: Postgres support (env-switchable), fallback to SQLite, TTL safety net

import os, json, time, signal
from shared.redis_utils import BlackboardClient

bb = BlackboardClient(host=os.getenv("REDIS_HOST", "localhost"))
SWEEP_INTERVAL = int(os.getenv("ARCHIVER_SWEEP_SEC", "30"))
TERMINAL       = {"COMPLETED", "KILLED_BY_BUDGET", "BLOCKED_SECURITY"}

_shutdown = False
def _handle_sigterm(sig, frame):
    global _shutdown
    _shutdown = True
signal.signal(signal.SIGTERM, _handle_sigterm)

# -- DB backend (Postgres preferred, SQLite fallback) ---------------------
DATABASE_URL = os.getenv("DATABASE_URL", "")  # e.g. postgresql://user:pass@db:5432/pipeforge

def get_connection():
    if DATABASE_URL:
        import psycopg2
        return psycopg2.connect(DATABASE_URL), "postgres"
    else:
        import sqlite3
        db_path = os.getenv("ARCHIVE_DB_PATH", "/app/data/pipeforge_archive.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        return sqlite3.connect(db_path), "sqlite"

def init_db():
    conn, backend = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS session_history (
            session_id   TEXT PRIMARY KEY,
            goal         TEXT,
            logs         TEXT,
            tokens       INTEGER,
            spend_usd    REAL,
            status       TEXT,
            priority     TEXT,
            retry_count  INTEGER,
            archived_at  TEXT
        )
    """)
    conn.commit()
    conn.close()
    print(f"[Archiver v3] DB ready ({backend})")

def archive_sweep():
    archived = 0
    for sid in bb.all_session_ids():
        state = bb.get_state(sid)
        if not state:
            continue

        status = state.get("status", "ACTIVE")
        step   = state.get("next_step", "")
        if status not in TERMINAL and step != "FINISH":
            continue

        conn, _ = get_connection()
        cur = conn.cursor()
        try:
            if DATABASE_URL:
                cur.execute(
                    """INSERT INTO session_history VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (session_id) DO NOTHING""",
                    (sid, state.get("goal"), json.dumps(state.get("memory",[])),
                     state.get("current_tokens",0), state.get("current_spend",0.0),
                     status, state.get("priority","normal"),
                     state.get("retry_count",0), time.strftime("%Y-%m-%d %H:%M:%S"))
                )
            else:
                cur.execute(
                    """INSERT OR IGNORE INTO session_history VALUES (?,?,?,?,?,?,?,?,?)""",
                    (sid, state.get("goal"), json.dumps(state.get("memory",[])),
                     state.get("current_tokens",0), state.get("current_spend",0.0),
                     status, state.get("priority","normal"),
                     state.get("retry_count",0), time.strftime("%Y-%m-%d %H:%M:%S"))
                )
            conn.commit()
            bb.delete(sid)
            archived += 1
            print(f"[Archiver] Archived {sid} ({status})")
        except Exception as e:
            print(f"[Archiver] DB error for {sid}: {e}")
        finally:
            conn.close()

    if archived:
        print(f"[Archiver] {archived} session(s) moved to cold storage.")

if __name__ == "__main__":
    init_db()
    print("[Archiver v3] Janitor Active.")
    while not _shutdown:
        archive_sweep()
        time.sleep(SWEEP_INTERVAL)