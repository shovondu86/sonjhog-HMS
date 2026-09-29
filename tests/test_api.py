from datetime import timedelta

from conftest import future, wd


# ── auth & scopes ────────────────────────────────────────────────────────────
def test_login_and_key_required(client, admin):
    assert client.get("/patients").status_code == 401
    assert client.get("/patients", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/patients", headers=admin).status_code == 200
    assert client.get("/").json()["status"] == "ok"


def test_agent_scope_rules(client, hosp, agent, admin):
    doc = hosp.doctor(mobile_number="01711111111", email="d@example.com", designation="Consultant")
    # read allowed, staff-private fields OMITTED (not null)
    got = client.get(f"/doctors/{doc['id']}", headers=agent).json()
    assert "mobile_number" not in got and "email" not in got and got["designation"] == "Consultant"
    assert client.get(f"/doctors/{doc['id']}", headers=admin).json()["mobile_number"] == "01711111111"
    # write only patients + appointments
    r = client.put(f"/doctors/{doc['id']}", json={"designation": "x"}, headers=agent)
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_SCOPE"
    assert client.post("/departments", json={"name": "X", "service_line": "support"}, headers=agent).status_code == 403
    assert client.get("/audit-log", headers=agent).json()["code"] == "FORBIDDEN_SCOPE"
    assert client.post("/patients", json={"name": "A", "age": 5, "mobile_number": "01700000000"}, headers=agent).status_code == 201


def test_agent_never_sees_private_doctor_fields_inside_appointments(client, hosp, agent):
    d = future("sunday")
    doc = hosp.doctor(mobile_number="01711111111")
    hosp.schedule(doc["id"], d)
    p = hosp.patient()
    r = hosp.book(p["id"], doc["id"], d, headers=agent)
    assert r.status_code == 201
    assert "mobile_number" not in r.json()["doctor"]
    assert r.json()["patient"]["mobile_number"] == "01812345678"


# ── patients ─────────────────────────────────────────────────────────────────
def test_mobile_filter_normalises_both_sides(client, hosp, admin):
    p = hosp.patient(mobile="01712-345678")
    hosp.patient(name="Other", mobile="01999999999")
    for q in ("+8801712345678", "8801712345678", "01712-345678", "01712345678"):
        r = client.get("/patients", params={"mobile_number": q}, headers=admin).json()
        assert [x["id"] for x in r] == [p["id"]], q
    assert client.get("/patients", params={"mobile_number": "017%"}, headers=admin).json() == []


def test_idempotency_key(client, admin):
    body = {"name": "Once", "age": 30, "mobile_number": "01700000001"}
    h = {**admin, "Idempotency-Key": "key-12345678"}
    a = client.post("/patients", json=body, headers=h)
    b = client.post("/patients", json=body, headers=h)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert len(client.get("/patients", headers=admin).json()) == 1
    c = client.post("/patients", json={**body, "age": 31}, headers=h)
    assert c.status_code == 409 and c.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


# ── booking rules ────────────────────────────────────────────────────────────
def test_booking_serials_time_and_rules(client, hosp, admin):
    d = future("sunday")
    doc = hosp.doctor()
    hosp.schedule(doc["id"], d, start="09:00", end="12:00", cap=2, slot_minutes=15,
                  room="3042", floor="3rd", desk="A")
    p1, p2, p3 = hosp.patient("A"), hosp.patient("B", "01800000002"), hosp.patient("C", "01800000003")

    a = hosp.book(p1["id"], doc["id"], d, appointment_time="23:59").json()  # client time ignored
    assert (a["serial_number"], a["appointment_time"], a["room"], a["floor"]) == (1, "09:00", "3042", "3rd")
    b = hosp.book(p2["id"], doc["id"], d).json()
    assert (b["serial_number"], b["appointment_time"]) == (2, "09:15")

    dup = hosp.book(p1["id"], doc["id"], d)
    assert dup.status_code == 409 and dup.json()["code"] == "DUPLICATE_BOOKING"

    full = hosp.book(p3["id"], doc["id"], d)
    assert full.status_code == 409 and full.json()["code"] == "SESSION_FULL"
    assert full.json()["next_available_date"] == str(d + timedelta(days=7))

    # cancelling frees the serial, which is reused (lowest free)
    r = client.put(f"/appointments/{a['id']}", json={"status": "cancelled", "cancel_reason": "changed mind"}, headers=admin)
    assert r.status_code == 200 and r.json()["cancel_reason"] == "changed mind"
    c = hosp.book(p3["id"], doc["id"], d).json()
    assert c["serial_number"] == 1

    # availability agrees
    av = client.get(f"/doctors/{doc['id']}/availability", params={"from": str(d), "to": str(d)}, headers=admin).json()
    s = av["days"][0]["sessions"][0]
    assert av["days"][0]["status"] == "full" and (s["capacity"], s["booked"], s["remaining"], s["next_serial"]) == (2, 2, 0, None)


def test_past_no_session_and_not_found(client, hosp):
    doc = hosp.doctor()
    p = hosp.patient()
    r = hosp.book(p["id"], doc["id"], future("monday") - timedelta(days=60))
    assert r.json()["code"] == "PAST_DATE"
    nxt = future("monday")
    hosp.schedule(doc["id"], future("tuesday"))
    r = hosp.book(p["id"], doc["id"], nxt)
    assert r.status_code == 409 and r.json()["code"] == "NO_SESSION"
    assert r.json()["next_available_date"] == str(future("tuesday", min_days=(nxt - future("tuesday", 0)).days + 1)) or r.json()["next_available_date"]
    assert client.post("/appointments", json={"patient_id": 999, "doctor_id": doc["id"], "appointment_date": str(nxt)},
                       headers=hosp.h).json()["code"] == "PATIENT_NOT_FOUND"
    assert client.post("/appointments", json={"patient_id": p["id"], "doctor_id": 999, "appointment_date": str(nxt)},
                       headers=hosp.h).json()["code"] == "DOCTOR_NOT_FOUND"


def test_two_sessions_need_schedule_id(client, hosp):
    d = future("wednesday")
    doc = hosp.doctor()
    s1 = hosp.schedule(doc["id"], d, "09:00", "12:00")
    s2 = hosp.schedule(doc["id"], d, "15:00", "20:00")
    p = hosp.patient()
    r = hosp.book(p["id"], doc["id"], d)
    assert r.status_code == 409 and r.json()["code"] == "SESSION_REQUIRED"
    ok = hosp.book(p["id"], doc["id"], d, schedule_id=s2["id"]).json()
    assert ok["session_start"] == "15:00" and ok["schedule_id"] == s2["id"]


def test_report_queue(client, hosp):
    d = future("thursday")
    doc = hosp.doctor()
    hosp.schedule(doc["id"], d, cap=1, report_capacity=2)
    p1, p2 = hosp.patient("A"), hosp.patient("B", "01800000002")
    r1 = hosp.book(p1["id"], doc["id"], d, visit_type="report_showing").json()
    assert (r1["queue"], r1["serial_number"]) == ("report", 1)
    r2 = hosp.book(p2["id"], doc["id"], d, visit_type="new").json()
    assert (r2["queue"], r2["serial_number"]) == ("regular", 1)  # report visits do not use regular capacity


def test_doctor_not_accepting(client, hosp):
    d = future("sunday")
    doc = hosp.doctor(accepting_appointments=False)
    hosp.schedule(doc["id"], d)
    p = hosp.patient()
    assert hosp.book(p["id"], doc["id"], d).json()["code"] == "DOCTOR_NOT_ACCEPTING"


# ── holidays / exceptions / affected appointments ────────────────────────────
def test_holiday_blocks_booking_and_lists_affected(client, hosp, admin):
    d = future("sunday")
    doc = hosp.doctor()
    hosp.schedule(doc["id"], d)
    p = hosp.patient()
    booked = hosp.book(p["id"], doc["id"], d).json()

    r = client.post("/holidays", json={"date": str(d), "name": "Durga Puja", "note": "staff only"}, headers=admin)
    assert r.status_code == 201
    assert [a["id"] for a in r.json()["affected_appointments"]] == [booked["id"]]
    # appointment itself is untouched
    assert client.get(f"/appointments/{booked['id']}", headers=admin).json()["status"] == "scheduled"

    assert client.post("/holidays", json={"date": str(d), "name": "dup"}, headers=admin).json()["code"] == "DUPLICATE_DATE"
    p2 = hosp.patient("B", "01800000002")
    blocked = hosp.book(p2["id"], doc["id"], d)
    assert blocked.status_code == 409 and blocked.json()["code"] == "HOLIDAY"
    assert blocked.json()["holiday_name"] == "Durga Puja"
    assert blocked.json()["next_available_date"] == str(d + timedelta(days=7))

    day = client.get(f"/doctors/{doc['id']}/availability", params={"from": str(d), "to": str(d)}, headers=admin).json()["days"][0]
    assert day["status"] == "holiday" and day["holiday_name"] == "Durga Puja" and day["sessions"] == []

    # agent sees the holiday but not the staff note
    lst = client.get("/holidays", headers=admin).json()
    assert lst[0]["note"] == "staff only"
    hid = lst[0]["id"]
    assert client.delete(f"/holidays/{hid}", headers=admin).status_code == 204
    assert hosp.book(p2["id"], doc["id"], d).status_code == 201


def test_leave_exception_and_extra_session(client, hosp, admin, agent):
    d = future("monday")
    doc = hosp.doctor()
    hosp.schedule(doc["id"], d, "09:00", "12:00")
    p = hosp.patient()
    a = hosp.book(p["id"], doc["id"], d).json()

    r = client.post(f"/doctors/{doc['id']}/exceptions", json={"date": str(d), "type": "leave", "note": "sick"}, headers=admin)
    assert r.status_code == 201 and [x["id"] for x in r.json()["affected_appointments"]] == [a["id"]]
    day = client.get(f"/doctors/{doc['id']}/availability", params={"from": str(d), "to": str(d)}, headers=admin).json()["days"][0]
    assert day["status"] == "leave"
    assert "note" not in client.get(f"/doctors/{doc['id']}/exceptions", headers=agent).json()[0]

    # extra one-off session on the same date? leave wins; use another date instead
    d2 = d + timedelta(days=1)
    ex = client.post(f"/doctors/{doc['id']}/exceptions", headers=admin, json={
        "date": str(d2), "type": "extra_session", "start_time": "18:00", "end_time": "20:00", "max_patients": 5})
    assert ex.status_code == 201, ex.text
    eid = ex.json()["exception"]["id"]
    b = hosp.book(p["id"], doc["id"], d2, exception_id=eid).json()
    assert b["exception_id"] == eid and b["serial_number"] == 1 and b["session_start"] == "18:00"
    blocked = client.delete(f"/exceptions/{eid}", headers=admin)
    assert blocked.status_code == 409 and blocked.json()["code"] == "HAS_FUTURE_APPOINTMENTS"


def test_schedule_put_reports_affected_and_conflicts(client, hosp, admin):
    d = future("sunday")
    doc, doc2 = hosp.doctor(), hosp.doctor("Dr. Two")
    s = hosp.schedule(doc["id"], d, "09:00", "12:00", room="101", desk="A")
    p = hosp.patient()
    a = hosp.book(p["id"], doc["id"], d).json()

    # overlap (same doctor) and room clash (other doctor)
    r = client.post(f"/doctors/{doc['id']}/schedules", headers=admin,
                    json={"weekday": wd(d), "start_time": "11:00", "end_time": "13:00", "max_patients": 5})
    assert r.status_code == 409 and r.json()["code"] == "SCHEDULE_OVERLAP"
    r = client.post(f"/doctors/{doc2['id']}/schedules", headers=admin,
                    json={"weekday": wd(d), "start_time": "10:00", "end_time": "11:00", "max_patients": 5, "room": "101", "desk": "A"})
    assert r.status_code == 409 and r.json()["code"] == "ROOM_CONFLICT"

    # moving the session away: appointment is reported, never moved
    r = client.put(f"/schedules/{s['id']}", json={"start_time": "15:00", "end_time": "18:00"}, headers=admin)
    assert r.status_code == 200
    assert [x["id"] for x in r.json()["affected_appointments"]] == [a["id"]]
    assert client.get(f"/appointments/{a['id']}", headers=admin).json()["appointment_time"] == "09:00"

    # deleting a session with future bookings is refused
    r = client.delete(f"/schedules/{s['id']}", headers=admin)
    assert r.status_code == 409 and r.json()["code"] == "HAS_FUTURE_APPOINTMENTS"


def test_rescheduling_via_put_assigns_new_serial(client, hosp, admin):
    d1, d2 = future("sunday"), future("sunday") + timedelta(days=7)
    doc = hosp.doctor()
    hosp.schedule(doc["id"], d1, cap=5)
    p, q = hosp.patient(), hosp.patient("B", "01800000002")
    hosp.book(q["id"], doc["id"], d2)  # serial 1 on d2
    a = hosp.book(p["id"], doc["id"], d1).json()
    r = client.put(f"/appointments/{a['id']}", json={"appointment_date": str(d2)}, headers=admin).json()
    assert r["appointment_date"] == str(d2) and r["serial_number"] == 2


# ── departments / doctors / sync ─────────────────────────────────────────────
def test_department_search_rules_and_delete(client, hosp, admin, agent):
    hosp.dept("ENT, Head & Neck Surgery", aliases=["kan nak gola"])
    hosp.dept("Gastroenterology")
    got = client.get("/departments", params={"search": "ent"}, headers=admin).json()
    assert [x["name"] for x in got] == ["ENT, Head & Neck Surgery"]
    assert [x["name"] for x in client.get("/departments", params={"search": "gola"}, headers=admin).json()] == ["ENT, Head & Neck Surgery"]
    assert client.post("/departments", json={"name": "gastroenterology", "service_line": "support"}, headers=admin).json()["code"] == "DUPLICATE_NAME"

    dep = hosp.dept("Cardiology")
    hosp.doctor(department_id=dep["id"])
    r = client.delete(f"/departments/{dep['id']}", headers=admin)
    assert r.status_code == 409 and r.json()["code"] == "HAS_DOCTORS"
    assert client.get(f"/departments/{dep['id']}", headers=admin).json()["doctor_count"] == 1


def test_soft_delete_and_sync(client, hosp, admin, agent):
    dep = hosp.dept("Neurology")
    first = client.get("/departments", headers=admin)
    stamp = first.headers["X-Server-Time"]
    assert "T" in stamp
    # inactive is hidden from agents, visible to sync with include_deleted
    off = hosp.dept("Dermatology", is_active=False)
    names = lambda r: sorted(x["name"] for x in r.json())
    assert names(client.get("/departments", headers=agent)) == ["Neurology"]
    assert names(client.get("/departments", params={"include_deleted": True}, headers=agent)) == ["Dermatology", "Neurology"]
    assert client.get(f"/departments/{off['id']}", headers=agent).status_code == 404

    # updated_since sees only changes after the stamp, including deletions
    client.put(f"/departments/{dep['id']}", json={"description": "brain"}, headers=admin)
    client.delete(f"/departments/{off['id']}", headers=admin)
    ch = client.get("/departments", params={"updated_since": stamp, "include_deleted": True}, headers=agent).json()
    assert {x["name"] for x in ch} == {"Neurology", "Dermatology"}
    gone = [x for x in ch if x["name"] == "Dermatology"][0]
    assert gone["deleted_at"] is not None
    assert "Dermatology" not in names(client.get("/departments", headers=admin))


def test_doctor_delete_rules_and_available_on(client, hosp, admin):
    d = future("saturday")
    doc, other = hosp.doctor("Dr. Sits"), hosp.doctor("Dr. Never")
    hosp.schedule(doc["id"], d)
    p = hosp.patient()
    hosp.book(p["id"], doc["id"], d)
    got = client.get("/doctors", params={"available_on": str(d)}, headers=admin).json()
    assert [x["name"] for x in got] == ["Dr. Sits"]
    r = client.delete(f"/doctors/{doc['id']}", headers=admin)
    assert r.status_code == 409 and r.json()["code"] == "HAS_FUTURE_APPOINTMENTS"
    assert client.delete(f"/doctors/{other['id']}", headers=admin).status_code == 204
    assert client.get(f"/doctors/{other['id']}", headers=admin).status_code == 404
    listed = client.get("/doctors", params={"include_deleted": True}, headers=admin).json()
    assert [x for x in listed if x["id"] == other["id"]][0]["deleted_at"]


def test_doctor_search_is_literal(client, hosp, admin):
    hosp.doctor("Dr. 100% Sure")
    hosp.doctor("Dr. Other")
    assert len(client.get("/doctors", params={"search": "100%"}, headers=admin).json()) == 1
    assert len(client.get("/doctors", params={"search": "%"}, headers=admin).json()) == 1


# ── content resources ────────────────────────────────────────────────────────
def test_hospital_packages_services_articles(client, admin, agent):
    h = client.get("/hospital", headers=admin).json()
    assert h["name"] and h["updated_at"]
    r = client.put("/hospital", json={"address": "Green Road", "desk_hours": {"appointment": "8am-10pm"},
                                       "other_contacts": [{"label": "Home service", "value": "10666"}]}, headers=admin)
    assert r.json()["desk_hours"]["appointment"] == "8am-10pm"
    assert client.put("/hospital", json={"address": "x"}, headers=agent).status_code == 403

    client.post("/packages", headers=admin, json={"name": "Gold (Male)", "price": 5000, "gender": "male", "investigations": ["CBC with ESR"]})
    client.post("/packages", headers=admin, json={"name": "Silver (Any)", "price": 3000})
    client.post("/packages", headers=admin, json={"name": "Pink (Female)", "price": 4000, "gender": "female"})
    males = {x["name"] for x in client.get("/packages", params={"gender": "male"}, headers=admin).json()}
    assert males == {"Gold (Male)", "Silver (Any)"}
    assert [x["name"] for x in client.get("/packages", params={"search": "esr"}, headers=admin).json()] == ["Gold (Male)"]

    client.post("/services", headers=admin, json={"name": "PFT Room", "kind": "facility", "service_line": "support", "room": "2010", "floor": "2nd"})
    assert client.get("/services", params={"kind": "facility"}, headers=agent).json()[0]["room"] == "2010"

    bad = client.post("/articles", headers=admin, json={"title": "t", "topic": "booking"})
    assert bad.status_code == 422
    art = client.post("/articles", headers=admin, json={"title": "How to book", "topic": "booking", "body": "Call us."}).json()
    assert art["is_published"] is False
    assert client.get("/articles", headers=agent).json() == []  # unpublished hidden from the agent
    client.put(f"/articles/{art['id']}", json={"is_published": True}, headers=admin)
    assert len(client.get("/articles", headers=agent).json()) == 1


def test_audit_log(client, hosp, admin):
    doc = hosp.doctor()
    client.put(f"/doctors/{doc['id']}", json={"designation": "Prof"}, headers=admin)
    d = future("sunday")
    hosp.schedule(doc["id"], d)
    log = client.get("/audit-log", params={"doctor_id": doc["id"]}, headers=admin).json()
    assert [(e["action"], e["entity_type"]) for e in log] == [("create", "schedule"), ("update", "doctor"), ("create", "doctor")]
    upd = [e for e in log if e["action"] == "update"][0]
    assert upd["before"]["designation"] is None and upd["after"]["designation"] == "Prof"
    assert upd["username"] == "admin"
    assert client.get("/audit-log", params={"entity_type": "schedule"}, headers=admin).json()[0]["entity_type"] == "schedule"


def test_queue(client, hosp, admin):
    d = __import__("app.scheduling", fromlist=["today"]).today()
    doc = hosp.doctor()
    s = hosp.schedule(doc["id"], d, "00:00", "23:59", cap=10)
    r = client.put(f"/queue/{s['id']}", json={"now_serving": 4}, headers=admin)
    assert r.status_code == 200 and r.json()["now_serving"] == 4
    q = client.get("/queue", headers=admin).json()
    assert q[0]["schedule_id"] == s["id"] and q[0]["now_serving"] == 4
    av = client.get(f"/doctors/{doc['id']}/availability", params={"from": str(d), "to": str(d)}, headers=admin).json()
    assert av["days"][0]["sessions"][0]["now_serving"] == 4
