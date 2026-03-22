# HS Copilot API Documentation

Comprehensive reference for the current FastAPI service in [C:\HS-copilot](C:/HS-copilot).

## Base URLs

- Local API: `http://127.0.0.1:8010`
- Health check: `GET /health`
- Interactive Swagger docs: `GET /docs`
- ReDoc: `GET /redoc`
- OpenAPI JSON: `GET /openapi.json`

## Purpose

The API supports two copilot modes:

- `clinical`
  - patient-first
  - clinical notes, labs, orders, appointments, admissions, patient-linked pharmacy activity
- `admin`
  - facility-first
  - billing, appointments, admissions, workforce, registrations, and facility inventory questions

The service sits beside the existing EMR backend and trusts the EMR JWT after verifying it with the shared `JWT_SECRET`.

## Authentication

All protected endpoints require:

```http
Authorization: Bearer <EMR_ACCESS_TOKEN>
```

The token is obtained from the existing backend auth flow:

```http
POST https://backend.healthstack.africa/authentication
Content-Type: application/json

{
  "strategy": "local",
  "email": "test@test.com",
  "password": "test"
}
```

Typical auth response fields:

- `accessToken`
- `authentication`
- `user`

The copilot resolves:

```text
JWT -> users -> employees -> facility
```

## Access Model

The API enforces:

- JWT validation
- facility-scoped access
- employee membership in the requested facility
- patient lookup restricted to the active facility

Clinical mode requires a patient context unless the question is a facility-level pharmacy stock question.

Admin mode is facility-scoped and does not require a patient.

## Route Summary

- `GET /health`
- `POST /api/v1/session/resolve`
- `GET /api/v1/patients/search`
- `GET /api/v1/patients/{patient_id}/summary`
- `POST /api/v1/copilot/chat`

## 1. Health

### `GET /health`

Checks service and MongoDB connectivity.

#### Response

```json
{
  "status": "ok",
  "app": "HS Copilot",
  "database": "healthstackv2"
}
```

#### Notes

- This route pings MongoDB.
- If Atlas is unreachable, this route fails.

## 2. Session Resolve

### `POST /api/v1/session/resolve`

Validates the Bearer token and resolves the user’s facility context.

#### Headers

```http
Authorization: Bearer <token>
Content-Type: application/json
```

#### Request body

```json
{
  "active_facility_id": "60203e1c1ec8a00015baa357"
}
```

`active_facility_id` is optional:

- if the user belongs to exactly one facility, the API can auto-select it
- if the user belongs to multiple facilities, the API returns `requires_facility_selection: true` until one is chosen

#### Response shape

```json
{
  "user_id": "6054aed837bc490015f56fe8",
  "user_email": "test@test.com",
  "employee_id": "6054aed837bc490015f56fe9",
  "active_facility_id": "60203e1c1ec8a00015baa357",
  "active_facility_name": "Test Facility",
  "roles": ["Admin", "Finance", "Pharmacy"],
  "accesslevel": "1",
  "location_ids": ["603f6902750da500154ccb3a"],
  "available_facilities": [
    {
      "facility_id": "60203e1c1ec8a00015baa357",
      "facility_name": "Test Facility",
      "employee_id": "6054aed837bc490015f56fe9",
      "roles": ["Admin"],
      "accesslevel": "1"
    }
  ],
  "requires_facility_selection": false
}
```

#### Main error cases

- `401`: missing or invalid JWT
- `401`: user not found in MongoDB
- `403`: no employee record for the user
- `403`: user does not belong to the requested facility

## 3. Patient Search

### `GET /api/v1/patients/search`

Searches patients inside the active facility only.

#### Query parameters

- `active_facility_id` required
- `query` required, minimum 2 characters

#### Example

```http
GET /api/v1/patients/search?active_facility_id=60203e1c1ec8a00015baa357&query=08080005000
Authorization: Bearer <token>
```

#### Search fields

The search ranks matches across:

- `firstname`
- `middlename`
- `lastname`
- `mrn`
- `phone`
- `email`
- `hs_id`
- fallback through `mpis` by `mrn` and `clientTags.tagName`

#### Response shape

```json
[
  {
    "patient_id": "636f767db91e900016d54ad8",
    "facility_id": "60203e1c1ec8a00015baa357",
    "mrn": "MRN-001",
    "hs_id": "HS-001",
    "firstname": "Malik",
    "middlename": "H",
    "lastname": "Berry",
    "full_name": "Malik H Berry",
    "gender": "Male",
    "dob": "1995-01-01T00:00:00",
    "phone": "08080005000",
    "email": "patient@example.com"
  }
]
```

#### Main error cases

- `401`: missing/invalid JWT
- `400`: no active facility selected
- `422`: missing or invalid query parameters

## 4. Patient Summary

### `GET /api/v1/patients/{patient_id}/summary`

Returns structured patient context for the active facility.

#### Query parameters

- `active_facility_id` required

#### Example

```http
GET /api/v1/patients/636f767db91e900016d54ad8/summary?active_facility_id=60203e1c1ec8a00015baa357
Authorization: Bearer <token>
```

#### Response shape

```json
{
  "session": {
    "user_id": "6054aed837bc490015f56fe8",
    "active_facility_id": "60203e1c1ec8a00015baa357",
    "active_facility_name": "Test Facility"
  },
  "patient": {
    "patient_id": "636f767db91e900016d54ad8",
    "full_name": "Malik H Berry",
    "mrn": "MRN-001"
  },
  "summary": {
    "patient": {},
    "mpi": {},
    "recent_appointments": [],
    "recent_clinical_documents": [],
    "recent_lab_results": [],
    "recent_orders": [],
    "recent_pharmacy_entries": [],
    "recent_admissions": [],
    "active_admission": null
  }
}
```

#### Summary sections

- `patient`
- `mpi`
- `recent_appointments`
- `recent_clinical_documents`
- `recent_lab_results`
- `recent_orders`
- `recent_pharmacy_entries`
- `recent_admissions`
- `active_admission`

#### Main error cases

- `404`: patient not found in the active facility
- `401`, `400`, `422` as above

## 5. Copilot Chat

### `POST /api/v1/copilot/chat`

Main question-answering endpoint.

Supports:

- `mode: "clinical"`
- `mode: "admin"`

#### Headers

```http
Authorization: Bearer <token>
Content-Type: application/json
```

#### Shared request fields

- `question` string, required
- `active_facility_id` string, required
- `mode` `"clinical"` or `"admin"`, optional, default `"clinical"`
- `notes_limit` integer, optional
- `history` array of chat messages, optional

Each history item:

```json
{
  "role": "user",
  "content": "previous message"
}
```

Allowed roles:

- `user`
- `assistant`

### 5A. Clinical chat

Clinical mode is patient-first.

#### Clinical request options

Provide one of:

- `patient_id`
- `patient_query`

`patient_id` is preferred.

#### Example: patient-specific clinical question

```json
{
  "question": "What are the latest vital signs and most recent clinical updates for this patient?",
  "active_facility_id": "60203e1c1ec8a00015baa357",
  "mode": "clinical",
  "patient_id": "636f767db91e900016d54ad8",
  "notes_limit": 5,
  "history": []
}
```

#### Example: patient resolution by text

```json
{
  "question": "What were this patient's recent orders?",
  "active_facility_id": "60203e1c1ec8a00015baa357",
  "mode": "clinical",
  "patient_query": "Malik Berry",
  "notes_limit": 5,
  "history": []
}
```

#### Clinical data sources

Structured retrieval:

- `clients`
- `appointments`
- `orders`
- `admissions`
- `productentries`
- `mpis`

Narrative/vector retrieval:

- `clinicaldocuments`
- `labresults`

Optional facility-level inventory context:

- `inventories`
- `inventorytransactions`

#### Special case

Clinical mode can answer a facility-level pharmacy inventory question without a patient if the question is inventory/pharmacy-specific.

### 5B. Admin chat

Admin mode is facility-first and does not require a patient.

#### Example: revenue and bills

```json
{
  "question": "Give me the revenue and outstanding bills summary for this month.",
  "active_facility_id": "60203e1c1ec8a00015baa357",
  "mode": "admin",
  "notes_limit": 5,
  "history": []
}
```

#### Example: appointment flow

```json
{
  "question": "What is today's appointment load and status mix?",
  "active_facility_id": "60203e1c1ec8a00015baa357",
  "mode": "admin",
  "notes_limit": 5,
  "history": []
}
```

#### Example: admissions and wards

```json
{
  "question": "How many active admissions do we have and which wards are busiest?",
  "active_facility_id": "60203e1c1ec8a00015baa357",
  "mode": "admin",
  "notes_limit": 5,
  "history": []
}
```

#### Example: workforce

```json
{
  "question": "What does the workforce breakdown look like in this facility?",
  "active_facility_id": "60203e1c1ec8a00015baa357",
  "mode": "admin",
  "notes_limit": 5,
  "history": []
}
```

#### Admin data sources

- `appointments`
- `bills`
- `admissions`
- `employees`
- `clients`
- `locations`
- `inventories`
- `inventorytransactions`
- `productentries`

#### Admin routing domains

The service infers the domain from the question and may return one or more of:

- `appointments`
- `billing`
- `admissions`
- `inventory`
- `workforce`
- `patients`
- `overview`

### Chat response shape

```json
{
  "session": {
    "user_id": "6054aed837bc490015f56fe8",
    "active_facility_id": "60203e1c1ec8a00015baa357",
    "active_facility_name": "Test Facility"
  },
  "mode": "admin",
  "patient": null,
  "patient_candidates": [],
  "answer": "For the current month ...",
  "answer_mode": "llm_openrouter",
  "sources": [
    {
      "collection": "bills",
      "document_id": "67f...",
      "title": "X-Ray",
      "created_at": "2026-03-22T08:15:00",
      "snippet": "billing_status unpaid ...",
      "score": 0.91
    }
  ],
  "structured_context": {}
}
```

#### Response fields

- `session`
- `mode`
- `patient`
- `patient_candidates`
- `answer`
- `answer_mode`
- `sources`
- `structured_context`

#### `answer_mode` values

- `retrieval_fallback`
- `llm_openai`
- `llm_openrouter`

#### `sources`

Each source includes:

- `collection`
- `document_id`
- `title`
- `created_at`
- `snippet`
- `score`

#### `structured_context`

Varies by mode.

Clinical mode commonly includes:

- `patient`
- `mpi`
- `recent_appointments`
- `recent_clinical_documents`
- `recent_lab_results`
- `recent_orders`
- `recent_pharmacy_entries`
- `recent_admissions`
- `active_admission`
- optional `pharmacy_inventory`

Admin mode commonly includes:

- `question`
- `domains`
- `time_window`
- `scope`
- `appointments`
- `billing`
- `admissions`
- `workforce`
- `patients`
- `locations`
- optional `pharmacy_inventory`
- `overview_sections`

### Clinical ambiguity behavior

If `patient_query` matches multiple patients, the API does not guess. It returns:

- `patient_candidates`
- a message telling the caller to retry with `patient_id`

## Error Handling

Common status codes:

- `200` successful request
- `201` backend auth response from the EMR, not from this API
- `400` facility not selected
- `401` missing/invalid Bearer token
- `403` user not allowed in that facility
- `404` patient not found in active facility
- `422` request validation error
- `500` unexpected internal issue

Typical auth errors:

```json
{
  "detail": "Missing Authorization header."
}
```

```json
{
  "detail": "Authorization header must be a Bearer token."
}
```

```json
{
  "detail": "Invalid JWT token."
}
```

Typical facility-selection error:

```json
{
  "detail": "An active facility must be selected for this operation."
}
```

Typical patient error:

```json
{
  "detail": "Patient was not found in the active facility."
}
```

## Example Testing Flow

### Step 1. Login to the EMR backend

```bash
curl -X POST https://backend.healthstack.africa/authentication \
  -H "Content-Type: application/json" \
  -d "{\"strategy\":\"local\",\"email\":\"test@test.com\",\"password\":\"test\"}"
```

### Step 2. Resolve the session

```bash
curl -X POST http://127.0.0.1:8010/api/v1/session/resolve \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d "{\"active_facility_id\":\"60203e1c1ec8a00015baa357\"}"
```

### Step 3A. Search a patient

```bash
curl "http://127.0.0.1:8010/api/v1/patients/search?active_facility_id=60203e1c1ec8a00015baa357&query=08080005000" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Step 3B. Ask a clinical question

```bash
curl -X POST http://127.0.0.1:8010/api/v1/copilot/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d "{\"question\":\"What are the latest vital signs for this patient?\",\"active_facility_id\":\"60203e1c1ec8a00015baa357\",\"mode\":\"clinical\",\"patient_id\":\"636f767db91e900016d54ad8\",\"notes_limit\":5,\"history\":[]}"
```

### Step 3C. Ask an admin question

```bash
curl -X POST http://127.0.0.1:8010/api/v1/copilot/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d "{\"question\":\"Give me the revenue and outstanding bills summary for this month.\",\"active_facility_id\":\"60203e1c1ec8a00015baa357\",\"mode\":\"admin\",\"notes_limit\":5,\"history\":[]}"
```

## Streamlit Tester

The included Streamlit client is [C:\HS-copilot\streamlit_app.py](C:/HS-copilot/streamlit_app.py).

Run:

```powershell
cd C:\HS-copilot
.venv\Scripts\streamlit.exe run streamlit_app.py
```

Then:

1. set `API Base URL`
2. use `Login via Backend`
3. resolve the session
4. choose `clinical` or `admin` mode
5. test the routes from the UI

## Implementation Files

Main route files:

- [C:\HS-copilot\app\api\routes\health.py](C:/HS-copilot/app/api/routes/health.py)
- [C:\HS-copilot\app\api\routes\session.py](C:/HS-copilot/app/api/routes/session.py)
- [C:\HS-copilot\app\api\routes\patients.py](C:/HS-copilot/app/api/routes/patients.py)
- [C:\HS-copilot\app\api\routes\copilot.py](C:/HS-copilot/app/api/routes/copilot.py)

Main request/response schemas:

- [C:\HS-copilot\app\models\schemas.py](C:/HS-copilot/app/models/schemas.py)

Main service implementations:

- [C:\HS-copilot\app\services\context.py](C:/HS-copilot/app/services/context.py)
- [C:\HS-copilot\app\services\patient_resolver.py](C:/HS-copilot/app/services/patient_resolver.py)
- [C:\HS-copilot\app\services\structured_retriever.py](C:/HS-copilot/app/services/structured_retriever.py)
- [C:\HS-copilot\app\services\admin_retriever.py](C:/HS-copilot/app/services/admin_retriever.py)
- [C:\HS-copilot\app\services\copilot.py](C:/HS-copilot/app/services/copilot.py)
- [C:\HS-copilot\app\services\llm.py](C:/HS-copilot/app/services/llm.py)

