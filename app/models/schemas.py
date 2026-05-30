from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class FacilityOption(BaseModel):
    facility_id: str
    facility_name: str | None = None
    employee_id: str
    roles: list[str] = Field(default_factory=list)
    accesslevel: str | None = None


class SessionResolveRequest(BaseModel):
    active_facility_id: str | None = None


class SessionContext(BaseModel):
    user_id: str
    user_email: str | None = None
    employee_id: str | None = None
    active_facility_id: str | None = None
    active_facility_name: str | None = None
    roles: list[str] = Field(default_factory=list)
    accesslevel: str | None = None
    location_ids: list[str] = Field(default_factory=list)
    available_facilities: list[FacilityOption] = Field(default_factory=list)
    requires_facility_selection: bool = False


class PatientSearchResult(BaseModel):
    patient_id: str
    facility_id: str | None = None
    mrn: str | None = None
    hs_id: str | None = None
    firstname: str | None = None
    middlename: str | None = None
    lastname: str | None = None
    full_name: str
    gender: str | None = None
    dob: datetime | None = None
    phone: str | None = None
    email: str | None = None


class RetrievedSource(BaseModel):
    collection: str
    document_id: str
    title: str | None = None
    created_at: datetime | None = None
    snippet: str | None = None
    score: float | None = None


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    active_facility_id: str
    mode: Literal["clinical", "admin"] = "clinical"
    patient_id: str | None = None
    patient_query: str | None = None
    notes_limit: int | None = None
    history: list[ChatMessage] = Field(default_factory=list)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Question cannot be empty.")
        return cleaned


class ChatResponse(BaseModel):
    session: SessionContext
    mode: Literal["clinical", "admin"] = "clinical"
    patient: PatientSearchResult | None = None
    patient_candidates: list[PatientSearchResult] = Field(default_factory=list)
    answer: str
    answer_mode: str = "retrieval_fallback"
    sources: list[RetrievedSource] = Field(default_factory=list)
    structured_context: dict[str, Any] = Field(default_factory=dict)


class ReferralNoteDraftRequest(BaseModel):
    active_facility_id: str
    patient_id: str | None = None
    patient_query: str | None = None
    referral_reason: str = Field(min_length=1)
    referring_to: str | None = None
    specialty: str | None = None
    urgency: Literal["routine", "urgent", "emergency"] = "routine"
    notes_limit: int | None = None
    additional_instructions: str | None = None

    @field_validator("referral_reason", "additional_instructions", mode="before")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @model_validator(mode="after")
    def require_patient_selector(self):
        if not self.patient_id and not self.patient_query:
            raise ValueError("Provide patient_id or patient_query.")
        return self


class ReferralNoteDraftResponse(BaseModel):
    session: SessionContext
    patient: PatientSearchResult | None = None
    patient_candidates: list[PatientSearchResult] = Field(default_factory=list)
    draft_note: str | None = None
    draft_mode: str = "retrieval_fallback"
    message: str | None = None
    sources: list[RetrievedSource] = Field(default_factory=list)
    structured_context: dict[str, Any] = Field(default_factory=dict)


class ReferralNoteDocxRequest(BaseModel):
    active_facility_id: str
    patient_id: str
    final_note: str = Field(min_length=1)
    filename: str | None = None

    @field_validator("patient_id", "final_note", "filename", mode="before")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


class PatientSummaryResponse(BaseModel):
    session: SessionContext
    patient: PatientSearchResult
    summary: dict[str, Any]
