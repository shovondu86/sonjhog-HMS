"""Rendering helpers. Responses are built as plain JSON (not re-validated by FastAPI) so
that staff-private fields can be OMITTED, not nulled, for `agent`-scoped keys."""
from typing import Iterable, Optional

from fastapi.responses import JSONResponse

from app.schemas import (
    AppointmentDetailOut, DoctorOut, HolidayOut, ScheduleExceptionOut,
)

_DOCTOR_PRIVATE = {"mobile_number", "email"}

# Schema class -> pydantic `exclude` spec applied for agent-scoped keys.
AGENT_EXCLUDE = {
    DoctorOut: _DOCTOR_PRIVATE,
    AppointmentDetailOut: {"doctor": _DOCTOR_PRIVATE},
    ScheduleExceptionOut: {"note"},
    HolidayOut: {"note"},
}


def is_agent(user) -> bool:
    return getattr(user, "scope", "admin") == "agent"


def render(user, schema, obj) -> dict:
    model = obj if isinstance(obj, schema) else schema.model_validate(obj)
    exclude = AGENT_EXCLUDE.get(schema) if is_agent(user) else None
    return model.model_dump(mode="json", exclude=exclude)


def render_list(user, schema, objs: Iterable) -> list:
    return [render(user, schema, o) for o in objs]


def respond(user, schema, obj, status_code: int = 200, headers: Optional[dict] = None):
    return JSONResponse(render(user, schema, obj), status_code=status_code, headers=headers)


def respond_list(user, schema, objs: Iterable, headers: Optional[dict] = None):
    return JSONResponse(render_list(user, schema, objs), headers=headers)


def snapshot(schema, obj) -> dict:
    """Full (admin) JSON snapshot for the audit log."""
    return schema.model_validate(obj).model_dump(mode="json")
