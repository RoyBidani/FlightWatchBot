# Price sources

What was tried, when, and what came out of it. Update this file every time a source is probed, added, breaks or dies.

| source | tier | status | date checked | quota / cost | notes |
|---|---|---|---|---|---|
| Google Flights via `fast-flights` (`sources/google.py`) | 2, browser-less unofficial | **ON, v1 primary** | 2026-10-19 | free, no key | TLV→LAX 10–20 Mar 2027, 1 stop, bag: 8 offers in ~1.2 s from a home IP; 9 offers from AWS us-east-1 behind NAT (2026-10-20). Fragile, see below |
| SerpApi Google Flights (`sources/serpapi.py`) | 1, paid API | built, off until `SERPAPI_KEY` is set | 2026-10-19 | free plan 250 searches/month, Starter $25 / 1,000 | Untested (no key yet). Checked bags not priced, offers come out as `bag_included=unknown` |
| Amadeus Self-Service | 1, official | **DEAD** | 2026-10-19 | n/a | Self-service portal decommissioned and keys disabled on 2026-07-17. Only Enterprise (contract) remains. Not built |
| Duffel (`sources/duffel.py`) | 1, official | **ON when `DUFFEL_TOKEN` is set: the bag source** | 2026-10-20 | searches free up to 1,500 per booking, then $0.005 each: ~240/month per trip ≈ $1.20 | Real per-offer checked-bag allowance and bag prices. Test token works (49 offers TLV→ATH incl. El Al, Aegean, Arkia, Sky Express + fake "Duffel Airways"). Live mode needs account approval |
| Travelpayouts / Aviasales Data API | 1, cached | not built | 2026-10-19 | free key | Cached prices, not live. No alerts from it; only worth adding as a trend/sanity check |
| Kiwi.com public search | 2 | not built | 2026-10-19 | n/a | Official Tequila API is partner-only; the site's own endpoints are unofficial. Google already covers OTAs |
| Kayak / Skyscanner / Google page via Playwright | 3 | not built | 2026-10-19 | n/a | Bot-managed (PerimeterX / Akamai / Datadome). From EC2 these are blocked outright, same as Yad2 on GitHub Actions. Not worth fighting |

## Google Flights via fast-flights

- Builds Google's `tfs` protobuf query (dates, airports, stops, cabin, adults) and fetches
  `https://www.google.com/travel/flights` with `primp` (Chrome TLS fingerprint), no browser.
- The answer is in `<script class="ds:1">`. `payload[2][0]` = "best flights", `payload[3][0]` = "other flights".
  fast-flights 3.1.0's own parser reads only `[3]` and misses the cheapest offer, so `google.py` parses both.
  Per itinerary `k`: `k[1][0][1]` price, `k[0][1]` airlines, `k[0][2]` segments, `k[0][9]` total minutes.
  Per segment `s`: `s[3]`/`s[6]` airports, `s[20]`/`s[21]` dates, `s[8]`/`s[10]` times, `s[22][0:2]` flight number.
- **Bags: not priced.** fast-flights' `checked_bags=1` is meant to make Google add the bag fee. Tested 2026-10-20 on
  TLV→ATH, TLV→BUD, TLV→LHR: 23 itineraries, every price identical with and without (Wizz Air too). Google ignores it,
  so `google.py` no longer sends it and marks offers `bag_included=None` on bag trips; alerts say the bag is not in
  the price. Checked 2026-10-20 in a real browser: Google's Bags filter only offers carry-on (IL and US). Real bag
  prices come from Duffel, below.
- **Error replies.** A `data:` payload ending in `errorHasStatus: true` starts with a status code: `3` = nothing on sale
  for these dates (e.g. > ~330 days out) → treated as no flights; `13` = temporary Google error, seen ~1 in 6 calls
  from AWS on 2026-10-20 → retried up to 3 times, then the source fails for this check.
- **Unpriced itineraries.** From AWS Google sometimes lists a flight with an empty price block (`k[1] == []`). Skipped.
- Round trips: the itinerary is the outbound leg; the price is the round-trip total with the cheapest return.
- `fast-flights` is pinned to 3.1.0 because `google.py` imports its query builder; the payload indexes are ours.
- **Fragile**: breaks when Google changes the payload. Symptom: `google failed: no results script in page` or
  `IndexError` / `TypeError` lines in the log. **Datacenter IPs**: works from AWS us-east-1 (NAT gateway IP),
  checked 2026-10-20. If it ever gets a consent/captcha page, set `SERPAPI_KEY` (same data, through SerpApi's IPs).

## SerpApi

- `engine=google_flights`, `type` 1 round / 2 one-way, `stops` = max_stops + 1, `travel_class` 1–4, `adults`.
- Its `bags` parameter is carry-on only, so checked-bag fees are unknown. For bag trips the bot prefers any offer
  with a known bag over an unknown one, and says "unknown, verify before booking" when it has to use one.
- 8 checks a day × 30 days = 240 searches per trip per month. The free plan covers exactly one trip.

## Duffel

- `POST /air/offer_requests?return_offers=true&supplier_timeout=15000`, header `Duffel-Version: v2`. One per trip per check.
  Body: slices (one per direction), passengers `[{"type": "adult"}] * pax`, `cabin_class`, `max_connections`.
- Bag included: every segment's `passengers[].baggages[]` has a `type: checked` entry with `quantity >= 1`.
  Airlines return each fare family as its own offer, e.g. El Al TLV→ATH 595 ₪ Lite (no bag) vs 781 ₪ (bag included),
  so the cheapest "with bag" offer is usually a bundled fare, not fare + fee.
- Bag sold separately: `GET /air/offers/{id}?return_available_services=true`, `available_services[]` with
  `type: baggage`, `metadata.type: checked`, `metadata.maximum_weight_kg`, `segment_ids`. The bot looks this up for the
  5 cheapest offers without a bag and adds the cheapest 23 kg bag per segment group × passengers. Most LCC/Israeli
  offers seen in test mode return no bag service, so they stay "bag unknown".
- Prices come in the airline's currency (USD, EUR...); converted with frankfurter.dev daily rates.
- Test mode returns real airline schedules plus a fake "Duffel Airways" (ZZ). **Never put a test token on the server**:
  the fake airline would win and send real-looking alerts.
- Live mode: dashboard "Go live", account details + payment method. Not done yet.
