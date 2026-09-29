from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_user
from app.models import Appointment, Patient, Doctor, AppointmentStatus
from app.notifications import send_sms, build_appointment_message
from app.schemas import AppointmentCreate, AppointmentOut, AppointmentUpdate, AppointmentDetailOut

router = APIRouter(
    prefix="/appointments", tags=["Appointments"], dependencies=[Depends(get_current_user)]
)


def _get_patient_and_doctor_or_404(db: Session, patient_id: int, doctor_id: int):
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail=f"Patient {patient_id} not found")
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail=f"Doctor {doctor_id} not found")
    return patient, doctor


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
def create_appointment(
    payload: AppointmentCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    patient, doctor = _get_patient_and_doctor_or_404(db, payload.patient_id, payload.doctor_id)
    appointment = Appointment(**payload.model_dump())
    db.add(appointment)
    db.commit()
    db.refresh(appointment)

    message = build_appointment_message(
        patient.name, doctor.name, appointment.appointment_date, appointment.appointment_time
    )
    background_tasks.add_task(send_sms, patient.mobile_number, message)

    return appointment


@router.get("", response_model=List[AppointmentDetailOut])
def list_appointments(
    skip: int = 0,
    limit: int = 100,
    patient_id: Optional[int] = None,
    doctor_id: Optional[int] = None,
    status_filter: Optional[AppointmentStatus] = None,
    db: Session = Depends(get_db),
):
    query = db.query(Appointment).options(
        joinedload(Appointment.patient), joinedload(Appointment.doctor)
    )
    if patient_id:
        query = query.filter(Appointment.patient_id == patient_id)
    if doctor_id:
        query = query.filter(Appointment.doctor_id == doctor_id)
    if status_filter:
        query = query.filter(Appointment.status == status_filter)
    return (
        query.order_by(Appointment.appointment_date.desc(), Appointment.appointment_time.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{appointment_id}", response_model=AppointmentDetailOut)
def get_appointment(appointment_id: int, db: Session = Depends(get_db)):
    appointment = (
        db.query(Appointment)
        .options(joinedload(Appointment.patient), joinedload(Appointment.doctor))
        .filter(Appointment.id == appointment_id)
        .first()
    )
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appointment


@router.put("/{appointment_id}", response_model=AppointmentOut)
def update_appointment(
    appointment_id: int, payload: AppointmentUpdate, db: Session = Depends(get_db)
):
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    data = payload.model_dump(exclude_unset=True)
    new_patient_id = data.get("patient_id", appointment.patient_id)
    new_doctor_id = data.get("doctor_id", appointment.doctor_id)
    if "patient_id" in data or "doctor_id" in data:
        _get_patient_and_doctor_or_404(db, new_patient_id, new_doctor_id)

    for field, value in data.items():
        setattr(appointment, field, value)

    db.commit()
    db.refresh(appointment)
    return appointment


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appointment(appointment_id: int, db: Session = Depends(get_db)):
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    db.delete(appointment)
    db.commit()
    return None
