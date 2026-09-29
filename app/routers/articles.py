from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import audit
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.enums import ArticleTopic, ServiceLine
from app.errors import not_found, validation_error
from app.models import Article, User
from app.routers.common import (
    ERRORS, IncludeDeleted, Limit, Skip, UpdatedSince, server_time_headers,
    sync_filter, visible_by_id,
)
from app.schemas import ArticleCreate, ArticleOut, ArticleUpdate
from app.serialize import is_agent, render, render_list, snapshot
from app.utils import apply_updates, utcnow

router = APIRouter(prefix="/articles", tags=["Articles"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=List[ArticleOut], operation_id="list_articles")
def list_articles(
    skip: int = Skip(),
    limit: int = Limit(),
    topic: Optional[ArticleTopic] = None,
    service_line: Optional[ServiceLine] = Query(
        None, description="Articles for that desk PLUS articles with no `service_line` (shared)."
    ),
    is_published: Optional[bool] = Query(
        None, description="Admin only. `agent` keys always get published articles."
    ),
    updated_since: Optional[datetime] = UpdatedSince(),
    include_deleted: bool = IncludeDeleted(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """FAQ and general content (how to book, about the hospital, careers, policies,
    directions). `agent` keys see only published articles unless `include_deleted=true`."""
    headers = server_time_headers()
    q = sync_filter(
        db.query(Article), Article, user,
        updated_since=updated_since, include_deleted=include_deleted,
        is_active=None if is_agent(user) else is_published, flag="is_published",
    )
    if topic is not None:
        q = q.filter(Article.topic == topic.value)
    if service_line is not None:
        q = q.filter(or_(Article.service_line == service_line.value, Article.service_line.is_(None)))
    rows = q.order_by(Article.id).offset(skip).limit(limit).all()
    return JSONResponse(render_list(user, ArticleOut, rows), headers=headers)


@router.post(
    "", response_model=ArticleOut, status_code=201, responses=ERRORS, operation_id="create_article"
)
def create_article(
    payload: ArticleCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """Requires `admin` scope."""
    row = Article(**payload.model_dump(mode="json"))
    db.add(row)
    db.flush()
    audit.record(db, user, "create", "article", row.id, None, snapshot(ArticleOut, row))
    db.commit()
    db.refresh(row)
    return JSONResponse(render(user, ArticleOut, row), status_code=201)


@router.get("/{article_id}", response_model=ArticleOut, responses=ERRORS, operation_id="get_article")
def get_article(article_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(Article, article_id)
    if not visible_by_id(row, user, flag="is_published"):
        raise not_found("Article")
    return JSONResponse(render(user, ArticleOut, row))


@router.put("/{article_id}", response_model=ArticleOut, responses=ERRORS, operation_id="update_article")
def update_article(
    article_id: int, payload: ArticleUpdate,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    """Requires `admin` scope. Partial update."""
    row = db.get(Article, article_id)
    if row is None or row.deleted_at is not None:
        raise not_found("Article")
    before = snapshot(ArticleOut, row)
    data = payload.model_dump(mode="json", exclude_unset=True)
    apply_updates(row, data, non_nullable=("title", "topic", "questions", "is_published"))
    if not (row.body or row.body_bn):
        raise validation_error(["body", "body"], "at least one of body, body_bn is required")
    db.flush()
    db.refresh(row)
    audit.record(db, user, "update", "article", row.id, before, snapshot(ArticleOut, row))
    db.commit()
    db.refresh(row)
    return JSONResponse(render(user, ArticleOut, row))


@router.delete("/{article_id}", status_code=204, responses=ERRORS, operation_id="delete_article")
def delete_article(article_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Requires `admin` scope. Soft delete (sets `deleted_at`)."""
    row = db.get(Article, article_id)
    if row is None or row.deleted_at is not None:
        raise not_found("Article")
    before = snapshot(ArticleOut, row)
    now = utcnow()
    row.deleted_at = now
    row.updated_at = now
    audit.record(db, user, "delete", "article", row.id, before, None)
    db.commit()
    return Response(status_code=204)
