"""Real server, real threads: N patients race for a session with fewer serials than N."""
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from conftest import future, wd

ROOT = os.path.dirname(os.path.dirname(__file__))
PORT = 8765


@pytest.fixture()
def live_server():
    db = os.path.join(tempfile.mkdtemp(), "live.db")
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}", "SMS_ENABLED": "false",
           "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "admin123", "AGENT_USERNAME": ""}
    subprocess.run([sys.executable, "-m", "app.seed"], cwd=ROOT, env=env, check=True, capture_output=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{PORT}"
    for _ in range(50):
        try:
            requests.get(base + "/", timeout=0.5)
            break
        except requests.RequestException:
            time.sleep(0.2)
    yield base
    proc.terminate()
    proc.wait(timeout=10)


def test_concurrent_bookings_never_share_a_serial(live_server):
    base = live_server
    key = requests.post(base + "/auth/login", data={"username": "admin", "password": "admin123"}).json()["api_key"]
    h = {"X-API-Key": key}
    d = future("sunday")
    doc = requests.post(base + "/doctors", json={"name": "Dr. Race"}, headers=h).json()
    requests.post(f"{base}/doctors/{doc['id']}/schedules", headers=h,
                  json={"weekday": wd(d), "start_time": "09:00", "end_time": "12:00", "max_patients": 5})
    pids = [
        requests.post(base + "/patients", headers=h,
                      json={"name": f"P{i}", "age": 30, "mobile_number": f"0170000{i:04d}"}).json()["id"]
        for i in range(12)
    ]

    def book(pid):
        return requests.post(base + "/appointments", headers=h, timeout=60,
                             json={"patient_id": pid, "doctor_id": doc["id"], "appointment_date": str(d)})

    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(book, pids))

    ok = [r.json() for r in results if r.status_code == 201]
    full = [r.json() for r in results if r.status_code == 409]
    assert len(ok) == 5, [r.text for r in results if r.status_code not in (201, 409)]
    assert sorted(a["serial_number"] for a in ok) == [1, 2, 3, 4, 5]
    assert len(full) == 7 and {f["code"] for f in full} == {"SESSION_FULL"}
