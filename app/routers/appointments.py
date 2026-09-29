from datetime import date
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app import idempotency
from app.database import get_db
from app.deps import get_current_user
from app.enums import AppointmentStatus, VisitType
from app.errors import ApiError, not_found
from app.models import Appointment, Doctor, Patient, User
from app.notifications import build_appointment_message, send_sms
from app.routers.common import ERRORS, Limit, Skip
from app.scheduling import apply_plan, plan_booking
from app.schemas import (
    AppointmentCreate, AppointmentDetailOut, AppointmentUpdate, ErrorResponse,
)
from app.serialize import render, render_list

router = APIRouter(
    prefix="/appointments", tags=["Appointments"], dependencies=[Depends(get_current_user)]
)

_OPTS = (joinedload(Appointment.patient), joinedload(Appointment.doctor).joinedload(Doctor.department))


def _load_detail(db: Session, appt_id: int) -> Appointment:
    return db.query(Appointment).options(*_OPTS).filter(Appointment.id == appt_id).one()


def _patient_or_404(db: Session, patient_id: int) -> Patient:
    p = db.get(Patient, patient_id)
    if p is None:
        raise ApiError(404, "PATIENT_NOT_FOUND", f"Patient {patient_id} not found")
    return p


def _doctor_or_404(db: Session, doctor_id: int) -> Doctor:
    d = db.get(Doctor, doctor_id)
    if d is None or d.deleted_at is not None:
        raise ApiError(404, "DOCTOR_NOT_FOUND", f"Doctor {doctor_id} not found")
    return d


@router.post(
    "", response_model=AppointmentDetailOut, status_code=201,
    operation_id="create_appointment_appointments_post",
    responses={
        404: {"model": ErrorResponse, "description": "`PATIENT_NOT_FOUND` or `DOCTOR_NOT_FOUND`"},
        409: {
            "model": ErrorResponse,
            "description": "`PAST_DATE`, `HOLIDAY`, `NO_SESSION`, `SESSION_REQUIRED`, "
            "`SESSION_FULL`, `DUPLICATE_BOOKING`, `DOCTOR_NOT_ACCEPTING`, or "
            "`IDEMPOTENCY_KEY_REUSED`.",
        },
    },
)
def create_appointment(
    payload: AppointmentCreate,
    background_tasks: BackgroundTasks,
    idempotency_key: Optional[str] = Header(
        None, alias="Idempotency-Key", min_length=8, max_length=128,
        description="Client-generated, unique per logical operation. Same key + same body "
        "returns the stored response; same key + different body is `409 IDEMPOTENCY_KEY_REUSED`.",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """CHANGED in 1.1.0 and 1.2.0. The server, in one transaction:

    1. Rejects a date before today (Asia/Dhaka) — `PAST_DATE`.
    2. Rejects a hospital holiday with `opd_closed` — `HOLIDAY`.
    3. Resolves the session: `schedule_id` if given, otherwise the doctor's only session
       that day. No session (no schedule row, leave, cancelled) — `NO_SESSION`. More than
       one session and no `schedule_id` — `SESSION_REQUIRED`.
    4. Rejects a second non-cancelled appointment for the same patient, doctor and date —
       `DUPLICATE_BOOKING`.
    5. Picks the queue: `report_showing` visits go to the session's report queue when
       `report_capacity > 0`; everything else goes to the regular queue.
    6. Assigns the lowest free `serial_number` in that queue, or `SESSION_FULL`.
    7. Derives `appointment_time`. A client-supplied `appointment_time` is ignored.

    Enforced by partial unique indexes as well as application checks, so two concurrent
    requests cannot take the same serial."""
    h = idempotency.body_hash(payload)
    prior = idempotency.lookup(db, user.id, "POST /appointments", idempotency_key, h)
    if prior:
        return JSONResponse(prior.response_body, status_code=prior.status_code)

    appt = None
    for _attempt in range(6):
        patient = _patient_or_404(db, payload.patient_id)
        doctor = _doctor_or_404(db, payload.doctor_id)
        plan = plan_booking(
            db, patient_id=patient.id, doctor=doctor, appt_date=payload.appointment_date,
            schedule_id=payload.schedule_id, exception_id=payload.exception_id,
            visit_type=payload.visit_type.value,
        )
        appt = Appointment(
            patient_id=patient.id, doctor_id=doctor.id,
            appointment_date=payload.appointment_date,
            visit_type=payload.visit_type.value,
            status=payload.status,
            source=payload.source.value if payload.source else None,
            notes=payload.notes,
        )
        apply_plan(appt, plan)
        db.add(appt)
        try:
            db.flush()
            break
        except IntegrityError:
            # Lost a race for the serial (or the duplicate rule): re-plan from fresh data.
            db.rollback()
            appt = None
    if appt is None:
        raise ApiError(409, "SESSION_FULL", "Could not reserve a serial; please retry.")

    appt_id, patient_name, patient_mobile = appt.id, patient.name, patient.mobile_number
    doctor_name, a_date, a_time = doctor.name, appt.appointment_date, appt.appointment_time
    serial, room = appt.serial_number, appt.room
    body = render(user, AppointmentDetailOut, _load_detail(db, appt_id))
    idempotency.store(db, user.id, "POST /appointments", idempotency_key, h, 201, body)
    db.commit()

    background_tasks.add_task(
        send_sms, patient_mobile,
        build_appointment_message(patient_name, doctor_name, a_date, a_time, serial, room),
    )
    return JSONResponse(body, status_code=201)


@router.get(
    "", response_model=List[AppointmentDetailOut],
    operation_id="list_appointments_appointments_get",
)
def list_appointments(
    skip: int = Skip(),
    limit: int = Limit(),
    patient_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
    schedule_id: Optional[int] = Query(None, description="NEW in 1.1.0."),
    status_filter: Optional[AppointmentStatus] = None,
    date_from: Optional[date] = Query(None, description="NEW in 1.1.0. Inclusive."),
    date_to: Optional[date] = Query(None, description="NEW in 1.1.0. Inclusive."),
    visit_type: Optional[VisitType] = Query(None, description="NEW in 1.2.0."),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Ordered by `appointment_date`, then `queue`, then `serial_number`.
    CHANGED in 1.1.0: new filters `date_from`, `date_to`, `schedule_id`.
    CHANGED in 1.2.0: new filter `visit_type`."""
    q = db.query(Appointment).options(*_OPTS)
    if patient_id is not None:
        q = q.filter(Appointment.patient_id == patient_id)
    if doctor_id is not None:
        q = q.filter(Appointment.doctor_id == doctor_id)
    if schedule_id is not None:
        q = q.filter(Appointment.schedule_id == schedule_id)
    if status_filter is not None:
        q = q.filter(Appointment.status == status_filter)
    if date_from is not None:
        q = q.filter(Appointment.appointment_date >= date_from)
    if date_to is not None:
        q = q.filter(Appointment.appointment_date <= date_to)
    if visit_type is not None:
        q = q.filter(Appointment.visit_type == visit_type.value)
    rows = (
        q.order_by(Appointment.appointment_date, Appointment.queue, Appointment.serial_number)
        .offset(skip).limit(limit).all()
    )
    return JSONResponse(render_list(user, AppointmentDetailOut, rows))


@router.get(
    "/{appointment_id}", response_model=AppointmentDetailOut, responses=ERRORS,
    operation_id="get_appointment_appointments__appointment_id__get",
)
def get_appointment(
    appointment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    appt = db.query(Appointment).options(*_OPTS).filter(Appointment.id == appointment_id).first()
    if not appt:
        raise not_found("Appointment")
    return JSONResponse(render(user, AppointmentDetailOut, appt))


@router.put(
    "/{appointment_id}", response_model=AppointmentDetailOut,
    responses={**ERRORS, 409: {"model": ErrorResponse, "description": "Same codes as `POST /appointments`."}},
    operation_id="update_appointment_appointments__appointment_id__put",
)
def update_appointment(
    appointment_id: int, payload: AppointmentUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """CHANGED in 1.1.0. Setting `status: cancelled` frees the serial for reuse. Changing
    `doctor_id`, `appointment_date` or `schedule_id` re-runs every rule of
    `POST /appointments` and assigns a new serial."""
    appt = db.get(Appointment, appointment_id)
    if not appt:
        raise not_found("Appointment")
    data = payload.model_dump(exclude_unset=True)
    data.pop("appointment_time", None)  # deprecated; derived by the server

    new_patient_id = data.get("patient_id") or appt.patient_id
    new_doctor_id = data.get("doctor_id") or appt.doctor_id
    new_date = data.get("appointment_date") or appt.appointment_date
    new_status = data.get("status") or appt.status
    new_visit = (data["visit_type"].value if data.get("visit_type") else appt.visit_type)
    old_status = appt.status

    patient = _patient_or_404(db, new_patient_id)
    doctor = _doctor_or_404(db, new_doctor_id)

    reactivated = old_status == AppointmentStatus.cancelled and new_status != AppointmentStatus.cancelled
    explicit_session = ("schedule_id" in data and data["schedule_id"] != appt.schedule_id) or (
        "exception_id" in data and data["exception_id"] != appt.exception_id
    )
    rerun = (
        new_doctor_id != appt.doctor_id
        or new_date != appt.appointment_date
        or new_visit != appt.visit_type
        or explicit_session
        or reactivated
    ) and new_status != AppointmentStatus.cancelled

    try:
        if rerun:
            sid = data.get("schedule_id") if "schedule_id" in data else None
            eid = data.get("exception_id") if "exception_id" in data else None
            if (
                new_doctor_id == appt.doctor_id
                and new_date == appt.appointment_date
                and "schedule_id" not in data
                and "exception_id" not in data
            ):
                # Same doctor/date (e.g. only visit_type or status changed): stay in the
                # session the patient was booked into unless told otherwise.
                sid, eid = appt.schedule_id, appt.exception_id
            plan = plan_booking(
                db, patient_id=patient.id, doctor=doctor, appt_date=new_date,
                schedule_id=sid, exception_id=eid, visit_type=new_visit, exclude_id=appt.id,
            )
            apply_plan(appt, plan)
        elif new_patient_id != appt.patient_id and new_status != AppointmentStatus.cancelled:
            # Only the patient changed: same slot, but the duplicate rule still applies.
            if db.query(Appointment.id).filter(
                Appointment.patient_id == new_patient_id,
                Appointment.doctor_id == appt.doctor_id,
                Appointment.appointment_date == appt.appointment_date,
                Appointment.status != AppointmentStatus.cancelled,
                Appointment.id != appt.id,
            ).first():
                raise ApiError(
                    409, "DUPLICATE_BOOKING",
                    "This patient already has an appointment with this doctor on that date.",
                )
        appt.patient_id = patient.id
        appt.doctor_id = doctor.id
        appt.appointment_date = new_date
        appt.visit_type = new_visit
        appt.status = new_status
        if "notes" in data:
            appt.notes = data["notes"]
        if new_status == AppointmentStatus.cancelled:
            if "cancel_reason" in data:
                appt.cancel_reason = data["cancel_reason"]
        else:
            appt.cancel_reason = None
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(
            409, "SESSION_FULL",
            "That serial was taken by a concurrent booking; please retry.",
        )
    db.commit()
    return JSONResponse(render(user, AppointmentDetailOut, _load_detail(db, appointment_id)))


@router.delete(
    "/{appointment_id}", status_code=204, responses=ERRORS,
    operation_id="delete_appointment_appointments__appointment_id__delete",
)
def delete_appointment(appointment_id: int, db: Session = Depends(get_db)):
    appt = db.get(Appointment, appointment_id)
    if not appt:
        raise not_found("Appointment")
    db.delete(appt)
    db.commit()
    return Response(status_code=204)
