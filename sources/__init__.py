import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


SOURCES = {}

try:
    from . import google
    SOURCES["google"] = google.search
except ImportError as e:
    log.warning("source google disabled: %s", e)

if os.getenv("DUFFEL_TOKEN"):
    from . import duffel
    SOURCES["duffel"] = duffel.search
else:
    log.warning("source duffel disabled: DUFFEL_TOKEN not set")

if os.getenv("SERPAPI_KEY"):
    from . import serpapi
    SOURCES["serpapi"] = serpapi.search
else:
    log.warning("source serpapi disabled: SERPAPI_KEY not set")
