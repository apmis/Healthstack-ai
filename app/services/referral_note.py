from datetime import datetime
from typing import Any

from app.core.config import get_settings
from app.models.schemas import ReferralNoteDraftRequest, ReferralNoteDraftResponse, RetrievedSource
from app.services.common import make_snippet, render_structured_text
from app.services.context import require_session
from app.services.llm import get_chat_provider
from app.services.patient_resolver import get_patient_or_404, search_patients
from app.services.structured_retriever import build_patient_summary
from app.services.structured_sources import build_structured_sources
from app.services.vector_retriever import search_patient_narratives


def _format_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if value:
        return str(value)
    return "Not available"


def _source_label(source: RetrievedSource) -> str:
    parts = [source.collection, source.title or source.document_id]
    if source.created_at:
        parts.append(_format_date(source.created_at))
    return " | ".join(part for part in parts if part)


def _summarize_items(items: list[dict[str, Any]], fields: list[str], limit: int = 3) -> list[str]:
    summaries: list[str] = []
    for item in items[:limit]:
        parts = [
            render_structured_text(item.get(field), field)
            for field in fields
            if item.get(field) not in (None, "", [], {})
        ]
        if parts:
            summaries.append(make_snippet("; ".join(parts), length=360))
    return summaries


def _fallback_referral_note(
    request: ReferralNoteDraftRequest,
    patient,
    session,
    summary: dict[str, Any],
    sources: list[RetrievedSource],
) -> str:
    appointments = _summarize_items(
        summary.get("recent_appointments", []),
        ["start_time", "appointment_reason", "appointment_status", "practitioner_name"],
    )
    orders = _summarize_items(
        summary.get("recent_orders", []),
        ["order_category", "order", "instruction", "order_status", "createdAt"],
    )
    labs = _summarize_items(
        summary.get("recent_lab_results", []),
        ["documentname", "createdAt", "documentdetail"],
        limit=2,
    )
    notes = _summarize_items(
        summary.get("recent_clinical_documents", []),
        ["documentname", "createdAt", "documentdetail"],
        limit=2,
    )
    source_line = "; ".join(_source_label(source) for source in sources[:6]) or "No specific source evidence available."
    today = datetime.utcnow().strftime("%Y-%m-%d")

    request_sentence = request.referral_reason.rstrip(".")

    return "\n".join(
        [
            "Referral Note Draft",
            "",
            f"Date: {today}",
            f"Referring facility: {session.active_facility_name or session.active_facility_id or 'Not available'}",
            f"Patient: {patient.full_name}",
            f"Patient identifiers: MRN {patient.mrn or 'Not available'}; HS ID {patient.hs_id or 'Not available'}; Patient ID {patient.patient_id}",
            f"Referral urgency: {request.urgency}",
            f"Referred to: {request.referring_to or request.specialty or 'Not specified'}",
            "",
            f"Reason for referral: {request.referral_reason}",
            "",
            "Relevant clinical summary:",
            *(f"- {item}" for item in (notes or ["No recent clinical-note details were available in the retrieved context."])),
            "",
            "Relevant investigations and results:",
            *(f"- {item}" for item in (labs or ["No recent lab-result details were available in the retrieved context."])),
            "",
            "Current treatments, orders, or medications:",
            *(f"- {item}" for item in (orders or ["No recent treatment/order details were available in the retrieved context."])),
            "",
            "Recent appointments:",
            *(f"- {item}" for item in (appointments or ["No recent appointment details were available in the retrieved context."])),
            "",
            "Request to receiving clinician:",
            f"Please assess and manage this patient for: {request_sentence}.",
            "",
            "Missing information or caveats:",
            "- This is an AI-generated draft and should be reviewed, edited, and signed by the referring clinician before use.",
            "",
            f"Sources: {source_line}",
        ]
    )


def draft_referral_note(token: str, request: ReferralNoteDraftRequest) -> ReferralNoteDraftResponse:
    session = require_session(token, request.active_facility_id)

    patient = None
    patient_candidates = []
    if request.patient_id:
        patient = get_patient_or_404(session.active_facility_id, request.patient_id)
    elif request.patient_query:
        patient_candidates = search_patients(session.active_facility_id, request.patient_query)
        if not patient_candidates:
            return ReferralNoteDraftResponse(
                session=session,
                message="No patient matched that query inside the active facility.",
            )
        if len(patient_candidates) > 1:
            return ReferralNoteDraftResponse(
                session=session,
                patient_candidates=patient_candidates,
                message="Multiple patients matched that query. Select one patient_id and retry the referral draft request.",
            )
        patient = patient_candidates[0]

    settings = get_settings()
    notes_limit = max(1, request.notes_limit or settings.default_notes_limit)
    summary = build_patient_summary(session.active_facility_id, patient.patient_id)
    retrieval_query = " ".join(
        part
        for part in [
            "referral note",
            request.referral_reason,
            request.specialty or "",
            request.additional_instructions or "",
        ]
        if part
    )
    sources = build_structured_sources(summary, retrieval_query, limit=notes_limit)
    sources.extend(
        search_patient_narratives(
            session.active_facility_id,
            patient.patient_id,
            retrieval_query,
            notes_limit,
        )
    )

    draft_mode = "retrieval_fallback"
    draft_note = _fallback_referral_note(request, patient, session, summary, sources)
    try:
        provider = get_chat_provider()
    except RuntimeError:
        provider = None
    if provider is not None:
        try:
            draft_note = provider.generate_referral_note(
                request=request,
                session=session,
                patient=patient,
                structured_context=summary,
                sources=sources,
            )
            draft_mode = f"llm_{settings.llm_provider.lower()}"
        except RuntimeError:
            draft_mode = "retrieval_fallback"

    return ReferralNoteDraftResponse(
        session=session,
        patient=patient,
        draft_note=draft_note,
        draft_mode=draft_mode,
        sources=sources,
        structured_context=summary,
    )
