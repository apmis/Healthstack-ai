# HS Copilot

Standalone Python copilot service for the HealthStack EMR.

## Purpose

This service sits beside the existing Node/Feathers backend and gives doctors a
facility-scoped chat interface over patient data.

The design follows the current EMR login flow:

1. Doctor signs in through the existing EMR auth service.
2. The EMR gets a JWT for the `users` entity.
3. The copilot decodes the JWT and resolves `users -> employees -> facilities`.
4. Every query is locked to an `activeFacilityId`.
5. Patient retrieval happens inside that facility only.
6. The copilot combines:
   - structured reads from MongoDB
   - narrative note retrieval from patient documents

## Current scope

This scaffold includes:

- FastAPI application
- MongoDB connection layer
- JWT-based session resolution
- facility-scoped patient search
- patient summary retrieval across:
  - `clients`
  - `appointments`
  - `clinicaldocuments`
  - `labresults`
  - `orders`
  - `admissions`
  - `mpis`
- facility-level pharmacy inventory retrieval across:
  - `inventories`
  - `inventorytransactions`
  - `productentries`
  - `products`
- Atlas Vector Search retrieval over chunked patient narratives
- lexical fallback when the chunk collection or vector index is unavailable

The API uses Vector Search first and falls back to direct lexical retrieval if
the chunk collection has not been populated yet.

The chat route now supports two answer modes:

- `retrieval_fallback`: deterministic summary composer from MongoDB data
- `llm_openai`: grounded LLM answer when `LLM_PROVIDER=openai` and `OPENAI_API_KEY` is set
- `llm_openrouter`: grounded LLM answer when `LLM_PROVIDER=openrouter` and `OPENROUTER_API_KEY` is set

The narrative retrieval path now also supports Atlas reranking:

1. Atlas Vector Search fetches a wider candidate set from `copilot_chunks`
2. Atlas reranking (`rerank-2.5`) reorders those chunks against the doctor's question
3. The best reranked chunks become the chatbot sources

The copilot now supports two pharmacy modes through the same chat endpoint:

1. patient-scoped pharmacy answers from `productentries` inside the patient summary
2. facility/store-scoped pharmacy inventory answers for questions like:
   - "Is amoxicillin in stock?"
   - "What is the current quantity?"
   - "Which items are low in stock?"
   - "Which batch expires next?"

Facility-level pharmacy inventory questions do not require `patient_id`.

## Vector indexing

Narrative retrieval is now designed around a dedicated `copilot_chunks`
collection. The indexer:

- reads `clinicaldocuments` and `labresults`
- renders `documentdetail` into key-aware text
- chunks each source document
- generates embeddings
- upserts chunk records for Atlas Vector Search

Run a dry run first:

```powershell
cd C:\HS-copilot
.venv\Scripts\python.exe scripts\reindex_vector_chunks.py --dry-run --limit 20
```

When you have a write-capable MongoDB user, create the supporting indexes and
write the chunk documents:

```powershell
.venv\Scripts\python.exe scripts\reindex_vector_chunks.py --ensure-index
```

If you want the backfill to keep resuming locally from your terminal until the
selected collection is exhausted, use the continuous wrapper:

```powershell
cd C:\HS-copilot
.venv\Scripts\python.exe scripts\continue_backfill.py --ensure-index --collection clinicaldocuments
```

This runs repeated resume batches until there are no more source documents left
to embed for that collection. It prints terminal progress after each batch,
including embedded count, percentage complete, rate, and ETA. Use `Ctrl+C` to
stop it cleanly. You can tune the batch size if needed:

```powershell
.venv\Scripts\python.exe scripts\continue_backfill.py --collection clinicaldocuments --batch-limit 1000
```

If your network or DNS is flaky and you want the job to keep retrying instead of
exiting after a few transient failures:

```powershell
.venv\Scripts\python.exe scripts\continue_backfill.py --collection clinicaldocuments --max-failures 0
```

If you switch embedding models or dimensions, rebuild from a clean slate:

```powershell
.venv\Scripts\python.exe scripts\reindex_vector_chunks.py --recreate-vector-index --ensure-index --reset-chunks
```

Where the vectors live:

- database: `healthstackv2`
- collection: `copilot_chunks`
- Atlas Vector Search index: `patient_note_chunks`

## Ongoing sync

To keep the vector store aligned with the source collections after the backfill,
run the change-stream worker:

```powershell
cd C:\HS-copilot
.venv\Scripts\python.exe scripts\sync_vector_chunks.py
```

In practice, use two terminals:

1. Terminal 1: run `scripts\continue_backfill.py` until the initial corpus is done.
2. Terminal 2: run `scripts\sync_vector_chunks.py` so new writes from the EMR keep
   `copilot_chunks` updated.

This watches `clinicaldocuments` and `labresults` for `insert`, `update`,
`replace`, and `delete` events and updates `copilot_chunks` accordingly.

The default embedding provider is a deterministic local hash embedder for
development. For production quality retrieval, switch `EMBEDDING_PROVIDER` to
`atlas_voyage`, set `VOYAGE_API_KEY`, and reindex the chunk collection.

Recommended production setting for this EMR:

- `EMBEDDING_PROVIDER=atlas_voyage`
- `EMBEDDING_MODEL=voyage-context-3`
- `EMBEDDING_DIMENSIONS=1024`
- `CHUNK_OVERLAP_TOKENS=0`
- `INDEX_BATCH_TOKEN_BUDGET=80000`

`voyage-context-3` is the best fit for patient-note chunk retrieval because it
embeds each chunk with document context, which matches this copilot's
`clinicaldocuments` and `labresults` use case.

## Setup

```powershell
cd C:\HS-copilot
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

## Streamlit API tester

To test the API quickly without building a frontend, run the Streamlit client:

```powershell
cd C:\HS-copilot
.venv\Scripts\streamlit.exe run streamlit_app.py
```

The tester lets you:

- paste a Bearer JWT
- resolve the copilot session and activate a facility
- search patients
- fetch a patient summary
- send chat questions to `/api/v1/copilot/chat`

Use the same local API base URL you run for FastAPI, usually:

```text
http://127.0.0.1:8010
```

## API endpoints

- `GET /health`
- `POST /api/v1/session/resolve`
- `GET /api/v1/patients/search`
- `GET /api/v1/patients/{patient_id}/summary`
- `POST /api/v1/copilot/chat`

## Chat configuration

The chatbot now uses:

- vector retrieval over `clinicaldocuments` and `labresults`
- structured retrieval over `appointments`, `orders`, `productentries`, `admissions`, core patient data, and pharmacy inventory collections
- optional LLM generation on top of that grounded context

To enable a real LLM-backed chatbot, set:

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=your_key_here
```

If `LLM_PROVIDER=none`, the API still answers using the retrieval-based fallback.

To use OpenRouter instead, set:

```env
LLM_PROVIDER=openrouter
LLM_MODEL=google/gemini-3-flash-preview
OPENROUTER_API_KEY=your_key_here
OPENROUTER_HTTP_REFERER=http://localhost:8010
OPENROUTER_APP_TITLE=HS Copilot
```

Atlas reranking is enabled by default when `VOYAGE_API_KEY` is present. The main
knobs are:

```env
RERANKER_PROVIDER=atlas_voyage
RERANKER_MODEL=rerank-2.5
RERANKER_CANDIDATE_LIMIT=24
```

## Security model

- JWT is required for doctor-facing endpoints.
- Facility scoping is enforced server-side.
- Patient lookups are restricted to the active facility.
- The API service is read-only against MongoDB.
- The chunk indexing job needs a separate write-capable MongoDB user.
