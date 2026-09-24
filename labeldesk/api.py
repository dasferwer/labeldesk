import json
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from labeldesk.db import connect, init
from labeldesk.learning import simulate, uncertainty
from labeldesk.selection import submit as submit_selection


@asynccontextmanager
async def lifespan(app):
    init()
    yield


def authorize(x_api_key: str = Header(default="")):
    key = os.environ.get("API_KEY", "")
    if not key or not secrets.compare_digest(x_api_key, key):
        raise HTTPException(401, "Неверный API-ключ")


def admin(x_admin_key: str = Header(default="")):
    key = os.environ.get("ADMIN_KEY", "")
    if not key or not secrets.compare_digest(x_admin_key, key):
        raise HTTPException(403, "Нужен ключ администратора")


def actor_identity(x_actor_key: str = Header(default="")):
    configured = json.loads(os.environ.get("ANNOTATOR_KEYS", "{}"))
    for actor, key in configured.items():
        if key and secrets.compare_digest(x_actor_key, key):
            return actor
    raise HTTPException(403, "Нужен персональный ключ разметчика")


app = FastAPI(title="LabelDesk", lifespan=lifespan)


class Task(BaseModel):
    text: str = Field(min_length=2, max_length=5000)
    split: Literal["pool", "test"] = "pool"
    gold: Literal[0, 1] | None = None


class Claim(BaseModel):
    actor: str = Field(min_length=1, max_length=80)


class Annotation(Claim):
    task_id: uuid.UUID
    token: uuid.UUID
    label: Literal[0, 1]


class Decision(BaseModel):
    label: Literal[0, 1]
    revision: int = Field(ge=0)


@app.get("/health", dependencies=[Depends(authorize)])
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/tasks", dependencies=[Depends(authorize), Depends(admin)])
def add(task: Task):
    if task.split == "test" and task.gold is None:
        raise HTTPException(422, "Тестовой записи нужна эталонная метка")
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(320028)")
        if conn.execute("SELECT count(*) AS n FROM tasks").fetchone()["n"] >= 2000:
            raise HTTPException(409, "Лимит стенда: 2000 текстов")
        record = conn.execute(
            """INSERT INTO tasks (id,text,split,gold) VALUES (%s,%s,%s,%s)
            ON CONFLICT (text) DO NOTHING RETURNING id""",
            (uuid.uuid4(), task.text, task.split, task.gold),
        ).fetchone()
        if not record:
            raise HTTPException(409, "Такой текст уже есть в пуле или тесте")
        return record


@app.post("/claim", dependencies=[Depends(authorize)])
def claim(body: Claim, actor: str = Depends(actor_identity)):
    if body.actor != actor:
        raise HTTPException(403, "Ключ принадлежит другому разметчику")
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(320028)")
        existing = conn.execute(
            """SELECT t.id,t.text,t.revision,a.token,a.selection_run FROM assignments a
            JOIN tasks t ON t.id=a.task_id WHERE a.actor=%s AND a.expires_at>now()
            AND t.resolved IS NULL AND NOT EXISTS(SELECT 1 FROM annotations n WHERE n.task_id=t.id AND n.actor=a.actor)
            ORDER BY a.expires_at LIMIT 1""",
            (actor,),
        ).fetchone()
        if existing:
            return {
                "task": {k: existing[k] for k in ("id", "text", "revision")},
                "token": existing["token"],
                "selection_run": existing["selection_run"],
            }
        selection = conn.execute(
            "SELECT max(id) AS id FROM selection_runs WHERE status='completed'"
        ).fetchone()["id"]
        task = conn.execute(
            """SELECT t.id,t.text,t.revision FROM tasks t
            LEFT JOIN rankings rank ON rank.task_id=t.id AND rank.run_id=%s
            WHERE t.split='pool' AND t.resolved IS NULL
            AND NOT EXISTS (SELECT 1 FROM annotations a WHERE a.task_id=t.id AND a.actor=%s)
            AND NOT EXISTS (SELECT 1 FROM assignments a WHERE a.task_id=t.id AND a.actor=%s AND a.expires_at>now())
            AND (SELECT count(DISTINCT actor) FROM (
                SELECT actor FROM annotations WHERE task_id=t.id
                UNION SELECT actor FROM assignments WHERE task_id=t.id AND expires_at>now()
            ) participants)<2 ORDER BY rank.position NULLS LAST,t.id LIMIT 1 FOR UPDATE OF t""",
            (selection, body.actor, body.actor),
        ).fetchone()
        if not task:
            return {"task": None}
        token = uuid.uuid4()
        conn.execute(
            """INSERT INTO assignments(task_id,actor,token,expires_at,selection_run) VALUES (%s,%s,%s,now()+interval '10 minutes',%s)
            ON CONFLICT (task_id,actor) DO UPDATE SET token=EXCLUDED.token,expires_at=EXCLUDED.expires_at,selection_run=EXCLUDED.selection_run""",
            (task["id"], body.actor, token, selection),
        )
        return {"task": task, "token": token, "selection_run": selection}


@app.post("/annotations", dependencies=[Depends(authorize)])
def annotate(body: Annotation, actor: str = Depends(actor_identity)):
    if body.actor != actor:
        raise HTTPException(403, "Ключ принадлежит другому разметчику")
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(320028)")
        task = conn.execute(
            "SELECT * FROM tasks WHERE id=%s FOR UPDATE", (body.task_id,)
        ).fetchone()
        if not task or task["split"] != "pool":
            raise HTTPException(404, "Задание не найдено")
        lease = conn.execute(
            "SELECT * FROM assignments WHERE task_id=%s AND actor=%s AND token=%s AND expires_at>now()",
            (body.task_id, body.actor, body.token),
        ).fetchone()
        if not lease:
            raise HTTPException(409, "Назначение истекло или уже использовано")
        conn.execute(
            "INSERT INTO annotations (task_id,actor,label) VALUES (%s,%s,%s)",
            (body.task_id, body.actor, body.label),
        )
        conn.execute(
            "DELETE FROM assignments WHERE task_id=%s AND actor=%s", (body.task_id, body.actor)
        )
        labels = conn.execute(
            "SELECT label FROM annotations WHERE task_id=%s", (body.task_id,)
        ).fetchall()
        resolved = (
            labels[0]["label"]
            if len(labels) == 2 and labels[0]["label"] == labels[1]["label"]
            else None
        )
        conn.execute(
            "UPDATE tasks SET revision=revision+1,resolved=%s WHERE id=%s", (resolved, body.task_id)
        )
        return {"resolved": resolved, "revision": task["revision"] + 1}


@app.get("/disagreements", dependencies=[Depends(authorize), Depends(admin)])
def disagreements():
    with connect() as conn:
        return conn.execute("""SELECT t.id,t.text,t.revision FROM tasks t WHERE t.split='pool'
            AND t.resolved IS NULL AND (SELECT count(*) FROM annotations WHERE task_id=t.id)>=2 ORDER BY t.id""").fetchall()


@app.post("/tasks/{identity}/resolve", dependencies=[Depends(authorize), Depends(admin)])
def resolve(identity: uuid.UUID, body: Decision):
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(320028)")
        task = conn.execute("SELECT * FROM tasks WHERE id=%s FOR UPDATE", (identity,)).fetchone()
        if not task or task["split"] != "pool":
            raise HTTPException(404, "Задание не найдено")
        if task["revision"] != body.revision:
            raise HTTPException(409, "Версия разметки изменилась")
        count = conn.execute(
            "SELECT count(*) AS n FROM annotations WHERE task_id=%s", (identity,)
        ).fetchone()["n"]
        if count < 2:
            raise HTTPException(409, "Сначала нужны две независимые аннотации")
        revision = body.revision + 1
        conn.execute(
            "UPDATE tasks SET resolved=%s,revision=%s WHERE id=%s", (body.label, revision, identity)
        )
        conn.execute(
            "INSERT INTO decisions (task_id,label,revision) VALUES (%s,%s,%s)",
            (identity, body.label, revision),
        )
        return {"revision": revision}


@app.get("/uncertain", dependencies=[Depends(authorize), Depends(admin)])
def uncertain():
    with connect() as conn:
        data = conn.execute("SELECT * FROM tasks WHERE split='pool' ORDER BY id").fetchall()
    try:
        return {
            "ids": uncertainty(
                [r for r in data if r["resolved"] is not None],
                [r for r in data if r["resolved"] is None],
            )
        }
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/simulate", dependencies=[Depends(authorize), Depends(admin)])
def simulation(budget: int = 12):
    with connect() as conn:
        data = conn.execute("SELECT id,text,gold,split FROM tasks ORDER BY id").fetchall()
        try:
            report = simulate(
                [r for r in data if r["split"] == "pool"],
                [r for r in data if r["split"] == "test"],
                budget,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return conn.execute(
            "INSERT INTO runs (report) VALUES (%s) RETURNING id,report", (Jsonb(report),)
        ).fetchone()


@app.get("/tasks/{identity}/history", dependencies=[Depends(authorize), Depends(admin)])
def history(identity: uuid.UUID):
    with connect() as conn:
        task = conn.execute(
            "SELECT id,text,revision,resolved FROM tasks WHERE id=%s AND split='pool'", (identity,)
        ).fetchone()
        if not task:
            raise HTTPException(404, "Задание не найдено")
        return {
            "task": task,
            "annotations": conn.execute(
                "SELECT actor,label,created_at FROM annotations WHERE task_id=%s ORDER BY id",
                (identity,),
            ).fetchall(),
            "decisions": conn.execute(
                "SELECT label,revision,created_at FROM decisions WHERE task_id=%s ORDER BY id",
                (identity,),
            ).fetchall(),
        }


@app.post("/selection-runs", status_code=202, dependencies=[Depends(authorize), Depends(admin)])
def select_next():
    try:
        return submit_selection()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/selection-runs/{identity}", dependencies=[Depends(authorize), Depends(admin)])
def selection_result(identity: int):
    with connect() as conn:
        row = conn.execute(
            "SELECT id,status,digest,error,created_at,finished_at FROM selection_runs WHERE id=%s",
            (identity,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Запуск отбора не найден")
        row["ranking"] = conn.execute(
            "SELECT task_id,position FROM rankings WHERE run_id=%s ORDER BY position", (identity,)
        ).fetchall()
        return row


@app.get("/agreement", dependencies=[Depends(authorize), Depends(admin)])
def agreement():
    from sklearn.metrics import cohen_kappa_score

    with connect() as conn:
        pairs = conn.execute(
            "SELECT task_id,array_agg(label ORDER BY actor) AS labels FROM annotations GROUP BY task_id HAVING count(*)=2"
        ).fetchall()
    left = [p["labels"][0] for p in pairs]
    right = [p["labels"][1] for p in pairs]
    # При единственном общем классе знаменатель каппы равен нулю.
    kappa = float(cohen_kappa_score(left, right)) if len(set(left + right)) > 1 else None
    return {
        "paired_tasks": len(pairs),
        "agreement": sum(a == b for a, b in zip(left, right)) / len(pairs) if pairs else None,
        "kappa": kappa,
        "note": "Пары могут включать разных разметчиков; это сводный диагностический показатель",
    }


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
