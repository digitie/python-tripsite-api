"""Site-agnostic reservation data model. See docs/DESIGN.md section 3."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class ReservationStatus(str, Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class ReservationCategory(str, Enum):
    LODGING = "lodging"
    ACTIVITY = "activity"
    DINING = "dining"
    OTHER = "other"


@dataclass(frozen=True)
class Reservation:
    site: str
    reservation_id: str
    category: ReservationCategory
    property_name: str
    room_name: str | None
    check_in: date | None
    check_out: date | None
    visit_at: datetime | None
    status: ReservationStatus
    status_label_raw: str
    price_amount: int | None
    price_currency: str = "KRW"
    booked_at: datetime | None = None
    detail_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation, used by the MCP server (section 9)."""
        return {
            "site": self.site,
            "reservation_id": self.reservation_id,
            "category": self.category.value,
            "property_name": self.property_name,
            "room_name": self.room_name,
            "check_in": self.check_in.isoformat() if self.check_in else None,
            "check_out": self.check_out.isoformat() if self.check_out else None,
            "visit_at": self.visit_at.isoformat() if self.visit_at else None,
            "status": self.status.value,
            "status_label_raw": self.status_label_raw,
            "price_amount": self.price_amount,
            "price_currency": self.price_currency,
            "booked_at": self.booked_at.isoformat() if self.booked_at else None,
            "detail_url": self.detail_url,
            "extra": self.extra,
        }
