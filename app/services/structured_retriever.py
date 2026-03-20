from typing import Any

from app.core.database import get_database
from app.services.common import build_full_name, candidate_id_values, normalize_value
from app.services.patient_resolver import get_patient_document


def _facility_filter(facility_id: str) -> dict[str, Any]:
    return {"facility": {"$in": candidate_id_values(facility_id)}}


def _patient_filter(field_name: str, patient_id: str) -> dict[str, Any]:
    return {field_name: {"$in": candidate_id_values(patient_id)}}


def _fetch_recent(collection_name: str, query: dict[str, Any], sort_field: str, limit: int = 5) -> list[dict[str, Any]]:
    db = get_database()
    cursor = db[collection_name].find(query).sort(sort_field, -1).limit(limit)
    return [normalize_value(document) for document in cursor]


def _build_pharmacy_query(facility_id: str, patient_id: str, patient_name: str | None) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [
        {"sourceId": {"$in": candidate_id_values(patient_id)}},
    ]
    if patient_name:
        clauses.append({"source": patient_name})

    return {
        **_facility_filter(facility_id),
        "transactioncategory": "debit",
        "$or": clauses,
    }


def build_patient_summary(facility_id: str, patient_id: str) -> dict[str, Any]:
    patient = normalize_value(get_patient_document(facility_id, patient_id))
    facility_filter = _facility_filter(facility_id)
    patient_name = build_full_name(patient) if patient else None

    appointments = _fetch_recent(
        "appointments",
        {
            **facility_filter,
            **_patient_filter("clientId", patient_id),
        },
        "start_time",
    )
    clinical_documents = _fetch_recent(
        "clinicaldocuments",
        {
            **facility_filter,
            **_patient_filter("client", patient_id),
        },
        "createdAt",
    )
    lab_results = _fetch_recent(
        "labresults",
        {
            **facility_filter,
            **_patient_filter("client", patient_id),
        },
        "createdAt",
    )
    orders = _fetch_recent(
        "orders",
        {
            **_patient_filter("clientId", patient_id),
            "requestingdoctor_facilityId": {"$in": candidate_id_values(facility_id)},
        },
        "createdAt",
    )
    admissions = _fetch_recent(
        "admissions",
        {
            **facility_filter,
            "$or": [
                _patient_filter("client_id", patient_id),
                _patient_filter("client", patient_id),
            ],
        },
        "createdAt",
    )
    mpi_records = _fetch_recent(
        "mpis",
        {
            **facility_filter,
            **_patient_filter("client", patient_id),
        },
        "updatedAt",
        limit=1,
    )
    pharmacy_entries = _fetch_recent(
        "productentries",
        _build_pharmacy_query(facility_id, patient_id, patient_name),
        "createdAt",
    )

    active_admission = next(
        (
            admission
            for admission in admissions
            if str(admission.get("status", "")).lower() not in {"discharged", "closed", "ended"}
        ),
        None,
    )

    return {
        "patient": patient,
        "mpi": mpi_records[0] if mpi_records else None,
        "recent_appointments": appointments,
        "recent_clinical_documents": clinical_documents,
        "recent_lab_results": lab_results,
        "recent_orders": orders,
        "recent_pharmacy_entries": pharmacy_entries,
        "recent_admissions": admissions,
        "active_admission": active_admission,
    }
