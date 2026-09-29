from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.enums import PackageGender
from app.errors import ApiError, not_found
from app.models import Package, User
from app.routers.common import (
    ERRORS, IncludeDeleted, Limit, Skip, UpdatedSince, server_time_headers,
    sync_filter, visible_by_id,
)
from app.schemas import ErrorResponse, PackageCreate, PackageOut, PackageUpdate
from app.serialize import render, render_list, snapshot
from app.utils import apply_updates, utcnow

router = APIRouter(prefix="/packages", tags=["Packages"], dependencies=[Depends(get_current_user)])


def _name_taken(db: Session, name: str, exclude_id: Optional[int] = None) -> bool:
    q = db.query(Package.id).filter(
        func.lower(Package.name) == name.strip().lower(), Package.deleted_at.is_(None)
    )
    if exclude_id is not None:
        q = q.filter(Package.id != exclude_id)
    return q.first() is not None


def _matches(p: Package, term: str) -> bool:
    t = term.lower()
    hay = [p.name, p.name_bn, *(p.aliases or []), *(p.investigations or [])]
    return any(h and t in h.lower() for h in hay)


@router.get("", response_model=List[PackageOut], operation_id="list_packages")
def list_packages(
    skip: int = Skip(),
    limit: int = Limit(),
    search: Optional[str] = Query(None, description="Matches `name`, `name_bn`, `aliases`, and investigation names."),
    gender: Optional[PackageGender] = Query(
        None, description="`male` returns `male` and `any` packages; likewise `female`."
    ),
    is_active: Optional[bool] = True,
    updated_since: Optional[datetime] = UpdatedSince(),
    include_deleted: bool = IncludeDeleted(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    headers = server_time_headers()
    q = sync_filter(
        db.query(Package), Package, user,
        updated_since=updated_since, include_deleted=include_deleted, is_active=is_active,
    )
    if gender is not None:
        wanted = ["any"] if gender == PackageGender.any else [gender.value, "any"]
        q = q.filter(Package.gender.in_(wanted))
    rows = q.order_by(func.lower(Package.name), Package.id).all()
    if search:
        rows = [r for r in rows if _matches(r, search)]
    return JSONResponse(render_list(user, PackageOut, rows[skip: skip + limit]), headers=headers)


@router.post(
    "", response_model=PackageOut, status_code=201, operation_id="create_package",
    responses={**ERRORS, 409: {"model": ErrorResponse, "description": "`DUPLICATE_NAME`"}},
)
def create_package(
    payload: PackageCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """Requires `admin` scope."""
    if _name_taken(db, payload.name):
        raise ApiError(409, "DUPLICATE_NAME", f"A package named '{payload.name}' already exists.")
    row = Package(**payload.model_dump(mode="json"))
    db.add(row)
    db.flush()
    audit.record(db, user, "create", "package", row.id, None, snapshot(PackageOut, row))
    db.commit()
    db.refresh(row)
    return JSONResponse(render(user, PackageOut, row), status_code=201)


@router.get("/{package_id}", response_model=PackageOut, responses=ERRORS, operation_id="get_package")
def get_package(package_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(Package, package_id)
    if not visible_by_id(row, user):
        raise not_found("Package")
    return JSONResponse(render(user, PackageOut, row))


@router.put("/{package_id}", response_model=PackageOut, responses=ERRORS, operation_id="update_package")
def update_package(
    package_id: int, payload: PackageUpdate,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    """Requires `admin` scope. Partial update."""
    row = db.get(Package, package_id)
    if row is None or row.deleted_at is not None:
        raise not_found("Package")
    before = snapshot(PackageOut, row)
    data = payload.model_dump(mode="json", exclude_unset=True)
    if data.get("name") and _name_taken(db, data["name"], exclude_id=row.id):
        raise ApiError(409, "DUPLICATE_NAME", f"A package named '{data['name']}' already exists.")
    apply_updates(
        row, data,
        non_nullable=("name", "price", "gender", "aliases", "investigations", "complementary", "is_active"),
    )
    db.flush()
    db.refresh(row)
    audit.record(db, user, "update", "package", row.id, before, snapshot(PackageOut, row))
    db.commit()
    db.refresh(row)
    return JSONResponse(render(user, PackageOut, row))


@router.delete("/{package_id}", status_code=204, responses=ERRORS, operation_id="delete_package")
def delete_package(package_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Requires `admin` scope. Soft delete (sets `deleted_at`)."""
    row = db.get(Package, package_id)
    if row is None or row.deleted_at is not None:
        raise not_found("Package")
    before = snapshot(PackageOut, row)
    now = utcnow()
    row.deleted_at = now
    row.updated_at = now
    audit.record(db, user, "delete", "package", row.id, before, None)
    db.commit()
    return Response(status_code=204)
