import re
from typing import Any

from fastapi import HTTPException, status

from app.core.config import get_settings
from app.core.database import get_database
from app.models.schemas import PatientSearchResult
from app.services.common import (
    build_full_name,
    candidate_id_values,
    keyword_score,
    object_id_to_str,
)


def _facility_filter(facility_id: str) -> dict[str, Any]:
    return {"facility": {"$in": candidate_id_values(facility_id)}}


def _build_patient_result(document: dict[str, Any]) -> PatientSearchResult:
    return PatientSearchResult(
        patient_id=object_id_to_str(document.get("_id")) or "",
        facility_id=object_id_to_str(document.get("facility")),
        mrn=document.get("mrn"),
        hs_id=document.get("hs_id"),
        firstname=document.get("firstname"),
        middlename=document.get("middlename"),
        lastname=document.get("lastname"),
        full_name=build_full_name(document),
        gender=document.get("gender"),
        dob=document.get("dob"),
        phone=document.get("phone"),
        email=document.get("email"),
    )


def _search_clients(facility_id: str, query: str, limit: int) -> list[dict[str, Any]]:
    db = get_database()
    regex = {"$regex": re.escape(query), "$options": "i"}
    cursor = db["clients"].find(
        {
            **_facility_filter(facility_id),
            "$or": [
                {"firstname": regex},
                {"middlename": regex},
                {"lastname": regex},
                {"mrn": regex},
                {"phone": regex},
                {"email": regex},
                {"hs_id": regex},
            ],
        }
    ).sort("updatedAt", -1).limit(limit)
    return list(cursor)


def _search_mpis(facility_id: str, query: str, limit: int) -> list[dict[str, Any]]:
    db = get_database()
    regex = {"$regex": re.escape(query), "$options": "i"}
    mpi_docs = list(
        db["mpis"].find(
            {
                **_facility_filter(facility_id),
                "$or": [{"mrn": regex}, {"clientTags.tagName": regex}],
            }
        ).limit(limit)
    )
    client_ids: list[Any] = []
    for mpi_doc in mpi_docs:
        client_value = mpi_doc.get("client")
        if client_value is None:
            continue
        for candidate in candidate_id_values(str(client_value)):
            if candidate not in client_ids:
                client_ids.append(candidate)
    if not client_ids:
        return []
    return list(db["clients"].find({"_id": {"$in": client_ids}}).limit(limit))


def _rank_patients(query: str, documents: list[dict[str, Any]]) -> list[PatientSearchResult]:
    scored: list[tuple[float, PatientSearchResult]] = []
    for document in documents:
        result = _build_patient_result(document)
        text = " ".join(
            item
            for item in [
                result.full_name,
                result.mrn or "",
                result.phone or "",
                result.email or "",
                result.hs_id or "",
            ]
            if item
        )
        score = keyword_score(query, text)
        if query.lower() in result.full_name.lower():
            score += 0.5
        if result.mrn and query.lower() == result.mrn.lower():
            score += 1.0
        scored.append((score, result))

    ranked = sorted(
        scored,
        key=lambda item: (item[0], item[1].full_name.lower()),
        reverse=True,
    )

    unique_results: list[PatientSearchResult] = []
    seen: set[str] = set()
    for _, result in ranked:
        if result.patient_id in seen:
            continue
        seen.add(result.patient_id)
        unique_results.append(result)
    return unique_results


def search_patients(
    facility_id: str,
    query: str,
    limit: int | None = None,
) -> list[PatientSearchResult]:
    settings = get_settings()
    capped_limit = max(1, min(limit or settings.max_patient_results, settings.max_patient_results))
    client_documents = _search_clients(facility_id, query, capped_limit * 3)
    if not client_documents:
        client_documents = _search_mpis(facility_id, query, capped_limit * 3)

    ranked = _rank_patients(query, client_documents)
    return ranked[:capped_limit]


def get_patient_document(facility_id: str, patient_id: str) -> dict[str, Any]:
    db = get_database()
    document = db["clients"].find_one(
        {
            **_facility_filter(facility_id),
            "_id": {"$in": candidate_id_values(patient_id)},
        }
    )
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient was not found in the active facility.",
        )
    return document


def get_patient_or_404(facility_id: str, patient_id: str) -> PatientSearchResult:
    return _build_patient_result(get_patient_document(facility_id, patient_id))
