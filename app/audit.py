from typing import Optional

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def record(
    db: Session,
    user: User,
    action: str,
    entity_type: str,
    entity_id: int,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    doctor_id: Optional[int] = None,
) -> None:
    """Append-only change history. Added to the caller's transaction, so it commits
    (or rolls back) together with the change itself."""
    db.add(
        AuditLog(
            user_id=user.id, username=user.username, action=action,
            entity_type=entity_type, entity_id=entity_id, doctor_id=doctor_id,
            before=before, after=after,
        )
    )
