import http.client
import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from app.core.config import get_settings
from app.models.schemas import ChatMessage, PatientSearchResult, RetrievedSource, SessionContext


def _format_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return None


def _source_label(source: RetrievedSource) -> str:
    parts = [source.collection, source.title or source.document_id]
    created_at = _format_datetime(source.created_at)
    if created_at:
        parts.append(created_at)
    return " | ".join(part for part in parts if part)


def _summarize_documents(documents: list[dict[str, Any]], fields: list[str], limit: int = 5) -> list[dict[str, Any]]:
    summary_items: list[dict[str, Any]] = []
    for document in documents[:limit]:
        item = {}
        for field_name in fields:
            value = document.get(field_name)
            if value not in (None, "", [], {}):
                item[field_name] = value
        if item:
            summary_items.append(item)
    return summary_items


def _build_context_payload(
    session: SessionContext,
    patient: PatientSearchResult | None,
    structured_context: dict[str, Any],
    sources: list[RetrievedSource],
) -> dict[str, Any]:
    patient_block = None
    if patient is not None:
        patient_block = {
            "patient_id": patient.patient_id,
            "full_name": patient.full_name,
            "mrn": patient.mrn,
            "hs_id": patient.hs_id,
            "gender": patient.gender,
            "dob": patient.dob,
            "phone": patient.phone,
        }

    pharmacy_inventory = structured_context.get("pharmacy_inventory") or {}

    return {
        "session": {
            "active_facility_id": session.active_facility_id,
            "active_facility_name": session.active_facility_name,
            "roles": session.roles,
            "accesslevel": session.accesslevel,
        },
        "patient": patient_block,
        "active_admission": structured_context.get("active_admission"),
        "recent_appointments": _summarize_documents(
            structured_context.get("recent_appointments", []),
            ["start_time", "appointment_reason", "appointment_status", "practitioner_name", "location_name"],
        ),
        "recent_orders": _summarize_documents(
            structured_context.get("recent_orders", []),
            ["order_category", "order", "instruction", "order_status", "treatment_status", "medication_status", "createdAt"],
        ),
        "recent_pharmacy_entries": _summarize_documents(
            structured_context.get("recent_pharmacy_entries", []),
            ["type", "source", "transactioncategory", "productitems", "createdAt"],
        ),
        "recent_lab_results": _summarize_documents(
            structured_context.get("recent_lab_results", []),
            ["documentname", "createdAt", "documentdetail"],
            limit=3,
        ),
        "recent_clinical_documents": _summarize_documents(
            structured_context.get("recent_clinical_documents", []),
            ["documentname", "createdAt", "documentdetail"],
            limit=3,
        ),
        "pharmacy_inventory": {
            "scope": pharmacy_inventory.get("scope"),
            "matched_products": _summarize_documents(
                pharmacy_inventory.get("matched_products", []),
                ["name", "generic", "category", "classification", "subclassification", "baseunit"],
                limit=5,
            ),
            "inventory_matches": _summarize_documents(
                pharmacy_inventory.get("inventory_matches", []),
                ["name", "quantity", "reorder_level", "sellingprice", "costprice", "store_name", "updatedAt"],
                limit=5,
            ),
            "recent_inventory_transactions": _summarize_documents(
                pharmacy_inventory.get("recent_inventory_transactions", []),
                ["name", "type", "transactioncategory", "quantity", "amount", "store_name", "createdAt"],
                limit=5,
            ),
            "recent_inventory_dispenses": _summarize_documents(
                pharmacy_inventory.get("recent_inventory_dispenses", []),
                ["type", "source", "transactioncategory", "productitems", "store_name", "createdAt"],
                limit=5,
            ),
            "low_stock_items": _summarize_documents(
                pharmacy_inventory.get("low_stock_items", []),
                ["name", "quantity", "reorder_level", "store_name", "updatedAt"],
                limit=5,
            ),
            "expiring_batches": _summarize_documents(
                pharmacy_inventory.get("expiring_batches", []),
                ["name", "batchNo", "quantity", "expirydate", "store_name"],
                limit=5,
            ),
        },
        "sources": [
            {
                "label": _source_label(source),
                "snippet": source.snippet,
                "score": source.score,
            }
            for source in sources
        ],
    }


def _build_admin_context_payload(
    session: SessionContext,
    structured_context: dict[str, Any],
    sources: list[RetrievedSource],
) -> dict[str, Any]:
    billing = structured_context.get("billing") or {}
    admissions = structured_context.get("admissions") or {}
    workforce = structured_context.get("workforce") or {}
    patients = structured_context.get("patients") or {}
    appointments = structured_context.get("appointments") or {}
    pharmacy_inventory = structured_context.get("pharmacy_inventory") or {}

    return {
        "session": {
            "active_facility_id": session.active_facility_id,
            "active_facility_name": session.active_facility_name,
            "roles": session.roles,
            "accesslevel": session.accesslevel,
        },
        "time_window": structured_context.get("time_window"),
        "domains": structured_context.get("domains"),
        "appointments": {
            "window_total": appointments.get("window_total"),
            "status_breakdown": appointments.get("status_breakdown"),
            "location_breakdown": appointments.get("location_breakdown"),
            "recent": _summarize_documents(
                appointments.get("recent", []),
                ["appointment_reason", "appointment_status", "start_time", "practitioner_name", "location_name"],
            ),
            "upcoming": _summarize_documents(
                appointments.get("upcoming", []),
                ["appointment_reason", "appointment_status", "start_time", "practitioner_name", "location_name"],
            ),
        },
        "billing": {
            "window_total": billing.get("window_total"),
            "totals": billing.get("totals"),
            "status_breakdown": billing.get("status_breakdown"),
            "top_services": billing.get("top_services"),
            "recent": _summarize_documents(
                billing.get("recent", []),
                ["billing_status", "createdAt", "serviceInfo", "paymentInfo", "participantInfo"],
            ),
        },
        "admissions": {
            "active_count": admissions.get("active_count"),
            "window_total": admissions.get("window_total"),
            "ward_breakdown": admissions.get("ward_breakdown"),
            "recent": _summarize_documents(
                admissions.get("recent", []),
                ["ward_name", "bed", "status", "start_time", "createdAt"],
            ),
        },
        "workforce": {
            "total_employees": workforce.get("total_employees"),
            "profession_breakdown": workforce.get("profession_breakdown"),
            "position_breakdown": workforce.get("position_breakdown"),
            "role_breakdown": workforce.get("role_breakdown"),
            "assigned_location_count": workforce.get("assigned_location_count"),
            "recent": _summarize_documents(
                workforce.get("recent", []),
                ["firstname", "lastname", "profession", "position", "department", "roles", "createdAt"],
            ),
        },
        "patients": {
            "total_patients": patients.get("total_patients"),
            "new_registrations": patients.get("new_registrations"),
            "recent": _summarize_documents(
                patients.get("recent", []),
                ["firstname", "lastname", "gender", "phone", "mrn", "hs_id", "createdAt"],
            ),
        },
        "pharmacy_inventory": {
            "scope": pharmacy_inventory.get("scope"),
            "matched_products": _summarize_documents(
                pharmacy_inventory.get("matched_products", []),
                ["name", "generic", "category", "classification", "subclassification", "baseunit"],
                limit=5,
            ),
            "inventory_matches": _summarize_documents(
                pharmacy_inventory.get("inventory_matches", []),
                ["name", "quantity", "reorder_level", "store_name", "updatedAt"],
                limit=5,
            ),
            "recent_inventory_transactions": _summarize_documents(
                pharmacy_inventory.get("recent_inventory_transactions", []),
                ["name", "type", "transactioncategory", "quantity", "amount", "store_name", "createdAt"],
                limit=5,
            ),
            "recent_inventory_dispenses": _summarize_documents(
                pharmacy_inventory.get("recent_inventory_dispenses", []),
                ["type", "source", "transactioncategory", "productitems", "store_name", "createdAt"],
                limit=5,
            ),
            "low_stock_items": _summarize_documents(
                pharmacy_inventory.get("low_stock_items", []),
                ["name", "quantity", "reorder_level", "store_name", "updatedAt"],
                limit=5,
            ),
            "expiring_batches": _summarize_documents(
                pharmacy_inventory.get("expiring_batches", []),
                ["name", "batchNo", "quantity", "expirydate", "store_name"],
                limit=5,
            ),
        },
        "sources": [
            {
                "label": _source_label(source),
                "snippet": source.snippet,
                "score": source.score,
            }
            for source in sources
        ],
    }


def _system_prompt(mode: str) -> str:
    if mode == "admin":
        return (
            "You are HealthStack Copilot, a facility-scoped hospital operations assistant for administrators. "
            "Answer only from the supplied EMR and operations context. "
            "Do not invent counts, revenue figures, appointments, bills, admissions, staffing levels, stock counts, expiry dates, or payments. "
            "If the context is insufficient, say exactly what is missing. "
            "Prefer concise operational language. "
            "When citing evidence, use inline references like [collection | title | date]."
        )
    return (
        "You are HealthStack Copilot, a facility-scoped clinical and pharmacy operations assistant for doctors. "
        "Answer only from the supplied EMR context. "
        "Do not invent facts, test results, medications, diagnoses, or timelines. "
        "Do not invent stock counts, dispensing events, batch numbers, expiry dates, or store availability. "
        "If the context is insufficient, say exactly what is missing. "
        "Prefer concise clinical language. "
        "When citing evidence, use inline references like [collection | title | date]. "
        "Treat records with missing patient or facility references as low-confidence administrative artifacts."
    )


def _build_user_prompt(
    question: str,
    session: SessionContext,
    patient: PatientSearchResult | None,
    structured_context: dict[str, Any],
    sources: list[RetrievedSource],
    mode: str,
) -> str:
    if mode == "admin":
        payload = _build_admin_context_payload(session, structured_context, sources)
    else:
        payload = _build_context_payload(session, patient, structured_context, sources)
    return (
        f"Question:\n{question}\n\n"
        "Grounded EMR context:\n"
        f"{json.dumps(payload, indent=2, default=str)}\n\n"
        "Instructions:\n"
        f"- Answer the {'administrator' if mode == 'admin' else 'doctor'}'s question directly.\n"
        "- Distinguish confirmed facts from likely inferences.\n"
        "- If there is no evidence for a requested claim, say so.\n"
        "- Mention key appointments, bills, admissions, workforce counts, inventory status, batches, expiry dates, orders, labs, and notes only if relevant.\n"
        "- End with a short 'Sources:' line if any narrative or structured evidence was used."
    )


class ChatProvider(ABC):
    @abstractmethod
    def generate(
        self,
        question: str,
        history: list[ChatMessage],
        session: SessionContext,
        patient: PatientSearchResult | None,
        structured_context: dict[str, Any],
        sources: list[RetrievedSource],
        mode: str = "clinical",
    ) -> str:
        raise NotImplementedError


class OpenAIChatProvider(ChatProvider):
    endpoint = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str, model_name: str, temperature: float):
        settings = get_settings()
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = temperature
        self.timeout_secs = max(30, settings.llm_request_timeout_secs)
        self.max_retries = max(1, settings.llm_request_retries)
        self.backoff_secs = max(0.5, settings.llm_request_backoff_secs)

    def _request_chat_completion(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model_name,
            "temperature": self.temperature,
            "messages": messages,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_secs) as response:
                    body = json.loads(response.read().decode("utf-8"))
                    choice = (body.get("choices") or [{}])[0]
                    content = ((choice.get("message") or {}).get("content") or "").strip()
                    if not content:
                        raise RuntimeError("OpenAI chat response was empty.")
                    return content
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"OpenAI chat request failed: {error_body}") from exc
            except (TimeoutError, urllib.error.URLError, http.client.RemoteDisconnected, ConnectionResetError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                time.sleep(self.backoff_secs * attempt)

        raise RuntimeError("OpenAI chat request failed after retries.") from last_error

    def generate(
        self,
        question: str,
        history: list[ChatMessage],
        session: SessionContext,
        patient: PatientSearchResult | None,
        structured_context: dict[str, Any],
        sources: list[RetrievedSource],
        mode: str = "clinical",
    ) -> str:
        messages = [{"role": "system", "content": _system_prompt(mode)}]
        for message in history[-6:]:
            messages.append({"role": message.role, "content": message.content})
        messages.append(
            {
                "role": "user",
                "content": _build_user_prompt(question, session, patient, structured_context, sources, mode),
            }
        )
        return self._request_chat_completion(messages)


class OpenRouterChatProvider(ChatProvider):
    def __init__(
        self,
        api_key: str,
        model_name: str,
        temperature: float,
        base_url: str,
        http_referer: str,
        app_title: str,
    ):
        settings = get_settings()
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = temperature
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self.http_referer = http_referer
        self.app_title = app_title
        self.timeout_secs = max(30, settings.llm_request_timeout_secs)
        self.max_retries = max(1, settings.llm_request_retries)
        self.backoff_secs = max(0.5, settings.llm_request_backoff_secs)

    def _request_chat_completion(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model_name,
            "temperature": self.temperature,
            "messages": messages,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": self.http_referer,
                "X-Title": self.app_title,
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_secs) as response:
                    body = json.loads(response.read().decode("utf-8"))
                    choice = (body.get("choices") or [{}])[0]
                    content = ((choice.get("message") or {}).get("content") or "").strip()
                    if not content:
                        raise RuntimeError("OpenRouter chat response was empty.")
                    return content
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"OpenRouter chat request failed: {error_body}") from exc
            except (TimeoutError, urllib.error.URLError, http.client.RemoteDisconnected, ConnectionResetError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                time.sleep(self.backoff_secs * attempt)

        raise RuntimeError("OpenRouter chat request failed after retries.") from last_error

    def generate(
        self,
        question: str,
        history: list[ChatMessage],
        session: SessionContext,
        patient: PatientSearchResult | None,
        structured_context: dict[str, Any],
        sources: list[RetrievedSource],
        mode: str = "clinical",
    ) -> str:
        messages = [{"role": "system", "content": _system_prompt(mode)}]
        for message in history[-6:]:
            messages.append({"role": message.role, "content": message.content})
        messages.append(
            {
                "role": "user",
                "content": _build_user_prompt(question, session, patient, structured_context, sources, mode),
            }
        )
        return self._request_chat_completion(messages)


def get_chat_provider() -> ChatProvider | None:
    settings = get_settings()
    provider = settings.llm_provider.lower()
    if provider in {"", "none"}:
        return None
    if provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when LLM_PROVIDER=openai.")
        return OpenAIChatProvider(
            api_key=settings.openai_api_key,
            model_name=settings.llm_model,
            temperature=settings.llm_temperature,
        )
    if provider == "openrouter":
        if not settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter.")
        return OpenRouterChatProvider(
            api_key=settings.openrouter_api_key,
            model_name=settings.llm_model,
            temperature=settings.llm_temperature,
            base_url=settings.openrouter_api_base,
            http_referer=settings.openrouter_http_referer,
            app_title=settings.openrouter_app_title,
        )
    raise RuntimeError(f"Unsupported LLM provider: {settings.llm_provider}")
