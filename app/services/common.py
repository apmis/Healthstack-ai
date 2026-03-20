import re
from datetime import datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId


def ensure_object_id(value: Any) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if not isinstance(value, str):
        raise InvalidId(f"Expected ObjectId-compatible value, got: {value!r}")
    return ObjectId(value)


def candidate_id_values(value: str) -> list[Any]:
    values: list[Any] = [value]
    try:
        values.append(ensure_object_id(value))
    except InvalidId:
        pass
    return values


def object_id_to_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return str(value)
    return str(value)


def build_full_name(document: dict[str, Any]) -> str:
    parts = [
        document.get("firstname"),
        document.get("middlename"),
        document.get("lastname"),
    ]
    full_name = " ".join(part for part in parts if part)
    return full_name or document.get("clientname") or "Unknown Patient"


def flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return " ".join(item for item in (flatten_text(item) for item in value) if item)
    if isinstance(value, dict):
        return " ".join(item for item in (flatten_text(item) for item in value.values()) if item)
    return str(value)


def render_structured_text(value: Any, prefix: str | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if prefix:
            return f"{prefix}: {text}"
        return text
    if isinstance(value, datetime):
        text = value.isoformat()
        if prefix:
            return f"{prefix}: {text}"
        return text
    if isinstance(value, list):
        rendered_items = [render_structured_text(item, prefix) for item in value]
        return "\n".join(item for item in rendered_items if item)
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            rendered = render_structured_text(item, child_prefix)
            if rendered:
                lines.append(rendered)
        return "\n".join(lines)
    text = str(value).strip()
    if not text:
        return ""
    if prefix:
        return f"{prefix}: {text}"
    return text


def make_snippet(text: str, length: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= length:
        return compact
    return f"{compact[: length - 3].rstrip()}..."


def keyword_score(query: str, text: str) -> float:
    query_terms = {term for term in re.findall(r"[a-zA-Z0-9]+", query.lower()) if len(term) > 2}
    if not query_terms:
        return 0.0

    haystack = text.lower()
    hits = sum(1 for term in query_terms if term in haystack)
    return hits / len(query_terms)


def normalize_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_value(item) for key, item in value.items()}
    return value
