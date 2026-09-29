import os
import sys
import tempfile
from datetime import timedelta

import pytest

_DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "admin123"
os.environ["AGENT_USERNAME"] = "voice"
os.environ["AGENT_PASSWORD"] = "voice123"
os.environ["SMS_ENABLED"] = "false"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import migrate, seed  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.enums import WEEKDAY_BY_INDEX  # noqa: E402
from app.main import app  # noqa: E402
from app.scheduling import today  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    seed.seed()
    with TestClient(app) as c:
        yield c


def _login(client, u, p):
    r = client.post("/auth/login", data={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return {"X-API-Key": r.json()["api_key"]}


@pytest.fixture()
def admin(client):
    return _login(client, "admin", "admin123")


@pytest.fixture()
def agent(client):
    return _login(client, "voice", "voice123")


def future(weekday_name: str, min_days: int = 2):
    """First date at least `min_days` from today (Asia/Dhaka) falling on that weekday."""
    d = today() + timedelta(days=min_days)
    while WEEKDAY_BY_INDEX[d.weekday()] != weekday_name:
        d += timedelta(days=1)
    return d


def wd(d):
    return WEEKDAY_BY_INDEX[d.weekday()]


class Hosp:
    """Small builder used across tests."""

    def __init__(self, client, admin):
        self.c, self.h = client, admin

    def dept(self, name="Cardiology", **kw):
        r = self.c.post("/departments", json={"name": name, "service_line": "appointment", **kw}, headers=self.h)
        assert r.status_code == 201, r.text
        return r.json()

    def doctor(self, name="Dr. Anika Rahman", **kw):
        r = self.c.post("/doctors", json={"name": name, **kw}, headers=self.h)
        assert r.status_code == 201, r.text
        return r.json()

    def patient(self, name="Karim Uddin", mobile="01812345678", age=34):
        r = self.c.post("/patients", json={"name": name, "age": age, "mobile_number": mobile}, headers=self.h)
        assert r.status_code == 201, r.text
        return r.json()

    def schedule(self, doctor_id, d, start="09:00", end="12:00", cap=3, **kw):
        body = {"weekday": wd(d), "start_time": start, "end_time": end, "max_patients": cap, **kw}
        r = self.c.post(f"/doctors/{doctor_id}/schedules", json=body, headers=self.h)
        assert r.status_code == 201, r.text
        return r.json()

    def book(self, patient_id, doctor_id, d, headers=None, **kw):
        return self.c.post(
            "/appointments",
            json={"patient_id": patient_id, "doctor_id": doctor_id, "appointment_date": str(d), **kw},
            headers=headers or self.h,
        )


@pytest.fixture()
def hosp(client, admin):
    return Hosp(client, admin)
