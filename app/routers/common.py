from datetime import datetime
from typing import Optional

from fastapi import Query
from sqlalchemy import or_

from app.schemas import ErrorResponse
from app.serialize import is_agent
from app.utils import as_utc, utcnow

# Documented business-rule responses shared by most routers.
ERRORS = {
    403: {"model": ErrorResponse, "description": "`FORBIDDEN_SCOPE`"},
    404: {"model": ErrorResponse, "description": "Not found"},
}


def Skip():
    return Query(0, ge=0)


def Limit():
    return Query(100, ge=1, le=500)


def UpdatedSince():
    return Query(
        None,
        description="Only records created, updated or deleted after this instant. "
        "Pass the previous response's `X-Server-Time`.",
    )


def IncludeDeleted():
    return Query(
        False,
        description="Also return soft-deleted, inactive and unpublished records, "
        "so a sync job can remove them from its index.",
    )


def server_time_headers() -> dict:
    """Taken BEFORE the query runs, so a record written mid-request is picked up by the
    next `updated_since` rather than lost."""
    return {"X-Server-Time": utcnow().isoformat()}


def sync_filter(query, model, user, *, updated_since: Optional[datetime],
                include_deleted: bool, is_active: Optional[bool] = None,
                flag: str = "is_active"):
    """Soft-delete / inactive / unpublished visibility + the `updated_since` convention."""
    col = getattr(model, flag)
    if not include_deleted:
        query = query.filter(model.deleted_at.is_(None))
        if is_active is not None:
            query = query.filter(col == is_active)
        if is_agent(user):
            query = query.filter(col.is_(True))
    if updated_since is not None:
        since = as_utc(updated_since)
        query = query.filter(or_(model.updated_at > since, model.deleted_at > since))
    return query


def visible_by_id(obj, user, flag: Optional[str] = "is_active") -> bool:
    """By-id reads hide soft-deleted records; `agent` keys also never see inactive /
    unpublished content."""
    if obj is None or getattr(obj, "deleted_at", None) is not None:
        return False
    if flag and is_agent(user) and not getattr(obj, flag):
        return False
    return True
