from fastapi import APIRouter, Depends, Query

from app.api.deps import get_bearer_token
from app.models.schemas import PatientSearchResult, PatientSummaryResponse
from app.services.context import require_session
from app.services.patient_resolver import get_patient_or_404, search_patients
from app.services.structured_retriever import build_patient_summary

router = APIRouter(tags=["patients"])


@router.get("/patients/search", response_model=list[PatientSearchResult])
def search_patient_records(
    active_facility_id: str = Query(..., min_length=1),
    query: str = Query(..., min_length=2),
    token: str = Depends(get_bearer_token),
) -> list[PatientSearchResult]:
    session = require_session(token, active_facility_id)
    return search_patients(session.active_facility_id, query)


@router.get("/patients/{patient_id}/summary", response_model=PatientSummaryResponse)
def get_patient_summary(
    patient_id: str,
    active_facility_id: str = Query(..., min_length=1),
    token: str = Depends(get_bearer_token),
) -> PatientSummaryResponse:
    session = require_session(token, active_facility_id)
    patient = get_patient_or_404(session.active_facility_id, patient_id)
    summary = build_patient_summary(session.active_facility_id, patient.patient_id)
    return PatientSummaryResponse(session=session, patient=patient, summary=summary)

