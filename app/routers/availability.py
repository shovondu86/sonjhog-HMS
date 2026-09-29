from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_user
from app.errors import not_found, validation_error
from app.models import Doctor, User
from app.routers.common import ERRORS
from app.scheduling import Ctx, availability_days, today
from app.schemas import DepartmentSummary, DoctorAvailability

router = APIRouter(tags=["Availability"], dependencies=[Depends(get_current_user)])


def _range(date_from: Optional[date], date_to: Optional[date]):
    d_from = date_from or today()
    d_to = date_to or d_from + timedelta(days=6)
    if d_to < d_from:
        raise validation_error(["query", "to"], "`to` must not be before `from`")
    if d_to > d_from + timedelta(days=31):
        raise validation_error(["query", "to"], "`to` must be at most 31 days after `from`")
    return d_from, d_to


def _entry(doctor: Doctor, days: list) -> dict:
    dept = None
    if doctor.department is not None:
        dept = DepartmentSummary.model_validate(doctor.department).model_dump(mode="json")
    return DoctorAvailability(
        doctor_id=doctor.id, doctor_name=doctor.name, doctor_name_bn=doctor.name_bn,
        department=dept, accepting_appointments=doctor.accepting_appointments, days=days,
    ).model_dump(mode="json")


FROM = Query(None, alias="from", description="Inclusive. Defaults to today (Asia/Dhaka).")
TO = Query(
    None, alias="to",
    description="Inclusive. Defaults to `from + 6 days`. At most 31 days after `from`.",
)


@router.get(
    "/doctors/{doctor_id}/availability", response_model=DoctorAvailability, responses=ERRORS,
    operation_id="get_doctor_availability",
)
def get_doctor_availability(
    doctor_id: int,
    date_from: Optional[date] = FROM,
    date_to: Optional[date] = TO,
    db: Session = Depends(get_db),
):
    """Weekly schedule, minus holidays, minus exceptions, minus bookings, per date. This is
    the endpoint the voice agent calls; it must never be cached for longer than a few
    seconds so a schedule change is heard on the next call.

    One entry per date in the range, including dates with no session."""
    doctor = (
        db.query(Doctor).options(joinedload(Doctor.department)).filter(Doctor.id == doctor_id).first()
    )
    if doctor is None or doctor.deleted_at is not None:
        raise not_found("Doctor")
    d_from, d_to = _range(date_from, date_to)
    ctx = Ctx(db, [doctor.id], d_from, d_to)
    return JSONResponse(_entry(doctor, availability_days(ctx, doctor.id, d_from, d_to)))


@router.get(
    "/availability", response_model=List[DoctorAvailability], operation_id="get_availability"
)
def get_availability(
    department_id: Optional[int] = None,
    date_from: Optional[date] = FROM,
    date_to: Optional[date] = TO,
    db: Session = Depends(get_db),
):
    """Every active doctor matching the filters with their availability on the given date
    range — answers "which gynae doctor sits tomorrow?". Doctors whose every day is
    `no_session` are omitted."""
    d_from, d_to = _range(date_from, date_to)
    q = (
        db.query(Doctor)
        .options(joinedload(Doctor.department))
        .filter(Doctor.deleted_at.is_(None), Doctor.is_active.is_(True))
    )
    if department_id is not None:
        q = q.filter(Doctor.department_id == department_id)
    doctors = q.order_by(Doctor.name, Doctor.id).all()
    ctx = Ctx(db, [d.id for d in doctors], d_from, d_to)
    out = []
    for d in doctors:
        days = availability_days(ctx, d.id, d_from, d_to)
        if all(x["status"] == "no_session" for x in days):
            continue
        out.append(_entry(d, days))
    return JSONResponse(out)
