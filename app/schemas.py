from datetime import date, time, datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict

from app.models import AppointmentStatus


# ---------- Auth ----------

class LoginRequest(BaseModel):
    username: str
    password: str


class ApiKeyResponse(BaseModel):
    api_key: str


# ---------- Patient ----------

class PatientBase(BaseModel):
    name: str = Field(..., max_length=150)
    age: int = Field(..., ge=0, le=150)
    mobile_number: str = Field(..., max_length=20)


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = Field(None, ge=0, le=150)
    mobile_number: Optional[str] = None


class PatientOut(PatientBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None


# ---------- Doctor ----------

class DoctorBase(BaseModel):
    name: str = Field(..., max_length=150)
    designation: Optional[str] = None
    education: Optional[str] = None
    specialization: Optional[str] = None
    description: Optional[str] = None
    mobile_number: Optional[str] = None
    email: Optional[str] = None


class DoctorCreate(DoctorBase):
    pass


class DoctorUpdate(BaseModel):
    name: Optional[str] = None
    designation: Optional[str] = None
    education: Optional[str] = None
    specialization: Optional[str] = None
    description: Optional[str] = None
    mobile_number: Optional[str] = None
    email: Optional[str] = None


class DoctorOut(DoctorBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None


# ---------- Appointment ----------

class AppointmentBase(BaseModel):
    patient_id: int
    doctor_id: int
    appointment_date: date
    appointment_time: time
    status: AppointmentStatus = AppointmentStatus.scheduled
    notes: Optional[str] = None


class AppointmentCreate(AppointmentBase):
    pass


class AppointmentUpdate(BaseModel):
    patient_id: Optional[int] = None
    doctor_id: Optional[int] = None
    appointment_date: Optional[date] = None
    appointment_time: Optional[time] = None
    status: Optional[AppointmentStatus] = None
    notes: Optional[str] = None


class AppointmentOut(AppointmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None


class AppointmentDetailOut(AppointmentOut):
    patient: PatientOut
    doctor: DoctorOut
