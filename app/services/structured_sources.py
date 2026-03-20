from typing import Any

from app.models.schemas import RetrievedSource
from app.services.common import keyword_score, make_snippet, object_id_to_str


def _source_from_document(
    collection: str,
    document: dict[str, Any],
    title: str | None,
    snippet_parts: list[str],
    question: str,
    base_score: float,
    created_field: str = "createdAt",
) -> RetrievedSource:
    snippet = make_snippet(" ".join(part for part in snippet_parts if part))
    title_text = title or ""
    return RetrievedSource(
        collection=collection,
        document_id=object_id_to_str(document.get("_id")) or "",
        title=title,
        created_at=document.get(created_field) or document.get("updatedAt"),
        snippet=snippet,
        score=base_score + keyword_score(question, f"{title_text} {snippet}"),
    )


def build_structured_sources(summary: dict[str, Any], question: str, limit: int = 6) -> list[RetrievedSource]:
    scored_sources: list[RetrievedSource] = []

    for appointment in summary.get("recent_appointments", [])[:2]:
        scored_sources.append(
            _source_from_document(
                "appointments",
                appointment,
                appointment.get("appointment_reason") or appointment.get("appointment_status") or "Appointment",
                [
                    appointment.get("appointment_reason") or "",
                    appointment.get("appointment_status") or "",
                    str(appointment.get("start_time") or ""),
                    appointment.get("practitioner_name") or "",
                    appointment.get("location_name") or "",
                ],
                question=question,
                base_score=0.4,
                created_field="start_time",
            )
        )

    for order in summary.get("recent_orders", [])[:2]:
        scored_sources.append(
            _source_from_document(
                "orders",
                order,
                order.get("order") or order.get("order_category") or "Order",
                [
                    order.get("order") or "",
                    order.get("order_category") or "",
                    order.get("instruction") or "",
                    order.get("order_status") or "",
                    order.get("treatment_status") or "",
                    order.get("medication_status") or "",
                ],
                question=question,
                base_score=0.5,
            )
        )

    for entry in summary.get("recent_pharmacy_entries", [])[:2]:
        item_names = ", ".join(
            item.get("name") or ""
            for item in (entry.get("productitems") or [])[:3]
            if isinstance(item, dict) and item.get("name")
        )
        scored_sources.append(
            _source_from_document(
                "productentries",
                entry,
                entry.get("type") or "Dispense",
                [
                    entry.get("type") or "",
                    entry.get("transactioncategory") or "",
                    entry.get("source") or "",
                    item_names,
                ],
                question=question,
                base_score=0.6,
            )
        )

    ranked = sorted(
        (source for source in scored_sources if source.document_id),
        key=lambda source: (source.score or 0.0, source.created_at),
        reverse=True,
    )
    return ranked[:limit]
