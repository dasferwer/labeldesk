import pytest

from labeldesk.learning import simulate, uncertainty


def dataset(n, prefix):
    return [
        {
            "id": f"{prefix}-{i}",
            "text": f"{'приятный отличный' if i % 2 else 'плохой сломанный'} товар {prefix} {i}",
            "gold": i % 2,
        }
        for i in range(n)
    ]


def test_equal_budget_and_holdout_separation():
    pool, test = dataset(20, "pool"), dataset(8, "test")
    report = simulate(pool, test, 6)
    assert len(report["runs"]) == 6
    ids = {row["id"] for row in test}
    for run in report["runs"]:
        assert len(set(run["selected_ids"])) == run["budget"] == 6
        assert not ids.intersection(run["selected_ids"])
        assert 0 <= run["f1"] <= 1


def test_holdout_labels_do_not_influence_selection():
    pool, test = dataset(20, "pool"), dataset(8, "test")
    first = simulate(pool, test, 6)
    changed = [{**row, "gold": 1 - row["gold"]} for row in test]
    second = simulate(pool, changed, 6)
    assert [r["selected_ids"] for r in first["runs"]] == [r["selected_ids"] for r in second["runs"]]
    assert first["test_sha256"] != second["test_sha256"]


def test_training_requires_both_classes():
    with pytest.raises(ValueError):
        uncertainty([{"text": "хороший товар", "resolved": 1}], [])
    with pytest.raises(ValueError):
        simulate(dataset(4, "pool"), dataset(2, "test"), 5)
