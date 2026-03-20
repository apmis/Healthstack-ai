import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import get_database
from app.models.schemas import RetrievedSource
from app.services.common import candidate_id_values, keyword_score, make_snippet, normalize_value, object_id_to_str
from app.services.question_router import PHARMACY_KEYWORDS

PRODUCT_SEARCH_STOPWORDS = PHARMACY_KEYWORDS | {
    "what",
    "when",
    "where",
    "which",
    "show",
    "tell",
    "latest",
    "recent",
    "there",
    "this",
    "that",
    "these",
    "those",
    "have",
    "was",
    "were",
    "has",
    "had",
    "with",
    "from",
    "into",
    "for",
    "and",
    "the",
    "our",
    "your",
    "their",
    "his",
    "her",
    "its",
    "patient",
    "facility",
    "store",
    "stores",
    "ward",
    "wards",
    "available",
    "availability",
    "please",
    "about",
    "currently",
    "current",
    "all",
    "any",
    "does",
    "do",
    "can",
    "could",
    "should",
    "would",
    "how",
    "many",
    "much",
    "are",
    "is",
    "be",
    "of",
    "to",
    "in",
    "on",
    "at",
}


def _facility_filter(facility_id: str) -> dict[str, Any]:
    return {"facility": {"$in": candidate_id_values(facility_id)}}


def _candidate_values_for_many(values: Iterable[str]) -> list[Any]:
    candidates: list[Any] = []
    for value in values:
        for candidate in candidate_id_values(value):
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _store_filter(location_ids: list[str]) -> dict[str, Any]:
    if not location_ids:
        return {}
    return {"storeId": {"$in": _candidate_values_for_many(location_ids)}}


def _extract_product_terms(question: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9+()./-]*", question.lower())
    terms: list[str] = []
    for token in tokens:
        clean = token.strip("().,-/").lower()
        if len(clean) < 3 or clean in PRODUCT_SEARCH_STOPWORDS:
            continue
        if clean not in terms:
            terms.append(clean)
    return terms[:6]


def _location_map(facility_id: str) -> dict[str, str]:
    db = get_database()
    mapping: dict[str, str] = {}
    for location in db["locations"].find(_facility_filter(facility_id), {"name": 1}):
        location_id = object_id_to_str(location.get("_id"))
        if location_id:
            mapping[location_id] = str(location.get("name") or location_id)
    return mapping


def _product_text(document: dict[str, Any]) -> str:
    return " ".join(
        str(document.get(field) or "")
        for field in ("name", "generic", "category", "classification", "subclassification", "baseunit")
        if document.get(field)
    )


def _search_products(question: str, limit: int = 5) -> list[dict[str, Any]]:
    db = get_database()
    terms = _extract_product_terms(question)
    if not terms:
        return []

    regex_clauses = []
    for term in terms:
        regex = {"$regex": re.escape(term), "$options": "i"}
        for field_name in ("name", "generic", "category", "classification", "subclassification"):
            regex_clauses.append({field_name: regex})

    cursor = db["products"].find({"$or": regex_clauses}).limit(60)
    scored: list[tuple[float, dict[str, Any]]] = []
    for document in cursor:
        text = _product_text(document)
        score = keyword_score(question, text)
        name = str(document.get("name") or "")
        if any(term in name.lower() for term in terms):
            score += 0.5
        if score <= 0:
            continue
        scored.append((score, normalize_value(document)))

    ranked = sorted(scored, key=lambda item: (item[0], str(item[1].get("name") or "").lower()), reverse=True)
    unique_results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for _, document in ranked:
        document_id = object_id_to_str(document.get("_id"))
        if not document_id or document_id in seen_ids:
            continue
        seen_ids.add(document_id)
        unique_results.append(document)
        if len(unique_results) >= limit:
            break
    return unique_results


def has_pharmacy_product_match(question: str) -> bool:
    return bool(_search_products(question, limit=1))


def _apply_store_labels(documents: list[dict[str, Any]], location_names: dict[str, str]) -> list[dict[str, Any]]:
    labelled: list[dict[str, Any]] = []
    for document in documents:
        item = dict(document)
        store_id = object_id_to_str(item.get("storeId"))
        if store_id:
            item["store_name"] = location_names.get(store_id, store_id)
        labelled.append(item)
    return labelled


def _find_inventory_documents(
    facility_id: str,
    product_ids: list[str],
    location_ids: list[str],
    limit: int = 10,
) -> tuple[list[dict[str, Any]], bool]:
    db = get_database()
    facility_query = {
        **_facility_filter(facility_id),
        "productId": {"$in": _candidate_values_for_many(product_ids)},
    }
    scoped_query = {**facility_query, **_store_filter(location_ids)}
    documents = list(db["inventories"].find(scoped_query).sort([("updatedAt", -1), ("quantity", 1)]).limit(limit))
    used_fallback = False
    if not documents and location_ids:
        used_fallback = True
        documents = list(db["inventories"].find(facility_query).sort([("updatedAt", -1), ("quantity", 1)]).limit(limit))
    return [normalize_value(document) for document in documents], used_fallback


def _find_recent_inventory_transactions(
    facility_id: str,
    product_ids: list[str],
    location_ids: list[str],
    limit: int = 8,
) -> tuple[list[dict[str, Any]], bool]:
    db = get_database()
    facility_query = {
        **_facility_filter(facility_id),
        "productId": {"$in": _candidate_values_for_many(product_ids)},
    }
    scoped_query = {**facility_query, **_store_filter(location_ids)}
    documents = list(db["inventorytransactions"].find(scoped_query).sort("createdAt", -1).limit(limit))
    used_fallback = False
    if not documents and location_ids:
        used_fallback = True
        documents = list(db["inventorytransactions"].find(facility_query).sort("createdAt", -1).limit(limit))
    return [normalize_value(document) for document in documents], used_fallback


def _find_recent_dispenses(
    facility_id: str,
    product_ids: list[str],
    location_ids: list[str],
    limit: int = 8,
) -> tuple[list[dict[str, Any]], bool]:
    db = get_database()
    facility_query = {
        **_facility_filter(facility_id),
        "transactioncategory": "debit",
        "productitems.productId": {"$in": _candidate_values_for_many(product_ids)},
    }
    scoped_query = {**facility_query, **_store_filter(location_ids)}
    documents = list(db["productentries"].find(scoped_query).sort("createdAt", -1).limit(limit))
    used_fallback = False
    if not documents and location_ids:
        used_fallback = True
        documents = list(db["productentries"].find(facility_query).sort("createdAt", -1).limit(limit))
    return [normalize_value(document) for document in documents], used_fallback


def _find_low_stock_items(
    facility_id: str,
    location_ids: list[str],
    limit: int = 8,
) -> tuple[list[dict[str, Any]], bool]:
    db = get_database()
    facility_query = {
        **_facility_filter(facility_id),
        "reorder_level": {"$type": "number"},
    }
    scoped_query = {**facility_query, **_store_filter(location_ids)}

    def _filter(cursor_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        low_stock: list[dict[str, Any]] = []
        for document in cursor_documents:
            quantity = document.get("quantity")
            reorder_level = document.get("reorder_level")
            if isinstance(quantity, (int, float)) and isinstance(reorder_level, (int, float)) and quantity <= reorder_level:
                low_stock.append(normalize_value(document))
        return low_stock[:limit]

    scoped_documents = list(db["inventories"].find(scoped_query).sort([("quantity", 1), ("updatedAt", -1)]).limit(120))
    low_stock = _filter(scoped_documents)
    used_fallback = False
    if not low_stock and location_ids:
        used_fallback = True
        facility_documents = list(db["inventories"].find(facility_query).sort([("quantity", 1), ("updatedAt", -1)]).limit(120))
        low_stock = _filter(facility_documents)
    return low_stock, used_fallback


def _find_expiring_batches(
    facility_id: str,
    location_ids: list[str],
    matched_product_ids: list[str] | None = None,
    days_ahead: int = 90,
    limit: int = 8,
) -> tuple[list[dict[str, Any]], bool]:
    db = get_database()
    now = datetime.now(UTC)
    cutoff = now + timedelta(days=days_ahead)
    base_query = _facility_filter(facility_id)
    if matched_product_ids:
        base_query["productId"] = {"$in": _candidate_values_for_many(matched_product_ids)}

    scoped_query = {**base_query, **_store_filter(location_ids)}

    def _collect(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        expiring: list[dict[str, Any]] = []
        for document in documents:
            store_id = object_id_to_str(document.get("storeId"))
            for batch in document.get("batches") or []:
                expiry = batch.get("expirydate")
                if isinstance(expiry, str):
                    try:
                        expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                    except ValueError:
                        expiry = None
                if not isinstance(expiry, datetime):
                    continue
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                if now <= expiry <= cutoff:
                    expiring.append(
                        {
                            "inventory_id": object_id_to_str(document.get("_id")),
                            "productId": object_id_to_str(document.get("productId")),
                            "name": document.get("name"),
                            "storeId": store_id,
                            "quantity": batch.get("quantity"),
                            "batchNo": batch.get("batchNo"),
                            "expirydate": expiry,
                        }
                    )
        expiring.sort(key=lambda item: item.get("expirydate") or datetime.max.replace(tzinfo=UTC))
        return expiring[:limit]

    scoped_documents = list(db["inventories"].find(scoped_query).limit(200))
    expiring_batches = _collect(scoped_documents)
    used_fallback = False
    if not expiring_batches and location_ids:
        used_fallback = True
        expiring_batches = _collect(list(db["inventories"].find(base_query).limit(200)))
    return expiring_batches, used_fallback


def build_pharmacy_context(
    facility_id: str,
    question: str,
    location_ids: list[str] | None = None,
) -> dict[str, Any]:
    locations = list(location_ids or [])
    location_names = _location_map(facility_id)
    matched_products = _search_products(question)
    matched_product_ids = [object_id_to_str(product.get("_id")) for product in matched_products if object_id_to_str(product.get("_id"))]

    inventory_matches: list[dict[str, Any]] = []
    recent_inventory_transactions: list[dict[str, Any]] = []
    recent_inventory_dispenses: list[dict[str, Any]] = []
    expiring_batches: list[dict[str, Any]] = []
    store_fallback_used = False

    if matched_product_ids:
        inventory_matches, inventory_fallback = _find_inventory_documents(facility_id, matched_product_ids, locations)
        recent_inventory_transactions, transactions_fallback = _find_recent_inventory_transactions(
            facility_id,
            matched_product_ids,
            locations,
        )
        recent_inventory_dispenses, dispenses_fallback = _find_recent_dispenses(
            facility_id,
            matched_product_ids,
            locations,
        )
        expiring_batches, expiry_fallback = _find_expiring_batches(
            facility_id,
            locations,
            matched_product_ids=matched_product_ids,
        )
        store_fallback_used = any((inventory_fallback, transactions_fallback, dispenses_fallback, expiry_fallback))

    low_stock_items, low_stock_fallback = _find_low_stock_items(facility_id, locations)
    if low_stock_fallback:
        store_fallback_used = True

    inventory_matches = _apply_store_labels(inventory_matches, location_names)
    recent_inventory_transactions = _apply_store_labels(recent_inventory_transactions, location_names)
    recent_inventory_dispenses = _apply_store_labels(recent_inventory_dispenses, location_names)
    low_stock_items = _apply_store_labels(low_stock_items, location_names)

    for batch in expiring_batches:
        store_id = object_id_to_str(batch.get("storeId"))
        if store_id:
            batch["store_name"] = location_names.get(store_id, store_id)

    return {
        "question": question,
        "scope": {
            "facility_id": facility_id,
            "location_ids": locations,
            "location_names": [location_names.get(location_id, location_id) for location_id in locations],
            "store_filter_applied": bool(locations),
            "used_facility_fallback": store_fallback_used,
        },
        "matched_products": matched_products,
        "inventory_matches": inventory_matches,
        "recent_inventory_transactions": recent_inventory_transactions,
        "recent_inventory_dispenses": recent_inventory_dispenses,
        "low_stock_items": low_stock_items,
        "expiring_batches": expiring_batches,
    }


def build_pharmacy_sources(pharmacy_context: dict[str, Any], limit: int = 6) -> list[RetrievedSource]:
    sources: list[RetrievedSource] = []

    for document in pharmacy_context.get("inventory_matches", [])[:limit]:
        snippet = make_snippet(
            " ".join(
                str(value)
                for value in (
                    document.get("name"),
                    f"quantity {document.get('quantity')}",
                    f"reorder {document.get('reorder_level')}" if document.get("reorder_level") not in (None, "") else "",
                    f"store {document.get('store_name')}" if document.get("store_name") else "",
                    f"selling price {document.get('sellingprice')}" if document.get("sellingprice") not in (None, "") else "",
                )
                if value
            )
        )
        sources.append(
            RetrievedSource(
                collection="inventories",
                document_id=object_id_to_str(document.get("_id")) or "",
                title=document.get("name"),
                created_at=document.get("updatedAt") or document.get("createdAt"),
                snippet=snippet,
                score=1.0,
            )
        )

    remaining = max(0, limit - len(sources))
    for document in pharmacy_context.get("recent_inventory_transactions", [])[:remaining]:
        snippet = make_snippet(
            " ".join(
                str(value)
                for value in (
                    document.get("name"),
                    document.get("type"),
                    document.get("transactioncategory"),
                    f"quantity {document.get('quantity')}",
                    f"amount {document.get('amount')}" if document.get("amount") not in (None, "") else "",
                    f"store {document.get('store_name')}" if document.get("store_name") else "",
                )
                if value
            )
        )
        sources.append(
            RetrievedSource(
                collection="inventorytransactions",
                document_id=object_id_to_str(document.get("_id")) or "",
                title=document.get("name") or document.get("type"),
                created_at=document.get("createdAt") or document.get("updatedAt"),
                snippet=snippet,
                score=0.9,
            )
        )

    remaining = max(0, limit - len(sources))
    for document in pharmacy_context.get("recent_inventory_dispenses", [])[:remaining]:
        item_names = ", ".join(
            str(item.get("name") or "")
            for item in (document.get("productitems") or [])[:3]
            if isinstance(item, dict) and item.get("name")
        )
        snippet = make_snippet(
            " ".join(
                str(value)
                for value in (
                    document.get("type"),
                    document.get("transactioncategory"),
                    document.get("source"),
                    item_names,
                    f"store {document.get('store_name')}" if document.get("store_name") else "",
                )
                if value
            )
        )
        sources.append(
            RetrievedSource(
                collection="productentries",
                document_id=object_id_to_str(document.get("_id")) or "",
                title=document.get("type") or "Dispense",
                created_at=document.get("createdAt") or document.get("updatedAt"),
                snippet=snippet,
                score=0.85,
            )
        )

    return sources[:limit]
