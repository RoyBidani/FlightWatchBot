import base64
import json
import time

from fast_flights import FlightQuery, Passengers, create_query
from primp import Client
from selectolax.lexbor import LexborHTMLParser

from . import Offer, date_pairs

URL = "https://www.google.com/travel/flights"


def hhmm(t):
    t = [*(t or []), None, None]
    return f"{t[0] or 0:02d}:{t[1] or 0:02d}"


def ymd(d):
    return f"{d[0]}-{d[1]:02d}-{d[2]:02d}"


def varint(n):
    out = b""
    while True:
        b, n = n & 0x7F, n >> 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def field(num, payload):
    return varint(num << 3 | 2) + varint(len(payload)) + payload


def pick(seg):
    return b"".join(field(k, v.encode()) for k, v in ((1, seg[0]), (2, seg[2][:10]), (3, seg[1]), (5, seg[4][:2]), (6, seg[4][2:])))


def tfs(trip, out, ret, picks=((), ())):
    legs = [FlightQuery(date=out, from_airport=trip["origin"], to_airport=trip["dest"])]
    if ret:
        legs.append(FlightQuery(date=ret, from_airport=trip["dest"], to_airport=trip["origin"]))
    info = create_query(flights=legs, trip="round-trip" if ret else "one-way", seat=trip["cabin"],
                        passengers=Passengers(adults=trip["pax"]), max_stops=trip["stops"],
                        currency=trip["currency"], language="en").pb()
    # ponytail: field 4 of each leg = the chosen flight (from, date, to, airline, number), decoded from a shared Google link; fast-flights' proto lacks it
    data = [fd.SerializeToString() + b"".join(field(4, pick(sg)) for sg in pk) for fd, pk in zip(info.data, picks)]
    del info.data[:]
    return base64.urlsafe_b64encode(info.SerializeToString() + b"".join(field(3, d) for d in data)).decode().rstrip("=")


def booking_link(trip, o):
    out, ret = o.segments[0][2][:10], o.ret_date or None
    back = min(search_dates(trip, out, ret, (o.segments, ())), key=lambda x: x.price, default=None) if ret else None
    if ret and not back:
        return o.link
    return f"{URL}/booking?tfs={tfs(trip, out, ret, (o.segments, back.segments if back else ()))}&hl=en&curr={trip['currency']}"


def search(trip):
    offers, errors, pairs = [], [], list(date_pairs(trip))
    # ponytail: one Google request per date pair, ±3 days = 49 requests per check; a range query if Google ever minds
    for out, ret in pairs:
        try:
            offers += search_dates(trip, out, ret)
        except Exception as e:
            errors.append(e)
    if errors and len(errors) == len(pairs):
        raise errors[0]
    return offers


def search_dates(trip, out, ret, picks=((), ())):
    params = {"tfs": tfs(trip, out, ret, picks), "hl": "en", "curr": trip["currency"]}
    link = f"{URL}/search?tfs={params['tfs']}&hl=en&curr={trip['currency']}"
    for attempt in range(3):
        html = Client(impersonate="chrome_145", impersonate_os="macos", timeout=30).get(URL, params=params).text
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
                source="Google Flights", link=link, ret_date=ret or ""))
    return offers
