from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.enums import ServiceKind, ServiceLine
from app.errors import ApiError, not_found
from app.models import Department, Service, User
from app.routers.common import (
    ERRORS, IncludeDeleted, Limit, Skip, UpdatedSince, server_time_headers,
    sync_filter, visible_by_id,
)
from app.schemas import ErrorResponse, ServiceCreate, ServiceOut, ServiceUpdate
from app.serialize import render, render_list, snapshot
from app.utils import apply_updates, utcnow, word_prefix_match

router = APIRouter(prefix="/services", tags=["Services"], dependencies=[Depends(get_current_user)])


def _name_taken(db: Session, name: str, exclude_id: Optional[int] = None) -> bool:
    q = db.query(Service.id).filter(
        func.lower(Service.name) == name.strip().lower(), Service.deleted_at.is_(None)
    )
    if exclude_id is not None:
        q = q.filter(Service.id != exclude_id)
    return q.first() is not None


def _check_department(db: Session, department_id: Optional[int]):
    if department_id is not None:
        d = db.get(Department, department_id)
        if d is None or d.deleted_at is not None:
            raise not_found(f"Department {department_id}")


@router.get("", response_model=List[ServiceOut], operation_id="list_services")
def list_services(
    skip: int = Skip(),
    limit: int = Limit(),
    search: Optional[str] = Query(None, description="Matches `name`, `name_bn` and `aliases`, by word prefix."),
    kind: Optional[ServiceKind] = None,
    service_line: Optional[ServiceLine] = None,
    department_id: Optional[int] = None,
    is_active: Optional[bool] = True,
    updated_since: Optional[datetime] = UpdatedSince(),
    include_deleted: bool = IncludeDeleted(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    headers = server_time_headers()
    q = sync_filter(
        db.query(Service), Service, user,
        updated_since=updated_since, include_deleted=include_deleted, is_active=is_active,
    )
    if kind is not None:
        q = q.filter(Service.kind == kind.value)
    if service_line is not None:
        q = q.filter(Service.service_line == service_line.value)
    if department_id is not None:
        q = q.filter(Service.department_id == department_id)
    rows = q.order_by(func.lower(Service.name), Service.id).all()
    if search:
        rows = [r for r in rows if word_prefix_match(search, [r.name, r.name_bn, *(r.aliases or [])])]
    return JSONResponse(render_list(user, ServiceOut, rows[skip: skip + limit]), headers=headers)


@router.post(
    "", response_model=ServiceOut, status_code=201, operation_id="create_service",
    responses={**ERRORS, 409: {"model": ErrorResponse, "description": "`DUPLICATE_NAME`"}},
)
def create_service(
    payload: ServiceCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """Requires `admin` scope."""
    if _name_taken(db, payload.name):
        raise ApiError(409, "DUPLICATE_NAME", f"A service named '{payload.name}' already exists.")
    _check_department(db, payload.department_id)
    row = Service(**payload.model_dump(mode="json"))
    db.add(row)
    db.flush()
    audit.record(db, user, "create", "service", row.id, None, snapshot(ServiceOut, row))
    db.commit()
    db.refresh(row)
    return JSONResponse(render(user, ServiceOut, row), status_code=201)


@router.get("/{service_id}", response_model=ServiceOut, responses=ERRORS, operation_id="get_service")
def get_service(service_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(Service, service_id)
    if not visible_by_id(row, user):
        raise not_found("Service")
    return JSONResponse(render(user, ServiceOut, row))


@router.put("/{service_id}", response_model=ServiceOut, responses=ERRORS, operation_id="update_service")
def update_service(
    service_id: int, payload: ServiceUpdate,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    """Requires `admin` scope. Partial update."""
    row = db.get(Service, service_id)
    if row is None or row.deleted_at is not None:
        raise not_found("Service")
    before = snapshot(ServiceOut, row)
    data = payload.model_dump(mode="json", exclude_unset=True)
    if data.get("name") and _name_taken(db, data["name"], exclude_id=row.id):
        raise ApiError(409, "DUPLICATE_NAME", f"A service named '{data['name']}' already exists.")
    if data.get("department_id") is not None:
        _check_department(db, data["department_id"])
    apply_updates(
        row, data, non_nullable=("name", "kind", "service_line", "aliases", "is_24x7", "is_active")
    )
    db.flush()
    db.refresh(row)
    audit.record(db, user, "update", "service", row.id, before, snapshot(ServiceOut, row))
    db.commit()
    db.refresh(row)
    return JSONResponse(render(user, ServiceOut, row))


@router.delete("/{service_id}", status_code=204, responses=ERRORS, operation_id="delete_service")
def delete_service(service_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Requires `admin` scope. Soft delete (sets `deleted_at`)."""
    row = db.get(Service, service_id)
    if row is None or row.deleted_at is not None:
        raise not_found("Service")
    before = snapshot(ServiceOut, row)
    now = utcnow()
    row.deleted_at = now
    row.updated_at = now
    audit.record(db, user, "delete", "service", row.id, before, None)
    db.commit()
    return Response(status_code=204)
