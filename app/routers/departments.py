from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.enums import ServiceLine
from app.errors import ApiError, not_found
from app.models import Department, Doctor, User
from app.routers.common import (
    ERRORS, IncludeDeleted, Limit, Skip, UpdatedSince, server_time_headers,
    sync_filter, visible_by_id,
)
from app.schemas import (
    DepartmentCreate, DepartmentOut, DepartmentUpdate, ErrorResponse,
)
from app.serialize import render, render_list, snapshot
from app.utils import apply_updates, utcnow, word_prefix_match

router = APIRouter(
    prefix="/departments", tags=["Departments"], dependencies=[Depends(get_current_user)]
)


def _with_counts(db: Session, rows: List[Department]) -> List[Department]:
    ids = [r.id for r in rows]
    counts = {}
    if ids:
        counts = dict(
            db.query(Doctor.department_id, func.count(Doctor.id))
            .filter(
                Doctor.department_id.in_(ids),
                Doctor.deleted_at.is_(None),
                Doctor.is_active.is_(True),
            )
            .group_by(Doctor.department_id)
            .all()
        )
    for r in rows:
        r.doctor_count = counts.get(r.id, 0)
    return rows


def _name_taken(db: Session, name: str, exclude_id: Optional[int] = None) -> bool:
    q = db.query(Department.id).filter(
        func.lower(Department.name) == name.strip().lower(), Department.deleted_at.is_(None)
    )
    if exclude_id is not None:
        q = q.filter(Department.id != exclude_id)
    return q.first() is not None


@router.get(
    "", response_model=List[DepartmentOut], operation_id="list_departments_departments_get"
)
def list_departments(
    skip: int = Skip(),
    limit: int = Limit(),
    search: Optional[str] = Query(
        None,
        description='Case-insensitive match against `name`, `name_bn` and every entry of '
        '`aliases`, by word prefix (so `ent` matches "ENT, Head & Neck Surgery" but not '
        '"Gastroenterology").',
    ),
    service_line: Optional[ServiceLine] = None,
    is_active: Optional[bool] = True,
    updated_since: Optional[datetime] = UpdatedSince(),
    include_deleted: bool = IncludeDeleted(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    headers = server_time_headers()
    q = sync_filter(
        db.query(Department), Department, user,
        updated_since=updated_since, include_deleted=include_deleted, is_active=is_active,
    )
    if service_line is not None:
        q = q.filter(Department.service_line == service_line.value)
    rows = q.order_by(func.lower(Department.name), Department.id).all()
    if search:
        rows = [
            r for r in rows
            if word_prefix_match(search, [r.name, r.name_bn, *(r.aliases or [])])
        ]
    page = _with_counts(db, rows[skip: skip + limit])
    return JSONResponse(render_list(user, DepartmentOut, page), headers=headers)


@router.post(
    "", response_model=DepartmentOut, status_code=201, operation_id="create_department_departments_post",
    responses={**ERRORS, 409: {"model": ErrorResponse, "description": "`DUPLICATE_NAME`"}},
)
def create_department(
    payload: DepartmentCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """Requires `admin` scope."""
    if _name_taken(db, payload.name):
        raise ApiError(409, "DUPLICATE_NAME", f"A department named '{payload.name}' already exists.")
    data = payload.model_dump(mode="json")
    dept = Department(**data)
    db.add(dept)
    db.flush()
    _with_counts(db, [dept])
    audit.record(db, user, "create", "department", dept.id, None, snapshot(DepartmentOut, dept))
    db.commit()
    db.refresh(dept)
    _with_counts(db, [dept])
    return JSONResponse(render(user, DepartmentOut, dept), status_code=201)


@router.get(
    "/{department_id}", response_model=DepartmentOut, responses=ERRORS,
    operation_id="get_department_departments__department_id__get",
)
def get_department(
    department_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    dept = db.get(Department, department_id)
    if not visible_by_id(dept, user):
        raise not_found("Department")
    _with_counts(db, [dept])
    return JSONResponse(render(user, DepartmentOut, dept))


@router.put(
    "/{department_id}", response_model=DepartmentOut, responses=ERRORS,
    operation_id="update_department_departments__department_id__put",
)
def update_department(
    department_id: int, payload: DepartmentUpdate,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    """Requires `admin` scope."""
    dept = db.get(Department, department_id)
    if dept is None or dept.deleted_at is not None:
        raise not_found("Department")
    _with_counts(db, [dept])
    before = snapshot(DepartmentOut, dept)
    data = payload.model_dump(mode="json", exclude_unset=True)
    if data.get("name") and _name_taken(db, data["name"], exclude_id=dept.id):
        raise ApiError(409, "DUPLICATE_NAME", f"A department named '{data['name']}' already exists.")
    apply_updates(dept, data, non_nullable=("name", "service_line", "is_active", "aliases", "conditions"))
    db.flush()
    audit.record(db, user, "update", "department", dept.id, before, snapshot(DepartmentOut, dept))
    db.commit()
    db.refresh(dept)
    _with_counts(db, [dept])
    return JSONResponse(render(user, DepartmentOut, dept))


@router.delete(
    "/{department_id}", status_code=204,
    responses={**ERRORS, 409: {"model": ErrorResponse, "description": "`HAS_DOCTORS`"}},
    operation_id="delete_department_departments__department_id__delete",
)
def delete_department(
    department_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """Requires `admin` scope. Refused with `409 HAS_DOCTORS` while any doctor
    references it — set `is_active: false` instead. CHANGED in 1.2.0: soft delete
    (sets `deleted_at`)."""
    dept = db.get(Department, department_id)
    if dept is None or dept.deleted_at is not None:
        raise not_found("Department")
    if db.query(Doctor.id).filter(
        Doctor.department_id == dept.id, Doctor.deleted_at.is_(None)
    ).first():
        raise ApiError(
            409, "HAS_DOCTORS",
            "Doctors still reference this department. Set is_active to false instead.",
        )
    _with_counts(db, [dept])
    before = snapshot(DepartmentOut, dept)
    now = utcnow()
    dept.deleted_at = now
    dept.updated_at = now
    audit.record(db, user, "delete", "department", dept.id, before, None)
    db.commit()
    return Response(status_code=204)
