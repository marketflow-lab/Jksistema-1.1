"""Safe, VIN-free contracts for decoded vehicle identity facts."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Final, Literal


VEHICLE_IDENTITY_SCHEMA: Final = "jk.vehicle_identity_facts.v1"
VEHICLE_IDENTITY_POLICY: Final = "jk_public_vin_decode_v1"

VehicleIdentityStatus = Literal[
    "confirmed",
    "partial",
    "ambiguous",
    "not_found",
    "invalid",
    "unavailable",
]

VEHICLE_IDENTITY_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "confirmed",
        "partial",
        "ambiguous",
        "not_found",
        "invalid",
        "unavailable",
    }
)

_SAFE_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class VehicleIdentityFactsV1:
    """Allowlisted vehicle facts safe to pass to later pipeline stages.

    Deliberately, this contract has no VIN, buyer identifier, raw decoder
    payload, or free-form decoder error text.
    """

    status: VehicleIdentityStatus
    manufacturer: str = ""
    make: str = ""
    model: str = ""
    model_year: str = ""
    series: str = ""
    vehicle_type: str = ""
    body_class: str = ""
    engine_model: str = ""
    engine_displacement_l: str = ""
    fuel_type: str = ""
    plant_country: str = ""
    plant_company: str = ""
    source: str = "nhtsa_vpic"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in VEHICLE_IDENTITY_STATUSES:
            raise ValueError("vehicle_identity_status_invalid")
        if self.reason and not _SAFE_REASON_RE.fullmatch(self.reason):
            raise ValueError("vehicle_identity_reason_invalid")

    def as_dict(self) -> dict[str, str]:
        return {
            "schema": VEHICLE_IDENTITY_SCHEMA,
            "policy": VEHICLE_IDENTITY_POLICY,
            **asdict(self),
        }

    @property
    def has_vehicle_facts(self) -> bool:
        return any(
            (
                self.manufacturer,
                self.make,
                self.model,
                self.model_year,
                self.series,
                self.vehicle_type,
                self.body_class,
                self.engine_model,
                self.engine_displacement_l,
                self.fuel_type,
                self.plant_country,
                self.plant_company,
            )
        )


def empty_vehicle_identity(
    status: VehicleIdentityStatus,
    *,
    reason: str,
) -> VehicleIdentityFactsV1:
    """Build a content-free outcome without accepting arbitrary messages."""

    return VehicleIdentityFactsV1(status=status, reason=reason)


__all__ = [
    "VEHICLE_IDENTITY_POLICY",
    "VEHICLE_IDENTITY_SCHEMA",
    "VEHICLE_IDENTITY_STATUSES",
    "VehicleIdentityFactsV1",
    "VehicleIdentityStatus",
    "empty_vehicle_identity",
]
