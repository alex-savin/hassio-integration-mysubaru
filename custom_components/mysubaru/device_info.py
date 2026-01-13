"""Helpers for building consistent device info with model/year/trim."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .const import DOMAIN


def _detect_model_and_trim(model_code: str | None) -> Tuple[str | None, str | None]:
    if not model_code or len(model_code) < 3:
        return None, None

    code = model_code.upper()
    model_indicator = code[0]
    trim_indicator = code[1:3]

    model_map = {
        "P": "Outback",
        "S": "Forester",
        "L": "Legacy",
        "C": "Crosstrek",
        "K": "Crosstrek",
        "A": "Ascent",
        "W": "WRX",
        "I": "Impreza",
        "B": "BRZ",
        "T": "Solterra",
    }
    model = model_map.get(model_indicator)

    trim_map = {
        "Outback": {
            "DL": "Limited XT",
            "FL": "Limited",
            "CL": "Convenience",
            "BL": "Base",
            "DH": "Limited XT Hybrid",
            "FH": "Limited Hybrid",
            "CH": "Convenience Hybrid",
            "BH": "Base Hybrid",
        },
        "Forester": {
            "DL": "Limited",
            "FL": "Premier",
            "CL": "Convenience",
            "BL": "Base",
            "DH": "Limited Hybrid",
            "FH": "Premier Hybrid",
            "CH": "Convenience Hybrid",
            "BH": "Base Hybrid",
        },
        "Legacy": {
            "DL": "Limited",
            "FL": "Limited XT",
            "CL": "Convenience",
            "BL": "Base",
        },
        "Crosstrek": {
            "DL": "Limited",
            "FL": "Premier",
            "CL": "Convenience",
            "BL": "Base",
            "DH": "Limited Hybrid",
            "FH": "Premier Hybrid",
            "CH": "Convenience Hybrid",
            "BH": "Base Hybrid",
            "RH": "Convenience",
        },
        "Ascent": {
            "DL": "Limited",
            "FL": "Premier",
            "CL": "Convenience",
            "BL": "Base",
        },
        "WRX": {
            "DL": "Limited",
            "FL": "STI",
            "CL": "Convenience",
            "BL": "Base",
        },
        "Impreza": {
            "DL": "Limited",
            "FL": "Premier",
            "CL": "Convenience",
            "BL": "Base",
        },
        "BRZ": {
            "DL": "Limited",
            "FL": "STI",
            "CL": "Convenience",
            "BL": "Base",
        },
        "Solterra": {
            "DL": "Limited",
            "FL": "Premier",
            "CL": "Convenience",
            "BL": "Base",
        },
    }

    trim = None
    if model and model in trim_map:
        trim = trim_map[model].get(trim_indicator)

    return model, trim


def _coalesce_model(vehicle: Dict[str, Any]) -> str | None:
    return vehicle.get("ModelName") or vehicle.get("CarName") or vehicle.get("CarNickname")


def _coalesce_trim(vehicle: Dict[str, Any]) -> str | None:
    return vehicle.get("TrimName") or vehicle.get("Trim") or vehicle.get("ModelTrim")


def build_device_info(vin: str, vehicle: Dict[str, Any], base_name: str) -> Dict[str, Any]:
    model = _coalesce_model(vehicle)
    trim = _coalesce_trim(vehicle)
    detected_model, detected_trim = _detect_model_and_trim(vehicle.get("ModelCode"))

    if not model and detected_model:
        model = detected_model
    if not trim and detected_trim:
        trim = detected_trim

    year_raw = vehicle.get("ModelYear")
    year = str(year_raw).strip() if year_raw else ""

    display_model = model
    if trim and display_model:
        if trim.lower() not in display_model.lower():
            display_model = f"{display_model} {trim}".strip()
    elif trim and not display_model:
        display_model = trim

    suggested_name_parts = [part for part in (model, year) if part]
    suggested_name = " ".join(suggested_name_parts)

    return {
        "identifiers": {(DOMAIN, vin)},
        "manufacturer": "Subaru",
        "name": base_name,
        "model": display_model,
        "hw_version": trim if trim else None,
        "sw_version": year if year else None,
        "suggested_area": suggested_name if suggested_name else None,
    }
