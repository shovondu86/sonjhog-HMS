"""Pydantic models mirroring components/schemas of hospital-api-v1_2_openapi.yaml."""
from datetime import date, datetime, time
from typing import Annotated, Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from app.enums import (
    AppointmentSource, AppointmentStatus, ArticleTopic, AuditEntityType, DayStatus,
    ErrorCode, ExceptionType, ImportProblemCode, PackageGender, QueueName,
    ServiceKind, ServiceLine, VisitType, Weekday,
)


def _fmt_time(t: time) -> str:
    return t.strftime("%H:%M") if t.second == 0 else t.strftime("%H:%M:%S")


# Serialised as "09:00" (the spec's own examples); parsed from "09:00" or "09:00:00".
HHMM = Annotated[time, PlainSerializer(_fmt_time, return_type=str, when_used="json")]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _none_to_list(v):
    return [] if v is None else v


# ── common ───────────────────────────────────────────────────────────────────
class ErrorResponse(BaseModel):
    code: ErrorCode
    detail: str
    next_available_date: Optional[date] = None
    holiday_name: Optional[str] = None
    candidates: Optional[List[dict]] = None


class ApiKeyResponse(BaseModel):
    api_key: str


# ── patients ─────────────────────────────────────────────────────────────────
class PatientCreate(BaseModel):
    name: str = Field(..., max_length=150)
    age: int = Field(..., ge=0, le=150)
    mobile_number: str = Field(..., max_length=20)


class PatientUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=150)
    age: Optional[int] = Field(None, ge=0, le=150)
    mobile_number: Optional[str] = Field(None, max_length=20)


class PatientOut(ORM, PatientCreate):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None


# ── departments ──────────────────────────────────────────────────────────────
class DepartmentCreate(BaseModel):
    name: str = Field(..., max_length=150)
    name_bn: Optional[str] = Field(None, max_length=150)
    aliases: List[str] = Field(default_factory=list)
    service_line: ServiceLine
    description: Optional[str] = None
    description_bn: Optional[str] = None
    conditions: List[str] = Field(default_factory=list)
    opening_hours: Optional[str] = Field(None, max_length=200)
    is_active: bool = True

    _l = field_validator("aliases", "conditions", mode="before")(_none_to_list)

    @field_validator("aliases")
    @classmethod
    def _alias_len(cls, v):
        if any(len(a) > 80 for a in v):
            raise ValueError("each alias must be at most 80 characters")
        return v

    @field_validator("conditions")
    @classmethod
    def _cond_len(cls, v):
        if any(len(a) > 120 for a in v):
            raise ValueError("each condition must be at most 120 characters")
        return v


class DepartmentUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=150)
    name_bn: Optional[str] = Field(None, max_length=150)
    aliases: Optional[List[str]] = None
    service_line: Optional[ServiceLine] = None
    description: Optional[str] = None
    description_bn: Optional[str] = None
    conditions: Optional[List[str]] = None
    opening_hours: Optional[str] = Field(None, max_length=200)
    is_active: Optional[bool] = None


class DepartmentOut(ORM, DepartmentCreate):
    id: int
    doctor_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None


class DepartmentSummary(ORM):
    id: int
    name: str
    name_bn: Optional[str] = None


# ── doctors ──────────────────────────────────────────────────────────────────
class DoctorCreate(BaseModel):
    name: str = Field(..., max_length=150)
    name_bn: Optional[str] = Field(None, max_length=150)
    designation: Optional[str] = None
    education: Optional[str] = None
    specialization: Optional[str] = None
    department_id: Optional[int] = None
    description: Optional[str] = None
    mobile_number: Optional[str] = None
    email: Optional[str] = None
    is_active: bool = True
    accepting_appointments: bool = True
    consultation_fee: Optional[float] = Field(None, ge=0)


class DoctorUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=150)
    name_bn: Optional[str] = Field(None, max_length=150)
    designation: Optional[str] = None
    education: Optional[str] = None
    specialization: Optional[str] = None
    department_id: Optional[int] = None
    description: Optional[str] = None
    mobile_number: Optional[str] = None
    email: Optional[str] = None
    is_active: Optional[bool] = None
    accepting_appointments: Optional[bool] = None
    consultation_fee: Optional[float] = Field(None, ge=0)


class DoctorOut(ORM, DoctorCreate):
    id: int
    department: Optional[DepartmentSummary] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None


class DoctorSummary(ORM):
    id: int
    name: str
    name_bn: Optional[str] = None
    designation: Optional[str] = None
    specialization: Optional[str] = None


# ── schedules ────────────────────────────────────────────────────────────────
class SessionPlace(BaseModel):
    room: Optional[str] = Field(None, max_length=30)
    floor: Optional[str] = Field(None, max_length=30)
    desk: Optional[str] = Field(None, max_length=10)


class ScheduleCreate(SessionPlace):
    weekday: Weekday
    start_time: HHMM
    end_time: HHMM
    max_patients: int = Field(..., ge=1)
    slot_minutes: Optional[int] = Field(None, ge=1)
    report_capacity: int = Field(0, ge=0)
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    is_active: bool = True

    @model_validator(mode="after")
    def _check(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be before valid_from")
        return self


class ScheduleUpdate(BaseModel):
    weekday: Optional[Weekday] = None
    start_time: Optional[HHMM] = None
    end_time: Optional[HHMM] = None
    room: Optional[str] = Field(None, max_length=30)
    floor: Optional[str] = Field(None, max_length=30)
    desk: Optional[str] = Field(None, max_length=10)
    max_patients: Optional[int] = Field(None, ge=1)
    slot_minutes: Optional[int] = Field(None, ge=1)
    report_capacity: Optional[int] = Field(None, ge=0)
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    is_active: Optional[bool] = None


class ScheduleOut(ORM, ScheduleCreate):
    id: int
    doctor_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None


# appointments are defined below; ScheduleChangeResult etc. reference them.
class AppointmentBase(BaseModel):
    pass


class AppointmentCreate(BaseModel):
    patient_id: int
    doctor_id: int
    appointment_date: date
    schedule_id: Optional[int] = None
    exception_id: Optional[int] = None
    appointment_time: Optional[HHMM] = Field(
        None, deprecated=True, description="Ignored — derived by the server from the serial."
    )
    status: AppointmentStatus = AppointmentStatus.scheduled
    source: Optional[AppointmentSource] = None
    visit_type: VisitType = VisitType.new
    notes: Optional[str] = None


class AppointmentUpdate(BaseModel):
    patient_id: Optional[int] = None
    doctor_id: Optional[int] = None
    appointment_date: Optional[date] = None
    schedule_id: Optional[int] = None
    exception_id: Optional[int] = None
    appointment_time: Optional[HHMM] = Field(None, deprecated=True)
    status: Optional[AppointmentStatus] = None
    cancel_reason: Optional[str] = Field(None, max_length=300)
    visit_type: Optional[VisitType] = None
    notes: Optional[str] = None


class AppointmentOut(ORM):
    id: int
    patient_id: int
    doctor_id: int
    appointment_date: date
    appointment_time: HHMM
    serial_number: int = Field(..., ge=1)
    visit_type: VisitType = VisitType.new
    queue: QueueName = QueueName.regular
    schedule_id: Optional[int] = None
    exception_id: Optional[int] = None
    session_start: Optional[HHMM] = None
    session_end: Optional[HHMM] = None
    room: Optional[str] = None
    floor: Optional[str] = None
    desk: Optional[str] = None
    status: AppointmentStatus
    source: Optional[AppointmentSource] = None
    cancel_reason: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class AppointmentDetailOut(AppointmentOut):
    patient: PatientOut
    doctor: DoctorOut


class ScheduleChangeResult(BaseModel):
    schedule: ScheduleOut
    affected_appointments: List[AppointmentDetailOut]


class ScheduleExceptionCreate(SessionPlace):
    date: date
    type: ExceptionType
    schedule_id: Optional[int] = None
    start_time: Optional[HHMM] = None
    end_time: Optional[HHMM] = None
    max_patients: Optional[int] = Field(None, ge=1)
    slot_minutes: Optional[int] = Field(None, ge=1)
    report_capacity: Optional[int] = Field(None, ge=0)
    note: Optional[str] = Field(None, max_length=500)


class ScheduleExceptionOut(ORM, ScheduleExceptionCreate):
    id: int
    doctor_id: int
    created_at: datetime


class ScheduleExceptionResult(BaseModel):
    exception: ScheduleExceptionOut
    affected_appointments: List[AppointmentDetailOut]


# ── availability ─────────────────────────────────────────────────────────────
class AvailableSession(SessionPlace):
    schedule_id: Optional[int] = None
    exception_id: Optional[int] = None
    start_time: HHMM
    end_time: HHMM
    capacity: int
    booked: int
    remaining: int
    next_serial: Optional[int] = None
    report_capacity: int = 0
    report_booked: int = 0
    report_remaining: int = 0
    now_serving: Optional[int] = None


class AvailabilityDay(BaseModel):
    date: date
    weekday: Weekday
    status: DayStatus
    holiday_name: Optional[str] = None
    sessions: List[AvailableSession]


class DoctorAvailability(BaseModel):
    doctor_id: int
    doctor_name: str
    doctor_name_bn: Optional[str] = None
    department: Optional[DepartmentSummary] = None
    accepting_appointments: bool = True
    days: List[AvailabilityDay]


# ── holidays ─────────────────────────────────────────────────────────────────
class HolidayCreate(BaseModel):
    date: date
    name: str = Field(..., max_length=150)
    name_bn: Optional[str] = Field(None, max_length=150)
    opd_closed: bool = True
    emergency_open: bool = True
    note: Optional[str] = Field(None, max_length=500)


class HolidayUpdate(BaseModel):
    date: Optional[date] = None
    name: Optional[str] = Field(None, max_length=150)
    name_bn: Optional[str] = Field(None, max_length=150)
    opd_closed: Optional[bool] = None
    emergency_open: Optional[bool] = None
    note: Optional[str] = Field(None, max_length=500)


class HolidayOut(ORM, HolidayCreate):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None


class HolidayResult(BaseModel):
    holiday: HolidayOut
    affected_appointments: List[AppointmentDetailOut]


# ── schedule import ──────────────────────────────────────────────────────────
class ImportProblem(BaseModel):
    severity: str = Field(..., pattern="^(error|warning|info)$")
    code: ImportProblemCode
    row: Optional[int] = None
    doctor_name: Optional[str] = None
    weekday: Optional[Weekday] = None
    detail: str


class ImportedDoctor(BaseModel):
    doctor_id: int
    doctor_name: str
    sessions: List[ScheduleCreate]
    changed: Optional[bool] = None


class ImportSummary(BaseModel):
    doctors_in_file: int = 0
    doctors_changed: int = 0
    sessions: int = 0
    errors: int = 0
    warnings: int = 0
    mobiles_removed: int = 0


class ScheduleImportResult(BaseModel):
    mode: str = Field(..., pattern="^(preview|apply)$")
    applied: Optional[bool] = None
    valid_from: date
    summary: ImportSummary
    doctors: List[ImportedDoctor]
    problems: List[ImportProblem]
    affected_appointments: List[AppointmentDetailOut]


# ── audit ────────────────────────────────────────────────────────────────────
class AuditEntry(ORM):
    id: int
    at: datetime
    user_id: int
    username: Optional[str] = None
    action: str = Field(..., pattern="^(create|update|delete|import)$")
    entity_type: AuditEntityType
    entity_id: int
    doctor_id: Optional[int] = None
    before: Optional[dict] = None
    after: Optional[dict] = None


# ── hospital ─────────────────────────────────────────────────────────────────
class ContactLine(BaseModel):
    label: str = Field(..., max_length=80)
    label_bn: Optional[str] = Field(None, max_length=80)
    value: str = Field(..., max_length=120)
    hours: Optional[str] = Field(None, max_length=80)


class DeskHours(BaseModel):
    appointment: Optional[str] = Field(None, max_length=120)
    emergency: Optional[str] = Field(None, max_length=120)
    support: Optional[str] = Field(None, max_length=120)


class HospitalUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=150)
    name_bn: Optional[str] = Field(None, max_length=150)
    address: Optional[str] = Field(None, max_length=300)
    address_bn: Optional[str] = Field(None, max_length=300)
    landmark: Optional[str] = Field(None, max_length=200)
    landmark_bn: Optional[str] = Field(None, max_length=200)
    main_phone: Optional[str] = Field(None, max_length=40)
    emergency_hotline: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=120)
    website: Optional[str] = Field(None, max_length=200)
    other_contacts: Optional[List[ContactLine]] = None
    opd_hours: Optional[str] = Field(None, max_length=200)
    opd_hours_bn: Optional[str] = Field(None, max_length=200)
    friday_note: Optional[str] = Field(None, max_length=200)
    friday_note_bn: Optional[str] = Field(None, max_length=200)
    desk_hours: Optional[DeskHours] = None
    beds: Optional[int] = Field(None, ge=0)


class HospitalOut(ORM, HospitalUpdate):
    name: str
    updated_at: datetime


# ── packages ─────────────────────────────────────────────────────────────────
class PackageCreate(BaseModel):
    name: str = Field(..., max_length=150)
    name_bn: Optional[str] = Field(None, max_length=150)
    aliases: List[str] = Field(default_factory=list)
    price: float = Field(..., ge=0)
    regular_price: Optional[float] = Field(None, ge=0)
    gender: PackageGender = PackageGender.any
    age_group: Optional[str] = Field(None, max_length=80)
    investigations: List[str] = Field(default_factory=list)
    complementary: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    description_bn: Optional[str] = None
    preparation: Optional[str] = None
    is_active: bool = True

    _l = field_validator("aliases", "investigations", "complementary", mode="before")(_none_to_list)


class PackageUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=150)
    name_bn: Optional[str] = Field(None, max_length=150)
    aliases: Optional[List[str]] = None
    price: Optional[float] = Field(None, ge=0)
    regular_price: Optional[float] = Field(None, ge=0)
    gender: Optional[PackageGender] = None
    age_group: Optional[str] = Field(None, max_length=80)
    investigations: Optional[List[str]] = None
    complementary: Optional[List[str]] = None
    description: Optional[str] = None
    description_bn: Optional[str] = None
    preparation: Optional[str] = None
    is_active: Optional[bool] = None


class PackageOut(ORM, PackageCreate):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None


# ── services ─────────────────────────────────────────────────────────────────
class ServiceCreate(SessionPlace):
    name: str = Field(..., max_length=150)
    name_bn: Optional[str] = Field(None, max_length=150)
    aliases: List[str] = Field(default_factory=list)
    kind: ServiceKind
    service_line: ServiceLine
    department_id: Optional[int] = None
    description: Optional[str] = None
    description_bn: Optional[str] = None
    hours: Optional[str] = Field(None, max_length=120)
    is_24x7: bool = False
    phone: Optional[str] = Field(None, max_length=40)
    is_active: bool = True

    _l = field_validator("aliases", mode="before")(_none_to_list)


class ServiceUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=150)
    name_bn: Optional[str] = Field(None, max_length=150)
    aliases: Optional[List[str]] = None
    kind: Optional[ServiceKind] = None
    service_line: Optional[ServiceLine] = None
    department_id: Optional[int] = None
    description: Optional[str] = None
    description_bn: Optional[str] = None
    hours: Optional[str] = Field(None, max_length=120)
    is_24x7: Optional[bool] = None
    phone: Optional[str] = Field(None, max_length=40)
    room: Optional[str] = Field(None, max_length=30)
    floor: Optional[str] = Field(None, max_length=30)
    desk: Optional[str] = Field(None, max_length=10)
    is_active: Optional[bool] = None


class ServiceOut(ORM, ServiceCreate):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None


# ── articles ─────────────────────────────────────────────────────────────────
class ArticleCreate(BaseModel):
    title: str = Field(..., max_length=200)
    title_bn: Optional[str] = Field(None, max_length=200)
    topic: ArticleTopic
    service_line: Optional[ServiceLine] = None
    questions: List[str] = Field(default_factory=list)
    body: Optional[str] = None
    body_bn: Optional[str] = None
    is_published: bool = False

    _l = field_validator("questions", mode="before")(_none_to_list)

    @model_validator(mode="after")
    def _body_required(self):
        if not (self.body or self.body_bn):
            raise ValueError("at least one of body, body_bn is required")
        return self


class ArticleUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    title_bn: Optional[str] = Field(None, max_length=200)
    topic: Optional[ArticleTopic] = None
    service_line: Optional[ServiceLine] = None
    questions: Optional[List[str]] = None
    body: Optional[str] = None
    body_bn: Optional[str] = None
    is_published: Optional[bool] = None


class ArticleOut(ORM):
    id: int
    title: str
    title_bn: Optional[str] = None
    topic: ArticleTopic
    service_line: Optional[ServiceLine] = None
    questions: List[str] = Field(default_factory=list)
    body: Optional[str] = None
    body_bn: Optional[str] = None
    is_published: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    _l = field_validator("questions", mode="before")(_none_to_list)


# ── queue ────────────────────────────────────────────────────────────────────
class QueueStatus(BaseModel):
    schedule_id: int
    doctor_id: int
    doctor_name: Optional[str] = None
    date: date
    queue: QueueName = QueueName.regular
    now_serving: int
    booked: int
    updated_at: Optional[datetime] = None


class NowServingIn(BaseModel):
    now_serving: int = Field(..., ge=0, description="0 means not started.")
    queue: QueueName = QueueName.regular
