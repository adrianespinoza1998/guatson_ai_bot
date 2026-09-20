"""HTTP Basic Auth for the dashboard. See docs/specs/dashboard.md.

Routes behind `require_dashboard_auth` return 404 (not 401) when DASHBOARD_PASSWORD
isn't configured — the dashboard is opt-in, never accidentally exposed on an install
that didn't set a password. `HTTPBasic(auto_error=False)` is required for that: the
default `auto_error=True` would 401 before we ever got a chance to check settings.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import Settings

DASHBOARD_USERNAME = "admin"

_security = HTTPBasic(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def require_dashboard_auth(
    settings: Settings = Depends(get_app_settings),
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> None:
    if not settings.dashboard_password:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Basic"},
    )
    if credentials is None:
        raise unauthorized

    is_valid_username = secrets.compare_digest(
        credentials.username.encode("utf-8"), DASHBOARD_USERNAME.encode("utf-8")
    )
    is_valid_password = secrets.compare_digest(
        credentials.password.encode("utf-8"), settings.dashboard_password.encode("utf-8")
    )
    if not (is_valid_username and is_valid_password):
        raise unauthorized
