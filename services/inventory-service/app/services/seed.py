import logging
from datetime import date, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from app.repository.tables import seat_inventory, trains

log = logging.getLogger(__name__)

# train_no, name, from, to, departs, {class: (tatkal seats, base fare in Rs)}
# fares are approximate, not the current IRCTC chart
DEMO_TRAINS = [
    ("12951", "Mumbai Central - New Delhi Rajdhani Express", "MMCT", "NDLS", "17:00",
     {"3A": (8, 2495), "2A": (4, 3515)}),
    ("12627", "Karnataka Express", "SBC", "NDLS", "19:20",
     {"SL": (12, 905), "3A": (8, 2385), "2A": (4, 3470)}),
    ("22691", "KSR Bengaluru - Hazrat Nizamuddin Rajdhani", "SBC", "NZM", "20:00",
     {"3A": (8, 3180), "2A": (4, 4400)}),
    ("12009", "Mumbai Central - Ahmedabad Shatabdi Express", "MMCT", "ADI", "06:20",
     {"CC": (10, 1055), "EC": (4, 2035)}),
]


def seed_demo_data(engine: Engine, days: int) -> None:
    today = date.today()
    with engine.begin() as conn:
        for train_no, name, src, dst, departs, classes in DEMO_TRAINS:
            conn.execute(
                pg_insert(trains)
                .values(train_no=train_no, name=name, from_station=src, to_station=dst, departure_time=departs)
                .on_conflict_do_nothing(index_elements=["train_no"])
            )
            for offset in range(1, days + 1):
                for travel_class, (seats, fare_rupees) in classes.items():
                    conn.execute(
                        pg_insert(seat_inventory)
                        .values(train_no=train_no, journey_date=today + timedelta(days=offset),
                                travel_class=travel_class, total_seats=seats, available_seats=seats,
                                base_fare_paise=fare_rupees * 100)
                        .on_conflict_do_nothing(constraint="uq_inventory_train_date_class")
                    )
    log.info("demo data seeded", extra={"trains": len(DEMO_TRAINS), "days": days})
