from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Column, Date, Enum, Float, ForeignKey, Index, Integer,
    String, Text, Time, UniqueConstraint, func, text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import DateTime, TypeDecorator

from app.database import Base
from app.enums import AppointmentStatus  # noqa: F401  (re-exported)
from app.utils import utcnow


class UTCDateTime(TypeDecorator):
    """Stores UTC, always returns timezone-aware UTC (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _created():
    return Column(UTCDateTime, default=utcnow, server_default=func.now())


def _updated_on_write():
    """Content resources: set on create AND every write (the sync convention)."""
    return Column(UTCDateTime, default=utcnow, onupdate=utcnow)


# ── users ────────────────────────────────────────────────────────────────────
class User(Base):
    """Login-only user. No self-registration endpoint — created via the seed script."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    api_key = Column(String(64), unique=True, index=True, nullable=False)
    scope = Column(String(20), nullable=False, default="admin", server_default="admin")
    created_at = _created()


# ── patients ─────────────────────────────────────────────────────────────────
class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    age = Column(Integer, nullable=False)
    mobile_number = Column(String(20), nullable=False, index=True)
    # 11-digit national form, for the exact `mobile_number` filter.
    mobile_normalized = Column(String(20), index=True)
    created_at = _created()
    updated_at = Column(UTCDateTime, onupdate=utcnow)

    appointments = relationship(
        "Appointment", back_populates="patient", cascade="all, delete-orphan"
    )


# ── departments ──────────────────────────────────────────────────────────────
class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    name_bn = Column(String(150))
    aliases = Column(JSON, nullable=False, default=list)
    service_line = Column(String(20), nullable=False)
    description = Column(Text)
    description_bn = Column(Text)
    conditions = Column(JSON, nullable=False, default=list)
    opening_hours = Column(String(200))
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    created_at = _created()
    updated_at = _updated_on_write()
    deleted_at = Column(UTCDateTime)

    doctors = relationship("Doctor", back_populates="department")


# ── doctors ──────────────────────────────────────────────────────────────────
class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    name_bn = Column(String(150))
    designation = Column(String(150))
    education = Column(String(255))
    specialization = Column(String(150), index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), index=True)
    description = Column(Text)
    mobile_number = Column(String(20))
    email = Column(String(150))
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    accepting_appointments = Column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    consultation_fee = Column(Float)
    created_at = _created()
    updated_at = _updated_on_write()
    deleted_at = Column(UTCDateTime)

    department = relationship("Department", back_populates="doctors")
    appointments = relationship("Appointment", back_populates="doctor")
    schedules = relationship("DoctorSchedule", back_populates="doctor")


# ── schedules ────────────────────────────────────────────────────────────────
class DoctorSchedule(Base):
    """One weekly OPD session. A weekday with no row means NO SESSION."""

    __tablename__ = "doctor_schedules"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False, index=True)
    weekday = Column(String(10), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    room = Column(String(30))
    floor = Column(String(30))
    desk = Column(String(10))
    max_patients = Column(Integer, nullable=False)
    slot_minutes = Column(Integer)
    report_capacity = Column(Integer, nullable=False, default=0, server_default=text("0"))
    valid_from = Column(Date)
    valid_to = Column(Date)
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    created_at = _created()
    updated_at = _updated_on_write()

    doctor = relationship("Doctor", back_populates="schedules")


class ScheduleException(Base):
    """Leave, a cancelled session, or a one-off extra session on a single date."""

    __tablename__ = "schedule_exceptions"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    type = Column(String(20), nullable=False)
    schedule_id = Column(Integer, ForeignKey("doctor_schedules.id"))
    start_time = Column(Time)
    end_time = Column(Time)
    room = Column(String(30))
    floor = Column(String(30))
    desk = Column(String(10))
    max_patients = Column(Integer)
    slot_minutes = Column(Integer)
    report_capacity = Column(Integer)
    note = Column(String(500))
    created_at = _created()


class Holiday(Base):
    __tablename__ = "holidays"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, unique=True)
    name = Column(String(150), nullable=False)
    name_bn = Column(String(150))
    opd_closed = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    emergency_open = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    note = Column(String(500))
    created_at = _created()
    updated_at = _updated_on_write()


# ── appointments ─────────────────────────────────────────────────────────────
_NOT_CANCELLED = text("status <> 'cancelled'")


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    appointment_date = Column(Date, nullable=False)
    appointment_time = Column(Time, nullable=False)
    serial_number = Column(Integer, nullable=False)
    visit_type = Column(String(20), nullable=False, default="new", server_default="new")
    queue = Column(String(10), nullable=False, default="regular", server_default="regular")
    schedule_id = Column(Integer, ForeignKey("doctor_schedules.id"))
    exception_id = Column(Integer, ForeignKey("schedule_exceptions.id"))
    # Copied from the session at booking time.
    session_start = Column(Time)
    session_end = Column(Time)
    room = Column(String(30))
    floor = Column(String(30))
    desk = Column(String(10))
    status = Column(
        Enum(AppointmentStatus), default=AppointmentStatus.scheduled, nullable=False
    )
    source = Column(String(20))
    cancel_reason = Column(String(300))
    notes = Column(Text)
    created_at = _created()
    updated_at = Column(UTCDateTime, onupdate=utcnow)

    patient = relationship("Patient", back_populates="appointments")
    doctor = relationship("Doctor", back_populates="appointments")

    # Two concurrent requests cannot take the same serial. All partial, so a
    # cancelled appointment frees its serial.
    __table_args__ = (
        Index(
            "uq_appt_session_serial",
            "schedule_id", "appointment_date", "queue", "serial_number",
            unique=True, sqlite_where=_NOT_CANCELLED, postgresql_where=_NOT_CANCELLED,
        ),
        Index(
            "uq_appt_exception_serial",
            "exception_id", "queue", "serial_number",
            unique=True, sqlite_where=_NOT_CANCELLED, postgresql_where=_NOT_CANCELLED,
        ),
        Index(
            "uq_appt_patient_doctor_date",
            "patient_id", "doctor_id", "appointment_date",
            unique=True, sqlite_where=_NOT_CANCELLED, postgresql_where=_NOT_CANCELLED,
        ),
        Index("ix_appt_doctor_date", "doctor_id", "appointment_date"),
    )


# ── infrastructure ───────────────────────────────────────────────────────────
class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    endpoint = Column(String(60), nullable=False)
    key = Column(String(128), nullable=False)
    request_hash = Column(String(64), nullable=False)
    status_code = Column(Integer, nullable=False)
    response_body = Column(JSON, nullable=False)
    created_at = _created()

    __table_args__ = (UniqueConstraint("user_id", "endpoint", "key", name="uq_idem"),)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    at = Column(UTCDateTime, default=utcnow, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    username = Column(String(50))
    action = Column(String(10), nullable=False)
    entity_type = Column(String(20), nullable=False, index=True)
    entity_id = Column(Integer, nullable=False)
    doctor_id = Column(Integer, index=True)
    before = Column(JSON)
    after = Column(JSON)


class QueueState(Base):
    """Optional queue feature: the serial currently being seen. One row per
    schedule / date / queue, so it resets by itself every day."""

    __tablename__ = "queue_state"

    id = Column(Integer, primary_key=True)
    schedule_id = Column(Integer, ForeignKey("doctor_schedules.id"), nullable=False)
    date = Column(Date, nullable=False)
    queue = Column(String(10), nullable=False, default="regular")
    now_serving = Column(Integer, nullable=False, default=0)
    updated_at = _updated_on_write()

    __table_args__ = (UniqueConstraint("schedule_id", "date", "queue", name="uq_queue"),)


# ── hospital content ─────────────────────────────────────────────────────────
class Hospital(Base):
    """Single-row profile (id = 1)."""

    __tablename__ = "hospital"

    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    name_bn = Column(String(150))
    address = Column(String(300))
    address_bn = Column(String(300))
    landmark = Column(String(200))
    landmark_bn = Column(String(200))
    main_phone = Column(String(40))
    emergency_hotline = Column(String(40))
    email = Column(String(120))
    website = Column(String(200))
    other_contacts = Column(JSON)
    opd_hours = Column(String(200))
    opd_hours_bn = Column(String(200))
    friday_note = Column(String(200))
    friday_note_bn = Column(String(200))
    desk_hours = Column(JSON)
    beds = Column(Integer)
    updated_at = _updated_on_write()


class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    name_bn = Column(String(150))
    aliases = Column(JSON, nullable=False, default=list)
    price = Column(Float, nullable=False)
    regular_price = Column(Float)
    gender = Column(String(10), nullable=False, default="any")
    age_group = Column(String(80))
    investigations = Column(JSON, nullable=False, default=list)
    complementary = Column(JSON, nullable=False, default=list)
    description = Column(Text)
    description_bn = Column(Text)
    preparation = Column(Text)
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    created_at = _created()
    updated_at = _updated_on_write()
    deleted_at = Column(UTCDateTime)


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    name_bn = Column(String(150))
    aliases = Column(JSON, nullable=False, default=list)
    kind = Column(String(10), nullable=False)
    service_line = Column(String(20), nullable=False)
    department_id = Column(Integer, ForeignKey("departments.id"))
    description = Column(Text)
    description_bn = Column(Text)
    hours = Column(String(120))
    is_24x7 = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    phone = Column(String(40))
    room = Column(String(30))
    floor = Column(String(30))
    desk = Column(String(10))
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    created_at = _created()
    updated_at = _updated_on_write()
    deleted_at = Column(UTCDateTime)


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    title_bn = Column(String(200))
    topic = Column(String(20), nullable=False)
    service_line = Column(String(20))
    questions = Column(JSON, nullable=False, default=list)
    body = Column(Text)
    body_bn = Column(Text)
    is_published = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    created_at = _created()
    updated_at = _updated_on_write()
    deleted_at = Column(UTCDateTime)
