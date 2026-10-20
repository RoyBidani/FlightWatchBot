import json
import time

from fast_flights import FlightQuery, Passengers, create_query
from primp import Client
from selectolax.lexbor import LexborHTMLParser

from . import Offer

URL = "https://www.google.com/travel/flights"


def hhmm(t):
    t = [*(t or []), None, None]
    return f"{t[0] or 0:02d}:{t[1] or 0:02d}"


def ymd(d):
    return f"{d[0]}-{d[1]:02d}-{d[2]:02d}"


def search(trip):
    legs = [FlightQuery(date=trip["out"], from_airport=trip["origin"], to_airport=trip["dest"])]
    if trip["ret"]:
        legs.append(FlightQuery(date=trip["ret"], from_airport=trip["dest"], to_airport=trip["origin"]))
    q = create_query(flights=legs, trip="round-trip" if trip["ret"] else "one-way", seat=trip["cabin"],
                     passengers=Passengers(adults=trip["pax"]), max_stops=trip["stops"],
                     currency=trip["currency"], language="en")
    for attempt in range(3):
        html = Client(impersonate="chrome_145", impersonate_os="macos", timeout=30).get(URL, params=q.params()).text
        script = LexborHTMLParser(html).css_first(r"script.ds\:1")
        if script is None:
            raise RuntimeError(f"no results script in page (consent/captcha?): {html[:120]!r}")
        data = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
        if not data.endswith("errorHasStatus: true"):
            break
        code = data[1:data.index(",")]
        if code == "3":
            return []
        time.sleep(2)
    else:
        raise RuntimeError(f"google error status {code} three times in a row")
    payload = json.loads(data)
    offers = []
    for section in (payload[2], payload[3]):
        for k in (section or [None])[0] or []:
            f, price = k[0], (k[1][0] + [None, None])[1] if k[1] else None
            if not price:
                continue
            offers.append(Offer(
                price=price, currency=trip["currency"], airline=", ".join(f[1]),
                segments=[[s[3], s[6], f"{ymd(s[20])} {hhmm(s[8])}", f"{ymd(s[21])} {hhmm(s[10])}",
                           f"{s[22][0]}{s[22][1]}"] for s in f[2]],
                stops=len(f[2]) - 1, duration_min=f[9], bag_included=None if trip["bag"] else False,
                source="Google Flights", link=q.url()))
    return offers
