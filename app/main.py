import logging

from fastapi import FastAPI

from app import migrate
from app.errors import ApiError, api_error_handler
from app.routers import (
    appointments, articles, audit, auth, availability, departments, doctors, holidays,
    hospital, imports, packages, patients, queue, schedules, services,
)

log = logging.getLogger("app")

# Create tables and apply additive upgrades (1.0.0 -> 1.2.0) before serving.
try:
    migrate.run()
except Exception:  # pragma: no cover
    log.exception("Database upgrade failed — run `python -m app.migrate` and read the error.")
    raise

DESCRIPTION = """
Patients, doctors, departments, OPD schedules, appointments, and the hospital information
the call centre's voice agent reads to callers.

Keys are issued per user by `POST /auth/login` and sent as `X-API-Key`.
`admin` — full read/write. `agent` — the voice agent: read on everything except
`/audit-log`; write ONLY patients and appointments (anything else is `403 FORBIDDEN_SCOPE`);
staff-private fields are omitted and unpublished/inactive content is hidden.

All `date`/`time` values are hospital-local (Asia/Dhaka). Business-rule failures return an
`ErrorResponse` with a machine-readable `code`; validation failures keep FastAPI's 422.
"""

TAGS = [
    {"name": "Auth"}, {"name": "Patients"},
    {"name": "Departments", "description": "NEW in 1.1.0"}, {"name": "Doctors"},
    {"name": "Schedules", "description": "NEW in 1.1.0 — weekly OPD sessions and exceptions"},
    {"name": "Availability", "description": "NEW in 1.1.0 — computed, read-only"},
    {"name": "Holidays", "description": "NEW in 1.2.0 — hospital-wide closures"},
    {"name": "Appointments"},
    {"name": "Imports", "description": "NEW in 1.2.0 — upload the OPD room-allocation spreadsheet"},
    {"name": "Audit", "description": "NEW in 1.2.0 — change history (admin only)"},
    {"name": "Hospital", "description": "NEW in 1.2.0 — address, contacts, hours"},
    {"name": "Packages", "description": "NEW in 1.2.0 — health check-up packages"},
    {"name": "Services", "description": "NEW in 1.2.0 — services and facility rooms"},
    {"name": "Articles", "description": "NEW in 1.2.0 — FAQ and general content"},
    {"name": "Queue", "description": "NEW in 1.2.0, OPTIONAL — the serial currently being seen"},
    {"name": "Health"},
]

app = FastAPI(
    title="Hospital Management API", version="1.2.0",
    description=DESCRIPTION, openapi_tags=TAGS,
)
app.add_exception_handler(ApiError, api_error_handler)

app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(departments.router)
app.include_router(doctors.router)
app.include_router(imports.router)  # before /schedules/{schedule_id}
app.include_router(schedules.router)
app.include_router(availability.router)
app.include_router(holidays.router)
app.include_router(appointments.router)
app.include_router(audit.router)
app.include_router(hospital.router)
app.include_router(packages.router)
app.include_router(services.router)
app.include_router(articles.router)
app.include_router(queue.router)


@app.get("/", tags=["Health"], operation_id="health_check__get")
def health_check():
    return {"status": "ok", "service": "Hospital Management API"}
