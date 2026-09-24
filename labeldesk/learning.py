import hashlib
import json

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.pipeline import make_pipeline


def model(texts, labels):
    if len(set(labels)) < 2:
        raise ValueError("Нужны размеченные примеры обоих классов")
    estimator = make_pipeline(
        TfidfVectorizer(ngram_range=(1, 2)), LogisticRegression(random_state=42)
    )
    estimator.fit(texts, labels)
    return estimator


def uncertainty(labeled, candidates):
    estimator = model([r["text"] for r in labeled], [r["resolved"] for r in labeled])
    if not candidates:
        return []
    scores = np.abs(estimator.predict_proba([r["text"] for r in candidates])[:, 1] - 0.5)
    return [candidates[i]["id"] for i in np.argsort(scores, kind="stable")]


def simulate(pool, test, budget=12, seeds=(11, 22, 33)):
    if budget < 2 or budget > len(pool):
        raise ValueError("Бюджет должен быть от 2 до размера пула")
    if not test or any(row["gold"] not in (0, 1) for row in pool + test):
        raise ValueError("Для симуляции нужны эталонные метки пула и теста")
    # Общие стартовые примеры обоих классов имитируют заранее размеченный набор.
    initial = []
    for label in (0, 1):
        found = next((i for i, row in enumerate(pool) if row["gold"] == label), None)
        if found is None:
            raise ValueError("В пуле нужны оба класса")
        initial.append(found)
    reports = []
    for seed in seeds:
        for strategy in ("random", "active"):
            rng = np.random.default_rng(seed)
            chosen = initial.copy()
            while len(chosen) < budget:
                available = [i for i in range(len(pool)) if i not in chosen]
                if strategy == "random":
                    chosen.append(int(rng.choice(available)))
                else:
                    estimator = model(
                        [pool[i]["text"] for i in chosen], [pool[i]["gold"] for i in chosen]
                    )
                    probability = estimator.predict_proba([pool[i]["text"] for i in available])[
                        :, 1
                    ]
                    distance = np.abs(probability - 0.5)
                    tied = np.flatnonzero(np.isclose(distance, distance.min()))
                    chosen.append(available[int(rng.choice(tied))])
            estimator = model([pool[i]["text"] for i in chosen], [pool[i]["gold"] for i in chosen])
            predicted = estimator.predict([r["text"] for r in test])
            truth = [r["gold"] for r in test]
            reports.append(
                {
                    "seed": seed,
                    "strategy": strategy,
                    "budget": budget,
                    "accuracy": float(accuracy_score(truth, predicted)),
                    "f1": float(f1_score(truth, predicted, zero_division=0)),
                    "selected_ids": [str(pool[i]["id"]) for i in chosen],
                }
            )
    fingerprint = hashlib.sha256(json.dumps(test, sort_keys=True, default=str).encode()).hexdigest()
    return {"test_sha256": fingerprint, "test_size": len(test), "runs": reports}
