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


def _format_amount(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value)


def _render_breakdown(items: list[dict[str, Any]], label_key: str = "label", count_key: str = "count", limit: int = 3) -> str | None:
    parts = []
    for item in items[:limit]:
        label = item.get(label_key)
        count = item.get(count_key)
        if label in (None, "") or count in (None, ""):
            continue
        parts.append(f"{label} ({count})")
    return ", ".join(parts) if parts else None


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


def generate_admin_answer(
    question: str,
    summary: dict[str, Any],
    sources: list[RetrievedSource],
    session: SessionContext,
) -> str:
    lines: list[str] = []
    time_window = summary.get("time_window") or {}
    label = time_window.get("label") or "current"
    days = time_window.get("days")
    domains = summary.get("domains") or []

    lines.append(
        "Facility context: "
        f"{session.active_facility_name or 'Unknown Facility'} "
        f"({session.active_facility_id or 'facility id unavailable'})."
    )
    if label == "today":
        lines.append("Reporting window: today.")
    elif days:
        lines.append(f"Reporting window: last {days} days ({label}).")

    appointments = summary.get("appointments") or {}
    if appointments:
        line = f"Appointments: {appointments.get('window_total', 0)} records in window."
        status_line = _render_breakdown(appointments.get("status_breakdown", []))
        location_line = _render_breakdown(appointments.get("location_breakdown", []))
        if status_line:
            line += f" Status mix: {status_line}."
        if location_line:
            line += f" Top locations: {location_line}."
        lines.append(line)

    billing = summary.get("billing") or {}
    if billing:
        totals = billing.get("totals") or {}
        line = (
            "Billing: "
            f"{billing.get('window_total', 0)} bills, "
            f"service value { _format_amount(totals.get('service_amount', 0)) }, "
            f"paid { _format_amount(totals.get('amount_paid', 0)) }, "
            f"outstanding { _format_amount(totals.get('outstanding_balance', 0)) }."
        )
        status_line = _render_breakdown(billing.get("status_breakdown", []))
        if status_line:
            line += f" Status mix: {status_line}."
        top_services = billing.get("top_services") or []
        if top_services:
            service_bits = ", ".join(
                f"{item.get('service_name')} ({_format_amount(item.get('amount'))})"
                for item in top_services[:3]
                if item.get("service_name")
            )
            if service_bits:
                line += f" Top services: {service_bits}."
        lines.append(line)

    admissions = summary.get("admissions") or {}
    if admissions:
        line = (
            "Admissions: "
            f"{admissions.get('active_count', 0)} active and "
            f"{admissions.get('window_total', 0)} started in the window."
        )
        ward_line = _render_breakdown(admissions.get("ward_breakdown", []))
        if ward_line:
            line += f" Ward mix: {ward_line}."
        lines.append(line)

    workforce = summary.get("workforce") or {}
    if workforce:
        line = f"Workforce: {workforce.get('total_employees', 0)} employees."
        profession_line = _render_breakdown(workforce.get("profession_breakdown", []))
        position_line = _render_breakdown(workforce.get("position_breakdown", []))
        if profession_line:
            line += f" Top professions: {profession_line}."
        if position_line:
            line += f" Top positions: {position_line}."
        lines.append(line)

    patients = summary.get("patients") or {}
    if patients:
        lines.append(
            "Patient registrations: "
            f"{patients.get('total_patients', 0)} total patients, "
            f"{patients.get('new_registrations', 0)} new in the reporting window."
        )

    lines.extend(_pharmacy_inventory_lines(summary))

    if not domains:
        lines.append("No domain routing was inferred from the question, so the answer used the broad facility snapshot.")

    if sources:
        top_sources = "; ".join(
            filter(
                None,
                [f"{source.collection}:{source.title or source.document_id}" for source in sources[:4]],
            )
        )
        lines.append(f"Evidence used for '{question}': {top_sources}.")
    else:
        lines.append(f"No matching supporting record was found for '{question}'.")

    lines.append("This answer is retrieval-based from facility operations data and has not applied an LLM yet.")
    return " ".join(lines)
