from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import ApiKeyResponse
from app.security import verify_password

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=ApiKeyResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    Simple login — no registration endpoint exists.
    Users are created only via the seed script (app/seed.py).

    Returns the user's persistent API key. Send it back on every other request
    as the `X-API-Key` header (not an Authorization/Bearer token).
    """
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    return ApiKeyResponse(api_key=user.api_key)
