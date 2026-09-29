from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import audit
from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models import Hospital, User
from app.routers.common import ERRORS
from app.schemas import HospitalOut, HospitalUpdate
from app.serialize import render, snapshot
from app.utils import apply_updates

router = APIRouter(prefix="/hospital", tags=["Hospital"], dependencies=[Depends(get_current_user)])


def get_or_create(db: Session) -> Hospital:
    h = db.get(Hospital, 1)
    if h is None:
        h = Hospital(id=1, name=settings.hospital_name)
        db.add(h)
        db.commit()
        db.refresh(h)
    return h


@router.get("", response_model=HospitalOut, operation_id="get_hospital")
def get_hospital(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The single hospital profile. Always exists (seeded on migration)."""
    return JSONResponse(render(user, HospitalOut, get_or_create(db)))


@router.put("", response_model=HospitalOut, responses=ERRORS, operation_id="update_hospital")
def update_hospital(
    payload: HospitalUpdate, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    """Requires `admin` scope. Partial update."""
    h = get_or_create(db)
    before = snapshot(HospitalOut, h)
    data = payload.model_dump(mode="json", exclude_unset=True)
    apply_updates(h, data, non_nullable=("name",))
    db.flush()
    db.refresh(h)
    audit.record(db, user, "update", "hospital", h.id, before, snapshot(HospitalOut, h))
    db.commit()
    db.refresh(h)
    return JSONResponse(render(user, HospitalOut, h))
