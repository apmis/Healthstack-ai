import math
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from bson import ObjectId
from pymongo import UpdateOne
from pymongo.collection import Collection
from pymongo.operations import SearchIndexModel

from app.core.config import get_settings
from app.core.database import get_database
from app.services.chunking import ChunkRecord, chunk_source_document, estimate_token_count
from app.services.embeddings import EmbeddingProvider, get_embedding_provider

SOURCE_COLLECTIONS = ("clinicaldocuments", "labresults")


def _serialize_chunk(chunk: ChunkRecord, embedding: list[float], provider: EmbeddingProvider) -> dict[str, Any]:
    return {
        "source_collection": chunk.source_collection,
        "source_document_id": chunk.source_document_id,
        "facility_id": chunk.facility_id,
        "client_id": chunk.client_id,
        "has_facility_id": chunk.has_facility_id,
        "has_client_id": chunk.has_client_id,
        "reference_complete": chunk.has_facility_id and chunk.has_client_id,
        "synthetic_text": chunk.synthetic_text,
        "title": chunk.title,
        "chunk_index": chunk.chunk_index,
        "chunk_count": chunk.chunk_count,
        "text": chunk.text,
        "created_at": chunk.created_at,
        "updated_at": chunk.updated_at,
        "created_by": chunk.created_by,
        "location_id": chunk.location_id,
        "embedding": embedding,
        "embedding_provider": provider.provider_name,
        "embedding_model": provider.model_name,
        "embedding_dimensions": provider.dimensions,
        "indexed_at": datetime.utcnow(),
    }


def _projection() -> dict[str, int]:
    return {
        "documentdetail": 1,
        "documentname": 1,
        "facility": 1,
        "client": 1,
        "createdAt": 1,
        "updatedAt": 1,
        "createdBy": 1,
        "locationId": 1,
    }


def _get_resume_boundary(source_collection: str) -> tuple[datetime | None, ObjectId | None]:
    db = get_database()
    settings = get_settings()
    last_chunk = db[settings.vector_chunks_collection].find_one(
        {"source_collection": source_collection},
        {"updated_at": 1, "source_document_id": 1},
        sort=[("updated_at", -1), ("source_document_id", -1)],
    )
    if not last_chunk:
        return None, None

    updated_at = last_chunk.get("updated_at")
    source_document_id = last_chunk.get("source_document_id")
    if not source_document_id:
        return updated_at, None

    try:
        return updated_at, ObjectId(str(source_document_id))
    except Exception:
        return updated_at, None


def _iter_source_documents(
    collection_name: str,
    limit: int | None = None,
    updated_after: datetime | None = None,
    resume_from_existing: bool = False,
) -> Iterable[dict[str, Any]]:
    db = get_database()
    filters: list[dict[str, Any]] = []
    if updated_after is not None:
        filters.append({
            "$or": [
            {"updatedAt": {"$gte": updated_after}},
            {"createdAt": {"$gte": updated_after}},
        ]
        })

    if resume_from_existing:
        resume_updated_at, resume_source_id = _get_resume_boundary(collection_name)
        if resume_updated_at is not None:
            resume_filter: dict[str, Any] = {"updatedAt": {"$gt": resume_updated_at}}
            if resume_source_id is not None:
                resume_filter = {
                    "$or": [
                        {"updatedAt": {"$gt": resume_updated_at}},
                        {"updatedAt": resume_updated_at, "_id": {"$gt": resume_source_id}},
                    ]
                }
            filters.append(resume_filter)

    if not filters:
        query: dict[str, Any] = {}
    elif len(filters) == 1:
        query = filters[0]
    else:
        query = {"$and": filters}

    cursor = db[collection_name].find(query, _projection()).sort([("updatedAt", 1), ("_id", 1)])
    if limit:
        cursor = cursor.limit(limit)
    return cursor


def ensure_chunk_collection_indexes() -> None:
    db = get_database()
    settings = get_settings()
    collection = db[settings.vector_chunks_collection]
    collection.create_index(
        [
            ("source_collection", 1),
            ("source_document_id", 1),
            ("chunk_index", 1),
        ],
        unique=True,
        name="source_chunk_unique",
    )
    collection.create_index(
        [("facility_id", 1), ("client_id", 1), ("source_collection", 1)],
        name="facility_client_source_lookup",
    )


def ensure_vector_search_index() -> str:
    db = get_database()
    settings = get_settings()
    collection = db[settings.vector_chunks_collection]

    existing = list(collection.list_search_indexes())
    if any(index.get("name") == settings.vector_index_name for index in existing):
        return "existing"

    model = SearchIndexModel(
        definition={
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": settings.embedding_dimensions,
                    "similarity": "cosine",
                },
                {"type": "filter", "path": "facility_id"},
                {"type": "filter", "path": "client_id"},
                {"type": "filter", "path": "source_collection"},
            ]
        },
        name=settings.vector_index_name,
        type="vectorSearch",
    )
    collection.create_search_index(model=model)
    return "created"


def drop_vector_search_index() -> str:
    db = get_database()
    settings = get_settings()
    collection = db[settings.vector_chunks_collection]
    existing = list(collection.list_search_indexes())
    if not any(index.get("name") == settings.vector_index_name for index in existing):
        return "missing"
    collection.drop_search_index(settings.vector_index_name)
    return "dropped"


def clear_chunk_collection() -> int:
    db = get_database()
    settings = get_settings()
    result = db[settings.vector_chunks_collection].delete_many({})
    return result.deleted_count


def _delete_stale_chunks(collection: Collection, source_collection: str, source_document_id: str, chunk_count: int) -> int:
    result = collection.delete_many(
        {
            "source_collection": source_collection,
            "source_document_id": source_document_id,
            "chunk_index": {"$gte": chunk_count},
        }
    )
    return result.deleted_count


def delete_source_document_chunks(source_collection: str, source_document_id: str) -> int:
    db = get_database()
    settings = get_settings()
    result = db[settings.vector_chunks_collection].delete_many(
        {
            "source_collection": source_collection,
            "source_document_id": source_document_id,
        }
    )
    return result.deleted_count


def _document_token_estimate(document_chunks: list[ChunkRecord]) -> int:
    raw_tokens = sum(estimate_token_count(chunk.text) for chunk in document_chunks)
    # Add headroom because the Atlas tokenizer is stricter than our whitespace estimate.
    return math.ceil(raw_tokens * 1.5) + (len(document_chunks) * 12)


def _batch_token_estimate(document_chunks_batch: list[list[ChunkRecord]]) -> int:
    return sum(_document_token_estimate(document_chunks) for document_chunks in document_chunks_batch)


def _is_token_limit_error(error: RuntimeError) -> bool:
    message = str(error).lower()
    return any(
        pattern in message
        for pattern in (
            "max allowed tokens",
            "lower the number of tokens in the batch",
            "too many tokens",
            "context window",
            "do not support truncation for contextualized chunk embeddings",
        )
    )


def _upsert_documents(
    target_collection: Collection,
    provider: EmbeddingProvider,
    document_chunks_batch: list[list[ChunkRecord]],
    stats: dict[str, int],
    dry_run: bool,
) -> None:
    if not document_chunks_batch:
        return

    try:
        embeddings_by_document = provider.embed_documents(
            [[chunk.text for chunk in document_chunks] for document_chunks in document_chunks_batch]
        )
    except RuntimeError as exc:
        if not _is_token_limit_error(exc):
            raise
        if len(document_chunks_batch) > 1:
            midpoint = max(1, len(document_chunks_batch) // 2)
            _upsert_documents(
                target_collection,
                provider,
                document_chunks_batch[:midpoint],
                stats,
                dry_run=dry_run,
            )
            _upsert_documents(
                target_collection,
                provider,
                document_chunks_batch[midpoint:],
                stats,
                dry_run=dry_run,
            )
            return

        document_chunks = document_chunks_batch[0]
        if len(document_chunks) > 1:
            midpoint = max(1, len(document_chunks) // 2)
            _upsert_documents(
                target_collection,
                provider,
                [document_chunks[:midpoint]],
                stats,
                dry_run=dry_run,
            )
            _upsert_documents(
                target_collection,
                provider,
                [document_chunks[midpoint:]],
                stats,
                dry_run=dry_run,
            )
            return
        raise

    if dry_run:
        return

    operations = []
    chunk_counts: dict[tuple[str, str], int] = {}
    for document_chunks, embeddings in zip(document_chunks_batch, embeddings_by_document, strict=True):
        for chunk, embedding in zip(document_chunks, embeddings, strict=True):
            key = (chunk.source_collection, chunk.source_document_id)
            chunk_counts[key] = chunk.chunk_count
            operations.append(
                UpdateOne(
                    {
                        "source_collection": chunk.source_collection,
                        "source_document_id": chunk.source_document_id,
                        "chunk_index": chunk.chunk_index,
                    },
                    {"$set": _serialize_chunk(chunk, embedding, provider)},
                    upsert=True,
                )
            )

    if operations:
        target_collection.bulk_write(operations, ordered=False)
        stats["chunks_upserted"] += len(operations)
        for source_key, chunk_count in chunk_counts.items():
            stats["chunks_deleted"] += _delete_stale_chunks(
                target_collection,
                source_key[0],
                source_key[1],
                chunk_count,
            )


def index_source_document(
    source_collection: str,
    document: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, int]:
    settings = get_settings()
    db = get_database()
    target_collection = db[settings.vector_chunks_collection]
    provider = get_embedding_provider()
    stats = {
        "documents_seen": 1,
        "documents_indexed": 0,
        "chunks_upserted": 0,
        "chunks_deleted": 0,
        "documents_skipped": 0,
    }

    chunks = chunk_source_document(source_collection, document)
    if not chunks:
        stats["documents_skipped"] = 1
        return stats

    stats["documents_indexed"] = 1
    _upsert_documents(target_collection, provider, [chunks], stats, dry_run=dry_run)
    return stats


def index_source_document_by_id(
    source_collection: str,
    source_document_id: Any,
    dry_run: bool = False,
) -> dict[str, int]:
    db = get_database()
    document = db[source_collection].find_one({"_id": source_document_id}, _projection())
    if not document:
        return {
            "documents_seen": 0,
            "documents_indexed": 0,
            "chunks_upserted": 0,
            "chunks_deleted": 0,
            "documents_skipped": 1,
        }
    return index_source_document(source_collection, document, dry_run=dry_run)


def index_source_documents(
    source_collection: str,
    limit: int | None = None,
    dry_run: bool = False,
    updated_after: datetime | None = None,
    resume_from_existing: bool = False,
) -> dict[str, int]:
    settings = get_settings()
    db = get_database()
    target_collection = db[settings.vector_chunks_collection]
    provider = get_embedding_provider()

    stats = {
        "documents_seen": 0,
        "documents_indexed": 0,
        "chunks_upserted": 0,
        "chunks_deleted": 0,
        "documents_skipped": 0,
    }

    batch_size = max(1, settings.index_batch_size)
    batch_token_budget = max(1000, settings.index_batch_token_budget)
    pending_documents: list[list[ChunkRecord]] = []
    pending_chunk_count = 0
    pending_token_estimate = 0

    def flush() -> None:
        nonlocal pending_chunk_count, pending_token_estimate
        if not pending_documents:
            return
        _upsert_documents(target_collection, provider, pending_documents, stats, dry_run=dry_run)
        pending_documents.clear()
        pending_chunk_count = 0
        pending_token_estimate = 0

    for document in _iter_source_documents(
        source_collection,
        limit=limit,
        updated_after=updated_after,
        resume_from_existing=resume_from_existing,
    ):
        stats["documents_seen"] += 1
        chunks = chunk_source_document(source_collection, document)
        if not chunks:
            stats["documents_skipped"] += 1
            continue
        stats["documents_indexed"] += 1
        document_token_estimate = _document_token_estimate(chunks)
        if pending_documents and (
            pending_chunk_count >= batch_size
            or pending_token_estimate + document_token_estimate > batch_token_budget
        ):
            flush()
        pending_documents.append(chunks)
        pending_chunk_count += len(chunks)
        pending_token_estimate += document_token_estimate
        if pending_chunk_count >= batch_size or pending_token_estimate >= batch_token_budget:
            flush()

    flush()
    return stats


def index_all_sources(
    collections: Iterable[str] = SOURCE_COLLECTIONS,
    limit_per_collection: int | None = None,
    dry_run: bool = False,
    updated_after: datetime | None = None,
    resume_from_existing: bool = False,
) -> dict[str, dict[str, int]]:
    return {
        collection_name: index_source_documents(
            collection_name,
            limit=limit_per_collection,
            dry_run=dry_run,
            updated_after=updated_after,
            resume_from_existing=resume_from_existing,
        )
        for collection_name in collections
    }
