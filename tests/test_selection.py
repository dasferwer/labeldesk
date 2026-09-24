import uuid

from labeldesk.db import connect
from labeldesk.selection import submit, tick


def seed():
    with connect() as conn:
        for text, label in [
            ("Хорошо отлично", 1),
            ("Плохо ужасно", 0),
            ("Хорошо плохо", None),
            ("Отлично прекрасно", None),
        ]:
            conn.execute(
                "INSERT INTO tasks(id,text,split,resolved) VALUES (%s,%s,'pool',%s)",
                (uuid.uuid4(), text, label),
            )
        conn.execute(
            "INSERT INTO tasks(id,text,split,gold) VALUES (%s,'Секретный эталон','test',1)",
            (uuid.uuid4(),),
        )


def test_snapshot_excludes_holdout_and_preserves_labels(client):
    seed()
    run = submit()
    assert submit()["id"] == run["id"]
    with connect() as conn:
        snapshot = conn.execute(
            "SELECT snapshot FROM selection_runs WHERE id=%s", (run["id"],)
        ).fetchone()["snapshot"]
        assert len(snapshot) == 4
        assert all("gold" not in r for r in snapshot)
        conn.execute("UPDATE tasks SET resolved=1,revision=revision+1 WHERE resolved=0")
        assert (
            conn.execute(
                "SELECT snapshot FROM selection_runs WHERE id=%s", (run["id"],)
            ).fetchone()["snapshot"]
            == snapshot
        )
    assert tick()
    result = client.get(f"/selection-runs/{run['id']}").json()
    assert result["status"] == "completed"
    assert len(result["ranking"]) == 2
    claimed = client.post("/claim", json={"actor": "first"}).json()
    assert claimed["task"]["id"] == result["ranking"][0]["task_id"]
    assert claimed["selection_run"] == run["id"]
    assert client.post("/claim", json={"actor": "first"}).json() == claimed


def test_actor_cannot_impersonate_second_reviewer(client):
    seed()
    assert client.post("/claim", json={"actor": "second"}).status_code == 403
    assert (
        client.post("/claim", json={"actor": "first"}, headers={"X-Actor-Key": "wrong"}).status_code
        == 403
    )


def test_failed_ranking_has_no_partial_visible_order(client, monkeypatch):
    from labeldesk import selection

    seed()
    run = submit()

    def fail(*args):
        raise ValueError("Недостаточно признаков")

    monkeypatch.setattr(selection, "uncertainty", fail)
    assert not tick()
    result = client.get(f"/selection-runs/{run['id']}").json()
    assert result["status"] == "failed"
    assert result["ranking"] == []
    assert client.post("/claim", json={"actor": "first"}).json()["selection_run"] is None
