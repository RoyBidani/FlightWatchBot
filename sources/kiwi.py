import requests

from datetime import date, timedelta

from . import Offer

URL = "https://api.skypicker.com/umbrella/v2/graphql?featureName=SearchItinerariesQuery"
CABINS = {"economy": "ECONOMY", "premium-economy": "PREMIUM_ECONOMY", "business": "BUSINESS", "first": "FIRST_CLASS"}
LEG = ("{ duration sectorSegments { segment { code carrier { code name } "
       "source { localTime station { code } } destination { localTime station { code } } } } }")
QUERY = """query Q($search: {input}, $filter: ItinerariesFilterInput, $options: ItinerariesOptionsInput) {{
  {root}(search: $search, filter: $filter, options: $options) {{
    __typename
    ... on AppError {{ error: message }}
    ... on Itineraries {{ itineraries {{ ... on {kind} {{
      price {{ amount }}
      bagsInfo {{ includedCheckedBags checkedBagTiers {{ tierPrice {{ amount }} bags {{ weight {{ value }} }} }} }}
      bookingOptions {{ edges {{ node {{ bookingUrl }} }} }}
      {legs}
    }} }} }}
  }}
}}"""


def search(trip):
    rt = bool(trip["ret"])
    query = QUERY.format(input="SearchReturnInput" if rt else "SearchOnewayInput", root="returnItineraries" if rt else "onewayItineraries",
                         kind="ItineraryReturn" if rt else "ItineraryOneWay", legs=f"outbound {LEG} inbound {LEG}" if rt else f"sector {LEG}")
    flex = timedelta(days=trip.get("flex", 0))
    window = lambda d: {"start": f"{date.fromisoformat(d) - flex}T00:00:00", "end": f"{date.fromisoformat(d) + flex}T23:59:59"}
    itinerary = {"source": {"ids": [f"Station:airport:{trip['origin']}"]}, "destination": {"ids": [f"Station:airport:{trip['dest']}"]},
                 "outboundDepartureDate": window(trip["out"])}
    if rt:
        itinerary["inboundDepartureDate"] = window(trip["ret"])
    variables = {
        "search": {"itinerary": itinerary, "cabinClass": {"cabinClass": CABINS[trip["cabin"]], "applyMixedClasses": False},
                   "passengers": {"adults": trip["pax"], "children": 0, "infants": 0, "adultsHoldBags": [int(trip["bag"])] * trip["pax"],
                                  "adultsHandBags": [0] * trip["pax"], "childrenHoldBags": [], "childrenHandBags": []}},
        "filter": {"maxStopsCount": trip["stops"], "transportTypes": ["FLIGHT"], "contentProviders": ["KIWI", "FRESH"], "limit": 40,
                   "enableSelfTransfer": False, "enableThrowAwayTicketing": False, "enableTrueHiddenCity": False},
        "options": {"currency": trip["currency"].lower(), "locale": "en", "partner": "skypicker", "sortBy": "PRICE",
                    "sortOrder": "ASCENDING", "mergePriceDiffRule": "INCREASED", "serverToken": None, "searchSessionId": None}}
    r = requests.post(URL, json={"query": query, "variables": variables}, headers={"content-type": "application/json"}, timeout=40)
    r.raise_for_status()
    j = r.json()
    data = (j.get("data") or {}).get("returnItineraries" if rt else "onewayItineraries") or {}
    if data.get("__typename") != "Itineraries":
        raise RuntimeError(f"kiwi: {data.get('error') or j.get('errors') or j}"[:200])
    offers = []
    for it in data["itineraries"]:
        legs = [it["outbound"], it["inbound"]] if rt else [it["sector"]]
        segs = [s["segment"] for s in legs[0]["sectorSegments"]]
        tiers = it["bagsInfo"]["checkedBagTiers"] if trip["bag"] else []
        offers.append(Offer(
            price=round(float(it["price"]["amount"])), currency=trip["currency"],
            airline=", ".join(dict.fromkeys(s["segment"]["carrier"]["name"] for leg in legs for s in leg["sectorSegments"])),
            segments=[[s["source"]["station"]["code"], s["destination"]["station"]["code"], s["source"]["localTime"][:16].replace("T", " "),
                       s["destination"]["localTime"][:16].replace("T", " "), f"{s['carrier']['code']}{s['code']}"] for s in segs],
            stops=len(segs) - 1, duration_min=legs[0]["duration"] // 60,
            bag_included=bool(tiers) if trip["bag"] else False,
            bag_fee=round(float(tiers[0]["tierPrice"]["amount"])) if tiers else 0,
            bag_kg=tiers[0]["bags"][0]["weight"]["value"] if tiers else 0,
            ret_date=it["inbound"]["sectorSegments"][0]["segment"]["source"]["localTime"][:10] if rt else "",
            source="Kiwi.com", link="https://www.kiwi.com" + ((it["bookingOptions"]["edges"] or [{"node": {"bookingUrl": "/en/"}}])[0]["node"]["bookingUrl"])))
    return offers
