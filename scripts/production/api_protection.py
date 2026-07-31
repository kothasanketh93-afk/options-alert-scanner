"""
production/api_protection.py
------------------------------
Part 9, item 7: API Protection.

Everything the dashboard API's business logic (routes/services) does
NOT have to change to get: request timeouts, a consistent JSON error
envelope, request-ID tracing, and pagination/validation helpers for
any route that returns a list.

Split into two halves:

1. Pure-Python helpers (pagination, envelope building, param
   validation) -- no FastAPI/Starlette dependency, importable and
   testable anywhere, including this offline sandbox.
2. ASGI middleware (TimeoutMiddleware, RequestIDMiddleware) -- these
   need Starlette, which only the dashboard host installs (see
   requirements-dashboard.txt). Imported lazily / guarded so this
   module doesn't fail to import in an environment without it; the
   scanner process never imports this module at all.

Nothing in app/backend/api/routes/*.py or services/*.py is required to
change -- app.py opts in by adding the two middlewares and swapping
its existing bare `except Exception` handler's response body for the
shared envelope shape (same status code, same information, just a
consistent `error` key other clients can rely on instead of a
per-route-dependent shape).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    _STARLETTE_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised where starlette isn't installed
    BaseHTTPMiddleware = object  # type: ignore[assignment,misc]
    _STARLETTE_AVAILABLE = False


DEFAULT_MAX_PAGE_SIZE = 200


# --------------------------------------------------------------------
# 1. Pure-Python helpers
# --------------------------------------------------------------------

def error_envelope(status_code: int, error_type: str, message: str,
                    request_id: Optional[str] = None) -> Dict[str, Any]:
    """The one JSON error shape every protected route/middleware
    returns, so a client can always do `body["error"]["message"]`
    regardless of which layer raised it."""
    return {
        "error": {
            "type": error_type,
            "message": message,
            "request_id": request_id,
        },
        "status_code": status_code,
    }


@dataclass(frozen=True)
class PaginationParams:
    page: int
    page_size: int


class PaginationError(ValueError):
    """Raised by validate_pagination_params on out-of-range input.
    Callers on a FastAPI route should catch this and raise
    HTTPException(400, str(exc)) -- kept as a plain ValueError subclass
    here so this module doesn't need FastAPI just to validate params."""


def validate_pagination_params(page: int, page_size: int,
                                max_page_size: int = DEFAULT_MAX_PAGE_SIZE) -> PaginationParams:
    if page < 1:
        raise PaginationError(f"page must be >= 1, got {page}.")
    if page_size < 1:
        raise PaginationError(f"page_size must be >= 1, got {page_size}.")
    if page_size > max_page_size:
        raise PaginationError(f"page_size must be <= {max_page_size}, got {page_size}.")
    return PaginationParams(page=page, page_size=page_size)


def paginate(items: Sequence[Any], page: int, page_size: int,
             max_page_size: int = DEFAULT_MAX_PAGE_SIZE) -> Dict[str, Any]:
    """Slices `items` (already-fetched, in-memory -- this project's
    lists are small enough that this is fine; a DB-backed route would
    push page/page_size into the query instead) and returns a
    consistent envelope: {items, page, page_size, total, total_pages}."""
    params = validate_pagination_params(page, page_size, max_page_size)
    total = len(items)
    total_pages = max(1, (total + params.page_size - 1) // params.page_size)
    start = (params.page - 1) * params.page_size
    end = start + params.page_size
    return {
        "items": list(items[start:end]),
        "page": params.page,
        "page_size": params.page_size,
        "total": total,
        "total_pages": total_pages,
    }


# --------------------------------------------------------------------
# 2. ASGI middleware (requires Starlette/FastAPI)
# --------------------------------------------------------------------

if _STARLETTE_AVAILABLE:

    class RequestIDMiddleware(BaseHTTPMiddleware):
        """Attaches a unique request ID to every request (available to
        route handlers via request.state.request_id) and echoes it back
        as an X-Request-ID response header -- lets a client correlate a
        500 they saw with a specific line in the server's error logs."""

        async def dispatch(self, request: Request, call_next):
            request_id = str(uuid.uuid4())
            request.state.request_id = request_id
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response

    class TimeoutMiddleware(BaseHTTPMiddleware):
        """Bounds how long any single request is allowed to run. This
        is a read-only monitoring API over local files/state -- every
        route should complete in well under a second; a request that
        doesn't is far more likely stuck (a hung git pull inside
        replay_service, a blocked file read) than doing legitimate slow
        work, so returning 504 and freeing the connection is the safer
        default over hanging indefinitely."""

        def __init__(self, app, timeout_seconds: float = 10.0):
            super().__init__(app)
            self.timeout_seconds = timeout_seconds

        async def dispatch(self, request: Request, call_next):
            import asyncio
            try:
                return await asyncio.wait_for(call_next(request), timeout=self.timeout_seconds)
            except asyncio.TimeoutError:
                request_id = getattr(request.state, "request_id", None)
                return JSONResponse(
                    status_code=504,
                    content=error_envelope(
                        504, "RequestTimeout",
                        f"request exceeded {self.timeout_seconds}s and was aborted.",
                        request_id=request_id,
                    ),
                )

else:  # pragma: no cover - only reached where starlette isn't installed
    RequestIDMiddleware = None  # type: ignore[assignment]
    TimeoutMiddleware = None  # type: ignore[assignment]
