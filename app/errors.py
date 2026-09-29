from datetime import date

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Business-rule failure. Serialised as the spec's `ErrorResponse` body:
    {"code": ..., "detail": ..., optional extras}."""

    def __init__(self, status_code: int, code: str, detail: str, **extra):
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.extra = {k: v for k, v in extra.items() if v is not None}


def _jsonable(v):
    return v.isoformat() if isinstance(v, date) else v


async def api_error_handler(request: Request, exc: ApiError):
    body = {"code": exc.code, "detail": exc.detail}
    body.update({k: _jsonable(v) for k, v in exc.extra.items()})
    return JSONResponse(status_code=exc.status_code, content=body)


def not_found(what: str, code: str = "NOT_FOUND") -> ApiError:
    return ApiError(404, code, f"{what} not found")


def forbidden_scope(detail: str = "This API key's scope does not allow this operation.") -> ApiError:
    return ApiError(403, "FORBIDDEN_SCOPE", detail)


def validation_error(loc: list, msg: str) -> RequestValidationError:
    """A FastAPI-shaped 422 (HTTPValidationError) for rules that can only be checked
    after merging a partial update with the stored record."""
    return RequestValidationError([{"loc": tuple(loc), "msg": msg, "type": "value_error"}])
