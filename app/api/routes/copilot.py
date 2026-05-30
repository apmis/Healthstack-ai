from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from io import BytesIO

from app.api.deps import get_bearer_token
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    ReferralNoteDocxRequest,
    ReferralNoteDraftRequest,
    ReferralNoteDraftResponse,
)
from app.services.copilot import respond_to_chat
from app.services.referral_docx import build_referral_note_docx
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


@router.post("/copilot/referral-note/docx")
def export_copilot_referral_note_docx(
    payload: ReferralNoteDocxRequest,
    token: str = Depends(get_bearer_token),
) -> StreamingResponse:
    docx_bytes, filename = build_referral_note_docx(token, payload)
    return StreamingResponse(
        BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
