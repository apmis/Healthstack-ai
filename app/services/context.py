from typing import Any

import jwt
from fastapi import HTTPException, status
from jwt import InvalidTokenError

from app.core.config import get_settings
from app.core.database import get_database
from app.models.schemas import FacilityOption, SessionContext
from app.services.common import candidate_id_values, object_id_to_str


def _extract_user_id(payload: dict[str, Any]) -> str:
    user_id = payload.get("sub") or payload.get("userId") or payload.get("_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT payload does not contain a user identifier.",
        )
    return str(user_id)


def _decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_aud": False, "verify_iss": False},
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid JWT token.",
        ) from exc


def _extract_location_ids(employee: dict[str, Any]) -> list[str]:
    location_ids: list[str] = []
    for item in employee.get("locations", []) or []:
        if isinstance(item, dict):
            raw_value = item.get("_id") or item.get("locationId") or item.get("location")
        else:
            raw_value = item
        value = object_id_to_str(raw_value)
        if value and value not in location_ids:
            location_ids.append(value)
    return location_ids


def resolve_session(token: str, requested_facility_id: str | None = None) -> SessionContext:
    payload = _decode_token(token)
    user_id = _extract_user_id(payload)
    db = get_database()

    user = db["users"].find_one({"_id": {"$in": candidate_id_values(user_id)}})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user was not found in MongoDB.",
        )

    employees = list(db["employees"].find({"userId": {"$in": candidate_id_values(user_id)}}))
    if not employees:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No employee records were found for this user.",
        )

    facility_lookup_values: list[Any] = []
    for employee in employees:
        facility_value = employee.get("facility")
        if facility_value is None:
            continue
        for candidate in candidate_id_values(str(facility_value)):
            if candidate not in facility_lookup_values:
                facility_lookup_values.append(candidate)

    facilities = list(db["facilities"].find({"_id": {"$in": facility_lookup_values}}))
    facility_map = {object_id_to_str(document["_id"]): document for document in facilities}

    options: list[FacilityOption] = []
    for employee in employees:
        facility_id = object_id_to_str(employee.get("facility"))
        if not facility_id:
            continue
        facility = facility_map.get(facility_id, {})
        options.append(
            FacilityOption(
                facility_id=facility_id,
                facility_name=facility.get("facilityName"),
                employee_id=object_id_to_str(employee.get("_id")) or "",
                roles=[str(role) for role in employee.get("roles", []) if role],
                accesslevel=employee.get("accesslevel"),
            )
        )

    selected_employee: dict[str, Any] | None = None
    if requested_facility_id:
        selected_employee = next(
            (
                employee
                for employee in employees
                if object_id_to_str(employee.get("facility")) == requested_facility_id
            ),
            None,
        )
        if not selected_employee:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The authenticated user does not belong to the requested facility.",
            )
    elif len(options) == 1:
        selected_employee = employees[0]

    session = SessionContext(
        user_id=object_id_to_str(user.get("_id")) or user_id,
        user_email=user.get("email"),
        available_facilities=options,
        requires_facility_selection=selected_employee is None and len(options) > 1,
    )

    if not selected_employee:
        return session

    active_facility_id = object_id_to_str(selected_employee.get("facility"))
    facility = facility_map.get(active_facility_id or "", {})
    session.employee_id = object_id_to_str(selected_employee.get("_id"))
    session.active_facility_id = active_facility_id
    session.active_facility_name = facility.get("facilityName")
    session.roles = [str(role) for role in selected_employee.get("roles", []) if role]
    session.accesslevel = selected_employee.get("accesslevel")
    session.location_ids = _extract_location_ids(selected_employee)
    session.requires_facility_selection = False
    return session


def require_session(token: str, active_facility_id: str) -> SessionContext:
    session = resolve_session(token, active_facility_id)
    if not session.active_facility_id or not session.employee_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An active facility must be selected for this operation.",
        )
    return session

