import argparse
import html
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("flightbot")
logging.getLogger("primp").setLevel(logging.WARNING)

from sources import SOURCES, date_pairs

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"
STATE_FILE = Path(os.getenv("FLIGHTBOT_STATE", "trips.json"))
INTERVAL = 3 * 3600
PLATEAU_DAYS = 14
AUTOSTOP_DAYS = 3
DEFAULTS = {"pax": 1, "stops": 1, "cabin": "economy", "currency": "ILS", "min_drop": 50, "min_drop_pct": 2, "flex": 0}
CABINS = ("economy", "premium-economy", "business", "first")
SYMBOLS = {"ILS": "₪", "USD": "$", "EUR": "€"}
YES, NO = {"yes", "y", "כן"}, {"no", "n", "לא"}

AIRPORTS = {
    "TLV": "Tel Aviv", "ETM": "Eilat", "LAX": "Los Angeles", "SFO": "San Francisco", "LAS": "Las Vegas",
    "JFK": "New York", "EWR": "Newark", "BOS": "Boston", "MIA": "Miami", "ORD": "Chicago",
    "YYZ": "Toronto", "LHR": "London", "CDG": "Paris", "FRA": "Frankfurt", "MUC": "Munich", "BER": "Berlin",
    "AMS": "Amsterdam", "ZRH": "Zurich", "VIE": "Vienna", "FCO": "Rome", "MXP": "Milan", "MAD": "Madrid",
    "BCN": "Barcelona", "LIS": "Lisbon", "ATH": "Athens", "LCA": "Larnaca", "IST": "Istanbul",
    "WAW": "Warsaw", "PRG": "Prague", "BUD": "Budapest", "DXB": "Dubai", "BKK": "Bangkok", "NRT": "Tokyo",
}
CITIES = {v.lower(): k for k, v in AIRPORTS.items()} | {
    "israel": "TLV", "la": "LAX", "nyc": "JFK", "tokyo narita": "NRT",
    "תל אביב": "TLV", "אילת": "ETM", "לוס אנג'לס": "LAX", "לוס אנגלס": "LAX", "ניו יורק": "JFK",
    "לונדון": "LHR", "פריז": "CDG", "רומא": "FCO", "אתונה": "ATH", "ברלין": "BER", "בנגקוק": "BKK",
}
ASK = {
    "from": "Flying from? Tap below, or type a city or 3-letter airport code.",
    "to": "Flying to? Tap below, or type a city or 3-letter airport code.",
    "go": "Going date? Tap a day (‹ › to change month), or type it, e.g. 9.9.2027",
    "back": "Returning date? Tap a day, or One way.",
    "bag": "Taking a checked bag (23 kg)?",
    "direct": "Direct flights only?",
    "flex": "Can your dates move? I'll check every going / return combination in the range and send the cheapest.",
}
NEXT = {"from": "to", "to": "go", "go": "back", "back": "bag", "bag": "direct", "direct": "flex", "flex": "confirm"}
USAGE = "Usage: <code>/new TLV LAX 2027-03-10 [2027-03-20] [bag] [pax=2] [stops=0] [cabin=business]</code>"
EDIT_USAGE = ("Usage: <code>/edit 1 [go=2027-09-10] [back=2027-09-24|none] [bag|nobag] [stops=0] [pax=2] [cabin=economy]</code>"
              "\nChange only what you write. back=none makes it one way.")
MENU = [("new", "Track a new trip"), ("list", "Your trips"), ("status", "Lowest ever vs cheapest now"),
        ("check", "Check prices now"), ("edit", "Change a trip: /edit 1 stops=1"), ("stop", "Stop a trip: /stop 1"),
        ("lang", "Alert language: /lang he or /lang en"), ("cancel", "Abort a setup"), ("help", "All commands")]
HELP = ("Use the buttons, or:\n/new start tracking a trip, or " + USAGE[7:] + "\n/list your trips\n"
        "/status [id] lowest ever vs cheapest now\n/edit &lt;id&gt; go=… back=… bag|nobag stops=… change a trip\n/stop &lt;id&gt; stop tracking\n/check check prices now\n"
        "/lang en|he alert language\n/cancel abort setup")
L = {
    "en": {"first": "🎯 <b>Tracking started!</b> Cheapest right now:", "new_low": "📉 <b>New lowest price!</b> (was {was})",
           "now": "🔎 <b>Cheapest right now</b> · lowest ever {low} ({when})",
           "plateau": "⏰ <b>No lower price for 14 days.</b>\nLowest seen: <b>{low}</b> ({when}). Book it.",
           "autostop": "🏁 <b>Departure in {days} days</b>, stopped tracking #{id}.\nLowest seen: <b>{low}</b> ({when}).",
           "empty": "⏳ <b>No matching offers yet.</b>\nAirlines open sales about 11 months ahead. I keep checking and "
                    "message you the moment a price shows up.",
           "fail": "⚠️ Couldn't reach Google Flights this time. I'll retry on the next check.",
           "shift": "({n} day{s} {dir})", "earlier": "earlier", "later": "later",
           "rt": "round trip", "ow": "one way", "pax": "{n} adult", "paxs": "{n} adults", "go": "Going", "back": "Return",
           "nonstop": "Nonstop", "stops": "{n} stop in {via}", "stopss": "{n} stops in {via}", "book": "🔎 Open on {src}",
           "rt_note": "<i>The price includes the return flight.</i>",
           "alt": "💡 Cheaper fare: <b>{price}</b> on <a href=\"{link}\">{src}</a>, bag not confirmed, check before booking",
           "yes": "🧳 Bag included{kg}", "added": "🧳 Bag{kg}: +{fee}, already in the price", "no": "🧳 Bag NOT included",
           "unk": "🧳 Bag <b>not</b> in this price, add the fee before booking", "off": "🎒 No checked bag"},
    "he": {"first": "🎯 <b>התחלתי לעקוב!</b> המחיר הזול ביותר כרגע:", "new_low": "📉 <b>מחיר נמוך חדש!</b> (קודם {was})",
           "now": "🔎 <b>הזול ביותר כרגע</b> · הנמוך אי פעם {low} ({when})",
           "plateau": "⏰ <b>אין מחיר נמוך יותר כבר 14 יום.</b>\nהנמוך ביותר: <b>{low}</b> ({when}). זה הזמן להזמין.",
           "autostop": "🏁 <b>ההמראה בעוד {days} ימים</b>, הפסקתי לעקוב אחרי #{id}.\nהנמוך ביותר: <b>{low}</b> ({when}).",
           "empty": "⏳ <b>עדיין אין טיסות למכירה בתאריכים האלה.</b>\nחברות התעופה פותחות מכירה כ-11 חודשים מראש. "
                    "אני ממשיך לבדוק ואשלח הודעה ברגע שיופיע מחיר.",
           "fail": "⚠️ לא הצלחתי להגיע ל-Google Flights הפעם. אנסה שוב בבדיקה הבאה.",
           "shift": "({n} ימים {dir})", "earlier": "מוקדם יותר", "later": "מאוחר יותר",
           "rt": "הלוך ושוב", "ow": "כיוון אחד", "pax": "נוסע {n}", "paxs": "{n} נוסעים", "go": "הלוך", "back": "חזור",
           "nonstop": "טיסה ישירה", "stops": "עצירה {n} ב-{via}", "stopss": "{n} עצירות ב-{via}", "book": "🔎 לפתוח ב-{src}",
           "rt_note": "<i>המחיר כולל את טיסת החזור.</i>",
           "alt": "💡 מחיר זול יותר: <b>{price}</b> ב-<a href=\"{link}\">{src}</a>, המזוודה לא מאושרת, לבדוק לפני הזמנה",
           "yes": "🧳 מזוודה כלולה{kg}", "added": "🧳 מזוודה{kg}: +{fee}, כבר בתוך המחיר", "no": "🧳 מזוודה לא כלולה",
           "unk": "🧳 המזוודה <b>לא</b> כלולה במחיר, להוסיף את העלות לפני הזמנה", "off": "🎒 בלי מזוודה"},
}

LOCK = threading.Lock()
PENDING = []
CHECKER = None
DRY = False
state = {}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load():
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except FileNotFoundError:
        return {"offset": 0, "next_id": 1, "chats": {}, "trips": []}


def save():
    if DRY:
        return
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, STATE_FILE)


def send(chat_id, text, buttons=None):
    if DRY:
        print(f"--- to {chat_id}\n{text}\n")
        return True
    r = None
    body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        body["reply_markup"] = {"inline_keyboard": buttons}
    for attempt in range(3):
        try:
            r = requests.post(f"{API}/sendMessage", timeout=30, json=body)
        except requests.RequestException as e:
            log.warning("telegram %s attempt %s: %s", chat_id, attempt + 1, str(e).replace(TOKEN, "***"))
            time.sleep(5)
            continue
        if r.ok:
            return True
        if r.status_code == 429:
            time.sleep(r.json().get("parameters", {}).get("retry_after", 5))
            continue
        break
    log.error("telegram %s -> %s", chat_id, f"{r.status_code} {r.text[:200]}" if r is not None else "unreachable")
    return False


def tg(method, **params):
    try:
        r = requests.post(f"{API}/{method}", json=params, timeout=30)
        if not r.ok:
            log.warning("%s -> %s %s", method, r.status_code, r.text[:200])
    except requests.RequestException as e:
        log.warning("%s failed: %s", method, str(e).replace(TOKEN, "***")[:200])


def kb(*rows):
    return [[{"text": t, "callback_data": d} for t, d in row] for row in rows]


FROM_KB = kb([("Tel Aviv (TLV)", "TLV")])
TO_KB = kb([("Los Angeles", "LAX"), ("New York", "JFK"), ("London", "LHR")],
           [("Paris", "CDG"), ("Athens", "ATH"), ("Bangkok", "BKK")])
MAIN_KB = kb([("New trip", "/new"), ("My trips", "/list"), ("Check now", "/check")])


def calendar(month, earliest):
    first = date(*month, 1)
    nxt, prev = (first + timedelta(days=32)).replace(day=1), (first - timedelta(days=1)).replace(day=1)
    rows = [[("‹", f"m:{prev:%Y-%m}") if prev >= earliest.replace(day=1) else (" ", "x"), (f"{first:%B %Y}", "x"),
             ("›", f"m:{nxt:%Y-%m}") if nxt <= date.today() + timedelta(days=366) else (" ", "x")],
            [(d, "x") for d in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")]]
    week, d = [(" ", "x")] * first.weekday(), first
    while d < nxt:
        week.append((str(d.day), d.isoformat()) if d >= earliest else ("·", "x"))
        if len(week) == 7:
            rows.append(week)
            week = []
        d += timedelta(days=1)
    if week:
        rows.append(week + [(" ", "x")] * (7 - len(week)))
    return kb(*rows)


def esc(s):
    return html.escape(str(s), quote=False)


def money(p, cur):
    return f"{p:,.0f} {SYMBOLS.get(cur, cur)}"


def day(iso):
    return datetime.fromisoformat(iso).strftime("%-d %b %Y")


def wday(iso):
    return datetime.fromisoformat(iso).strftime("%a %-d %b %Y")


def shift(d, base, s):
    n = (date.fromisoformat(d) - date.fromisoformat(base)).days
    return "" if not n else " " + s["shift"].format(n=abs(n), s="s" if abs(n) > 1 else "", dir=s["earlier" if n < 0 else "later"])


def dates(t, s=L["en"], o=None):
    out, ret = (o["segments"][0][2][:10], o.get("ret_date", t["ret"]) or None) if o else (t["out"], t["ret"])
    flex = "" if o or not t.get("flex") else f"  (±{t['flex']})"
    return (f"🛫 {s['go']}  <b>{wday(out)}</b>{shift(out, t['out'], s)}{flex}\n"
            + (f"🛬 {s['back']}  <b>{wday(ret)}</b>{shift(ret, t['ret'], s) if t['ret'] else ''}{flex}" if ret else f"↪️ {s['ow']}"))


def header(t, s=L["en"], o=None):
    return f"✈️ <b>{t['origin']} → {t['dest']}</b>  ·  #{t['id']}\n{dates(t, s, o)}"


def format_offer(t, o, head):
    s = L[state["chats"][t["chat_id"]]["lang"]]
    segs = o["segments"]
    via = [g[1] for g in segs[:-1]]
    plus = (date.fromisoformat(segs[-1][3][:10]) - date.fromisoformat(segs[0][2][:10])).days
    route = f"{segs[0][2][11:]} {segs[0][0]} → " + "".join(f"{v} → " for v in via) + \
        f"{segs[-1][3][11:]} {segs[-1][1]}" + (f" (+{plus} day)" if plus else "")
    stops = s["stops" if len(via) == 1 else "stopss"].format(n=len(via), via=", ".join(via)) if via else s["nonstop"]
    bag = "off" if not t["bag"] else {True: "yes", False: "no", None: "unk"}[o["bag_included"]]
    return "\n".join([
        header(t, s, o),
        "",
        head,
        f"💰 <b>{money(o['price'], o['currency'])}</b>  {s['rt'] if t['ret'] else s['ow']} · {s['pax' if t['pax'] == 1 else 'paxs'].format(n=t['pax'])}",
        "",
        f"🛩 {esc(o['airline'])}",
        f"🕐 {esc(route)}",
        f"↔️ {esc(stops)} · {o['duration_min'] // 60}h {o['duration_min'] % 60:02d}m",
        s["added" if o.get("bag_fee") else "yes"].format(fee=money(o["bag_fee"], o["currency"]), kg=f" ({o['bag_kg']} kg)" if o.get("bag_kg") else "")
        if bag == "yes" else s[bag],
        *([s["alt"].format(price=money(o["alt"]["price"], o["currency"]), src=esc(o["alt"]["source"]), link=esc(o["alt"]["link"]))]
          if o.get("alt") else []),
        "",
        f'<a href="{esc(o["link"])}">{s["book"].format(src=esc(o["source"]))}</a>',
        *([s["rt_note"]] if t["ret"] else []),
    ])


def matches(t, o):
    return (o.stops <= t["stops"] and bool(o.segments) and (o.segments[0][2][:10], o.ret_date or None) in set(date_pairs(t))
            and o.currency == t["currency"] and not (t["bag"] and o.bag_included is False))


def cheapest(t):
    offers, ok = [], False
    for name, search in SOURCES.items():
        try:
            got = [o for o in search(t) if matches(t, o)]
            log.info("trip %s %s: %s offers, cheapest %s", t["id"], name, len(got), min((o.price for o in got), default=None))
            offers += got
            ok = True
        except Exception as e:
            log.warning("trip %s %s failed: %s", t["id"], name, (str(e).splitlines() or [repr(e)])[0][:200])
    if t["bag"]:
        add_bag_prices(offers)
    best = min(offers, key=lambda o: (t["bag"] and o.bag_included is None, o.price), default=None)
    cheapest_any = min(offers, key=lambda o: o.price, default=None)
    if best and cheapest_any.price < best.price:
        best.alt = {"price": cheapest_any.price, "source": cheapest_any.source, "link": cheapest_any.link}
    return best, ok


def add_bag_prices(offers):
    fees = {}
    for o in offers:
        f = o.segments[0][4]
        if o.bag_fee and (f not in fees or o.bag_fee < fees[f].bag_fee):
            fees[f] = o
    for o in offers:
        # ponytail: matched on the first outbound flight number; the bag fee covers the whole trip as that source priced it
        k = fees.get(o.segments[0][4])
        if o.bag_included is None and k:
            o.price, o.bag_included, o.bag_fee, o.bag_kg = o.price + k.bag_fee, True, k.bag_fee, k.bag_kg


def evaluate(t, best, report, ok=True):
    s = L[state["chats"][t["chat_id"]]["lang"]]
    cid, low = t["chat_id"], t["current_low"]
    if best is None and not ok:
        if report:
            send(cid, f"{header(t, s)}\n\n{s['fail']}")
        return
    if best is None:
        if report or (low is None and not t.get("empty_announced")):
            if send(cid, f"{header(t, s)}\n\n{s['empty']}"):
                t["empty_announced"] = True
        return
    if low and t["bag"] and low.get("bag_included") is None and best.bag_included:
        low = None
    o = asdict(best) | {"found_at": now()}
    t["last_run_cheapest"] = {"price": best.price, "currency": best.currency, "source": best.source, "at": o["found_at"]}
    if low is None or best.price <= low["price"] - max(t["min_drop"], low["price"] * t["min_drop_pct"] / 100):
        head = s["first"] if low is None else s["new_low"].format(was=money(low["price"], low["currency"]))
        if send(cid, format_offer(t, o, head)):
            t.update(current_low=o, plateau_announced=False, low_gone=False)
            t["history"].append(o)
        return
    low_str, when = money(low["price"], low["currency"]), day(low["found_at"])
    if report:
        send(cid, format_offer(t, o, s["now"].format(low=low_str, when=when)))
    if best.price > low["price"] and not t["low_gone"]:
        t["low_gone"] = True
        t["history"].append({"gone_at": o["found_at"], "cheapest_now": best.price, "source": best.source})
    if not t["plateau_announced"] and datetime.now(timezone.utc) - datetime.fromisoformat(low["found_at"]) >= timedelta(days=PLATEAU_DAYS):
        if send(cid, f"{header(t, s)}\n\n" + s["plateau"].format(low=low_str, when=when) + f'\n\n<a href="{esc(low["link"])}">{s["book"].format(src=esc(low["source"]))}</a>'):
            t["plateau_announced"] = True


def check_all(chat=None, trip_id=None, report=False):
    with LOCK:
        trips = [t for t in state["trips"] if t["active"] and chat in (None, t["chat_id"]) and trip_id in (None, t["id"])]
    for t in trips:
        try:
            days = (date.fromisoformat(t["out"]) - date.today()).days - t.get("flex", 0)
            if days <= AUTOSTOP_DAYS:
                with LOCK:
                    t["active"] = False
                    save()
                low = t["current_low"]
                s = L[state["chats"][t["chat_id"]]["lang"]]
                send(t["chat_id"], f"{header(t, s)}\n\n" + s["autostop"].format(
                    days=days, id=t["id"], low=money(low["price"], low["currency"]) if low else "-",
                    when=day(low["found_at"]) if low else "-"))
                continue
            best, ok = cheapest(t)
            with LOCK:
                if t["active"]:
                    evaluate(t, best, report, ok)
                    save()
        except Exception:
            log.exception("trip %s check failed", t["id"])
    if chat is None and trip_id is None:
        log.info("check done: %s trips", len(trips))


def resolve(text):
    t = text.strip()
    if t.upper() in AIRPORTS:
        return t.upper()
    if t.lower() in CITIES:
        return CITIES[t.lower()]
    return t.upper() if re.fullmatch(r"[A-Za-z]{3}", t) else None


def parse_date(s):
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass


def set_dates(t, text):
    parts = text.replace(",", " ").split()
    ds = [parse_date(p) for p in parts]
    if len(ds) not in (1, 2) or None in ds:
        return "Can't read that date. Tap a day, or type it like 9.9.2027 or 2027-09-09."
    if ds[0] - date.today() <= timedelta(days=AUTOSTOP_DAYS):
        return f"The going date must be more than {AUTOSTOP_DAYS} days from today."
    if len(ds) == 2 and ds[1] < ds[0]:
        return "The returning date is before the going date."
    t["out"], t["ret"] = ds[0].isoformat(), ds[1].isoformat() if len(ds) == 2 else None


def parse_opts(t, arg):
    pos = []
    for tok in arg.split():
        k, eq, v = tok.partition("=")
        k = k.lower()
        if eq and k == "cabin" and v in CABINS:
            t[k] = v
        elif eq and k in ("pax", "stops", "flex") and v.isdigit() and int(v) <= {"pax": 9, "stops": 2, "flex": 3}[k] and (k != "pax" or int(v) >= 1):
            t[k] = int(v)
        elif eq and k in ("go", "back"):
            t["out" if k == "go" else "ret"] = None if v.lower() == "none" else v
        elif eq:
            return None, f"Bad option {esc(tok)}."
        elif tok.lower() in ("bag", "nobag"):
            t["bag"] = tok.lower() == "bag"
        else:
            pos.append(tok)
    return pos, None


def parse_oneliner(arg):
    t = dict(DEFAULTS, bag=False, ret=None)
    pos, err = parse_opts(t, arg)
    if err:
        return None, err + " " + USAGE
    if len(pos) not in (3, 4):
        return None, USAGE
    t["origin"], t["dest"] = resolve(pos[0]), resolve(pos[1])
    if not t["origin"] or not t["dest"] or t["origin"] == t["dest"]:
        return None, "Unknown or identical airports, use 3-letter codes. " + USAGE
    err = set_dates(t, " ".join(pos[2:]))
    return (None, err) if err else (t, None)


def summary(t):
    return "\n".join([
        "📝 <b>Your trip</b>",
        f"✈️ {t['origin']} {AIRPORTS.get(t['origin'], '')} → {t['dest']} {AIRPORTS.get(t['dest'], '')}",
        dates(t),
        "🧳 Checked bag, its price included" if t["bag"] else "🎒 No checked bag",
        "📅 " + (f"Flexible ±{t['flex']} day{'s' if t['flex'] > 1 else ''} ({len(list(date_pairs(t)))} date combinations)"
                if t.get("flex") else "Exact dates"),
        "↔️ " + ("Direct only" if not t["stops"] else f"Up to {t['stops']} connection{'s' if t['stops'] > 1 else ''}"),
        f"👤 {t['pax']} adult{'s' if t['pax'] > 1 else ''} · {t['cabin']} · {t['currency']}",
        f"🔔 Alerts on drops of {money(t['min_drop'], t['currency'])} or {t['min_drop_pct']}%+",
    ])


def earliest(s):
    return date.fromisoformat(s["trip"]["out"]) if s["step"] == "back" else date.today() + timedelta(days=AUTOSTOP_DAYS + 1)


def date_picker(s, month=None):
    e = earliest(s)
    return calendar(month or (e.year, e.month), e) + (kb([("One way", "one way")]) if s["step"] == "back" else [])


def ask(s, prefix=""):
    step = s["step"]
    if step == "confirm":
        return prefix + summary(s["trip"]), kb([("Save changes" if s.get("edit") else "Start tracking", "yes"), ("Cancel", "no")])
    if step in ("go", "back"):
        return prefix + ASK[step], date_picker(s)
    return prefix + ASK[step], {"from": FROM_KB, "to": TO_KB, "bag": kb([("Yes, with a bag", "yes"), ("No bag", "no")]),
                                "direct": kb([("Direct only", "yes"), ("Connections OK", "no")]),
                                "flex": kb([("Exact dates", "0"), ("±1 day", "1")], [("±2 days", "2"), ("±3 days", "3")])}[step]


def apply_edit(t, new, change):
    t.update({k: new.get(k, DEFAULTS.get(k)) for k in ("out", "ret", "bag", "stops", "pax", "cabin", "flex")}, current_low=None,
             last_run_cheapest=None, plateau_announced=False, low_gone=False, empty_announced=False)
    t["history"].append({"edited_at": now(), "change": change})
    PENDING.append({"trip_id": t["id"]})
    return f"Updated #{t['id']}, price tracking restarted:\n" + summary(t), MAIN_KB


def setup_step(chat, cid, text):
    s = chat["setup"]
    t, step, a = s["trip"], s["step"], text.strip().lower()
    nxt = NEXT.get(step)
    if step in ("from", "to"):
        code = resolve(text)
        if not code or code == t.get("origin"):
            return ask(s, "Don't know that one. Send the 3-letter airport code (e.g. LAX).\n")
        t["origin" if step == "from" else "dest"] = code
    elif step == "go":
        err = set_dates(t, text)
        if err:
            return ask(s, err + "\n")
        if t["ret"]:
            nxt = "confirm" if s.get("edit") else "bag"
    elif step == "back":
        err = None
        if a in ("one way", "oneway", "none", "-"):
            t["ret"] = None
        else:
            err = set_dates(t, f"{t['out']} {text}")
        if err:
            return ask(s, err + "\n")
        if s.get("edit"):
            nxt = "confirm"
    elif step == "flex":
        n = "0" if "exact" in a else next((c for c in a if c.isdigit()), None)
        if n not in ("0", "1", "2", "3"):
            return ask(s, "Tap one of the buttons.\n")
        t["flex"] = int(n)
    elif a not in YES | NO:
        return ask(s, "Tap a button, or answer yes / no.\n")
    elif step == "bag":
        t["bag"] = a in YES
    elif step == "direct":
        t["stops"] = 0 if a in YES else DEFAULTS["stops"]
    else:
        chat["setup"] = None
        if a in NO:
            return "Cancelled.", MAIN_KB
        if s.get("edit"):
            old = next((x for x in state["trips"] if x["id"] == s["edit"] and x["chat_id"] == cid and x["active"]), None)
            return apply_edit(old, t, f"dates {t['out']} {t['ret'] or 'one way'}") if old else ("That trip is no longer active.", MAIN_KB)
        t |= {"id": state["next_id"], "chat_id": cid, "active": True, "created_at": now(), "current_low": None,
              "history": [], "last_run_cheapest": None, "plateau_announced": False, "low_gone": False}
        state["next_id"] += 1
        state["trips"].append(t)
        PENDING.append({"trip_id": t["id"]})
        return f"✅ <b>Tracking #{t['id']}.</b> First price in a moment, then I check every {INTERVAL // 3600} hours.", MAIN_KB
    s["step"] = nxt
    note = f"✓ {t['origin' if step == 'from' else 'dest']} ({AIRPORTS.get(code, 'not in my list, using as is')})\n" if step in ("from", "to") else ""
    return ask(s, note)


def status(t):
    low, last = t["current_low"], t["last_run_cheapest"]
    lines = [header(t), ""]
    if low:
        when = f" · {datetime.fromisoformat(low['segments'][0][2][:10]):%-d %b}" + (
            f" → {datetime.fromisoformat(low['ret_date']):%-d %b}" if low.get("ret_date") else "") if t.get("flex") else ""
        lines.append(f"🏆 Lowest ever: <b>{money(low['price'], low['currency'])}</b>{when} · found {day(low['found_at'])} · {esc(low['airline'])}")
    if last:
        at = datetime.fromisoformat(last["at"]).astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%-d %b %H:%M")
        lines.append(f"🔎 Cheapest now: {money(last['price'], last['currency'])} · checked {at}")
    lines.append(f"📅 Departs in {(date.fromisoformat(t['out']) - date.today()).days} days" if low or last else "⏳ No price yet.")
    return "\n".join(lines)


def handle(msg):
    cid = str(msg["chat"]["id"])
    text = (msg.get("text") or "").strip()
    chat = state["chats"].setdefault(cid, {"lang": "en", "setup": None})
    cmd, _, arg = text.partition(" ")
    cmd, arg = cmd.split("@")[0].lower(), arg.strip()
    mine = [t for t in state["trips"] if t["chat_id"] == cid and t["active"]]
    log.info("chat %s: %s", cid, cmd if cmd.startswith("/") else "(reply)")
    tid, _, rest = arg.partition(" ")
    picked = next((t for t in mine if str(t["id"]) == tid.lstrip("#")), None)
    if cmd in ("/new", "/start") and not arg and (cmd == "/new" or not mine):
        chat["setup"] = {"step": "from", "trip": dict(DEFAULTS, bag=False, ret=None)}
        return ask(chat["setup"], "I watch a flight price and tell you when it drops. A few quick taps.\n\n" if cmd == "/start" else "")
    if cmd == "/new":
        t, err = parse_oneliner(arg)
        if err:
            return err
        chat["setup"] = {"step": "confirm", "trip": t}
        return ask(chat["setup"])
    if cmd == "/dates":
        if not picked:
            return "Which trip?", MAIN_KB
        chat["setup"] = {"step": "go", "trip": dict(picked), "edit": picked["id"]}
        return ask(chat["setup"], f"New dates for #{picked['id']} {picked['origin']} → {picked['dest']}.\n")
    if cmd == "/edit":
        if not picked or not rest.strip():
            return EDIT_USAGE
        new = dict(picked)
        pos, err = parse_opts(new, rest)
        err = err or (EDIT_USAGE if pos else set_dates(new, f"{new['out']} {new['ret'] or ''}"))
        return err if err else apply_edit(picked, new, rest.strip())
    if cmd in ("/start", "/help"):
        return HELP, MAIN_KB
    if cmd == "/cancel":
        chat["setup"] = None
        return "Setup cancelled.", MAIN_KB
    if cmd == "/lang":
        if arg not in L:
            return "/lang en or /lang he"
        chat["lang"] = arg
        return f"Alert language: {arg}"
    if cmd == "/list":
        if not mine:
            return "No trips yet.", MAIN_KB
        lines = [f"✈️ <b>#{t['id']} {t['origin']} → {t['dest']}</b>\n     {datetime.fromisoformat(t['out']):%-d %b}"
                 + (f" → {datetime.fromisoformat(t['ret']):%-d %b %Y}" if t["ret"] else f" {t['out'][:4]}, one way")
                 + (f" · low <b>{money(t['current_low']['price'], t['currency'])}</b>" if t["current_low"] else " · no price yet") for t in mine]
        return "🗂 <b>Your trips</b>\n\n" + "\n\n".join(lines), kb(*[[(f"#{t['id']} {t['dest']} · Status", f"/status {t['id']}"),
                                       (f"#{t['id']} {t['dest']} · Dates", f"/dates {t['id']}"), (f"#{t['id']} {t['dest']} · Stop", f"/stop {t['id']} ?")] for t in mine])
    if cmd == "/status":
        shown = [picked] if picked else [] if arg else mine
        return "\n\n".join(status(t) for t in shown) or "No such trip. /list"
    if cmd == "/stop":
        if not picked:
            return "Usage: /stop &lt;id&gt; (see /list)"
        if rest == "?":
            return f"Stop tracking #{picked['id']} {picked['origin']} → {picked['dest']}?", kb([("Yes, stop it", f"/stop {picked['id']}"), ("Keep it", "/list")])
        picked["active"] = False
        return f"Stopped #{picked['id']}.", MAIN_KB
    if cmd == "/check":
        if not mine:
            return "No trips yet.", MAIN_KB
        PENDING.append({"chat": cid, "report": True})
        return f"Checking {len(mine)} trip(s)…"
    if chat["setup"] and not cmd.startswith("/"):
        return setup_step(chat, cid, text)
    return HELP, MAIN_KB


def handle_callback(cq):
    msg, data = cq["message"], cq.get("data", "x")
    where = {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"]}
    if data == "x":
        return None, None
    if data.startswith("m:"):
        s = state["chats"].get(str(msg["chat"]["id"]), {}).get("setup")
        if not s or s["step"] not in ("go", "back"):
            return None, None
        return None, ("editMessageReplyMarkup", where | {"reply_markup": {"inline_keyboard": date_picker(s, tuple(map(int, data[2:].split("-"))))}})
    label = wday(data) if parse_date(data) else next(
        (b["text"] for row in msg.get("reply_markup", {}).get("inline_keyboard", []) for b in row if b.get("callback_data") == data), data)
    return handle({"chat": msg["chat"], "text": data}), ("editMessageText", where | {"text": f"{msg.get('text', '')}\n→ {label}"})


def start_pending():
    global CHECKER
    if PENDING and not (CHECKER and CHECKER.is_alive()):
        CHECKER = threading.Thread(target=check_all, kwargs=PENDING.pop(0), daemon=True)
        CHECKER.start()


def on_term(*_):
    raise SystemExit(0)


def daemon():
    if not TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN missing")
    signal.signal(signal.SIGTERM, on_term)
    try:
        requests.post(f"{API}/setMyCommands", timeout=30,
                      json={"commands": [{"command": c, "description": d} for c, d in MENU]}).raise_for_status()
    except Exception as e:
        log.warning("setMyCommands failed: %s", str(e).replace(TOKEN, "***")[:200])
    log.info("flightbot up: %s active trips, sources: %s, state: %s",
             sum(t["active"] for t in state["trips"]), ", ".join(SOURCES) or "NONE", STATE_FILE)
    last, backoff = -INTERVAL, 5
    try:
        while True:
            if time.monotonic() - last >= INTERVAL:
                PENDING.append({})
                last = time.monotonic()
            start_pending()
            try:
                r = requests.post(f"{API}/getUpdates", timeout=60,
                                  json={"offset": state["offset"], "timeout": 50, "allowed_updates": ["message", "callback_query"]})
                r.raise_for_status()
                updates, backoff = r.json()["result"], 5
            except Exception as e:
                log.warning("getUpdates failed: %s, retry in %ss", str(e).replace(TOKEN, "***")[:200], backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            for u in updates:
                reply, edit, cq = None, None, u.get("callback_query")
                with LOCK:
                    state["offset"] = u["update_id"] + 1
                    try:
                        if "message" in u:
                            reply = handle(u["message"])
                        elif cq and "message" in cq:
                            reply, edit = handle_callback(cq)
                    except Exception:
                        log.exception("update %s failed", u["update_id"])
                    save()
                if cq:
                    tg("answerCallbackQuery", callback_query_id=cq["id"])
                if edit:
                    tg(edit[0], **edit[1])
                if reply:
                    text, buttons = reply if isinstance(reply, tuple) else (reply, None)
                    send(str((u.get("message") or cq["message"])["chat"]["id"]), text, buttons)
                start_pending()
    except (SystemExit, KeyboardInterrupt):
        # ponytail: every change is already saved atomically; holding LOCK until exit just keeps the check thread from writing mid-shutdown
        LOCK.acquire()
        log.info("stopped")
        sys.exit(0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="FlightWatch Telegram bot")
    ap.add_argument("--once", action="store_true", help="run one price check for all trips and exit")
    ap.add_argument("--dry-run", action="store_true", help="like --once, but print messages and write nothing")
    ap.add_argument("--trip", help='with --dry-run: check this trip instead, e.g. "TLV LAX 2027-03-10 2027-03-20 bag"')
    args = ap.parse_args()
    state = load()
    DRY = args.dry_run
    if args.trip:
        trip, err = parse_oneliner(args.trip)
        if err:
            sys.exit(re.sub("<[^>]+>", "", err))
        state["chats"].setdefault("dry-run", {"lang": "en", "setup": None})
        state["trips"] = [trip | {"id": 0, "chat_id": "dry-run", "active": True, "current_low": None, "history": [],
                                  "last_run_cheapest": None, "plateau_announced": False, "low_gone": False}]
    if args.once or args.dry_run:
        check_all()
    else:
        daemon()
