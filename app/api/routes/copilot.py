from fastapi import APIRouter, Depends

from app.api.deps import get_bearer_token
from app.models.schemas import ChatRequest, ChatResponse, ReferralNoteDraftRequest, ReferralNoteDraftResponse
from app.services.copilot import respond_to_chat
from app.services.referral_note import draft_referral_note

router = APIRouter(tags=["copilot"])


@router.post("/copilot/chat", response_model=ChatResponse)
def chat_with_copilot(
    payload: ChatRequest,
    token: str = Depends(get_bearer_token),
) -> ChatResponse:
    return respond_to_chat(token, payload)


@router.post("/copilot/referral-note/draft", response_model=ReferralNoteDraftResponse)
def draft_copilot_referral_note(
    payload: ReferralNoteDraftRequest,
    token: str = Depends(get_bearer_token),
) -> ReferralNoteDraftResponse:
    return draft_referral_note(token, payload)
