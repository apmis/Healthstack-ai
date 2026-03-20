from fastapi import APIRouter, Depends

from app.api.deps import get_bearer_token
from app.models.schemas import SessionContext, SessionResolveRequest
from app.services.context import resolve_session

router = APIRouter(tags=["session"])


@router.post("/session/resolve", response_model=SessionContext)
def resolve_copilot_session(
    payload: SessionResolveRequest,
    token: str = Depends(get_bearer_token),
) -> SessionContext:
    return resolve_session(token, payload.active_facility_id)

