"""Government scheme eligibility checker.

Rule-based and deliberately conservative: it tells a farmer whether they are
LIKELY eligible, POSSIBLY eligible (more details needed) or NOT eligible for a
few central schemes, explains why, and says how to apply. Scheme facts live in
knowledge_base/schemes.json so extension workers can update them without
touching code. Results are guidance only; the official portal decides.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SCHEMES_FILE = Path(__file__).resolve().parent.parent / "knowledge_base" / "schemes.json"

DISCLAIMER = (
    "This is a guide, not an official decision. Eligibility rules and amounts change; "
    "confirm on the official portal or with your local agriculture office before applying."
)
STATE_NOTE = (
    "Your state government may also run its own farmer schemes. "
    "Ask your district agriculture office."
)


class FarmerProfile(BaseModel):
    """What the farmer tells us. Every yes/no field may be left null (unknown)."""

    state: str | None = Field(default=None, max_length=100)
    owns_land: bool | None = None
    land_hectares: float | None = Field(default=None, ge=0, le=100000)
    is_tenant_or_sharecropper: bool | None = None
    institutional_landholder: bool | None = None
    holds_constitutional_post: bool | None = None
    govt_employee_or_pensioner: bool | None = None
    pays_income_tax: bool | None = None
    practicing_professional: bool | None = None
    grows_notified_crop: bool | None = None


@lru_cache(maxsize=1)
def load_schemes() -> dict[str, dict[str, Any]]:
    """Scheme facts keyed by id."""
    if not SCHEMES_FILE.exists():
        raise FileNotFoundError(f"Scheme file not found: {SCHEMES_FILE}")

    with SCHEMES_FILE.open("r", encoding="utf-8") as file:
        records = json.load(file)

    if not isinstance(records, list):
        raise ValueError("schemes.json must contain a JSON list.")

    return {str(r["id"]): r for r in records if isinstance(r, dict) and "id" in r}


def list_schemes() -> list[dict[str, Any]]:
    return list(load_schemes().values())


# --------------------------------------------------------------------------
# Rules. Each returns (status, reasons, missing)
#   status: likely_eligible | possibly_eligible | not_eligible
# --------------------------------------------------------------------------
Result = tuple[str, list[str], list[str]]

PM_KISAN_EXCLUSIONS = {
    "institutional_landholder": "Institutional landholders are excluded.",
    "holds_constitutional_post": "Families of current or former holders of constitutional posts are excluded.",
    "govt_employee_or_pensioner": (
        "Serving or retired government officers and employees (and pensioners with a "
        "higher pension) are excluded, except multi-tasking, Class IV and Group D staff."
    ),
    "pays_income_tax": "Families that paid income tax in the last assessment year are excluded.",
    "practicing_professional": (
        "Registered professionals such as doctors, engineers, lawyers, chartered "
        "accountants and architects who practise are excluded."
    ),
}


# Plain questions to ask when an exclusion field was left blank.
PM_KISAN_QUESTIONS = {
    "institutional_landholder": "Is the land held by an institution rather than by a farmer family?",
    "holds_constitutional_post": (
        "Does anyone in your family hold, or has anyone held, a constitutional post "
        "(for example minister, MP, MLA or mayor)?"
    ),
    "govt_employee_or_pensioner": (
        "Is anyone in your family a serving or retired government officer or employee, or a pensioner?"
    ),
    "pays_income_tax": "Did your family pay income tax in the last assessment year?",
    "practicing_professional": (
        "Does anyone in your family practise as a registered doctor, engineer, lawyer, "
        "chartered accountant or architect?"
    ),
}


def _pm_kisan(profile: FarmerProfile) -> Result:
    triggered = [text for field, text in PM_KISAN_EXCLUSIONS.items() if getattr(profile, field) is True]
    if triggered:
        return "not_eligible", triggered, []

    if profile.owns_land is False:
        return (
            "not_eligible",
            [
                "PM-KISAN is for families that hold cultivable land in their own name. "
                "Farmers who only cultivate someone else's land are generally not covered."
            ],
            [],
        )

    missing: list[str] = []
    if profile.owns_land is None:
        missing.append("Do you own agricultural land (in your or your family's name)?")
    missing += [
        question
        for field, question in PM_KISAN_QUESTIONS.items()
        if getattr(profile, field) is None
    ]

    if profile.owns_land is True and not missing:
        return (
            "likely_eligible",
            ["You own farmland and none of the exclusion categories apply to you."],
            [],
        )

    reasons = ["No exclusion applies so far, but a few details are still needed."]
    return "possibly_eligible", reasons, missing


def _pmfby(profile: FarmerProfile) -> Result:
    if profile.grows_notified_crop is False:
        return (
            "not_eligible",
            ["PMFBY covers only crops notified for insurance in your area for that season."],
            [],
        )

    reasons = [
        "Enrolment is voluntary and open to farmers who grow notified crops, "
        "including tenant farmers and sharecroppers."
    ]
    if profile.grows_notified_crop is True:
        return "likely_eligible", reasons, []

    return (
        "possibly_eligible",
        reasons,
        ["Is your crop notified for insurance in your area this season? Ask your bank or agriculture office."],
    )


def _kcc(profile: FarmerProfile) -> Result:
    cultivates = profile.owns_land is True or profile.is_tenant_or_sharecropper is True
    if cultivates:
        return (
            "likely_eligible",
            [
                "Farmers who cultivate their own land, as well as tenant farmers and "
                "sharecroppers, can apply for a Kisan Credit Card at a bank."
            ],
            [],
        )

    if profile.owns_land is False and profile.is_tenant_or_sharecropper is False:
        return (
            "possibly_eligible",
            [
                "KCC is mainly for people who cultivate land, but dairy, fishery and other "
                "allied farming activities can also qualify. Ask your bank."
            ],
            [],
        )

    return (
        "possibly_eligible",
        ["KCC is available to farmers who own or lease land."],
        ["Do you own land, or cultivate land as a tenant or sharecropper?"],
    )


RULES: dict[str, Callable[[FarmerProfile], Result]] = {
    "pm_kisan": _pm_kisan,
    "pmfby": _pmfby,
    "kcc": _kcc,
}


def evaluate_schemes(profile: FarmerProfile) -> dict[str, Any]:
    """Check the profile against every scheme that has a rule."""
    schemes = load_schemes()
    results = []

    for scheme_id, rule in RULES.items():
        info = schemes.get(scheme_id)
        if info is None:
            logger.warning("No scheme facts for '%s' in %s", scheme_id, SCHEMES_FILE)
            continue

        status, reasons, missing = rule(profile)
        results.append(
            {
                "id": scheme_id,
                "name": info.get("name"),
                "status": status,
                "reasons": reasons,
                "missing_information": missing,
                "benefit": info.get("benefit"),
                "how_to_apply": info.get("how_to_apply"),
                "documents": info.get("documents", []),
                "official_url": info.get("official_url"),
                "helpline": info.get("helpline"),
                "last_verified": info.get("last_verified"),
            }
        )

    order = {"likely_eligible": 0, "possibly_eligible": 1, "not_eligible": 2}
    results.sort(key=lambda item: order.get(item["status"], 3))

    return {
        "results": results,
        "disclaimer": DISCLAIMER,
        "state_note": STATE_NOTE,
    }