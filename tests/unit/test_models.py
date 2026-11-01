from datetime import date, datetime, timezone

from tripsite_api.models import Reservation, ReservationCategory, ReservationStatus


def test_to_dict_lodging_reservation_serializes_dates_as_iso_strings() -> None:
    reservation = Reservation(
        site="yanolja",
        reservation_id="R-1",
        category=ReservationCategory.LODGING,
        property_name="테스트 호텔",
        room_name="디럭스",
        check_in=date(2026, 12, 24),
        check_out=date(2026, 12, 25),
        visit_at=None,
        status=ReservationStatus.CONFIRMED,
        status_label_raw="예약완료",
        price_amount=120_000,
        booked_at=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc),
        detail_url="https://example.invalid/r/1",
        extra={"raw_note": "창측"},
    )

    data = reservation.to_dict()

    assert data["category"] == "lodging"
    assert data["status"] == "confirmed"
    assert data["check_in"] == "2026-12-24"
    assert data["check_out"] == "2026-12-25"
    assert data["visit_at"] is None
    assert data["booked_at"] == "2026-11-01T09:00:00+00:00"
    assert data["extra"] == {"raw_note": "창측"}


def test_to_dict_non_lodging_reservation_uses_visit_at() -> None:
    reservation = Reservation(
        site="naver",
        reservation_id="R-2",
        category=ReservationCategory.DINING,
        property_name="테스트 식당",
        room_name=None,
        check_in=None,
        check_out=None,
        visit_at=datetime(2026, 12, 24, 19, 0, tzinfo=timezone.utc),
        status=ReservationStatus.CONFIRMED,
        status_label_raw="예약확정",
        price_amount=None,
    )

    data = reservation.to_dict()

    assert data["category"] == "dining"
    assert data["check_in"] is None
    assert data["check_out"] is None
    assert data["visit_at"] == "2026-12-24T19:00:00+00:00"
    assert data["price_currency"] == "KRW"
