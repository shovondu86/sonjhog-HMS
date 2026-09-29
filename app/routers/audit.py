from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.enums import AuditEntityType
from app.models import AuditLog
from app.routers.common import ERRORS
from app.schemas import AuditEntry
from app.utils import as_utc
from app.routers.common import Limit, Skip

router = APIRouter(tags=["Audit"], dependencies=[Depends(require_admin)])


@router.get(
    "/audit-log", response_model=List[AuditEntry], responses=ERRORS, operation_id="list_audit_log"
)
def list_audit_log(
    skip: int = Skip(),
    limit: int = Limit(),
    entity_type: Optional[AuditEntityType] = None,
    entity_id: Optional[int] = None,
    doctor_id: Optional[int] = Query(
        None, description="Entries for the doctor AND their schedules and exceptions."
    ),
    user_id: Optional[int] = None,
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    """Requires `admin` scope. Every create, update, delete and import of doctors,
    departments, schedules, exceptions, holidays, packages, services, articles and the
    hospital profile, newest first. Entries are never edited or deleted."""
    q = db.query(AuditLog)
    if entity_type is not None:
        q = q.filter(AuditLog.entity_type == entity_type.value)
    if entity_id is not None:
        q = q.filter(AuditLog.entity_id == entity_id)
    if doctor_id is not None:
        q = q.filter(AuditLog.doctor_id == doctor_id)
    if user_id is not None:
        q = q.filter(AuditLog.user_id == user_id)
    if date_from is not None:
        q = q.filter(AuditLog.at >= as_utc(date_from))
    if date_to is not None:
        q = q.filter(AuditLog.at <= as_utc(date_to))
    rows = q.order_by(AuditLog.at.desc(), AuditLog.id.desc()).offset(skip).limit(limit).all()
    return JSONResponse([AuditEntry.model_validate(r).model_dump(mode="json") for r in rows])
