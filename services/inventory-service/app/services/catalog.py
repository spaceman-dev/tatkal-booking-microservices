"""Train catalogue, availability and opening the Tatkal quota for a run."""
import logging
from datetime import date

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.core.db import is_unique_violation
from app.core.errors import AppError
from app.domain import fares
from app.repository import inventory_repo as repo

log = logging.getLogger(__name__)


class CatalogService:
    def __init__(self, engine: Engine):
        self.engine = engine

    def list_trains(self) -> list[dict]:
        with self.engine.connect() as conn:
            return repo.list_trains(conn)

    def availability(self, train_no: str, journey_date: date) -> dict:
        with self.engine.connect() as conn:
            if repo.get_train(conn, train_no) is None:
                raise AppError(404, "TRAIN_NOT_FOUND", "Train not found", f"Unknown train number {train_no}")
            rows = repo.list_inventory_for_run(conn, train_no, journey_date)
        classes = []
        for row in rows:
            q = fares.quote(row["travel_class"], row["base_fare_paise"], 1)
            classes.append({
                "travel_class": row["travel_class"],
                "total_seats": row["total_seats"],
                "available_seats": row["available_seats"],
                "base_fare_paise": row["base_fare_paise"],
                "tatkal_charge_paise": q.tatkal_charge_paise,
                "fare_per_seat_paise": q.fare_per_seat_paise,
            })
        return {"train_no": train_no, "journey_date": journey_date, "classes": classes}

    def open_quota(self, train_no: str, journey_date: date, travel_class: str, total_seats: int,
                   base_fare_paise: int) -> dict:
        if journey_date < date.today():
            raise AppError(422, "JOURNEY_DATE_IN_PAST", "Journey date is in the past",
                           f"Cannot open a quota for {journey_date}")
        fares.tatkal_charge_paise(travel_class, base_fare_paise)  # BR3: class must support Tatkal
        try:
            with self.engine.begin() as conn:
                if repo.get_train(conn, train_no) is None:
                    raise AppError(404, "TRAIN_NOT_FOUND", "Train not found", f"Unknown train number {train_no}")
                row = repo.insert_inventory(conn, train_no, journey_date, travel_class, total_seats, base_fare_paise)
        except IntegrityError as exc:
            if is_unique_violation(exc, "uq_inventory_train_date_class"):
                raise AppError(409, "QUOTA_ALREADY_OPEN", "Quota already open",
                               f"Tatkal quota for {train_no}/{journey_date}/{travel_class} already exists") from exc
            raise
        log.info("tatkal quota opened", extra={"train_no": train_no, "journey_date": str(journey_date),
                                               "travel_class": travel_class, "total_seats": total_seats})
        return self.get_quota(train_no, journey_date, travel_class)

    def get_quota(self, train_no: str, journey_date: date, travel_class: str) -> dict:
        with self.engine.connect() as conn:
            row = repo.get_inventory(conn, train_no, journey_date, travel_class)
            if row is None:
                raise AppError(404, "INVENTORY_NOT_FOUND", "No Tatkal quota for this train, date and class",
                               f"No Tatkal quota found for train {train_no} on {journey_date} in {travel_class}")
            seats_by_status = repo.count_holds_by_status(conn, row["inventory_id"])
        return {
            **row,
            "seats_held": seats_by_status.get("HELD", 0),
            "seats_confirmed": seats_by_status.get("CONFIRMED", 0),
            "seats_released": seats_by_status.get("RELEASED", 0),
        }
