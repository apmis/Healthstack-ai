from fastapi import APIRouter, Depends

from app.api.deps import get_bearer_token
from app.models.schemas import ChatRequest, ChatResponse
from app.services.copilot import respond_to_chat

router = APIRouter(tags=["copilot"])


@router.post("/copilot/chat", response_model=ChatResponse)
def chat_with_copilot(
    payload: ChatRequest,
    token: str = Depends(get_bearer_token),
) -> ChatResponse:
    return respond_to_chat(token, payload)

