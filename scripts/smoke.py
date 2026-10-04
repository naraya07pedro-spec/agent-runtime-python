"""Exercise the Compose stack over TCP, including approval and duplicate admission."""

import json
import time
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    env = dict(
        line.split("=", 1)
        for line in Path(".env").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    base = "http://127.0.0.1:8000"

    def request(method, path, body=None, approval=False, key=None):
        token = env["RUNTIME_APPROVAL_KEY" if approval else "RUNTIME_API_KEY"]
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = key
        req = Request(
            base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        with urlopen(req, timeout=15) as response:
            return json.load(response)

    def wait_for(eid, state):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            view = request("GET", f"/executions/{eid}")
            if view["state"] == state:
                return view
            if view["state"] in {"FAILED_PERMANENT", "CANCELLED"}:
                raise RuntimeError("smoke execution failed: " + str(view["error_code"]))
            time.sleep(0.1)
        raise RuntimeError("smoke state deadline exceeded")

    key = "smoke-" + uuid4().hex
    body = {"business_key": key, "prompt": "lookup customer demo"}
    first = request("POST", "/executions", body, key=key)
    repeated = request("POST", "/executions", body, key=key)
    assert first["id"] == repeated["id"]
    wait_for(first["id"], "SUCCEEDED")
    key = "approval-smoke-" + uuid4().hex
    notification = request(
        "POST", "/executions", {"business_key": key, "prompt": "notify customer demo"}, key=key
    )
    waiting = wait_for(notification["id"], "WAITING_FOR_APPROVAL")
    action = waiting["action"]
    request(
        "POST",
        f"/approvals/{action['id']}/decision",
        {"fingerprint": action["fingerprint"], "approve": True},
        approval=True,
    )
    wait_for(notification["id"], "SUCCEEDED")
    assert request("GET", "/ready")["status"] == "ready"
    metrics_request = Request(
        base + "/metrics", headers={"Authorization": "Bearer " + env["RUNTIME_API_KEY"]}
    )
    with urlopen(metrics_request, timeout=15) as response:
        metrics = response.read().decode()
    assert 'runtime_executions{state="SUCCEEDED"}' in metrics
    assert "runtime_requests_total" in metrics
    print(
        "Compose smoke passed: TCP API, readiness, metrics, worker, migrations, duplicate admission, persisted approval, external sandbox effect."
    )


if __name__ == "__main__":
    main()
