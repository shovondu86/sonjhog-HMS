from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.enums import AppointmentStatus
from app.errors import ApiError, not_found, validation_error
from app.models import (
    Appointment, Doctor, DoctorSchedule, QueueState, ScheduleException, User,
)
from app.routers.common import ERRORS
from app.scheduling import (
    affected_for_doctor, check_extra_session_conflicts, check_schedule_conflicts, today,
)
from app.schemas import (
    AppointmentDetailOut, ErrorResponse, ScheduleChangeResult, ScheduleCreate,
    ScheduleExceptionCreate, ScheduleExceptionOut, ScheduleExceptionResult, ScheduleOut,
    ScheduleUpdate,
)
from app.serialize import render, render_list, snapshot
from app.utils import apply_updates

router = APIRouter(tags=["Schedules"], dependencies=[Depends(get_current_user)])

CONFLICT = {
    409: {"model": ErrorResponse, "description": "`SCHEDULE_OVERLAP` or `ROOM_CONFLICT`"}
}
HAS_FUTURE = {
    409: {"model": ErrorResponse, "description": "`HAS_FUTURE_APPOINTMENTS`"}
}


def _doctor_or_404(db: Session, doctor_id: int) -> Doctor:
    d = db.get(Doctor, doctor_id)
    if d is None or d.deleted_at is not None:
        raise not_found("Doctor")
    return d


def _schedule_or_404(db: Session, schedule_id: int) -> DoctorSchedule:
    s = db.get(DoctorSchedule, schedule_id)
    if s is None:
        raise not_found("Schedule")
    return s


def _affected_payload(user, db, doctor_id, d_from=None, d_to=None):
    rows = affected_for_doctor(db, doctor_id, d_from, d_to)
    return render_list(user, AppointmentDetailOut, rows)


# ── weekly sessions ──────────────────────────────────────────────────────────
@router.get(
    "/doctors/{doctor_id}/schedules", response_model=List[ScheduleOut], responses=ERRORS,
    operation_id="list_doctor_schedules",
)
def list_doctor_schedules(
    doctor_id: int,
    effective_on: Optional[date] = Query(
        None,
        description="Only rows valid on this date (`valid_from <= date` and (`valid_to` is null "
        "or `valid_to >= date`)). Omit for all rows, past and future.",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The doctor's weekly OPD sessions. A weekday with no row means NO SESSION that
    day — there is no "unknown" state."""
    _doctor_or_404(db, doctor_id)
    q = db.query(DoctorSchedule).filter(DoctorSchedule.doctor_id == doctor_id)
    rows = q.order_by(DoctorSchedule.weekday, DoctorSchedule.start_time, DoctorSchedule.id).all()
    if effective_on is not None:
        rows = [
            r for r in rows
            if (r.valid_from is None or r.valid_from <= effective_on)
            and (r.valid_to is None or r.valid_to >= effective_on)
        ]
    return JSONResponse(render_list(user, ScheduleOut, rows))


@router.post(
    "/doctors/{doctor_id}/schedules", response_model=ScheduleOut, status_code=201,
    responses={**ERRORS, **CONFLICT}, operation_id="create_doctor_schedule",
)
def create_doctor_schedule(
    doctor_id: int, payload: ScheduleCreate,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    """Requires `admin` scope. Refused with `409 SCHEDULE_OVERLAP` if it overlaps another
    session of the same doctor on the same weekday within an overlapping validity range,
    and `409 ROOM_CONFLICT` if the room/desk is already allocated to another doctor for
    an overlapping time on that weekday."""
    _doctor_or_404(db, doctor_id)
    if payload.is_active:
        check_schedule_conflicts(
            db, doctor_id=doctor_id, weekday=payload.weekday.value,
            start=payload.start_time, end=payload.end_time, room=payload.room,
            desk=payload.desk, valid_from=payload.valid_from, valid_to=payload.valid_to,
        )
    data = payload.model_dump()
    data["weekday"] = payload.weekday.value
    row = DoctorSchedule(doctor_id=doctor_id, **data)
    db.add(row)
    db.flush()
    audit.record(db, user, "create", "schedule", row.id, None, snapshot(ScheduleOut, row),
                 doctor_id=doctor_id)
    db.commit()
    db.refresh(row)
    return JSONResponse(render(user, ScheduleOut, row), status_code=201)


@router.get(
    "/schedules/{schedule_id}", response_model=ScheduleOut, responses=ERRORS,
    operation_id="get_schedule",
)
def get_schedule(schedule_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return JSONResponse(render(user, ScheduleOut, _schedule_or_404(db, schedule_id)))


@router.put(
    "/schedules/{schedule_id}", response_model=ScheduleChangeResult,
    responses={**ERRORS, **CONFLICT}, operation_id="update_schedule",
)
def update_schedule(
    schedule_id: int, payload: ScheduleUpdate,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    """Requires `admin` scope. Takes effect immediately for every future date the row covers.
    To change a rota from a future date, end-date this row (`valid_to`) and create a new
    one with `valid_from` instead.

    Existing appointments are NEVER moved or cancelled by this call. Any scheduled
    appointment from today onward that no longer falls inside a session is returned in
    `affected_appointments` so staff can contact those patients."""
    row = _schedule_or_404(db, schedule_id)
    before = snapshot(ScheduleOut, row)
    data = payload.model_dump(exclude_unset=True)
    if "weekday" in data and data["weekday"] is not None:
        data["weekday"] = data["weekday"].value
    apply_updates(
        row, data,
        non_nullable=("weekday", "start_time", "end_time", "max_patients", "report_capacity", "is_active"),
    )
    if row.end_time <= row.start_time:
        raise validation_error(["body", "end_time"], "end_time must be after start_time")
    if row.valid_from and row.valid_to and row.valid_to < row.valid_from:
        raise validation_error(["body", "valid_to"], "valid_to must not be before valid_from")
    if row.is_active:
        check_schedule_conflicts(
            db, doctor_id=row.doctor_id, weekday=row.weekday, start=row.start_time,
            end=row.end_time, room=row.room, desk=row.desk,
            valid_from=row.valid_from, valid_to=row.valid_to, exclude_schedule_id=row.id,
        )
    db.flush()
    audit.record(db, user, "update", "schedule", row.id, before, snapshot(ScheduleOut, row),
                 doctor_id=row.doctor_id)
    affected = _affected_payload(user, db, row.doctor_id)
    db.commit()
    db.refresh(row)
    return JSONResponse(
        {"schedule": render(user, ScheduleOut, row), "affected_appointments": affected}
    )


@router.delete(
    "/schedules/{schedule_id}", status_code=204, responses={**ERRORS, **HAS_FUTURE},
    operation_id="delete_schedule",
)
def delete_schedule(schedule_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Requires `admin` scope. Refused with `409 HAS_FUTURE_APPOINTMENTS` while scheduled
    appointments from today onward are booked into this session. To retire a session,
    set `valid_to` instead."""
    row = _schedule_or_404(db, schedule_id)
    if db.query(Appointment.id).filter(
        Appointment.schedule_id == row.id,
        Appointment.status == AppointmentStatus.scheduled,
        Appointment.appointment_date >= today(),
    ).first():
        raise ApiError(
            409, "HAS_FUTURE_APPOINTMENTS",
            "Scheduled appointments are booked into this session. Set valid_to instead.",
        )
    before = snapshot(ScheduleOut, row)
    # Past appointments keep the room/floor/desk/time copied at booking; just unlink them.
    db.query(Appointment).filter(Appointment.schedule_id == row.id).update(
        {Appointment.schedule_id: None}, synchronize_session=False
    )
    db.query(ScheduleException).filter(ScheduleException.schedule_id == row.id).delete(
        synchronize_session=False
    )
    db.query(QueueState).filter(QueueState.schedule_id == row.id).delete(synchronize_session=False)
    doctor_id = row.doctor_id
    db.delete(row)
    audit.record(db, user, "delete", "schedule", schedule_id, before, None, doctor_id=doctor_id)
    db.commit()
    return Response(status_code=204)


# ── exceptions ───────────────────────────────────────────────────────────────
@router.get(
    "/doctors/{doctor_id}/exceptions", response_model=List[ScheduleExceptionOut],
    responses=ERRORS, operation_id="list_schedule_exceptions",
)
def list_schedule_exceptions(
    doctor_id: int,
    date_from: Optional[date] = Query(None, description="Defaults to today."),
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _doctor_or_404(db, doctor_id)
    q = db.query(ScheduleException).filter(
        ScheduleException.doctor_id == doctor_id,
        ScheduleException.date >= (date_from or today()),
    )
    if date_to is not None:
        q = q.filter(ScheduleException.date <= date_to)
    rows = q.order_by(ScheduleException.date, ScheduleException.id).all()
    return JSONResponse(render_list(user, ScheduleExceptionOut, rows))


@router.post(
    "/doctors/{doctor_id}/exceptions", response_model=ScheduleExceptionResult, status_code=201,
    responses={**ERRORS, **CONFLICT}, operation_id="create_schedule_exception",
)
def create_schedule_exception(
    doctor_id: int, payload: ScheduleExceptionCreate,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    """Requires `admin` scope. Leave, a cancelled session, or an extra one-off session on
    a single date. Takes effect immediately.

    As with schedule updates, existing appointments are NOT cancelled: those on the date
    that no longer fall inside a session are returned in `affected_appointments`."""
    _doctor_or_404(db, doctor_id)
    t = payload.type.value
    if t == "cancelled":
        if payload.schedule_id is None:
            raise validation_error(["body", "schedule_id"], "schedule_id is required for `cancelled`")
        sched = db.get(DoctorSchedule, payload.schedule_id)
        if sched is None or sched.doctor_id != doctor_id:
            raise not_found("Schedule")
    if t == "extra_session":
        missing = [
            f for f in ("start_time", "end_time", "max_patients") if getattr(payload, f) is None
        ]
        if missing:
            raise validation_error(["body", missing[0]], f"{', '.join(missing)} required for `extra_session`")
        if payload.end_time <= payload.start_time:
            raise validation_error(["body", "end_time"], "end_time must be after start_time")
        check_extra_session_conflicts(
            db, doctor_id=doctor_id, d=payload.date, start=payload.start_time,
            end=payload.end_time, room=payload.room, desk=payload.desk,
        )

    data = payload.model_dump()
    data["type"] = t
    if t != "cancelled":
        data["schedule_id"] = None  # ignored unless `cancelled`
    exc = ScheduleException(doctor_id=doctor_id, **data)
    db.add(exc)
    db.flush()
    audit.record(db, user, "create", "exception", exc.id, None,
                 snapshot(ScheduleExceptionOut, exc), doctor_id=doctor_id)
    affected = _affected_payload(user, db, doctor_id, payload.date, payload.date)
    db.commit()
    db.refresh(exc)
    return JSONResponse(
        {"exception": render(user, ScheduleExceptionOut, exc), "affected_appointments": affected},
        status_code=201,
    )


@router.delete(
    "/exceptions/{exception_id}", status_code=204, responses={**ERRORS, **HAS_FUTURE},
    operation_id="delete_schedule_exception",
)
def delete_schedule_exception(
    exception_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """Requires `admin` scope. Refused with `409 HAS_FUTURE_APPOINTMENTS` for an
    `extra_session` that has bookings."""
    exc = db.get(ScheduleException, exception_id)
    if exc is None:
        raise not_found("Exception")
    if exc.type == "extra_session" and db.query(Appointment.id).filter(
        Appointment.exception_id == exc.id,
        Appointment.status != AppointmentStatus.cancelled,
    ).first():
        raise ApiError(
            409, "HAS_FUTURE_APPOINTMENTS", "The extra session already has bookings."
        )
    before = snapshot(ScheduleExceptionOut, exc)
    db.query(Appointment).filter(Appointment.exception_id == exc.id).update(
        {Appointment.exception_id: None}, synchronize_session=False
    )
    doctor_id = exc.doctor_id
    db.delete(exc)
    audit.record(db, user, "delete", "exception", exception_id, before, None, doctor_id=doctor_id)
    db.commit()
    return Response(status_code=204)
