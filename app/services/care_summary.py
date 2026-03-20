from datetime import datetime
from typing import Any

from app.models.schemas import PatientSearchResult, RetrievedSource, SessionContext


def _format_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return None


def _latest_label(documents: list[dict[str, Any]], date_field: str, label_field: str) -> str | None:
    if not documents:
        return None
    document = documents[0]
    label = document.get(label_field) or document.get("documentname") or document.get("appointment_reason")
    date_value = _format_date(document.get(date_field))
    if label and date_value:
        return f"{label} ({date_value})"
    return label or date_value


def _pharmacy_inventory_lines(summary: dict[str, Any]) -> list[str]:
    pharmacy = summary.get("pharmacy_inventory") or {}
    lines: list[str] = []

    matched_products = pharmacy.get("matched_products", [])
    if matched_products:
        product_names = ", ".join(str(product.get("name") or "") for product in matched_products[:3] if product.get("name"))
        if product_names:
            lines.append(f"Matched pharmacy products: {product_names}.")

    inventory_matches = pharmacy.get("inventory_matches", [])
    if inventory_matches:
        top_match = inventory_matches[0]
        store_name = top_match.get("store_name") or "store not recorded"
        quantity = top_match.get("quantity")
        reorder_level = top_match.get("reorder_level")
        line = f"Inventory: {top_match.get('name') or 'product'} has quantity {quantity} at {store_name}."
        if reorder_level not in (None, ""):
            line += f" Reorder level is {reorder_level}."
        lines.append(line)

    recent_transactions = pharmacy.get("recent_inventory_transactions", [])
    if recent_transactions:
        latest_transaction = recent_transactions[0]
        lines.append(
            "Latest inventory transaction: "
            f"{latest_transaction.get('name') or latest_transaction.get('type') or 'transaction'} "
            f"{latest_transaction.get('transactioncategory') or 'movement'} "
            f"quantity {latest_transaction.get('quantity')}."
        )

    recent_dispenses = pharmacy.get("recent_inventory_dispenses", [])
    if recent_dispenses:
        latest_dispense = recent_dispenses[0]
        lines.append(
            "Latest facility dispense: "
            f"{latest_dispense.get('type') or 'dispense'} for "
            f"{latest_dispense.get('source') or 'unspecified source'}."
        )

    low_stock_items = pharmacy.get("low_stock_items", [])
    if low_stock_items:
        low_stock_names = ", ".join(
            str(item.get("name") or "")
            for item in low_stock_items[:3]
            if item.get("name")
        )
        if low_stock_names:
            lines.append(f"Low stock items: {low_stock_names}.")

    expiring_batches = pharmacy.get("expiring_batches", [])
    if expiring_batches:
        top_batch = expiring_batches[0]
        lines.append(
            "Nearest expiring batch: "
            f"{top_batch.get('name') or 'product'} batch {top_batch.get('batchNo') or 'unlabeled'} "
            f"expires on {top_batch.get('expirydate')}."
        )

    return lines


def generate_answer(
    question: str,
    patient: PatientSearchResult | None,
    summary: dict[str, Any],
    sources: list[RetrievedSource],
    session: SessionContext | None = None,
) -> str:
    lines: list[str] = []

    if patient is not None:
        patient_data = summary.get("patient", {})
        age_hint = patient_data.get("dob")
        demographics = [
            patient.full_name,
            patient.gender,
            f"DOB {age_hint.date().isoformat()}" if isinstance(age_hint, datetime) else None,
            f"MRN {patient.mrn}" if patient.mrn else None,
        ]
        lines.append("Patient context: " + ", ".join(item for item in demographics if item) + ".")
    elif session is not None:
        lines.append(
            "Facility context: "
            f"{session.active_facility_name or 'Unknown Facility'} "
            f"({session.active_facility_id or 'facility id unavailable'})."
        )

    active_admission = summary.get("active_admission")
    if patient is not None:
        if active_admission:
            ward = active_admission.get("ward_id") or active_admission.get("ward") or "ward not recorded"
            bed = active_admission.get("bed") or "bed not recorded"
            lines.append(f"Admission: currently admitted in {ward}, {bed}.")
        else:
            lines.append("Admission: no active admission record was found.")

    appointments = summary.get("recent_appointments", [])
    appointment_label = _latest_label(appointments, "start_time", "appointment_reason")
    if patient is not None and appointment_label:
        lines.append(f"Latest appointment: {appointment_label}.")

    orders = summary.get("recent_orders", [])
    if patient is not None and orders:
        latest_order = orders[0]
        order_name = latest_order.get("order") or latest_order.get("order_category") or "order"
        order_status = latest_order.get("order_status") or "status not recorded"
        lines.append(f"Latest order: {order_name} with status {order_status}.")

    pharmacy_entries = summary.get("recent_pharmacy_entries", [])
    if patient is not None and pharmacy_entries:
        latest_entry = pharmacy_entries[0]
        product_items = latest_entry.get("productitems") or []
        item_names = ", ".join(
            item.get("name") or "unnamed item"
            for item in product_items[:3]
            if isinstance(item, dict)
        )
        if item_names:
            lines.append(f"Latest pharmacy dispense: {item_names}.")
        else:
            lines.append("Latest pharmacy dispense: a debit product entry was found.")

    labs = summary.get("recent_lab_results", [])
    lab_label = _latest_label(labs, "createdAt", "documentname")
    if patient is not None and lab_label:
        lines.append(f"Latest lab result: {lab_label}.")

    notes = summary.get("recent_clinical_documents", [])
    note_label = _latest_label(notes, "createdAt", "documentname")
    if patient is not None and note_label:
        lines.append(f"Latest clinical note: {note_label}.")

    lines.extend(_pharmacy_inventory_lines(summary))

    if sources:
        top_sources = "; ".join(
            filter(
                None,
                [
                    f"{source.collection}:{source.title or source.document_id}"
                    for source in sources[:3]
                ],
            )
        )
        lines.append(f"Evidence used for '{question}': {top_sources}.")
    else:
        lines.append(f"No matching supporting record was found for '{question}'.")

    lines.append("This answer is retrieval-based from MongoDB records and has not applied an LLM yet.")
    return " ".join(lines)
