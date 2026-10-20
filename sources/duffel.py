import os
import re
from datetime import date
from urllib.parse import quote

import requests

from . import Offer

API = "https://api.duffel.com/air"
BAG_LOOKUPS = 5
FX = {}


def headers():
    return {"Authorization": f"Bearer {os.environ['DUFFEL_TOKEN']}", "Duffel-Version": "v2",
            "Accept": "application/json", "Content-Type": "application/json"}


def convert(amount, cur, want):
    if cur == want:
        return amount
    # ponytail: rates cached in memory for the day; if frankfurter is down the source fails for this check
    key = (cur, want, date.today())
    if key not in FX:
        r = requests.get("https://api.frankfurter.dev/v1/latest", params={"base": cur, "symbols": want}, timeout=30)
        r.raise_for_status()
        FX[key] = r.json()["rates"][want]
    return amount * FX[key]


def minutes(iso):
    d, h, m = (int(x or 0) for x in re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", iso).groups())
    return d * 1440 + h * 60 + m


def bags_included(offer):
    return all(sum(b["quantity"] for b in p.get("baggages", []) if b["type"] == "checked") >= 1
               for sl in offer["slices"] for seg in sl["segments"] for p in seg["passengers"])


def bag_fee(services, segment_ids, pax):
    best = {}
    for s in services:
        md = s.get("metadata") or {}
        if s["type"] == "baggage" and md.get("type") == "checked" and (md.get("maximum_weight_kg") or 23) >= 23:
            k = frozenset(s["segment_ids"])
            if k not in best or float(s["total_amount"]) < float(best[k]["total_amount"]):
                best[k] = s
    covered = set().union(*best) if best else set()
    if not covered >= set(segment_ids):
        return None, None
    # ponytail: assumes the airline's bag groups don't overlap (one per slice or per segment), true for the offers seen so far
    return sum(float(s["total_amount"]) for s in best.values()) * pax, next(iter(best.values()))["total_currency"]


def search(trip):
    legs = [(trip["origin"], trip["dest"], trip["out"])] + ([(trip["dest"], trip["origin"], trip["ret"])] if trip["ret"] else [])
    r = requests.post(f"{API}/offer_requests", params={"return_offers": "true", "supplier_timeout": 15000}, headers=headers(),
                      timeout=30, json={"data": {
                          "slices": [{"origin": o, "destination": d, "departure_date": day} for o, d, day in legs],
                          "passengers": [{"type": "adult"}] * trip["pax"], "cabin_class": trip["cabin"].replace("-", "_"),
                          "max_connections": trip["stops"]}})
    r.raise_for_status()
    raw = sorted(r.json()["data"]["offers"], key=lambda o: float(o["total_amount"]))
    link = f"https://www.google.com/travel/flights?hl=en&curr={trip['currency']}&q=" + quote(
        f"Flights from {trip['origin']} to {trip['dest']} on {trip['out']}" + (f" through {trip['ret']}" if trip["ret"] else " one way"))
    offers, lookups = [], 0
    for o in raw:
        price, cur = float(o["total_amount"]), o["total_currency"]
        included, fee = bags_included(o), 0.0
        if trip["bag"] and not included and lookups < BAG_LOOKUPS:
            lookups += 1
            g = requests.get(f"{API}/offers/{o['id']}", params={"return_available_services": "true"}, headers=headers(), timeout=30)
            g.raise_for_status()
            seg_ids = [seg["id"] for sl in o["slices"] for seg in sl["segments"]]
            add, add_cur = bag_fee(g.json()["data"].get("available_services") or [], seg_ids, trip["pax"])
            if add is not None:
                fee, included = convert(add, add_cur, cur), True
        out = o["slices"][0]
        offers.append(Offer(
            price=round(convert(price + fee, cur, trip["currency"])), currency=trip["currency"],
            airline=o["owner"]["name"],
            segments=[[s["origin"]["iata_code"], s["destination"]["iata_code"], s["departing_at"][:16].replace("T", " "),
                       s["arriving_at"][:16].replace("T", " "),
                       f"{s['marketing_carrier']['iata_code']}{s['marketing_carrier_flight_number']}"] for s in out["segments"]],
            stops=len(out["segments"]) - 1, duration_min=minutes(out["duration"]),
            bag_included=(True if included else None) if trip["bag"] else False,
            bag_fee=round(convert(fee, cur, trip["currency"])), source="Duffel (airline fare)", link=link))
    return offers
