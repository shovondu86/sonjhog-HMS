from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app import audit
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.errors import ApiError, not_found
from app.models import Appointment, Department, Doctor, User
from app.enums import AppointmentStatus
from app.routers.common import (
    ERRORS, IncludeDeleted, Limit, Skip, UpdatedSince, server_time_headers, sync_filter,
)
from app.scheduling import doctors_with_sessions_on, today
from app.schemas import DoctorCreate, DoctorOut, DoctorUpdate, ErrorResponse
from app.serialize import render, render_list, snapshot
from app.utils import apply_updates, like_contains, utcnow

router = APIRouter(prefix="/doctors", tags=["Doctors"], dependencies=[Depends(get_current_user)])


def _check_department(db: Session, department_id: Optional[int]):
    if department_id is not None:
        d = db.get(Department, department_id)
        if d is None or d.deleted_at is not None:
            raise not_found(f"Department {department_id}")


@router.post(
    "", response_model=DoctorOut, status_code=201, responses=ERRORS,
    operation_id="create_doctor_doctors_post",
)
def create_doctor(
    payload: DoctorCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """Requires `admin` scope."""
    _check_department(db, payload.department_id)
    doctor = Doctor(**payload.model_dump())
    db.add(doctor)
    db.flush()
    audit.record(db, user, "create", "doctor", doctor.id, None, snapshot(DoctorOut, doctor),
                 doctor_id=doctor.id)
    db.commit()
    db.refresh(doctor)
    return JSONResponse(render(user, DoctorOut, doctor), status_code=201)


@router.get("", response_model=List[DoctorOut], operation_id="list_doctors_doctors_get")
def list_doctors(
    skip: int = Skip(),
    limit: int = Limit(),
    specialization: Optional[str] = Query(
        None, description="Substring match on the display `specialization`. Prefer `department_id`."
    ),
    search: Optional[str] = Query(
        None, description="Substring match on `name` and `name_bn`. `%` and `_` are matched literally."
    ),
    department_id: Optional[int] = Query(None, description="NEW in 1.1.0."),
    is_active: Optional[bool] = Query(
        True,
        description="NEW in 1.1.0. Excluded from lists by default when false; "
        "pass `include_deleted=true` for an admin view of everything.",
    ),
    available_on: Optional[date] = Query(
        None,
        description="NEW in 1.1.0. Only doctors with at least one session on that date after "
        "applying exceptions and holidays (status `available` or `full`).",
    ),
    updated_since: Optional[datetime] = UpdatedSince(),
    include_deleted: bool = IncludeDeleted(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """CHANGED in 1.1.0: new filters `department_id`, `is_active`, `available_on`;
    `search` also matches `name_bn`."""
    headers = server_time_headers()
    q = sync_filter(
        db.query(Doctor).options(joinedload(Doctor.department)), Doctor, user,
        updated_since=updated_since, include_deleted=include_deleted, is_active=is_active,
    )
    # Doctors are not "hidden" from agents when inactive by a separate rule; the default
    # is_active=true filter already applies to everyone. sync_filter forces active for
    # agents, which is the safe reading of "inactive content is not returned".
    if specialization:
        q = q.filter(like_contains(Doctor.specialization, specialization))
    if search:
        q = q.filter(like_contains(Doctor.name, search) | like_contains(Doctor.name_bn, search))
    if department_id is not None:
        q = q.filter(Doctor.department_id == department_id)
    q = q.order_by(func.lower(Doctor.name), Doctor.id)

    if available_on is not None:
        rows = q.all()
        keep = doctors_with_sessions_on(db, [r.id for r in rows], available_on)
        rows = [r for r in rows if r.id in keep][skip: skip + limit]
    else:
        rows = q.offset(skip).limit(limit).all()
    return JSONResponse(render_list(user, DoctorOut, rows), headers=headers)


@router.get(
    "/{doctor_id}", response_model=DoctorOut, responses=ERRORS,
    operation_id="get_doctor_doctors__doctor_id__get",
)
def get_doctor(doctor_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    doctor = db.get(Doctor, doctor_id)
    if doctor is None or doctor.deleted_at is not None:
        raise not_found("Doctor")
    return JSONResponse(render(user, DoctorOut, doctor))


@router.put(
    "/{doctor_id}", response_model=DoctorOut, responses=ERRORS,
    operation_id="update_doctor_doctors__doctor_id__put",
)
def update_doctor(
    doctor_id: int, payload: DoctorUpdate,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    """Requires `admin` scope."""
    doctor = db.get(Doctor, doctor_id)
    if doctor is None or doctor.deleted_at is not None:
        raise not_found("Doctor")
    data = payload.model_dump(exclude_unset=True)
    if data.get("department_id") is not None:
        _check_department(db, data["department_id"])
    before = snapshot(DoctorOut, doctor)
    apply_updates(doctor, data, non_nullable=("name", "is_active", "accepting_appointments"))
    db.flush()
    db.refresh(doctor)
    audit.record(db, user, "update", "doctor", doctor.id, before, snapshot(DoctorOut, doctor),
                 doctor_id=doctor.id)
    db.commit()
    db.refresh(doctor)
    return JSONResponse(render(user, DoctorOut, doctor))


@router.delete(
    "/{doctor_id}", status_code=204,
    responses={**ERRORS, 409: {"model": ErrorResponse, "description": "`HAS_FUTURE_APPOINTMENTS`"}},
    operation_id="delete_doctor_doctors__doctor_id__delete",
)
def delete_doctor(doctor_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Requires `admin` scope. Refused with `409 HAS_FUTURE_APPOINTMENTS` while the doctor
    has scheduled appointments from today onward — set `is_active: false` instead.
    CHANGED in 1.2.0: soft delete (sets `deleted_at`), so past appointments keep their doctor."""
    doctor = db.get(Doctor, doctor_id)
    if doctor is None or doctor.deleted_at is not None:
        raise not_found("Doctor")
    if db.query(Appointment.id).filter(
        Appointment.doctor_id == doctor.id,
        Appointment.status == AppointmentStatus.scheduled,
        Appointment.appointment_date >= today(),
    ).first():
        raise ApiError(
            409, "HAS_FUTURE_APPOINTMENTS",
            "The doctor has scheduled appointments from today onward. Set is_active to false instead.",
        )
    before = snapshot(DoctorOut, doctor)
    now = utcnow()
    doctor.deleted_at = now
    doctor.updated_at = now
    audit.record(db, user, "delete", "doctor", doctor.id, before, None, doctor_id=doctor.id)
    db.commit()
    return Response(status_code=204)
