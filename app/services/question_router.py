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

ADMIN_DOMAIN_KEYWORDS = {
    "appointments": {
        "appointment",
        "appointments",
        "bookings",
        "booking",
        "visit",
        "visits",
        "flow",
        "frontdesk",
        "front",
        "desk",
        "checkin",
        "checkout",
        "queue",
        "queues",
    },
    "billing": {
        "bill",
        "bills",
        "billing",
        "invoice",
        "invoices",
        "revenue",
        "income",
        "receivable",
        "receivables",
        "unpaid",
        "outstanding",
        "payment",
        "payments",
        "cash",
        "collection",
        "collections",
    },
    "admissions": {
        "admission",
        "admissions",
        "ward",
        "wards",
        "bed",
        "beds",
        "occupancy",
        "occupied",
        "discharge",
        "discharges",
        "inpatient",
    },
    "workforce": {
        "staff",
        "employee",
        "employees",
        "doctor",
        "doctors",
        "nurse",
        "nurses",
        "workforce",
        "roster",
        "profession",
        "position",
        "team",
        "teams",
        "department",
        "departments",
    },
    "patients": {
        "patient",
        "patients",
        "client",
        "clients",
        "registration",
        "registrations",
        "registered",
        "demographic",
        "demographics",
    },
}

ADMIN_DOMAIN_PATTERNS = {
    "appointments": (r"\bpatient load\b", r"\bmissed appointments?\b", r"\bno[- ]show\b"),
    "billing": (r"\btop revenue\b", r"\boutstanding bills?\b", r"\bamount due\b", r"\bhow much\b"),
    "admissions": (r"\bbed occupancy\b", r"\bactive admissions?\b"),
    "workforce": (r"\bhow many staff\b", r"\bstaff count\b"),
    "patients": (r"\bnew patients?\b", r"\bpatient count\b"),
}

TIME_WINDOW_PATTERNS = (
    ("today", 1, (r"\btoday\b", r"\bthis morning\b", r"\bthis afternoon\b", r"\btonight\b")),
    ("week", 7, (r"\bthis week\b", r"\bweekly\b", r"\blast 7 days\b")),
    ("month", 30, (r"\bthis month\b", r"\bmonthly\b", r"\blast 30 days\b")),
    ("quarter", 90, (r"\bthis quarter\b", r"\blast 90 days\b")),
)


def _question_terms(question: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", question.lower()))


def is_pharmacy_question(question: str) -> bool:
    text = question.lower()
    terms = _question_terms(question)
    if terms & PHARMACY_KEYWORDS:
        return True
    return any(re.search(pattern, text) for pattern in PHARMACY_PATTERNS)


def is_inventory_question(question: str) -> bool:
    text = question.lower()
    terms = _question_terms(question)
    if terms & INVENTORY_KEYWORDS:
        return True
    return any(re.search(pattern, text) for pattern in PHARMACY_PATTERNS)


def infer_time_window(question: str) -> tuple[str, int]:
    text = question.lower()
    for label, days, patterns in TIME_WINDOW_PATTERNS:
        if any(re.search(pattern, text) for pattern in patterns):
            return label, days
    return "month", 30


def route_admin_question(question: str) -> list[str]:
    text = question.lower()
    terms = _question_terms(question)
    domains: list[str] = []

    if is_inventory_question(question):
        domains.append("inventory")

    for domain, keywords in ADMIN_DOMAIN_KEYWORDS.items():
        if terms & keywords:
            domains.append(domain)
            continue
        if any(re.search(pattern, text) for pattern in ADMIN_DOMAIN_PATTERNS.get(domain, ())):
            domains.append(domain)

    if not domains:
        return ["overview"]

    ordered = []
    for domain in ("appointments", "billing", "admissions", "inventory", "workforce", "patients"):
        if domain in domains and domain not in ordered:
            ordered.append(domain)
    return ordered or ["overview"]
