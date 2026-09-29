from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.errors import not_found, validation_error
from app.models import Appointment, Doctor, DoctorSchedule, QueueState, User
from app.routers.common import ERRORS
from app.scheduling import Ctx, today
from app.schemas import NowServingIn, QueueStatus

router = APIRouter(tags=["Queue"], dependencies=[Depends(get_current_user)])


def _status(ctx, sess, doctor, d, queue) -> dict:
    qs_val = ctx.now_serving.get((sess.id, queue), 0)
    return QueueStatus(
        schedule_id=sess.id, doctor_id=doctor.id, doctor_name=doctor.name, date=d, queue=queue,
        now_serving=qs_val, booked=len(ctx.used_serials(sess, d, queue)),
    ).model_dump(mode="json")


@router.get("/queue", response_model=List[QueueStatus], operation_id="get_queue")
def get_queue(doctor_id: Optional[int] = None, db: Session = Depends(get_db)):
    """OPTIONAL feature. The serial currently being seen in each of today's sessions, so the
    agent can answer "how far along is my serial?". Only meaningful if OPD desk staff update
    it as patients go in."""
    d = today()
    q = db.query(Doctor).filter(Doctor.deleted_at.is_(None), Doctor.is_active.is_(True))
    if doctor_id is not None:
        q = q.filter(Doctor.id == doctor_id)
    doctors = q.order_by(Doctor.name).all()
    ctx = Ctx(db, [x.id for x in doctors], d, d)
    out = []
    for doc in doctors:
        for sess in ctx.day(doc.id, d).sessions:
            if sess.kind != "s":  # extra sessions have no schedule_id to key on
                continue
            out.append(_status(ctx, sess, doc, d, "regular"))
            if sess.report_capacity > 0:
                out.append(_status(ctx, sess, doc, d, "report"))
    return JSONResponse(out)


@router.put(
    "/queue/{schedule_id}", response_model=QueueStatus, responses=ERRORS, operation_id="set_now_serving"
)
def set_now_serving(
    schedule_id: int, payload: NowServingIn,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    """OPTIONAL feature. Sets today's `now_serving` for the session. Allowed for `admin`
    keys (the OPD desk). Resets automatically each day."""
    sched = db.get(DoctorSchedule, schedule_id)
    if sched is None:
        raise not_found("Schedule")
    d = today()
    doc = db.get(Doctor, sched.doctor_id)
    ctx = Ctx(db, [sched.doctor_id], d, d)
    sess = next((s for s in ctx.day(sched.doctor_id, d).sessions if s.schedule_id == sched.id), None)
    if sess is None:
        raise not_found("Session today")
    queue = payload.queue.value
    if queue == "report" and sess.report_capacity <= 0:
        raise validation_error(["body", "queue"], "This session has no report queue")
    row = db.query(QueueState).filter_by(schedule_id=sched.id, date=d, queue=queue).first()
    if row is None:
        row = QueueState(schedule_id=sched.id, date=d, queue=queue, now_serving=payload.now_serving)
        db.add(row)
    else:
        row.now_serving = payload.now_serving
    db.commit()
    ctx = Ctx(db, [sched.doctor_id], d, d)
    body = _status(ctx, sess, doc, d, queue)
    body["updated_at"] = row.updated_at.isoformat() if row.updated_at else None
    return JSONResponse(body)
