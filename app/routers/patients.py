from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import idempotency
from app.database import get_db
from app.deps import get_current_user
from app.errors import not_found
from app.models import Patient, User
from app.schemas import ErrorResponse, PatientCreate, PatientOut, PatientUpdate
from app.serialize import render, render_list
from app.utils import apply_updates, like_contains, normalize_phone
from fastapi.responses import JSONResponse

from app.routers.common import ERRORS, Limit, Skip

router = APIRouter(
    prefix="/patients", tags=["Patients"], dependencies=[Depends(get_current_user)]
)


@router.post(
    "", response_model=PatientOut, status_code=201, operation_id="create_patient_patients_post",
    responses={409: {"model": ErrorResponse, "description": "`IDEMPOTENCY_KEY_REUSED`"}},
)
def create_patient(
    payload: PatientCreate,
    idempotency_key: Optional[str] = Header(
        None, alias="Idempotency-Key", min_length=8, max_length=128,
        description="Client-generated, unique per logical operation. A repeated request "
        "with the same key returns the original 201 body.",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """CHANGED in 1.1.0: accepts `Idempotency-Key`. A repeated request with the same key
    returns the original `201` body instead of creating a second patient."""
    h = idempotency.body_hash(payload)
    prior = idempotency.lookup(db, user.id, "POST /patients", idempotency_key, h)
    if prior:
        return JSONResponse(prior.response_body, status_code=prior.status_code)

    patient = Patient(**payload.model_dump())
    patient.mobile_normalized = normalize_phone(payload.mobile_number)
    db.add(patient)
    db.flush()
    body = render(user, PatientOut, patient)
    idempotency.store(db, user.id, "POST /patients", idempotency_key, h, 201, body)
    db.commit()
    return JSONResponse(body, status_code=201)


@router.get("", response_model=List[PatientOut], operation_id="list_patients_patients_get")
def list_patients(
    skip: int = Skip(),
    limit: int = Limit(),
    search: Optional[str] = Query(None, description="Substring match on name or mobile number."),
    mobile_number: Optional[str] = Query(
        None, max_length=20,
        description="NEW in 1.1.0. EXACT match after normalising both sides to the 11-digit "
        "national form: strip everything but digits, drop a leading `88`, so "
        "`+8801712345678`, `8801712345678`, `01712-345678` and `01712345678` all match "
        "the same patient. Wildcards are not interpreted.",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(Patient)
    if search:
        query = query.filter(
            like_contains(Patient.name, search) | like_contains(Patient.mobile_number, search)
        )
    if mobile_number is not None:
        query = query.filter(Patient.mobile_normalized == normalize_phone(mobile_number))
    rows = query.order_by(Patient.id.desc()).offset(skip).limit(limit).all()
    return JSONResponse(render_list(user, PatientOut, rows))


@router.get(
    "/{patient_id}", response_model=PatientOut, responses=ERRORS,
    operation_id="get_patient_patients__patient_id__get",
)
def get_patient(patient_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise not_found("Patient")
    return JSONResponse(render(user, PatientOut, patient))


@router.put(
    "/{patient_id}", response_model=PatientOut, responses=ERRORS,
    operation_id="update_patient_patients__patient_id__put",
)
def update_patient(
    patient_id: int, payload: PatientUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise not_found("Patient")
    data = payload.model_dump(exclude_unset=True)
    apply_updates(patient, data, non_nullable=("name", "age", "mobile_number"))
    if "mobile_number" in data and data["mobile_number"] is not None:
        patient.mobile_normalized = normalize_phone(patient.mobile_number)
    db.commit()
    db.refresh(patient)
    return JSONResponse(render(user, PatientOut, patient))


@router.delete(
    "/{patient_id}", status_code=204, responses=ERRORS,
    operation_id="delete_patient_patients__patient_id__delete",
)
def delete_patient(patient_id: int, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if not patient:
        raise not_found("Patient")
    db.delete(patient)
    db.commit()
    return Response(status_code=204)
