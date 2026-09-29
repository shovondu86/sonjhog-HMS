"""Upgrade a real 1.0.0-shaped database in place."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(__file__))

V1_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(50) NOT NULL UNIQUE,
  hashed_password VARCHAR(255) NOT NULL, api_key VARCHAR(64) NOT NULL UNIQUE, created_at DATETIME);
CREATE TABLE patients (id INTEGER PRIMARY KEY, name VARCHAR(150) NOT NULL, age INTEGER NOT NULL,
  mobile_number VARCHAR(20) NOT NULL, created_at DATETIME, updated_at DATETIME);
CREATE TABLE doctors (id INTEGER PRIMARY KEY, name VARCHAR(150) NOT NULL, designation VARCHAR(150),
  education VARCHAR(255), specialization VARCHAR(150), description TEXT, mobile_number VARCHAR(20),
  email VARCHAR(150), created_at DATETIME, updated_at DATETIME);
CREATE TABLE appointments (id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL, doctor_id INTEGER NOT NULL,
  appointment_date DATE NOT NULL, appointment_time TIME NOT NULL, status VARCHAR(9) NOT NULL, notes TEXT,
  created_at DATETIME, updated_at DATETIME);
"""

SCRIPT = r"""
import json
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import User
with TestClient(app) as c:
    # the fixture user has a placeholder password hash; read its API key directly
    db = SessionLocal(); u = db.query(User).filter_by(username="old").one(); k = u.api_key; scope = u.scope; db.close()
    h = {"X-API-Key": k}
    out = {
        "scope": scope,
        "doctors": c.get("/doctors", headers=h).json(),
        "appts": c.get("/appointments", headers=h).json(),
        "by_mobile": c.get("/patients", params={"mobile_number": "+8801712345678"}, headers=h).json(),
        "hospital": c.get("/hospital", headers=h).status_code,
    }
print("RESULT" + json.dumps(out))
"""


def test_upgrade_from_1_0_database():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "old.db")
    con = sqlite3.connect(path)
    con.executescript(V1_SCHEMA)
    con.execute("INSERT INTO users VALUES (1,'old','x','OLDKEY123','2026-01-01 00:00:00')")
    con.execute("INSERT INTO patients VALUES (1,'Rahim',40,'01712-345678','2026-01-01 00:00:00',NULL)")
    con.execute("INSERT INTO patients VALUES (2,'Karim',30,'01812345678','2026-01-01 00:00:00',NULL)")
    con.execute("INSERT INTO doctors VALUES (1,'Dr Old','Consultant','MBBS','Cardiology','x','017','a@b','2026-01-01 00:00:00',NULL)")
    for i, p in enumerate((1, 2), start=1):
        con.execute("INSERT INTO appointments VALUES (?,?,1,'2026-12-01','10:00:00','scheduled',NULL,'2026-01-01 00:00:00',NULL)", (i, p))
    con.commit()
    con.close()

    env = {**os.environ, "DATABASE_URL": f"sqlite:///{path}", "SMS_ENABLED": "false"}
    r = subprocess.run([sys.executable, "-m", "app.migrate"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "users.scope" in r.stdout and "appointments.serial_number" in r.stdout
    # idempotent
    r2 = subprocess.run([sys.executable, "-m", "app.migrate"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r2.returncode == 0 and "Added columns: none" in r2.stdout

    r3 = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True)
    line = [l for l in r3.stdout.splitlines() if l.startswith("RESULT")]
    assert line, r3.stderr[-2000:]
    out = json.loads(line[0][6:])
    assert out["scope"] == "admin"                                   # old users become admins
    assert out["doctors"][0]["is_active"] is True and out["doctors"][0]["accepting_appointments"] is True
    assert [a["serial_number"] for a in out["appts"]] == [1, 2]      # legacy serials back-filled
    assert [p["name"] for p in out["by_mobile"]] == ["Rahim"]        # phone normalised for old rows
    assert out["hospital"] == 200
