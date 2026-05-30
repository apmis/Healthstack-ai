import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import streamlit as st


DEFAULT_API_BASE_URL = "http://127.0.0.1:8010"
DEFAULT_BACKEND_AUTH_URL = "https://backend.healthstack.africa/authentication"


def _init_state() -> None:
    defaults = {
        "api_base_url": DEFAULT_API_BASE_URL,
        "backend_auth_url": DEFAULT_BACKEND_AUTH_URL,
        "backend_email": "",
        "backend_password": "",
        "jwt_token": "",
        "last_auth_response": None,
        "resolved_session": None,
        "selected_facility_id": "",
        "patient_results": [],
        "selected_patient_id": "",
        "selected_patient_label": "",
        "patient_summary": None,
        "chat_mode": "clinical",
        "chat_history": [],
        "last_chat_response": None,
        "referral_response": None,
        "referral_final_note": "",
        "referral_docx_bytes": None,
        "referral_docx_filename": "referral_note.docx",
        "last_error": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _http_request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[bool, int, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8")
            return True, response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(error_body)
        except json.JSONDecodeError:
            parsed = {"detail": error_body}
        return False, exc.code, parsed
    except urllib.error.URLError as exc:
        return False, 0, {"detail": str(exc.reason)}


def _api_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[bool, int, Any]:
    base_url = st.session_state.api_base_url.rstrip("/")
    url = f"{base_url}{path}"
    if params:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value not in (None, "")})
        if query:
            url = f"{url}?{query}"
    return _http_request(method, url, token=token, payload=payload)


def _api_binary_request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[bool, int, bytes | Any, dict[str, str]]:
    base_url = st.session_state.api_base_url.rstrip("/")
    url = f"{base_url}{path}"
    data = None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return True, response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(error_body)
        except json.JSONDecodeError:
            parsed = {"detail": error_body}
        return False, exc.code, parsed, dict(exc.headers)
    except urllib.error.URLError as exc:
        return False, 0, {"detail": str(exc.reason)}, {}


def _filename_from_content_disposition(header_value: str | None) -> str | None:
    if not header_value:
        return None
    for part in header_value.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "filename" and value:
            return value.strip('"')
    return None


def _facility_options() -> list[dict[str, Any]]:
    session = st.session_state.resolved_session or {}
    return session.get("available_facilities", []) or []


def _active_facility_id() -> str:
    if st.session_state.selected_facility_id:
        return st.session_state.selected_facility_id
    session = st.session_state.resolved_session or {}
    return session.get("active_facility_id") or ""


def _active_facility_label() -> str:
    facility_id = _active_facility_id()
    if not facility_id:
        return "No active facility"
    session = st.session_state.resolved_session or {}
    if session.get("active_facility_id") == facility_id:
        return session.get("active_facility_name") or facility_id
    for option in _facility_options():
        if option.get("facility_id") == facility_id:
            return option.get("facility_name") or facility_id
    return facility_id


def _patient_label(patient: dict[str, Any]) -> str:
    full_name = patient.get("full_name") or "Unknown Patient"
    mrn = patient.get("mrn") or "No MRN"
    patient_id = patient.get("patient_id") or ""
    return f"{full_name} | MRN: {mrn} | {patient_id}"


def _current_patient_id() -> str:
    return st.session_state.selected_patient_id or ""


def _render_sidebar() -> None:
    st.sidebar.header("Connection")
    st.session_state.api_base_url = st.sidebar.text_input("API Base URL", value=st.session_state.api_base_url)
    st.session_state.backend_auth_url = st.sidebar.text_input(
        "Backend Auth URL",
        value=st.session_state.backend_auth_url,
        help="EMR login endpoint used to fetch a JWT for copilot testing.",
    )
    st.sidebar.caption("EMR Login")
    st.session_state.backend_email = st.sidebar.text_input(
        "Email",
        value=st.session_state.backend_email,
        key="backend_email_input",
    )
    st.session_state.backend_password = st.sidebar.text_input(
        "Password",
        value=st.session_state.backend_password,
        type="password",
        key="backend_password_input",
    )
    if st.sidebar.button("Login via Backend", use_container_width=True):
        _login_via_backend()
    st.session_state.jwt_token = st.sidebar.text_area(
        "Bearer JWT",
        value=st.session_state.jwt_token,
        height=120,
        help="Paste the JWT returned by the EMR authentication flow.",
    )

    if st.sidebar.button("Health Check", use_container_width=True):
        ok, status_code, body = _api_request("GET", "/health")
        if ok:
            st.sidebar.success(f"API ok ({status_code})")
            st.sidebar.json(body)
        else:
            st.sidebar.error(f"Health check failed ({status_code})")
            st.sidebar.json(body)

    st.sidebar.divider()
    if st.session_state.last_auth_response:
        user_block = st.session_state.last_auth_response.get("user") or {}
        user_label = user_block.get("email") or user_block.get("name") or user_block.get("_id")
        if user_label:
            st.sidebar.caption(f"Logged in as: {user_label}")
    st.sidebar.caption(f"Active facility: {_active_facility_label()}")
    current_patient_id = _current_patient_id()
    if current_patient_id:
        st.sidebar.caption(f"Selected patient: {current_patient_id}")


def _login_via_backend() -> None:
    auth_url = st.session_state.backend_auth_url.strip()
    email = st.session_state.backend_email.strip()
    password = st.session_state.backend_password
    if not auth_url:
        st.error("Provide the backend authentication URL first.")
        return
    if not email or not password:
        st.error("Provide backend email and password first.")
        return

    ok, status_code, body = _http_request(
        "POST",
        auth_url,
        payload={
            "strategy": "local",
            "email": email,
            "password": password,
        },
    )
    if not ok:
        st.error(f"Backend login failed ({status_code})")
        st.json(body)
        return

    access_token = body.get("accessToken") or body.get("access_token")
    if not access_token:
        st.error("Backend login succeeded but no access token was returned.")
        st.json(body)
        return

    st.session_state.jwt_token = access_token
    st.session_state.last_auth_response = body
    st.success("Backend login succeeded. JWT loaded into the tester.")


def _resolve_session(active_facility_id: str | None = None) -> None:
    if not st.session_state.jwt_token.strip():
        st.error("Provide a Bearer JWT first.")
        return
    ok, status_code, body = _api_request(
        "POST",
        "/api/v1/session/resolve",
        token=st.session_state.jwt_token,
        payload={"active_facility_id": active_facility_id},
    )
    if not ok:
        st.error(f"Session resolve failed ({status_code})")
        st.json(body)
        return

    st.session_state.resolved_session = body
    st.session_state.selected_facility_id = body.get("active_facility_id") or active_facility_id or ""
    st.success("Session resolved.")


def _render_session_tab() -> None:
    st.subheader("Session")
    st.write("Resolve the copilot session from the EMR JWT and activate a facility.")

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Resolve Session", use_container_width=True):
            _resolve_session()

    session = st.session_state.resolved_session
    if session:
        st.json(session)
        options = _facility_options()
        if options:
            option_map = {
                f"{item.get('facility_name') or item.get('facility_id')} | {item.get('facility_id')}": item.get("facility_id")
                for item in options
            }
            current_value = _active_facility_id()
            default_index = 0
            values = list(option_map.values())
            if current_value in values:
                default_index = values.index(current_value)
            selected_label = st.selectbox("Available facilities", list(option_map.keys()), index=default_index)
            chosen_facility_id = option_map[selected_label]
            st.session_state.selected_facility_id = chosen_facility_id
            with col2:
                if st.button("Activate Facility", use_container_width=True):
                    _resolve_session(chosen_facility_id)
    else:
        st.info("No session resolved yet.")


def _render_patient_search_tab() -> None:
    st.subheader("Patient Search")
    active_facility_id = _active_facility_id()
    if not active_facility_id:
        st.warning("Resolve the session and activate a facility first.")
        return

    with st.form("patient_search_form"):
        query = st.text_input("Patient query", placeholder="Name, MRN, phone, hs_id")
        submitted = st.form_submit_button("Search")
    if submitted:
        ok, status_code, body = _api_request(
            "GET",
            "/api/v1/patients/search",
            token=st.session_state.jwt_token,
            params={"active_facility_id": active_facility_id, "query": query},
        )
        if not ok:
            st.error(f"Patient search failed ({status_code})")
            st.json(body)
        else:
            st.session_state.patient_results = body
            if body:
                st.success(f"Found {len(body)} patient(s).")
            else:
                st.info("No patients matched.")

    results = st.session_state.patient_results
    if results:
        option_map = {_patient_label(patient): patient.get("patient_id") for patient in results}
        labels = list(option_map.keys())
        default_index = 0
        if st.session_state.selected_patient_label in option_map:
            default_index = labels.index(st.session_state.selected_patient_label)
        selected_label = st.selectbox("Search results", labels, index=default_index)
        st.session_state.selected_patient_label = selected_label
        st.session_state.selected_patient_id = option_map[selected_label]
        st.dataframe(results, use_container_width=True)


def _render_summary_tab() -> None:
    st.subheader("Patient Summary")
    active_facility_id = _active_facility_id()
    if not active_facility_id:
        st.warning("Resolve the session and activate a facility first.")
        return

    patient_id = st.text_input("Patient ID", value=_current_patient_id(), key="summary_patient_id")
    if st.button("Fetch Summary", use_container_width=True):
        if not patient_id.strip():
            st.error("Provide a patient_id.")
        else:
            ok, status_code, body = _api_request(
                "GET",
                f"/api/v1/patients/{patient_id.strip()}/summary",
                token=st.session_state.jwt_token,
                params={"active_facility_id": active_facility_id},
            )
            if not ok:
                st.error(f"Summary fetch failed ({status_code})")
                st.json(body)
            else:
                st.session_state.patient_summary = body
                st.session_state.selected_patient_id = patient_id.strip()

    if st.session_state.patient_summary:
        st.json(st.session_state.patient_summary)


def _render_sources(sources: list[dict[str, Any]]) -> None:
    if not sources:
        st.caption("No sources returned.")
        return
    for index, source in enumerate(sources, start=1):
        title = source.get("title") or source.get("document_id") or f"Source {index}"
        label = f"{index}. {source.get('collection')} | {title}"
        with st.expander(label):
            st.json(source)


def _render_chat_tab() -> None:
    st.subheader("Copilot Chat")
    active_facility_id = _active_facility_id()
    if not active_facility_id:
        st.warning("Resolve the session and activate a facility first.")
        return

    mode = st.selectbox(
        "Copilot mode",
        options=["clinical", "admin"],
        index=0 if st.session_state.chat_mode == "clinical" else 1,
        help="Clinical mode is patient-first. Admin mode is facility-first for operations, billing, admissions, staff, and inventory.",
    )
    st.session_state.chat_mode = mode

    if mode == "clinical":
        col1, col2 = st.columns([2, 1])
        with col1:
            patient_id = st.text_input(
                "Patient ID (optional for pharmacy inventory questions)",
                value=_current_patient_id(),
                key="chat_patient_id",
            )
        with col2:
            notes_limit = st.number_input("Notes limit", min_value=1, max_value=10, value=5, step=1)

        patient_query = st.text_input(
            "Patient query fallback (optional)",
            placeholder="Use this if you want the API to resolve the patient from text",
            key="chat_patient_query",
        )
        st.caption("Clinical mode supports patient summaries, labs, notes, orders, appointments, and patient-linked pharmacy questions.")
    else:
        notes_limit = st.number_input("Sources limit", min_value=1, max_value=10, value=5, step=1)
        patient_id = ""
        patient_query = ""
        st.caption(
            "Admin mode is facility-scoped and works without a patient ID. Try questions about appointments, billing, admissions, staff, or inventory."
        )
        st.markdown(
            "- `What is today's appointment load and status mix?`\n"
            "- `Give me the revenue and outstanding bills summary for this month.`\n"
            "- `How many active admissions do we have and which wards are busiest?`\n"
            "- `Which drugs are low in stock right now?`\n"
            "- `What does the workforce breakdown look like in this facility?`"
        )

    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    chat_prompt = st.chat_input(
        "Ask a clinical, pharmacy, or admin operations question"
    )
    if st.button("Clear Chat", use_container_width=True):
        st.session_state.chat_history = []
        st.session_state.last_chat_response = None
        st.rerun()

    if chat_prompt:
        user_message = {"role": "user", "content": chat_prompt}
        st.session_state.chat_history.append(user_message)

        payload = {
            "question": chat_prompt,
            "active_facility_id": active_facility_id,
            "mode": mode,
            "notes_limit": int(notes_limit),
            "history": st.session_state.chat_history[:-1],
        }
        if mode == "clinical" and patient_id.strip():
            payload["patient_id"] = patient_id.strip()
            st.session_state.selected_patient_id = patient_id.strip()
        elif mode == "clinical" and patient_query.strip():
            payload["patient_query"] = patient_query.strip()

        ok, status_code, body = _api_request(
            "POST",
            "/api/v1/copilot/chat",
            token=st.session_state.jwt_token,
            payload=payload,
        )
        if not ok:
            assistant_text = f"Request failed ({status_code})."
            st.error(assistant_text)
            st.json(body)
            st.session_state.chat_history.append({"role": "assistant", "content": assistant_text})
        else:
            assistant_text = body.get("answer") or ""
            st.session_state.last_chat_response = body
            st.session_state.chat_history.append({"role": "assistant", "content": assistant_text})
            patient_block = body.get("patient") or {}
            if patient_block.get("patient_id"):
                st.session_state.selected_patient_id = patient_block["patient_id"]
        st.rerun()

    response = st.session_state.last_chat_response
    if response:
        st.caption(f"Answer mode: {response.get('answer_mode', 'unknown')}")
        if response.get("patient_candidates"):
            st.warning("Multiple patients matched. Pick a patient_id and retry.")
            st.json(response["patient_candidates"])
        with st.expander("Sources", expanded=True):
            _render_sources(response.get("sources") or [])
        with st.expander("Structured Context"):
            st.json(response.get("structured_context") or {})
        with st.expander("Raw Response"):
            st.json(response)


def _render_referral_note_tab() -> None:
    st.subheader("Referral Note")
    active_facility_id = _active_facility_id()
    if not active_facility_id:
        st.warning("Resolve the session and activate a facility first.")
        return

    st.caption("Draft the referral note, edit it, then export the final doctor-approved text as a Word document.")
    with st.form("referral_note_form"):
        col1, col2 = st.columns([1, 1])
        with col1:
            patient_id = st.text_input("Patient ID", value=_current_patient_id(), key="referral_patient_id")
            patient_query = st.text_input(
                "Patient query fallback",
                placeholder="Use this only if patient_id is empty",
                key="referral_patient_query",
            )
            referral_reason = st.text_area(
                "Referral reason",
                placeholder="Example: recurrent chest pain and abnormal ECG requiring cardiology review",
                key="referral_reason",
            )
        with col2:
            referring_to = st.text_input("Referring to", placeholder="Receiving clinician/facility", key="referring_to")
            specialty = st.text_input("Specialty", placeholder="Cardiology, nephrology, etc.", key="referral_specialty")
            urgency = st.selectbox("Urgency", options=["routine", "urgent", "emergency"], key="referral_urgency")
            notes_limit = st.number_input("Sources limit", min_value=1, max_value=10, value=5, step=1)
        additional_instructions = st.text_area(
            "Additional instructions",
            placeholder="Optional instructions for the draft",
            key="referral_additional_instructions",
        )
        submitted = st.form_submit_button("Draft Referral Note", use_container_width=True)

    if submitted:
        if not referral_reason.strip():
            st.error("Provide a referral reason.")
        elif not patient_id.strip() and not patient_query.strip():
            st.error("Provide a patient ID or patient query.")
        else:
            payload = {
                "active_facility_id": active_facility_id,
                "referral_reason": referral_reason.strip(),
                "referring_to": referring_to.strip() or None,
                "specialty": specialty.strip() or None,
                "urgency": urgency,
                "notes_limit": int(notes_limit),
                "additional_instructions": additional_instructions.strip() or None,
            }
            if patient_id.strip():
                payload["patient_id"] = patient_id.strip()
            else:
                payload["patient_query"] = patient_query.strip()

            ok, status_code, body = _api_request(
                "POST",
                "/api/v1/copilot/referral-note/draft",
                token=st.session_state.jwt_token,
                payload=payload,
            )
            if not ok:
                st.error(f"Referral draft failed ({status_code})")
                st.json(body)
            else:
                st.session_state.referral_response = body
                st.session_state.referral_final_note = body.get("draft_note") or ""
                st.session_state.referral_docx_bytes = None
                patient_block = body.get("patient") or {}
                if patient_block.get("patient_id"):
                    st.session_state.selected_patient_id = patient_block["patient_id"]

    response = st.session_state.referral_response
    if not response:
        return

    if response.get("message"):
        st.info(response["message"])
    if response.get("patient_candidates"):
        st.warning("Multiple patients matched. Select one patient_id and draft again.")
        st.json(response["patient_candidates"])
        return

    patient_block = response.get("patient") or {}
    resolved_patient_id = patient_block.get("patient_id") or _current_patient_id()
    if response.get("draft_note"):
        st.caption(f"Draft mode: {response.get('draft_mode', 'unknown')}")
        st.session_state.referral_final_note = st.text_area(
            "Editable final referral note",
            value=st.session_state.referral_final_note,
            height=520,
            key="referral_final_note_editor",
        )
        filename = st.text_input(
            "DOCX filename",
            value=st.session_state.referral_docx_filename,
            key="referral_docx_filename_input",
        )
        if st.button("Generate DOCX", use_container_width=True):
            if not resolved_patient_id:
                st.error("No resolved patient_id is available for export.")
            elif not st.session_state.referral_final_note.strip():
                st.error("Final referral note cannot be empty.")
            else:
                export_payload = {
                    "active_facility_id": active_facility_id,
                    "patient_id": resolved_patient_id,
                    "final_note": st.session_state.referral_final_note.strip(),
                    "filename": filename.strip() or None,
                }
                ok, status_code, body, headers = _api_binary_request(
                    "POST",
                    "/api/v1/copilot/referral-note/docx",
                    token=st.session_state.jwt_token,
                    payload=export_payload,
                )
                if not ok:
                    st.error(f"DOCX export failed ({status_code})")
                    st.json(body)
                else:
                    st.session_state.referral_docx_bytes = body
                    st.session_state.referral_docx_filename = (
                        _filename_from_content_disposition(headers.get("Content-Disposition"))
                        or filename
                        or "referral_note.docx"
                    )
                    st.success("DOCX generated.")

        if st.session_state.referral_docx_bytes:
            st.download_button(
                "Download Referral DOCX",
                data=st.session_state.referral_docx_bytes,
                file_name=st.session_state.referral_docx_filename,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )

        with st.expander("Sources", expanded=False):
            _render_sources(response.get("sources") or [])
        with st.expander("Raw Draft Response"):
            st.json(response)


def main() -> None:
    _init_state()
    st.set_page_config(page_title="HS Copilot API Tester", layout="wide")
    st.title("HS Copilot API Tester")
    st.caption("Streamlit console for testing the FastAPI clinical and admin copilot endpoints with an EMR JWT.")

    _render_sidebar()

    session_tab, search_tab, summary_tab, chat_tab, referral_tab = st.tabs(
        ["Session", "Patient Search", "Summary", "Chat", "Referral Note"]
    )
    with session_tab:
        _render_session_tab()
    with search_tab:
        _render_patient_search_tab()
    with summary_tab:
        _render_summary_tab()
    with chat_tab:
        _render_chat_tab()
    with referral_tab:
        _render_referral_note_tab()


if __name__ == "__main__":
    main()
