import uuid

from labeldesk.db import connect


def add(client, text="Отличная покупка", **kwargs):
    response = client.post("/tasks", json={"text": text, **kwargs})
    assert response.status_code == 200
    return response.json()["id"]


def submit(client, actor, label):
    claimed = client.post(
        "/claim", json={"actor": actor}, headers={"X-Actor-Key": actor + "-key"}
    ).json()
    assert claimed["task"]
    body = {
        "actor": actor,
        "task_id": claimed["task"]["id"],
        "token": claimed["token"],
        "label": label,
    }
    response = client.post("/annotations", json=body, headers={"X-Actor-Key": actor + "-key"})
    assert response.status_code == 200
    return response, body


def test_two_labels_disagreement_adjudication_and_versions(client):
    identity = add(client)
    _, first = submit(client, "first", 0)
    assert client.post("/annotations", json=first).status_code == 409
    response, _ = submit(client, "second", 1)
    assert response.json()["resolved"] is None
    conflicts = client.get("/disagreements").json()
    assert len(conflicts) == 1
    revision = conflicts[0]["revision"]
    assert (
        client.post(
            f"/tasks/{identity}/resolve", json={"label": 1, "revision": revision}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/tasks/{identity}/resolve", json={"label": 0, "revision": revision}
        ).status_code
        == 409
    )
    history = client.get(f"/tasks/{identity}/history").json()
    assert len(history["annotations"]) == 2
    assert len(history["decisions"]) == 1
    assert history["task"]["resolved"] == 1


def test_consensus_and_no_self_double_annotation(client):
    add(client)
    submit(client, "first", 1)
    assert client.post("/claim", json={"actor": "first"}).json()["task"] is None
    response, _ = submit(client, "second", 1)
    assert response.json()["resolved"] == 1
    assert (
        client.post("/claim", json={"actor": "third"}, headers={"X-Actor-Key": "third-key"}).json()[
            "task"
        ]
        is None
    )


def test_test_set_is_hidden_and_immutable(client):
    identity = add(client, split="test", gold=1)
    assert client.post("/claim", json={"actor": "first"}).json()["task"] is None
    assert client.get(f"/tasks/{identity}/history").status_code == 404
    assert (
        client.post(f"/tasks/{identity}/resolve", json={"label": 0, "revision": 0}).status_code
        == 404
    )
    assert (
        client.post("/tasks", json={"text": "Отличная покупка", "split": "pool"}).status_code == 409
    )
    assert client.get("/disagreements", headers={"X-Admin-Key": "wrong"}).status_code == 403


def test_lease_expiry_rejects_stale_annotation(client):
    add(client)
    claimed = client.post("/claim", json={"actor": "first"}).json()
    with connect() as conn:
        conn.execute("UPDATE assignments SET expires_at=now()-interval '1 second'")
    fresh = client.post("/claim", json={"actor": "first"}).json()
    assert fresh["token"] != claimed["token"]
    response = client.post(
        "/annotations",
        json={
            "actor": "first",
            "task_id": claimed["task"]["id"],
            "token": claimed["token"],
            "label": 1,
        },
    )
    assert response.status_code == 409


def test_at_most_two_outstanding_assignments(client):
    add(client)
    assert client.post("/claim", json={"actor": "a"}, headers={"X-Actor-Key": "a-key"}).json()[
        "task"
    ]
    assert client.post("/claim", json={"actor": "b"}, headers={"X-Actor-Key": "b-key"}).json()[
        "task"
    ]
    assert (
        client.post("/claim", json={"actor": "c"}, headers={"X-Actor-Key": "c-key"}).json()["task"]
        is None
    )


def test_frontend_and_auth(client):
    assert client.get("/").status_code == 200
    assert "LabelDesk" in client.get("/").text
    assert client.get("/health", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get(f"/tasks/{uuid.uuid4()}/history").status_code == 404
