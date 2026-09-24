import json
import os
from urllib.request import Request, urlopen

base = os.environ.get("API_URL", "http://localhost:8092")
headers = {
    "Content-Type": "application/json",
    "X-API-Key": os.environ.get("API_KEY", "local-demo-key"),
    "X-Admin-Key": os.environ.get("ADMIN_KEY", "local-admin-key"),
}


def call(path, data):
    request = Request(base + path, data=json.dumps(data).encode(), headers=headers)
    with urlopen(request, timeout=120) as response:
        return json.load(response)


for split, n in [("pool", 40), ("test", 20)]:
    for i in range(n):
        sentiment = ["плохая упаковка, сломанный товар", "приятный сервис, отличный товар"][i % 2]
        text = f"{sentiment}. Пример {split}, ситуация {i}."
        try:
            call("/tasks", {"text": text, "split": split, "gold": i % 2})
        except Exception as exc:
            if getattr(exc, "code", None) != 409:
                raise
result = call("/simulate?budget=12", {})
assert len(result["report"]["runs"]) == 6
print(json.dumps(result, ensure_ascii=False, indent=2))
