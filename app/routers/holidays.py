from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload

from app import audit
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.enums import AppointmentStatus
from app.errors import ApiError, not_found
from app.models import Appointment, Doctor, Holiday, User
from app.routers.common import ERRORS
from app.scheduling import today
from app.schemas import (
    AppointmentDetailOut, ErrorResponse, HolidayCreate, HolidayOut, HolidayResult, HolidayUpdate,
)
from app.serialize import render, render_list, snapshot
from app.utils import apply_updates

router = APIRouter(prefix="/holidays", tags=["Holidays"], dependencies=[Depends(get_current_user)])


def _affected(user, db: Session, h: Holiday) -> list:
    """Non-cancelled appointments on the date when OPD is closed. Never changed."""
    if not h.opd_closed:
        return []
    rows = (
        db.query(Appointment)
        .options(joinedload(Appointment.patient), joinedload(Appointment.doctor).joinedload(Doctor.department))
        .filter(
            Appointment.appointment_date == h.date,
            Appointment.status != AppointmentStatus.cancelled,
        )
        .order_by(Appointment.doctor_id, Appointment.serial_number)
        .all()
    )
    return render_list(user, AppointmentDetailOut, rows)


@router.get("", response_model=List[HolidayOut], operation_id="list_holidays")
def list_holidays(
    date_from: Optional[date] = Query(None, alias="from", description="Inclusive. Defaults to today."),
    date_to: Optional[date] = Query(None, alias="to", description="Inclusive. Defaults to `from + 365 days`."),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Ordered by date."""
    d_from = date_from or today()
    d_to = date_to or d_from + timedelta(days=365)
    rows = (
        db.query(Holiday).filter(Holiday.date >= d_from, Holiday.date <= d_to)
        .order_by(Holiday.date).all()
    )
    return JSONResponse(render_list(user, HolidayOut, rows))


@router.post(
    "", response_model=HolidayResult, status_code=201, operation_id="create_holiday",
    responses={**ERRORS, 409: {"model": ErrorResponse, "description": "`DUPLICATE_DATE`"}},
)
def create_holiday(
    payload: HolidayCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """Requires `admin` scope. Takes effect immediately: availability shows `holiday` and
    booking refuses the date. Existing appointments on the date are NOT cancelled; they are
    returned in `affected_appointments` so staff can contact the patients."""
    if db.query(Holiday.id).filter(Holiday.date == payload.date).first():
        raise ApiError(409, "DUPLICATE_DATE", f"A holiday already exists on {payload.date}.")
    h = Holiday(**payload.model_dump())
    db.add(h)
    db.flush()
    audit.record(db, user, "create", "holiday", h.id, None, snapshot(HolidayOut, h))
    affected = _affected(user, db, h)
    db.commit()
    db.refresh(h)
    return JSONResponse(
        {"holiday": render(user, HolidayOut, h), "affected_appointments": affected}, status_code=201
    )


@router.put(
    "/{holiday_id}", response_model=HolidayResult, responses=ERRORS, operation_id="update_holiday"
)
def update_holiday(
    holiday_id: int, payload: HolidayUpdate,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    """Requires `admin` scope."""
    h = db.get(Holiday, holiday_id)
    if h is None:
        raise not_found("Holiday")
    data = payload.model_dump(exclude_unset=True)
    if data.get("date") and data["date"] != h.date and db.query(Holiday.id).filter(
        Holiday.date == data["date"], Holiday.id != h.id
    ).first():
        raise ApiError(409, "DUPLICATE_DATE", f"A holiday already exists on {data['date']}.")
    before = snapshot(HolidayOut, h)
    apply_updates(h, data, non_nullable=("date", "name", "opd_closed", "emergency_open"))
    db.flush()
    audit.record(db, user, "update", "holiday", h.id, before, snapshot(HolidayOut, h))
    affected = _affected(user, db, h)
    db.commit()
    db.refresh(h)
    return JSONResponse({"holiday": render(user, HolidayOut, h), "affected_appointments": affected})


@router.delete(
    "/{holiday_id}", status_code=204, responses=ERRORS, operation_id="delete_holiday"
)
def delete_holiday(holiday_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Requires `admin` scope. The date becomes bookable again immediately."""
    h = db.get(Holiday, holiday_id)
    if h is None:
        raise not_found("Holiday")
    before = snapshot(HolidayOut, h)
    db.delete(h)
    audit.record(db, user, "delete", "holiday", holiday_id, before, None)
    db.commit()
    return Response(status_code=204)
