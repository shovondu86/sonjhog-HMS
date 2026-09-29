"""Availability + booking rules. Everything the voice agent hears is computed here,
live, from schedules, exceptions, holidays and existing bookings."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.enums import WEEKDAY_BY_INDEX, AppointmentStatus
from app.errors import ApiError, not_found
from app.models import (
    Appointment, Doctor, DoctorSchedule, Holiday, QueueState, ScheduleException,
)

TZ = ZoneInfo(settings.timezone)


def today() -> date:
    """'Today' by the hospital's calendar (Asia/Dhaka), not the server's."""
    return datetime.now(TZ).date()


def weekday_name(d: date) -> str:
    return WEEKDAY_BY_INDEX[d.weekday()]


def overlap_time(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    return a_start < b_end and b_start < a_end


def overlap_validity(a_from, a_to, b_from, b_to) -> bool:
    """Date ranges where None means open-ended."""
    return (a_from is None or b_to is None or a_from <= b_to) and (
        b_from is None or a_to is None or b_from <= a_to
    )


def lowest_free(used: Set[int], capacity: int) -> Optional[int]:
    for i in range(1, capacity + 1):
        if i not in used:
            return i
    return None


# ── sessions ─────────────────────────────────────────────────────────────────
@dataclass
class Session_:
    kind: str  # 's' = weekly schedule row, 'e' = extra_session exception
    id: int
    start_time: time
    end_time: time
    room: Optional[str]
    floor: Optional[str]
    desk: Optional[str]
    capacity: int
    slot_minutes: Optional[int]
    report_capacity: int

    @property
    def schedule_id(self) -> Optional[int]:
        return self.id if self.kind == "s" else None

    @property
    def exception_id(self) -> Optional[int]:
        return self.id if self.kind == "e" else None


@dataclass
class Day:
    date: date
    weekday: str
    status: str
    holiday_name: Optional[str] = None
    sessions: List[Session_] = field(default_factory=list)


def _from_schedule(s: DoctorSchedule) -> Session_:
    return Session_(
        "s", s.id, s.start_time, s.end_time, s.room, s.floor, s.desk,
        s.max_patients, s.slot_minutes, s.report_capacity or 0,
    )


def _from_extra(e: ScheduleException) -> Session_:
    return Session_(
        "e", e.id, e.start_time, e.end_time, e.room, e.floor, e.desk,
        e.max_patients or 0, e.slot_minutes, e.report_capacity or 0,
    )


def schedule_applies(s: DoctorSchedule, d: date) -> bool:
    return (
        s.is_active
        and s.weekday == weekday_name(d)
        and (s.valid_from is None or s.valid_from <= d)
        and (s.valid_to is None or s.valid_to >= d)
    )


class Ctx:
    """Preloads everything needed to evaluate many doctors over a date range with a
    handful of queries."""

    def __init__(
        self,
        db: Session,
        doctor_ids: Iterable[int],
        d_from: date,
        d_to: date,
        *,
        bookings: bool = True,
        exclude_appointment_id: Optional[int] = None,
    ):
        ids = list(doctor_ids)
        self.d_from, self.d_to = d_from, d_to
        self.schedules: Dict[int, List[DoctorSchedule]] = defaultdict(list)
        self.exceptions: Dict[Tuple[int, date], List[ScheduleException]] = defaultdict(list)
        self.used: Dict[tuple, Set[int]] = defaultdict(set)
        self.now_serving: Dict[Tuple[int, str], int] = {}

        if ids:
            rows = (
                db.query(DoctorSchedule)
                .filter(
                    DoctorSchedule.doctor_id.in_(ids),
                    DoctorSchedule.is_active.is_(True),
                    or_(DoctorSchedule.valid_from.is_(None), DoctorSchedule.valid_from <= d_to),
                    or_(DoctorSchedule.valid_to.is_(None), DoctorSchedule.valid_to >= d_from),
                )
                .all()
            )
            for r in rows:
                self.schedules[r.doctor_id].append(r)
            for e in (
                db.query(ScheduleException)
                .filter(
                    ScheduleException.doctor_id.in_(ids),
                    ScheduleException.date >= d_from,
                    ScheduleException.date <= d_to,
                )
                .all()
            ):
                self.exceptions[(e.doctor_id, e.date)].append(e)

        self.holidays: Dict[date, Holiday] = {
            h.date: h
            for h in db.query(Holiday).filter(Holiday.date >= d_from, Holiday.date <= d_to)
        }

        if bookings and ids:
            q = db.query(
                Appointment.schedule_id, Appointment.exception_id,
                Appointment.appointment_date, Appointment.queue, Appointment.serial_number,
            ).filter(
                Appointment.doctor_id.in_(ids),
                Appointment.appointment_date >= d_from,
                Appointment.appointment_date <= d_to,
                Appointment.status != AppointmentStatus.cancelled,
            )
            if exclude_appointment_id is not None:
                q = q.filter(Appointment.id != exclude_appointment_id)
            for sid, eid, d, queue, serial in q:
                if sid is not None:
                    self.used[("s", sid, d, queue)].add(serial)
                elif eid is not None:
                    self.used[("e", eid, d, queue)].add(serial)

            t = today()
            if d_from <= t <= d_to:
                for qs in db.query(QueueState).filter(QueueState.date == t):
                    self.now_serving[(qs.schedule_id, qs.queue)] = qs.now_serving

    # ── evaluation ──
    def day(self, doctor_id: int, d: date) -> Day:
        wd = weekday_name(d)
        hol = self.holidays.get(d)
        if hol and hol.opd_closed:
            return Day(d, wd, "holiday", hol.name)

        excs = self.exceptions.get((doctor_id, d), [])
        if any(e.type == "leave" for e in excs):
            return Day(d, wd, "leave")

        regular = [s for s in self.schedules.get(doctor_id, []) if schedule_applies(s, d)]
        cancelled = {e.schedule_id for e in excs if e.type == "cancelled"}
        kept = [s for s in regular if s.id not in cancelled]
        extras = [e for e in excs if e.type == "extra_session"]
        sessions = [_from_schedule(s) for s in kept] + [_from_extra(e) for e in extras]
        sessions.sort(key=lambda s: (s.start_time, s.end_time))

        if not sessions:
            return Day(d, wd, "leave" if regular else "no_session")

        status = "full"
        for s in sessions:
            if self.stats(s, d)["remaining"] > 0:
                status = "available"
                break
        return Day(d, wd, status, None, sessions)

    def used_serials(self, s: Session_, d: date, queue: str) -> Set[int]:
        return self.used.get((s.kind, s.id, d, queue), set())

    def stats(self, s: Session_, d: date) -> dict:
        used = self.used_serials(s, d, "regular")
        booked = len(used)
        out = {
            "capacity": s.capacity,
            "booked": booked,
            "remaining": max(s.capacity - booked, 0),
            "next_serial": lowest_free(used, s.capacity),
            "report_capacity": s.report_capacity,
            "report_booked": 0,
            "report_remaining": 0,
            "now_serving": None,
        }
        if s.report_capacity > 0:
            rused = self.used_serials(s, d, "report")
            out["report_booked"] = len(rused)
            out["report_remaining"] = max(s.report_capacity - len(rused), 0)
        if s.kind == "s" and d == today():
            out["now_serving"] = self.now_serving.get((s.id, "regular"))
        return out


def availability_days(ctx: Ctx, doctor_id: int, d_from: date, d_to: date) -> List[dict]:
    """One entry per date in the range, including dates with no session."""
    days = []
    d = d_from
    while d <= d_to:
        day = ctx.day(doctor_id, d)
        sessions = []
        for s in day.sessions:
            st = ctx.stats(s, d)
            sessions.append(
                {
                    "schedule_id": s.schedule_id, "exception_id": s.exception_id,
                    "start_time": s.start_time, "end_time": s.end_time,
                    "room": s.room, "floor": s.floor, "desk": s.desk,
                    **st,
                }
            )
        days.append(
            {
                "date": d, "weekday": day.weekday, "status": day.status,
                "holiday_name": day.holiday_name, "sessions": sessions,
            }
        )
        d += timedelta(days=1)
    return days


def next_available_date(
    db: Session, doctor_id: int, after: date, queue: str = "regular", horizon: int = 31
) -> Optional[date]:
    """The doctor's next date (within `horizon` days) with a free serial."""
    start = after + timedelta(days=1)
    end = after + timedelta(days=horizon)
    ctx = Ctx(db, [doctor_id], start, end)
    d = start
    while d <= end:
        day = ctx.day(doctor_id, d)
        if day.status == "available":
            if queue == "report":
                for s in day.sessions:
                    st = ctx.stats(s, d)
                    if (s.report_capacity > 0 and st["report_remaining"] > 0) or (
                        s.report_capacity == 0 and st["remaining"] > 0
                    ):
                        return d
            else:
                return d
        d += timedelta(days=1)
    return None


def doctors_with_sessions_on(db: Session, doctor_ids: List[int], d: date) -> Set[int]:
    """Doctors with at least one session that date (status available or full)."""
    ctx = Ctx(db, doctor_ids, d, d, bookings=False)
    return {i for i in doctor_ids if ctx.day(i, d).sessions}


# ── booking ──────────────────────────────────────────────────────────────────
@dataclass
class Plan:
    session: Session_
    queue: str
    serial: int
    time: time


def plan_booking(
    db: Session,
    *,
    patient_id: int,
    doctor: Doctor,
    appt_date: date,
    schedule_id: Optional[int] = None,
    exception_id: Optional[int] = None,
    visit_type: str = "new",
    exclude_id: Optional[int] = None,
) -> Plan:
    """Steps 1-7 of POST /appointments. Raises ApiError (409/404) on any rule failure."""
    if not doctor.is_active or not doctor.accepting_appointments:
        raise ApiError(
            409, "DOCTOR_NOT_ACCEPTING",
            f"{doctor.name} is not accepting new appointments.",
        )

    # 1. past date
    if appt_date < today():
        raise ApiError(409, "PAST_DATE", f"{appt_date} is in the past.")

    ctx = Ctx(db, [doctor.id], appt_date, appt_date, exclude_appointment_id=exclude_id)

    # 2. holiday
    hol = ctx.holidays.get(appt_date)
    if hol and hol.opd_closed:
        raise ApiError(
            409, "HOLIDAY", f"OPD is closed on {appt_date} ({hol.name}).",
            holiday_name=hol.name,
            next_available_date=next_available_date(db, doctor.id, appt_date, visit_type_queue(visit_type)),
        )

    # 3. resolve the session
    day = ctx.day(doctor.id, appt_date)
    sessions = day.sessions
    if schedule_id is not None and exception_id is not None:
        raise ApiError(409, "SESSION_REQUIRED", "Give either schedule_id or exception_id, not both.")
    if schedule_id is not None:
        sess = next((s for s in sessions if s.schedule_id == schedule_id), None)
        if sess is None:
            row = db.get(DoctorSchedule, schedule_id)
            if row is None or row.doctor_id != doctor.id:
                raise not_found("Schedule")
            raise ApiError(
                409, "NO_SESSION", f"That session does not run on {appt_date}.",
                next_available_date=next_available_date(db, doctor.id, appt_date, visit_type_queue(visit_type)),
            )
    elif exception_id is not None:
        sess = next((s for s in sessions if s.exception_id == exception_id), None)
        if sess is None:
            row = db.get(ScheduleException, exception_id)
            if row is None or row.doctor_id != doctor.id or row.type != "extra_session":
                raise not_found("Extra session")
            raise ApiError(
                409, "NO_SESSION", f"That extra session is not on {appt_date}.",
                next_available_date=next_available_date(db, doctor.id, appt_date, visit_type_queue(visit_type)),
            )
    else:
        if not sessions:
            raise ApiError(
                409, "NO_SESSION", f"The doctor has no OPD session on {appt_date}.",
                next_available_date=next_available_date(db, doctor.id, appt_date, visit_type_queue(visit_type)),
            )
        if len(sessions) > 1:
            raise ApiError(
                409, "SESSION_REQUIRED",
                f"The doctor has {len(sessions)} sessions on {appt_date}; pass schedule_id.",
                candidates=[
                    {
                        "schedule_id": s.schedule_id, "exception_id": s.exception_id,
                        "start_time": s.start_time.strftime("%H:%M"),
                        "end_time": s.end_time.strftime("%H:%M"),
                    }
                    for s in sessions
                ],
            )
        sess = sessions[0]

    # 4. duplicate
    q = db.query(Appointment.id).filter(
        Appointment.patient_id == patient_id,
        Appointment.doctor_id == doctor.id,
        Appointment.appointment_date == appt_date,
        Appointment.status != AppointmentStatus.cancelled,
    )
    if exclude_id is not None:
        q = q.filter(Appointment.id != exclude_id)
    if q.first():
        raise ApiError(
            409, "DUPLICATE_BOOKING",
            "This patient already has an appointment with this doctor on that date.",
        )

    # 5. queue
    queue = "report" if (visit_type == "report_showing" and sess.report_capacity > 0) else "regular"

    # 6. lowest free serial
    used = ctx.used_serials(sess, appt_date, queue)
    capacity = sess.report_capacity if queue == "report" else sess.capacity
    serial = lowest_free(used, capacity)
    if serial is None:
        raise ApiError(
            409, "SESSION_FULL", f"All {capacity} serials for {appt_date} are booked.",
            next_available_date=next_available_date(db, doctor.id, appt_date, queue),
        )

    # 7. time
    if queue == "regular" and sess.slot_minutes:
        t = (
            datetime.combine(appt_date, sess.start_time)
            + timedelta(minutes=(serial - 1) * sess.slot_minutes)
        ).time()
    else:
        t = sess.start_time
    return Plan(sess, queue, serial, t)


def visit_type_queue(visit_type: str) -> str:
    return "report" if visit_type == "report_showing" else "regular"


def apply_plan(appt: Appointment, plan: Plan) -> None:
    s = plan.session
    appt.schedule_id = s.schedule_id
    appt.exception_id = s.exception_id
    appt.queue = plan.queue
    appt.serial_number = plan.serial
    appt.appointment_time = plan.time
    appt.session_start = s.start_time
    appt.session_end = s.end_time
    appt.room, appt.floor, appt.desk = s.room, s.floor, s.desk


# ── conflict checks (schedules) ──────────────────────────────────────────────
def check_schedule_conflicts(
    db: Session,
    *,
    doctor_id: int,
    weekday: str,
    start: time,
    end: time,
    room: Optional[str],
    desk: Optional[str],
    valid_from: Optional[date],
    valid_to: Optional[date],
    exclude_schedule_id: Optional[int] = None,
) -> None:
    rows = (
        db.query(DoctorSchedule)
        .filter(DoctorSchedule.weekday == weekday, DoctorSchedule.is_active.is_(True))
        .all()
    )
    for r in rows:
        if r.id == exclude_schedule_id:
            continue
        if not overlap_validity(valid_from, valid_to, r.valid_from, r.valid_to):
            continue
        if not overlap_time(start, end, r.start_time, r.end_time):
            continue
        clash = {
            "schedule_id": r.id, "doctor_id": r.doctor_id, "weekday": r.weekday,
            "start_time": r.start_time.strftime("%H:%M"), "end_time": r.end_time.strftime("%H:%M"),
            "room": r.room, "desk": r.desk,
        }
        if r.doctor_id == doctor_id:
            raise ApiError(
                409, "SCHEDULE_OVERLAP",
                "This session overlaps another session of the same doctor.",
                candidates=[clash],
            )
        if room and r.room and room.strip().lower() == r.room.strip().lower() and (
            (desk or "").strip().lower() == (r.desk or "").strip().lower()
        ):
            raise ApiError(
                409, "ROOM_CONFLICT",
                f"Room {room} is already allocated to another doctor at that time.",
                candidates=[clash],
            )


def check_extra_session_conflicts(
    db: Session, *, doctor_id: int, d: date, start: time, end: time,
    room: Optional[str], desk: Optional[str],
) -> None:
    """Extra one-off session: must not overlap the doctor's own sessions that date,
    nor take a room another doctor holds at that time."""
    doctors = [r[0] for r in db.query(Doctor.id).filter(Doctor.deleted_at.is_(None))]
    ctx = Ctx(db, doctors, d, d, bookings=False)
    for did in doctors:
        day = ctx.day(did, d)
        for s in day.sessions:
            if not overlap_time(start, end, s.start_time, s.end_time):
                continue
            clash = {
                "doctor_id": did, "schedule_id": s.schedule_id, "exception_id": s.exception_id,
                "start_time": s.start_time.strftime("%H:%M"), "end_time": s.end_time.strftime("%H:%M"),
                "room": s.room, "desk": s.desk,
            }
            if did == doctor_id:
                raise ApiError(
                    409, "SCHEDULE_OVERLAP",
                    "This extra session overlaps another session of the same doctor.",
                    candidates=[clash],
                )
            if room and s.room and room.strip().lower() == s.room.strip().lower() and (
                (desk or "").strip().lower() == (s.desk or "").strip().lower()
            ):
                raise ApiError(
                    409, "ROOM_CONFLICT",
                    f"Room {room} is already allocated to another doctor at that time.",
                    candidates=[clash],
                )


# ── affected appointments ────────────────────────────────────────────────────
def affected_for_doctor(
    db: Session, doctor_id: int, d_from: Optional[date] = None, d_to: Optional[date] = None,
    unlinked_schedule_ids: Optional[Set[int]] = None,
) -> List[Appointment]:
    """Scheduled appointments (from `d_from`, default today) of the doctor that no
    longer fall inside any of their sessions. Never modifies anything."""
    d_from = d_from or today()
    q = db.query(Appointment).filter(
        Appointment.doctor_id == doctor_id,
        Appointment.status == AppointmentStatus.scheduled,
        Appointment.appointment_date >= d_from,
    )
    if d_to:
        q = q.filter(Appointment.appointment_date <= d_to)
    appts = q.order_by(Appointment.appointment_date, Appointment.appointment_time).all()
    if not appts:
        return []
    lo = min(a.appointment_date for a in appts)
    hi = max(a.appointment_date for a in appts)
    ctx = Ctx(db, [doctor_id], lo, hi, bookings=False)
    out = []
    for a in appts:
        if unlinked_schedule_ids and a.schedule_id in unlinked_schedule_ids:
            out.append(a)
            continue
        day = ctx.day(doctor_id, a.appointment_date)
        if not any(s.start_time <= a.appointment_time <= s.end_time for s in day.sessions):
            out.append(a)
    return out
