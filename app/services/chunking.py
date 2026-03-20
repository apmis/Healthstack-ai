import re
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.services.common import object_id_to_str, render_structured_text


@dataclass(slots=True)
class ChunkRecord:
    source_collection: str
    source_document_id: str
    facility_id: str
    client_id: str
    has_facility_id: bool
    has_client_id: bool
    synthetic_text: bool
    title: str
    chunk_index: int
    chunk_count: int
    text: str
    created_at: Any
    updated_at: Any
    created_by: str | None = None
    location_id: str | None = None


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def estimate_token_count(text: str) -> int:
    return len(_tokenize(text))


def _detokenize(tokens: list[str]) -> str:
    return " ".join(tokens).strip()


def split_text_into_chunks(
    text: str,
    chunk_size_tokens: int | None = None,
    chunk_overlap_tokens: int | None = None,
) -> list[str]:
    settings = get_settings()
    size = chunk_size_tokens or settings.chunk_size_tokens
    overlap = chunk_overlap_tokens if chunk_overlap_tokens is not None else settings.chunk_overlap_tokens
    overlap = min(max(overlap, 0), max(size - 1, 0))

    tokens = _tokenize(text)
    if not tokens:
        return []

    if len(tokens) <= size:
        return [_detokenize(tokens)]

    step = max(1, size - overlap)
    chunks: list[str] = []
    for start in range(0, len(tokens), step):
        chunk_tokens = tokens[start : start + size]
        if not chunk_tokens:
            continue
        chunk_text = _detokenize(chunk_tokens)
        if chunk_text:
            chunks.append(chunk_text)
        if start + size >= len(tokens):
            break
    return chunks


def build_source_text(document: dict[str, Any]) -> str:
    sections: list[str] = []
    title = str(document.get("documentname") or "").strip()
    if title:
        sections.append(f"Document Name: {title}")

    detail_text = render_structured_text(document.get("documentdetail"))
    if detail_text:
        sections.append(detail_text)

    return "\n\n".join(sections).strip()


def build_placeholder_source_text(source_collection: str, document: dict[str, Any]) -> str:
    title = str(document.get("documentname") or source_collection).strip() or source_collection
    parts = [
        f"Document Name: {title}",
        "System Note: Source document has no narrative content captured in documentdetail.",
    ]

    created_at_text = render_structured_text(document.get("createdAt"), "created_at")
    if created_at_text:
        parts.append(created_at_text)

    updated_at_text = render_structured_text(document.get("updatedAt"), "updated_at")
    if updated_at_text:
        parts.append(updated_at_text)

    return "\n\n".join(parts).strip()


def chunk_source_document(source_collection: str, document: dict[str, Any]) -> list[ChunkRecord]:
    source_document_id = object_id_to_str(document.get("_id"))
    if not source_document_id:
        return []

    facility_id_value = object_id_to_str(document.get("facility"))
    client_id_value = object_id_to_str(document.get("client"))
    source_text = build_source_text(document)
    synthetic_text = False
    if not source_text:
        source_text = build_placeholder_source_text(source_collection, document)
        synthetic_text = True

    chunks = split_text_into_chunks(source_text)
    if not chunks:
        return []

    created_by = object_id_to_str(document.get("createdBy"))
    location_id = object_id_to_str(document.get("locationId"))
    title = str(document.get("documentname") or source_collection).strip() or source_collection
    facility_id = facility_id_value or f"missing-facility:{source_document_id}"
    client_id = client_id_value or f"missing-client:{source_document_id}"

    return [
        ChunkRecord(
            source_collection=source_collection,
            source_document_id=source_document_id,
            facility_id=facility_id,
            client_id=client_id,
            has_facility_id=bool(facility_id_value),
            has_client_id=bool(client_id_value),
            synthetic_text=synthetic_text,
            title=title,
            chunk_index=index,
            chunk_count=len(chunks),
            text=chunk_text,
            created_at=document.get("createdAt"),
            updated_at=document.get("updatedAt") or document.get("createdAt"),
            created_by=created_by,
            location_id=location_id,
        )
        for index, chunk_text in enumerate(chunks)
    ]
