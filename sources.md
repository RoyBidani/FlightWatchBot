# Price sources

What was tried, when, and what came out of it. Update this file every time a source is probed, added, breaks or dies.

| source | tier | status | date checked | quota / cost | notes |
|---|---|---|---|---|---|
| Google Flights via `fast-flights` (`sources/google.py`) | 2, browser-less unofficial | **ON, v1 primary** | 2026-10-19 | free, no key | TLV→LAX 10–20 Mar 2027, 1 stop, bag: 8 offers in ~1.2 s from a home IP. Fragile, see below |
| SerpApi Google Flights (`sources/serpapi.py`) | 1, paid API | built, off until `SERPAPI_KEY` is set | 2026-10-19 | free plan 250 searches/month, Starter $25 / 1,000 | Untested (no key yet). Checked bags not priced, offers come out as `bag_included=unknown` |
| Amadeus Self-Service | 1, official | **DEAD** | 2026-10-19 | n/a | Self-service portal decommissioned and keys disabled on 2026-07-17. Only Enterprise (contract) remains. Not built |
| Duffel | 1, official | not built | 2026-10-19 | self-serve live tokens; searches free up to 1,500 per booking, then $0.005 each | With zero bookings every search is "excess": ~240 searches/month per trip ≈ $1.20. Paid, so not without asking. Test mode is free but returns fake airlines |
| Travelpayouts / Aviasales Data API | 1, cached | not built | 2026-10-19 | free key | Cached prices, not live. No alerts from it; only worth adding as a trend/sanity check |
| Kiwi.com public search | 2 | not built | 2026-10-19 | n/a | Official Tequila API is partner-only; the site's own endpoints are unofficial. Google already covers OTAs |
| Kayak / Skyscanner / Google page via Playwright | 3 | not built | 2026-10-19 | n/a | Bot-managed (PerimeterX / Akamai / Datadome). From EC2 these are blocked outright, same as Yad2 on GitHub Actions. Not worth fighting |

## Google Flights via fast-flights

- Builds Google's `tfs` protobuf query (dates, airports, stops, cabin, adults, `checked_bags`) and fetches
  `https://www.google.com/travel/flights` with `primp` (Chrome TLS fingerprint), no browser.
- The answer is in `<script class="ds:1">`. `payload[2][0]` = "best flights", `payload[3][0]` = "other flights".
  fast-flights 3.1.0's own parser reads only `[3]` and misses the cheapest offer, so `google.py` parses both.
  Per itinerary `k`: `k[1][0][1]` price, `k[0][1]` airlines, `k[0][2]` segments, `k[0][9]` total minutes.
  Per segment `s`: `s[3]`/`s[6]` airports, `s[20]`/`s[21]` dates, `s[8]`/`s[10]` times, `s[22][0:2]` flight number.
- `checked_bags=1` makes Google add its estimated checked-bag fee to the price, so offers count as bag included.
  What `1` means for pax > 1 (total vs per passenger) is unverified.
- Round trips: the itinerary is the outbound leg; the price is the round-trip total with the cheapest return.
- `fast-flights` is pinned to 3.1.0 because `google.py` imports its query builder; the payload indexes are ours.
- **Fragile**: breaks when Google changes the payload. Symptom: `google failed: no results script in page` or
  `IndexError` / `TypeError` lines in the log. **Datacenter IPs**: untested from EC2 as of 2026-10-19. If EC2 gets a
  consent/captcha page, set `SERPAPI_KEY` (same data, through SerpApi's IPs).

## SerpApi

- `engine=google_flights`, `type` 1 round / 2 one-way, `stops` = max_stops + 1, `travel_class` 1–4, `adults`.
- Its `bags` parameter is carry-on only, so checked-bag fees are unknown. For bag trips the bot prefers any offer
  with a known bag over an unknown one, and says "unknown, verify before booking" when it has to use one.
- 8 checks a day × 30 days = 240 searches per trip per month. The free plan covers exactly one trip.
