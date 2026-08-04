from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader


Role = Literal["operator", "reviewer", "auditor"]


@dataclass(frozen=True, slots=True)
class Principal:
    role: Role
    actor: str


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_principal(
    request: Request,
    api_key: Annotated[str | None, Depends(api_key_header)],
) -> Principal:
    settings = request.app.state.settings
    configured = [
        (settings.operator_api_key, "operator"),
        (settings.reviewer_api_key, "reviewer"),
        (settings.auditor_api_key, "auditor"),
    ]
    usable = [(key, role) for key, role in configured if key]
    if not usable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured; set the three FENGMOU_*_API_KEY values",
        )
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-API-Key is required")
    for expected, role in usable:
        if expected and hmac.compare_digest(api_key, expected):
            return Principal(role=role, actor=f"api-key:{role}")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def require_roles(*allowed: Role):
    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{principal.role}' is not allowed for this operation",
            )
        return principal

    return dependency


AnyPrincipal = Annotated[Principal, Depends(get_principal)]
OperatorPrincipal = Annotated[Principal, Depends(require_roles("operator"))]
ReviewerPrincipal = Annotated[Principal, Depends(require_roles("reviewer"))]
OperatorReviewerPrincipal = Annotated[Principal, Depends(require_roles("operator", "reviewer"))]
AuditorPrincipal = Annotated[Principal, Depends(require_roles("operator", "reviewer", "auditor"))]
