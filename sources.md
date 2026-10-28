# Price sources

What was tried, when, and what came out of it. Update this file every time a source is probed, added, breaks or dies.

| source | tier | status | date checked | quota / cost | notes |
|---|---|---|---|---|---|
| Google Flights via `fast-flights` (`sources/google.py`) | 2, browser-less unofficial | **ON, v1 primary** | 2026-10-19 | free, no key | TLV→LAX 10–20 Mar 2027, 1 stop, bag: 8 offers in ~1.2 s from a home IP; 9 offers from AWS us-east-1 behind NAT (2026-10-20). Fragile, see below |
| SerpApi Google Flights (`sources/serpapi.py`) | 1, paid API | built, off until `SERPAPI_KEY` is set | 2026-10-20 | free plan 250 searches/month, Starter $25 / 1,000 | Same data as Google. Its booking options (`baggage_prices`) were tested for bag fees on TLV→ATH: Wizz none, El Al / Sky Express only "1 free carry-on". **No checked-bag fees**, so it doesn't solve bags |
| Amadeus Self-Service | 1, official | **DEAD** | 2026-10-19 | n/a | Self-service portal decommissioned and keys disabled on 2026-07-17. Only Enterprise (contract) remains. Not built |
| Duffel | 1, official | built, tested in test mode, **removed** | 2026-10-20 | searches free up to 1,500 per booking, then $0.005 each | Real per-fare bag allowance (El Al TLV→ATH 595 ₪ Lite vs 781 ₪ with bag). Live mode asks for company details we don't have, so it can't be used. Test mode also returns a fake "Duffel Airways" |
| Travelpayouts / Aviasales Data API | 1, cached | not built | 2026-10-19 | free key | Cached prices, not live. No alerts from it; only worth adding as a trend/sanity check |
| Kiwi.com website API (`sources/kiwi.py`) | 2, browser-less unofficial | **ON, the bag source** | 2026-10-20 | free, no key | Total price **with** the checked bag + bag weight per airline. Works from home and AWS without Kiwi's browser tokens. Fragile, see below |
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
  prices come from Kiwi, below.
- **Error replies.** A `data:` payload ending in `errorHasStatus: true` starts with a status code: `3` = nothing on sale
  for these dates (e.g. > ~330 days out) → treated as no flights; `13` = temporary Google error, seen ~1 in 6 calls
  from AWS on 2026-10-20 → retried up to 3 times, then the source fails for this check.
- **Unpriced itineraries.** From AWS Google sometimes lists a flight with an empty price block (`k[1] == []`). Skipped.
- Round trips: the itinerary is the outbound leg; the price is the round-trip total with the cheapest return.
- Flexible dates: one request per going / return pair (±1 = 9, ±3 = 49), ~1 s each. TLV→AMS ±1: 7.6 s for both sources.
- `fast-flights` is pinned to 3.1.0 because `google.py` imports its query builder; the payload indexes are ours.
- **Fragile**: breaks when Google changes the payload. Symptom: `google failed: no results script in page` or
  `IndexError` / `TypeError` lines in the log. **Datacenter IPs**: works from AWS us-east-1 (NAT gateway IP),
  checked 2026-10-20. If it ever gets a consent/captcha page, set `SERPAPI_KEY` (same data, through SerpApi's IPs).

## SerpApi

- `engine=google_flights`, `type` 1 round / 2 one-way, `stops` = max_stops + 1, `travel_class` 1–4, `adults`.
- Its `bags` parameter is carry-on only, so checked-bag fees are unknown. For bag trips the bot prefers any offer
  with a known bag over an unknown one, and says "unknown, verify before booking" when it has to use one.
- 8 checks a day × 30 days = 240 searches per trip per month. The free plan covers exactly one trip.

## Kiwi.com

- The site's own GraphQL API: `POST https://api.skypicker.com/umbrella/v2/graphql?featureName=SearchItinerariesQuery`.
  Found by watching kiwi.com's network tab on 2026-10-20. The browser sends `kw-umbrella-token` / `kw-validation-token`
  headers, but the API answers without them (from home and from AWS).
- The website's query is 44 KB; `kiwi.py` sends a small one with only the fields it needs (price, bagsInfo, segments,
  booking URL). Round trip: `returnItineraries` / `ItineraryReturn` / `outbound` + `inbound`; one way:
  `onewayItineraries` / `ItineraryOneWay` / `sector`. Airports as `Station:airport:TLV`. Durations are in seconds.
- `passengers.adultsHoldBags: [1]` makes `price.amount` the total **including** one checked bag per adult;
  `bagsInfo.checkedBagTiers[0]` is that bag's share of the price and its weight (Wizz 20 kg, Sky Express 15 kg,
  Aegean / Lufthansa 23 kg). Checked: Wizz round trip 527 ₪ without, 1,034 ₪ with (+507 tier).
- Self-transfer / hidden-city / throw-away tickets are switched off in the filter.
- Flexible dates: `outboundDepartureDate` / `inboundDepartureDate` take a `start`–`end` window, so ±N days is one request.
- Kiwi sells the same tickets above the airline / Google price (TLV→AMS El Al 4,335 ₪ vs 4,038 ₪ on Google, 2026-10-20),
  and its prices move between calls. So for bag trips the bot uses Kiwi mainly as a **bag price list**: the cheapest
  Kiwi bag fee for the same first outbound flight number is added to Google's fare. Kiwi's own total still competes.
- **Fragile**: unofficial, no contract. Symptoms when it breaks: `kiwi failed: ...` lines, GraphQL `errors` in the
  message. The bot keeps working on Google alone, with "bag not in this price".
