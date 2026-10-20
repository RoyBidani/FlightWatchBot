import os

import requests

from . import Offer

CABINS = {"economy": 1, "premium-economy": 2, "business": 3, "first": 4}


def search(trip):
    params = {"engine": "google_flights", "api_key": os.environ["SERPAPI_KEY"], "hl": "en",
              "departure_id": trip["origin"], "arrival_id": trip["dest"], "outbound_date": trip["out"],
              "type": 1 if trip["ret"] else 2, "currency": trip["currency"], "adults": trip["pax"],
              "stops": trip["stops"] + 1, "travel_class": CABINS[trip["cabin"]]}
    if trip["ret"]:
        params["return_date"] = trip["ret"]
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
    r.raise_for_status()
    j = r.json()
    link = j.get("search_metadata", {}).get("google_flights_url", "https://www.google.com/travel/flights")
    return [Offer(
        price=it["price"], currency=trip["currency"],
        airline=", ".join(dict.fromkeys(s["airline"] for s in it["flights"])),
        segments=[[s["departure_airport"]["id"], s["arrival_airport"]["id"], s["departure_airport"]["time"],
                   s["arrival_airport"]["time"], s.get("flight_number", "")] for s in it["flights"]],
        # ponytail: SerpApi's bags param is carry-on only, checked bag fee unknown
        stops=len(it["flights"]) - 1, duration_min=it["total_duration"], bag_included=None if trip["bag"] else False,
        source="Google Flights (SerpApi)", link=link)
        for it in j.get("best_flights", []) + j.get("other_flights", []) if it.get("price")]
