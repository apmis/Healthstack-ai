from fastapi import HTTPException, status

from app.core.config import get_settings
from app.models.schemas import ChatRequest, ChatResponse
from app.services.care_summary import generate_answer
from app.services.context import require_session
from app.services.llm import get_chat_provider
from app.services.patient_resolver import get_patient_or_404, search_patients
from app.services.pharmacy_retriever import (
    build_pharmacy_context,
    build_pharmacy_sources,
    has_pharmacy_product_match,
)
from app.services.question_router import is_inventory_question, is_pharmacy_question
from app.services.structured_retriever import build_patient_summary
from app.services.structured_sources import build_structured_sources
from app.services.vector_retriever import search_patient_narratives


def _deduplicate_sources(sources):
    unique_sources = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        key = (source.collection, source.document_id)
        if key in seen:
            continue
        seen.add(key)
        unique_sources.append(source)
    return unique_sources


def respond_to_chat(token: str, payload: ChatRequest) -> ChatResponse:
    session = require_session(token, payload.active_facility_id)
    pharmacy_intent = is_pharmacy_question(payload.question)
    inventory_intent = is_inventory_question(payload.question)
    if not inventory_intent and not payload.patient_id and not payload.patient_query:
        inventory_intent = has_pharmacy_product_match(payload.question)
    pharmacy_intent = pharmacy_intent or inventory_intent

    if not payload.patient_id and not payload.patient_query and not inventory_intent:
        return ChatResponse(
            session=session,
            answer="Provide a patient_id or patient_query before asking a patient-level clinical question. Pharmacy stock and inventory questions can run at the facility level.",
        )

    patient = None
    patient_candidates = []
    if payload.patient_id:
        patient = get_patient_or_404(session.active_facility_id, payload.patient_id)
    elif payload.patient_query:
        patient_candidates = search_patients(session.active_facility_id, payload.patient_query)
        if not patient_candidates:
            return ChatResponse(
                session=session,
                answer="No patient matched that query inside the active facility.",
            )
        if len(patient_candidates) > 1:
            return ChatResponse(
                session=session,
                patient_candidates=patient_candidates,
                answer="Multiple patients matched that query. Select one patient_id and retry the chat request.",
            )
        patient = patient_candidates[0]

    if patient is None:
        if not inventory_intent:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Patient resolution failed unexpectedly.",
            )

    summary: dict[str, object] = {}
    if patient is not None:
        summary = build_patient_summary(session.active_facility_id, patient.patient_id)

    settings = get_settings()
    notes_limit = max(1, payload.notes_limit or settings.default_notes_limit)
    sources = []
    if patient is not None:
        sources.extend(build_structured_sources(summary, question=payload.question, limit=notes_limit))
        sources.extend(
            search_patient_narratives(
                session.active_facility_id,
                patient.patient_id,
                payload.question,
                notes_limit,
            )
        )

    if inventory_intent:
        pharmacy_context = build_pharmacy_context(
            session.active_facility_id,
            payload.question,
            location_ids=session.location_ids,
        )
        summary["pharmacy_inventory"] = pharmacy_context
        sources = build_pharmacy_sources(pharmacy_context, limit=notes_limit) + sources
        sources = _deduplicate_sources(sources)

    answer_mode = "retrieval_fallback"
    answer = generate_answer(payload.question, patient, summary, sources, session=session)
    try:
        provider = get_chat_provider()
    except RuntimeError:
        provider = None
    if provider is not None:
        try:
            answer = provider.generate(
                question=payload.question,
                history=payload.history,
                session=session,
                patient=patient,
                structured_context=summary,
                sources=sources,
            )
            settings = get_settings()
            answer_mode = f"llm_{settings.llm_provider.lower()}"
        except RuntimeError:
            answer_mode = "retrieval_fallback"
    return ChatResponse(
        session=session,
        patient=patient,
        answer=answer,
        answer_mode=answer_mode,
        sources=sources,
        structured_context=summary,
    )
