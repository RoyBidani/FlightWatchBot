import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger("flightbot")


@dataclass
class Offer:
    price: float
    currency: str
    airline: str
    segments: list
    stops: int
    duration_min: int
    bag_included: bool | None
    source: str
    link: str
    bag_fee: float = 0
    bag_kg: int = 0
    ret_date: str = ""
    alt: dict | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


def date_pairs(trip):
    flex, out = trip.get("flex", 0), date.fromisoformat(trip["out"])
    ret = date.fromisoformat(trip["ret"]) if trip["ret"] else None
    for i in range(-flex, flex + 1):
        o = out + timedelta(days=i)
        if o <= date.today():
            continue
        if ret is None:
            yield o.isoformat(), None
            continue
        for j in range(-flex, flex + 1):
            if ret + timedelta(days=j) >= o:
                yield o.isoformat(), (ret + timedelta(days=j)).isoformat()


SOURCES = {}

try:
    from . import google
    SOURCES["google"] = google.search
except ImportError as e:
    log.warning("source google disabled: %s", e)

from . import kiwi
SOURCES["kiwi"] = kiwi.search

if os.getenv("SERPAPI_KEY"):
    from . import serpapi
    SOURCES["serpapi"] = serpapi.search
else:
    log.warning("source serpapi disabled: SERPAPI_KEY not set")
