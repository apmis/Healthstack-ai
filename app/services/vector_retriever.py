from datetime import datetime
from typing import Any

from pymongo.errors import OperationFailure, PyMongoError

from app.core.config import get_settings
from app.core.database import get_database
from app.models.schemas import RetrievedSource
from app.services.common import (
    candidate_id_values,
    keyword_score,
    make_snippet,
    object_id_to_str,
    render_structured_text,
)
from app.services.embeddings import get_embedding_provider
from app.services.reranker import get_reranker

SOURCE_COLLECTIONS = ("clinicaldocuments", "labresults")


def _facility_filter(facility_id: str) -> dict[str, Any]:
    return {"facility": {"$in": candidate_id_values(facility_id)}}


def _patient_filter(field_name: str, patient_id: str) -> dict[str, Any]:
    return {field_name: {"$in": candidate_id_values(patient_id)}}


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


def _lexical_fallback(
    facility_id: str,
    patient_id: str,
    question: str,
    limit: int,
) -> list[RetrievedSource]:
    db = get_database()
    candidates: list[RetrievedSource] = []
    for collection_name, patient_field in (("clinicaldocuments", "client"), ("labresults", "client")):
        cursor = db[collection_name].find(
            {
                **_facility_filter(facility_id),
                **_patient_filter(patient_field, patient_id),
            }
        ).sort("createdAt", -1).limit(50)
        for document in cursor:
            narrative = render_structured_text(document.get("documentdetail"))
            if not narrative:
                continue
            score = keyword_score(question, narrative) if question else 0.0
            candidates.append(
                RetrievedSource(
                    collection=collection_name,
                    document_id=object_id_to_str(document.get("_id")) or "",
                    title=document.get("documentname"),
                    created_at=_to_datetime(document.get("createdAt")),
                    snippet=make_snippet(narrative),
                    score=score,
                )
            )

    ranked = sorted(
        candidates,
        key=lambda item: (
            item.score or 0.0,
            item.created_at or datetime.min,
        ),
        reverse=True,
    )
    selected = [item for item in ranked if (item.score or 0.0) > 0][:limit]
    if selected:
        return selected
    return ranked[:limit]


def _vector_search(
    facility_id: str,
    patient_id: str,
    question: str,
    limit: int,
) -> list[RetrievedSource]:
    settings = get_settings()
    db = get_database()
    provider = get_embedding_provider()
    query_vector = provider.embed_query(question)
    collection = db[settings.vector_chunks_collection]
    reranker = get_reranker()

    candidate_limit = max(limit, settings.reranker_candidate_limit if reranker else limit)
    num_candidates = max(candidate_limit * settings.vector_num_candidates_factor, candidate_limit)
    pipeline = [
        {
            "$vectorSearch": {
                "index": settings.vector_index_name,
                "path": "embedding",
                "queryVector": query_vector,
                "numCandidates": num_candidates,
                "limit": candidate_limit,
                "filter": {
                    "$and": [
                        {"facility_id": {"$eq": facility_id}},
                        {"client_id": {"$eq": patient_id}},
                        {"source_collection": {"$in": list(SOURCE_COLLECTIONS)}},
                    ]
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "source_collection": 1,
                "source_document_id": 1,
                "title": 1,
                "created_at": 1,
                "text": 1,
                "synthetic_text": 1,
                "vector_score": {"$meta": "vectorSearchScore"},
            }
        },
    ]

    candidate_documents = [
        document
        for document in collection.aggregate(pipeline)
        if not document.get("synthetic_text")
    ]
    if reranker is not None and candidate_documents:
        try:
            reranked = reranker.rerank(
                question,
                [str(document.get("text") or "") for document in candidate_documents],
                top_k=limit,
            )
            reranked_documents = []
            for item in reranked:
                index = item["index"]
                if index < 0 or index >= len(candidate_documents):
                    continue
                document = dict(candidate_documents[index])
                document["score"] = item["relevance_score"]
                reranked_documents.append(document)
            if reranked_documents:
                candidate_documents = reranked_documents
        except RuntimeError:
            candidate_documents = candidate_documents[:limit]

    return [
        RetrievedSource(
            collection=document.get("source_collection") or "copilot_chunks",
            document_id=str(document.get("source_document_id") or ""),
            title=document.get("title"),
            created_at=_to_datetime(document.get("created_at")),
            snippet=make_snippet(str(document.get("text") or "")),
            score=float(document.get("score") or document.get("vector_score") or 0.0),
        )
        for document in candidate_documents[:limit]
    ]


def search_patient_narratives(
    facility_id: str,
    patient_id: str,
    question: str,
    limit: int,
) -> list[RetrievedSource]:
    if not question.strip():
        return _lexical_fallback(facility_id, patient_id, question, limit)

    try:
        results = _vector_search(facility_id, patient_id, question, limit)
    except (OperationFailure, PyMongoError, RuntimeError):
        return _lexical_fallback(facility_id, patient_id, question, limit)

    if results:
        return results
    return _lexical_fallback(facility_id, patient_id, question, limit)
