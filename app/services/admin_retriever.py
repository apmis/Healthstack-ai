from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import get_database
from app.services.common import candidate_id_values, normalize_value, object_id_to_str
from app.services.pharmacy_retriever import build_pharmacy_context
from app.services.question_router import infer_time_window, route_admin_question


def _facility_filter(facility_id: str) -> dict[str, Any]:
    return {"facility": {"$in": candidate_id_values(facility_id)}}


def _billing_facility_filter(facility_id: str) -> dict[str, Any]:
    return {"participantInfo.billingFacility": {"$in": candidate_id_values(facility_id)}}


def _window_start(question: str) -> tuple[str, int, datetime]:
    label, days = infer_time_window(question)
    now = datetime.now(UTC)
    if label == "today":
        start_at = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_at = now - timedelta(days=days)
    return label, days, start_at


def _find_recent(
    collection_name: str,
    query: dict[str, Any],
    sort_field: str,
    *,
    limit: int = 5,
    sort_direction: int = -1,
    projection: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    db = get_database()
    cursor = db[collection_name].find(query, projection).sort(sort_field, sort_direction).limit(limit)
    return [normalize_value(document) for document in cursor]


def _count(collection_name: str, query: dict[str, Any]) -> int:
    db = get_database()
    return db[collection_name].count_documents(query)


def _group_counts(
    collection_name: str,
    match: dict[str, Any],
    field_path: str,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    db = get_database()
    pipeline = [
        {"$match": match},
        {"$group": {"_id": f"${field_path}", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    results = []
    for item in db[collection_name].aggregate(pipeline):
        value = item.get("_id")
        if value in (None, ""):
            continue
        results.append({"label": str(value), "count": int(item.get("count") or 0)})
    return results


def _summarize_appointments(facility_id: str, start_at: datetime) -> dict[str, Any]:
    base_query = {
        **_facility_filter(facility_id),
        "start_time": {"$gte": start_at},
    }
    return {
        "window_total": _count("appointments", base_query),
        "status_breakdown": _group_counts("appointments", base_query, "appointment_status"),
        "location_breakdown": _group_counts("appointments", base_query, "location_name"),
        "recent": _find_recent(
            "appointments",
            base_query,
            "start_time",
            projection={
                "appointment_reason": 1,
                "appointment_status": 1,
                "start_time": 1,
                "practitioner_name": 1,
                "location_name": 1,
                "firstname": 1,
                "lastname": 1,
            },
        ),
        "upcoming": _find_recent(
            "appointments",
            {
                **_facility_filter(facility_id),
                "start_time": {"$gte": datetime.now(UTC)},
            },
            "start_time",
            limit=5,
            sort_direction=1,
            projection={
                "appointment_reason": 1,
                "appointment_status": 1,
                "start_time": 1,
                "practitioner_name": 1,
                "location_name": 1,
                "firstname": 1,
                "lastname": 1,
            },
        ),
    }


def _summarize_billing(facility_id: str, start_at: datetime) -> dict[str, Any]:
    db = get_database()
    match = {
        **_billing_facility_filter(facility_id),
        "createdAt": {"$gte": start_at},
    }
    totals_pipeline = [
        {"$match": match},
        {
            "$project": {
                "amount_due": {"$ifNull": ["$paymentInfo.amountDue", 0]},
                "balance": {"$ifNull": ["$paymentInfo.balance", 0]},
                "paidup": {"$ifNull": ["$paymentInfo.paidup", 0]},
                "service_amount": {"$ifNull": ["$serviceInfo.amount", 0]},
            }
        },
        {
            "$group": {
                "_id": None,
                "count": {"$sum": 1},
                "total_due": {"$sum": "$amount_due"},
                "total_balance": {"$sum": "$balance"},
                "total_paid": {"$sum": "$paidup"},
                "total_service_amount": {"$sum": "$service_amount"},
            }
        },
    ]
    totals = next(db["bills"].aggregate(totals_pipeline), {})

    top_services_pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": "$serviceInfo.name",
                "count": {"$sum": 1},
                "amount": {"$sum": {"$ifNull": ["$serviceInfo.amount", 0]}},
            }
        },
        {"$sort": {"amount": -1, "count": -1}},
        {"$limit": 5},
    ]
    top_services = [
        {
            "service_name": str(item.get("_id") or "Unknown Service"),
            "count": int(item.get("count") or 0),
            "amount": float(item.get("amount") or 0),
        }
        for item in db["bills"].aggregate(top_services_pipeline)
        if item.get("_id") not in (None, "")
    ]

    return {
        "window_total": int(totals.get("count") or 0),
        "totals": {
            "amount_due": float(totals.get("total_due") or 0),
            "amount_paid": float(totals.get("total_paid") or 0),
            "outstanding_balance": float(totals.get("total_balance") or 0),
            "service_amount": float(totals.get("total_service_amount") or 0),
        },
        "status_breakdown": _group_counts("bills", match, "billing_status"),
        "top_services": top_services,
        "recent": _find_recent(
            "bills",
            match,
            "createdAt",
            projection={
                "serviceInfo.name": 1,
                "serviceInfo.amount": 1,
                "billing_status": 1,
                "paymentInfo.amountDue": 1,
                "paymentInfo.balance": 1,
                "createdAt": 1,
                "participantInfo.branch": 1,
            },
        ),
    }


def _summarize_admissions(facility_id: str, start_at: datetime) -> dict[str, Any]:
    base_query = _facility_filter(facility_id)
    recent_query = {
        **base_query,
        "createdAt": {"$gte": start_at},
    }
    active_query = {
        **base_query,
        "status": {"$nin": ["discharged", "closed", "ended", "unoccupied", "Discharged", "Closed", "Ended", "Unoccupied"]},
    }
    return {
        "active_count": _count("admissions", active_query),
        "window_total": _count("admissions", recent_query),
        "ward_breakdown": _group_counts("admissions", active_query, "ward_name"),
        "recent": _find_recent(
            "admissions",
            recent_query,
            "createdAt",
            projection={
                "ward_name": 1,
                "bed": 1,
                "status": 1,
                "start_time": 1,
                "createdAt": 1,
                "client.firstname": 1,
                "client.lastname": 1,
            },
        ),
    }


def _summarize_workforce(facility_id: str) -> dict[str, Any]:
    db = get_database()
    employees = [
        normalize_value(document)
        for document in db["employees"].find(
            _facility_filter(facility_id),
            {
                "firstname": 1,
                "lastname": 1,
                "profession": 1,
                "position": 1,
                "department": 1,
                "locations": 1,
                "roles": 1,
                "createdAt": 1,
            },
        )
    ]
    profession_counts = Counter(str(employee.get("profession") or "Unspecified") for employee in employees)
    position_counts = Counter(str(employee.get("position") or "Unspecified") for employee in employees)
    role_counts = Counter()
    location_count = 0
    for employee in employees:
        for role in employee.get("roles") or []:
            role_counts[str(role)] += 1
        location_count += len(employee.get("locations") or [])

    def _top(counter: Counter[str]) -> list[dict[str, Any]]:
        return [{"label": label, "count": count} for label, count in counter.most_common(5)]

    recent = sorted(
        employees,
        key=lambda item: item.get("createdAt") or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )[:5]

    return {
        "total_employees": len(employees),
        "profession_breakdown": _top(profession_counts),
        "position_breakdown": _top(position_counts),
        "role_breakdown": _top(role_counts),
        "assigned_location_count": location_count,
        "recent": recent,
    }


def _summarize_patients(facility_id: str, start_at: datetime) -> dict[str, Any]:
    base_query = _facility_filter(facility_id)
    recent_query = {
        **base_query,
        "createdAt": {"$gte": start_at},
    }
    return {
        "total_patients": _count("clients", base_query),
        "new_registrations": _count("clients", recent_query),
        "recent": _find_recent(
            "clients",
            recent_query,
            "createdAt",
            projection={
                "firstname": 1,
                "lastname": 1,
                "gender": 1,
                "phone": 1,
                "createdAt": 1,
                "mrn": 1,
                "hs_id": 1,
            },
        ),
    }


def _summarize_locations(facility_id: str) -> dict[str, Any]:
    locations = _find_recent(
        "locations",
        _facility_filter(facility_id),
        "updatedAt",
        limit=10,
        projection={"name": 1, "locationType": 1, "branch": 1, "updatedAt": 1},
    )
    return {
        "total_locations": _count("locations", _facility_filter(facility_id)),
        "type_breakdown": _group_counts("locations", _facility_filter(facility_id), "locationType", limit=5),
        "recent": locations[:5],
    }


def build_admin_summary(
    facility_id: str,
    question: str,
    *,
    location_ids: list[str] | None = None,
) -> dict[str, Any]:
    window_label, window_days, start_at = _window_start(question)
    domains = route_admin_question(question)

    summary: dict[str, Any] = {
        "question": question,
        "domains": domains,
        "time_window": {
            "label": window_label,
            "days": window_days,
            "start_at": start_at,
            "generated_at": datetime.now(UTC),
        },
        "scope": {
            "facility_id": facility_id,
            "location_ids": list(location_ids or []),
        },
    }

    include_all = "overview" in domains

    if include_all or "appointments" in domains:
        summary["appointments"] = _summarize_appointments(facility_id, start_at)
    if include_all or "billing" in domains:
        summary["billing"] = _summarize_billing(facility_id, start_at)
    if include_all or "admissions" in domains:
        summary["admissions"] = _summarize_admissions(facility_id, start_at)
    if include_all or "workforce" in domains:
        summary["workforce"] = _summarize_workforce(facility_id)
    if include_all or "patients" in domains:
        summary["patients"] = _summarize_patients(facility_id, start_at)
    if include_all or "appointments" in domains or "workforce" in domains:
        summary["locations"] = _summarize_locations(facility_id)
    if include_all or "inventory" in domains:
        summary["pharmacy_inventory"] = build_pharmacy_context(
            facility_id,
            question,
            location_ids=location_ids,
        )

    overview_sections = []
    for key in ("appointments", "billing", "admissions", "workforce", "patients", "pharmacy_inventory"):
        if key in summary:
            overview_sections.append(key)
    summary["overview_sections"] = overview_sections
    return summary


def _source_from_document(
    collection: str,
    document: dict[str, Any],
    title: str,
    snippet: str,
    score: float,
) -> dict[str, Any]:
    return {
        "collection": collection,
        "document_id": object_id_to_str(document.get("_id")) or "",
        "title": title,
        "created_at": document.get("createdAt") or document.get("updatedAt") or document.get("start_time"),
        "snippet": snippet,
        "score": score,
    }


def build_admin_sources(summary: dict[str, Any], question: str, *, limit: int = 6) -> list[dict[str, Any]]:
    from app.models.schemas import RetrievedSource
    from app.services.common import keyword_score, make_snippet

    sources: list[RetrievedSource] = []

    for appointment in (summary.get("appointments", {}) or {}).get("recent", [])[:2]:
        text = " ".join(
            str(value)
            for value in (
                appointment.get("appointment_reason"),
                appointment.get("appointment_status"),
                appointment.get("location_name"),
                appointment.get("practitioner_name"),
                appointment.get("start_time"),
            )
            if value not in (None, "")
        )
        source = _source_from_document(
            "appointments",
            appointment,
            appointment.get("appointment_reason") or appointment.get("appointment_status") or "Appointment",
            make_snippet(text),
            0.5 + keyword_score(question, text),
        )
        if source["document_id"]:
            sources.append(RetrievedSource(**source))

    for bill in (summary.get("billing", {}) or {}).get("recent", [])[:2]:
        text = " ".join(
            str(value)
            for value in (
                bill.get("serviceInfo", {}).get("name"),
                bill.get("billing_status"),
                bill.get("paymentInfo", {}).get("amountDue"),
                bill.get("paymentInfo", {}).get("balance"),
            )
            if value not in (None, "", {})
        )
        source = _source_from_document(
            "bills",
            bill,
            bill.get("serviceInfo", {}).get("name") or bill.get("billing_status") or "Bill",
            make_snippet(text),
            0.55 + keyword_score(question, text),
        )
        if source["document_id"]:
            sources.append(RetrievedSource(**source))

    for admission in (summary.get("admissions", {}) or {}).get("recent", [])[:2]:
        text = " ".join(
            str(value)
            for value in (
                admission.get("ward_name"),
                admission.get("bed"),
                admission.get("status"),
                admission.get("start_time"),
            )
            if value not in (None, "")
        )
        source = _source_from_document(
            "admissions",
            admission,
            admission.get("ward_name") or admission.get("status") or "Admission",
            make_snippet(text),
            0.5 + keyword_score(question, text),
        )
        if source["document_id"]:
            sources.append(RetrievedSource(**source))

    for employee in (summary.get("workforce", {}) or {}).get("recent", [])[:2]:
        text = " ".join(
            str(value)
            for value in (
                employee.get("firstname"),
                employee.get("lastname"),
                employee.get("profession"),
                employee.get("position"),
                employee.get("department"),
            )
            if value not in (None, "")
        )
        source = _source_from_document(
            "employees",
            employee,
            " ".join(
                part for part in (employee.get("firstname"), employee.get("lastname")) if part
            )
            or employee.get("profession")
            or "Employee",
            make_snippet(text),
            0.4 + keyword_score(question, text),
        )
        if source["document_id"]:
            sources.append(RetrievedSource(**source))

    from app.services.pharmacy_retriever import build_pharmacy_sources

    sources.extend(build_pharmacy_sources(summary.get("pharmacy_inventory", {}) or {}, limit=max(0, limit - len(sources))))
    ranked = sorted(sources, key=lambda item: (item.score or 0.0, item.created_at), reverse=True)
    deduplicated: list[RetrievedSource] = []
    seen: set[tuple[str, str]] = set()
    for source in ranked:
        key = (source.collection, source.document_id)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(source)
        if len(deduplicated) >= limit:
            break
    return deduplicated
