from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import importer
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.errors import ApiError, validation_error
from app.models import User
from app.routers.common import ERRORS
from app.scheduling import today
from app.schemas import AppointmentDetailOut, ErrorResponse, ScheduleCreate, ScheduleImportResult
from app.serialize import render_list

router = APIRouter(tags=["Imports"], dependencies=[Depends(get_current_user)])


@router.post(
    "/schedules/import", response_model=ScheduleImportResult, operation_id="import_schedules",
    responses={
        **ERRORS,
        409: {"model": ErrorResponse, "description": "`IMPORT_HAS_ERRORS` (apply only)"},
        415: {"model": ErrorResponse, "description": "`UNSUPPORTED_FILE`"},
    },
)
async def import_schedules(
    mode: str = Query(..., pattern="^(preview|apply)$"),
    valid_from: Optional[date] = Query(
        None,
        description="First date the new schedule applies. Defaults to today. Must not be in "
        "the past. Use a future date to load a new rota ahead of time.",
    ),
    default_max_patients: Optional[int] = Query(
        None, ge=1,
        description="Capacity for sessions the file gives none for. Order of precedence: the "
        "file's value, then the doctor's current capacity for that weekday, then this. None "
        "of the three is a `MISSING_CAPACITY` error.",
    ),
    file: UploadFile = File(..., description=".xls, .xlsx or .csv, at most 5 MB."),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Requires `admin` scope. Upload the OPD room-allocation spreadsheet (`.xls`,
    `.xlsx`) or the CSV format, and replace the weekly schedule of every doctor in the file.

    ALWAYS call with `mode=preview` first. Preview parses and validates and changes
    nothing. `mode=apply` repeats the same work and commits it, and is refused with
    `409 IMPORT_HAS_ERRORS` if any problem has severity `error`.

    **Spreadsheet rules**: merged day cells mean the same hours on every day they span;
    `x`, `X`, `-` means no session, an EMPTY day cell is `BLANK_HOURS`;
    `09:00 am To 12:00 pm & 03:00 pm To 08:00 pm` is TWO sessions; section headers
    `OPD - (3rd Floor) Desk - A` set floor and desk for the rows below; phone numbers in the
    name cell are discarded; rows naming a room but no doctor are `FACILITY_ROW` warnings.

    **CSV columns**: `doctor_id` (or `doctor_name`), `weekday`, `start_time`, `end_time`
    (24-hour `HH:MM`), `room`, `floor`, `desk`, `max_patients`, optional `slot_minutes`,
    `report_capacity`.

    **What apply does**, per changed doctor: every current schedule row is end-dated
    (`valid_to = valid_from - 1`) and the file's sessions are created with `valid_from`.
    Doctors NOT in the file are untouched. One transaction: all or nothing. One `import`
    audit entry per doctor."""
    vf = valid_from or today()
    if vf < today():
        raise validation_error(["query", "valid_from"], "valid_from must not be in the past")

    content = await file.read(importer.MAX_BYTES + 1)
    if len(content) > importer.MAX_BYTES:
        raise importer.unsupported("The file is larger than 5 MB.")
    if not content:
        raise importer.unsupported("The file is empty.")

    try:
        result, problems = importer.run_import(
            db, user, filename=file.filename or "", content=content, mode=mode,
            valid_from=vf, default_max_patients=default_max_patients,
        )
        if result["summary"]["errors"] and mode == "apply":
            raise ApiError(
                409, "IMPORT_HAS_ERRORS",
                f"{result['summary']['errors']} error(s) in the file; nothing was applied.",
                candidates=[p.dump() for p in problems],
            )
        # Render while the (hypothetical, for preview) changes are still visible.
        affected = render_list(user, AppointmentDetailOut, result["affected_appointments"])
        if mode == "apply":
            db.commit()
        else:
            db.rollback()  # preview changes nothing
    except Exception:
        db.rollback()
        raise

    return JSONResponse(
        {
            "mode": result["mode"],
            "applied": bool(result["applied"]) and mode == "apply",
            "valid_from": vf.isoformat(),
            "summary": result["summary"],
            "doctors": [
                {
                    "doctor_id": d["doctor_id"], "doctor_name": d["doctor_name"],
                    "changed": d["changed"],
                    "sessions": [
                        ScheduleCreate(**sess).model_dump(mode="json") for sess in d["sessions"]
                    ],
                }
                for d in result["doctors"]
            ],
            "problems": [p.dump() for p in problems],
            "affected_appointments": affected,
        }
    )
