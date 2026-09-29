from enum import Enum


class Weekday(str, Enum):
    saturday = "saturday"
    sunday = "sunday"
    monday = "monday"
    tuesday = "tuesday"
    wednesday = "wednesday"
    thursday = "thursday"
    friday = "friday"


# Python's date.weekday(): Monday == 0
WEEKDAY_BY_INDEX = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]


class ServiceLine(str, Enum):
    appointment = "appointment"
    emergency = "emergency"
    support = "support"


class AppointmentStatus(str, Enum):
    scheduled = "scheduled"
    completed = "completed"
    cancelled = "cancelled"


class AppointmentSource(str, Enum):
    voice_agent = "voice_agent"
    call_center = "call_center"
    front_desk = "front_desk"
    web = "web"
    other = "other"


class VisitType(str, Enum):
    new = "new"
    follow_up = "follow_up"
    report_showing = "report_showing"


class QueueName(str, Enum):
    regular = "regular"
    report = "report"


class ExceptionType(str, Enum):
    leave = "leave"
    cancelled = "cancelled"
    extra_session = "extra_session"


class DayStatus(str, Enum):
    available = "available"
    full = "full"
    leave = "leave"
    no_session = "no_session"
    holiday = "holiday"


class PackageGender(str, Enum):
    male = "male"
    female = "female"
    any = "any"


class ServiceKind(str, Enum):
    service = "service"
    facility = "facility"


class ArticleTopic(str, Enum):
    booking = "booking"
    about = "about"
    directions = "directions"
    policy = "policy"
    career = "career"
    other = "other"


class AuditEntityType(str, Enum):
    doctor = "doctor"
    department = "department"
    schedule = "schedule"
    exception = "exception"
    holiday = "holiday"
    package = "package"
    service = "service"
    article = "article"
    hospital = "hospital"


class ErrorCode(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    PATIENT_NOT_FOUND = "PATIENT_NOT_FOUND"
    DOCTOR_NOT_FOUND = "DOCTOR_NOT_FOUND"
    FORBIDDEN_SCOPE = "FORBIDDEN_SCOPE"
    PAST_DATE = "PAST_DATE"
    NO_SESSION = "NO_SESSION"
    SESSION_REQUIRED = "SESSION_REQUIRED"
    SESSION_FULL = "SESSION_FULL"
    DUPLICATE_BOOKING = "DUPLICATE_BOOKING"
    DOCTOR_NOT_ACCEPTING = "DOCTOR_NOT_ACCEPTING"
    SCHEDULE_OVERLAP = "SCHEDULE_OVERLAP"
    ROOM_CONFLICT = "ROOM_CONFLICT"
    HAS_DOCTORS = "HAS_DOCTORS"
    HAS_FUTURE_APPOINTMENTS = "HAS_FUTURE_APPOINTMENTS"
    DUPLICATE_NAME = "DUPLICATE_NAME"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    HOLIDAY = "HOLIDAY"
    DUPLICATE_DATE = "DUPLICATE_DATE"
    IMPORT_HAS_ERRORS = "IMPORT_HAS_ERRORS"
    UNSUPPORTED_FILE = "UNSUPPORTED_FILE"


class ImportProblemCode(str, Enum):
    UNKNOWN_DOCTOR = "UNKNOWN_DOCTOR"
    AMBIGUOUS_DOCTOR = "AMBIGUOUS_DOCTOR"
    BLANK_HOURS = "BLANK_HOURS"
    UNPARSEABLE_HOURS = "UNPARSEABLE_HOURS"
    MISSING_CAPACITY = "MISSING_CAPACITY"
    SCHEDULE_OVERLAP = "SCHEDULE_OVERLAP"
    ROOM_CONFLICT = "ROOM_CONFLICT"
    NOT_IN_FILE = "NOT_IN_FILE"
    FACILITY_ROW = "FACILITY_ROW"
    MOBILE_REMOVED = "MOBILE_REMOVED"
