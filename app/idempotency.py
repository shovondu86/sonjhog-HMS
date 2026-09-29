import hashlib
import json
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import IdempotencyKey
from app.utils import utcnow


def body_hash(payload) -> str:
    data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def lookup(db: Session, user_id: int, endpoint: str, key: Optional[str], req_hash: str):
    """Returns the stored row for a repeated key, or None. Same key + different body
    is a client bug: 409 IDEMPOTENCY_KEY_REUSED."""
    if not key:
        return None
    row = (
        db.query(IdempotencyKey)
        .filter_by(user_id=user_id, endpoint=endpoint, key=key)
        .first()
    )
    if row is None:
        return None
    if row.request_hash != req_hash:
        raise ApiError(
            409, "IDEMPOTENCY_KEY_REUSED",
            "This Idempotency-Key was already used with a different request body.",
        )
    return row


def store(db: Session, user_id: int, endpoint: str, key: Optional[str],
          req_hash: str, status_code: int, body) -> None:
    if not key:
        return
    # Keep keys for at least 24h; prune anything older than a week.
    db.query(IdempotencyKey).filter(
        IdempotencyKey.created_at < utcnow() - timedelta(days=7)
    ).delete(synchronize_session=False)
    db.add(
        IdempotencyKey(
            user_id=user_id, endpoint=endpoint, key=key, request_hash=req_hash,
            status_code=status_code, response_body=body,
        )
    )
