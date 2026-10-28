import os

import requests

CABINS = {"economy": 1, "premium-economy": 2, "business": 3, "first": 4}


def call(trip, out, ret, **extra):
    params = {"engine": "google_flights", "api_key": os.environ["SERPAPI_KEY"], "hl": "en", "gl": "il",
              "departure_id": trip["origin"], "arrival_id": trip["dest"], "outbound_date": out,
              "type": 1 if ret else 2, "currency": trip["currency"], "adults": trip["pax"],
              "stops": trip["stops"] + 1, "travel_class": CABINS[trip["cabin"]], **extra}
    if ret:
        params["return_date"] = ret
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError(f"serpapi: {j['error']}")
    return j


def flights(j):
    return j.get("best_flights", []) + j.get("other_flights", [])


def numbers(item):
    return [f["flight_number"].replace(" ", "") for f in item["flights"]]


def cheapest_seller(trip, offer):
    out, ret = offer.segments[0][2][:10], offer.ret_date or None
    want = [s[4] for s in offer.segments]
    item = next((i for i in flights(call(trip, out, ret)) if numbers(i) == want), None)
    if item is None:
        return None, None
    if ret:
        returns = flights(call(trip, out, ret, departure_token=item["departure_token"]))
        if not returns:
            return None, None
        item = min(returns, key=lambda i: i.get("price") or 10**9)
    options = [o.get("together") or {} for o in call(trip, out, ret, booking_token=item["booking_token"]).get("booking_options", [])]
    best = min((o for o in options if o.get("price")), key=lambda o: o["price"], default=None)
    return (best["book_with"], best["price"]) if best else (None, None)
