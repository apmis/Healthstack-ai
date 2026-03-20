import re


PHARMACY_KEYWORDS = {
    "pharmacy",
    "stock",
    "inventory",
    "drug",
    "drugs",
    "medicine",
    "medicines",
    "medication",
    "medications",
    "dispense",
    "dispensed",
    "dispensing",
    "batch",
    "batches",
    "expiry",
    "expire",
    "expired",
    "reorder",
    "tablet",
    "tablets",
    "capsule",
    "capsules",
    "syrup",
    "ampoule",
    "ampoules",
    "vial",
    "vials",
    "injectable",
}

INVENTORY_KEYWORDS = {
    "stock",
    "inventory",
    "batch",
    "batches",
    "expiry",
    "expire",
    "expired",
    "reorder",
    "store",
    "stores",
    "warehouse",
    "available",
    "availability",
}

PHARMACY_PATTERNS = (
    r"\bin stock\b",
    r"\bout of stock\b",
    r"\blow stock\b",
    r"\bstock level\b",
    r"\bdo we have\b",
    r"\bwhat do we have\b",
    r"\bhow many\b",
    r"\bexpiry date\b",
    r"\bexpires?\b",
)


def is_pharmacy_question(question: str) -> bool:
    text = question.lower()
    terms = set(re.findall(r"[a-z0-9]+", text))
    if terms & PHARMACY_KEYWORDS:
        return True
    return any(re.search(pattern, text) for pattern in PHARMACY_PATTERNS)


def is_inventory_question(question: str) -> bool:
    text = question.lower()
    terms = set(re.findall(r"[a-z0-9]+", text))
    if terms & INVENTORY_KEYWORDS:
        return True
    return any(re.search(pattern, text) for pattern in PHARMACY_PATTERNS)
