import hashlib
import json

from psycopg.types.json import Jsonb

from labeldesk.db import connect
from labeldesk.learning import uncertainty


def submit():
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(320028)")
        data = conn.execute(
            "SELECT id,text,resolved,revision FROM tasks WHERE split='pool' ORDER BY id"
        ).fetchall()
        data = json.loads(json.dumps(data, default=str))
        labeled = [r for r in data if r["resolved"] is not None]
        if {r["resolved"] for r in labeled} != {0, 1}:
            raise ValueError("Нужны подтверждённые примеры обоих классов")
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return conn.execute(
            "INSERT INTO selection_runs(snapshot,digest) VALUES (%s,%s) ON CONFLICT(digest) DO UPDATE SET digest=EXCLUDED.digest RETURNING id,status,digest",
            (Jsonb(data), digest),
        ).fetchone()


def tick():
    with connect() as conn:
        conn.autocommit = True
        queued = conn.execute(
            "SELECT id FROM selection_runs WHERE status='queued' ORDER BY id LIMIT 50"
        ).fetchall()
        for pending in queued:
            lock = 320100 + pending["id"]
            if not conn.execute("SELECT pg_try_advisory_lock(%s) AS locked", (lock,)).fetchone()[
                "locked"
            ]:
                continue
            try:
                run = conn.execute(
                    "SELECT * FROM selection_runs WHERE id=%s AND status='queued'", (pending["id"],)
                ).fetchone()
                if run is None:
                    continue
                try:
                    ordered = uncertainty(
                        [r for r in run["snapshot"] if r["resolved"] is not None],
                        [r for r in run["snapshot"] if r["resolved"] is None],
                    )
                    with conn.transaction():
                        with conn.cursor() as cursor:
                            cursor.executemany(
                                "INSERT INTO rankings(run_id,task_id,position) VALUES (%s,%s,%s)",
                                [
                                    (run["id"], identity, rank)
                                    for rank, identity in enumerate(ordered)
                                ],
                            )
                        conn.execute(
                            "UPDATE selection_runs SET status='completed',finished_at=now() WHERE id=%s",
                            (run["id"],),
                        )
                    return True
                except ValueError as exc:
                    conn.execute(
                        "UPDATE selection_runs SET status='failed',error=%s WHERE id=%s",
                        (str(exc), run["id"]),
                    )
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (lock,))
    return False
