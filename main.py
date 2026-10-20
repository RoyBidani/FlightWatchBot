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

import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("flightbot")
logging.getLogger("primp").setLevel(logging.WARNING)

from sources import SOURCES

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"
STATE_FILE = Path(os.getenv("FLIGHTBOT_STATE", "trips.json"))
INTERVAL = 3 * 3600
PLATEAU_DAYS = 14
AUTOSTOP_DAYS = 3
DEFAULTS = {"pax": 1, "stops": 1, "cabin": "economy", "currency": "ILS", "min_drop": 50, "min_drop_pct": 2}
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
    "from": "Flying from? City or 3-letter airport code (e.g. Tel Aviv / TLV).",
    "to": "Flying to? (e.g. Los Angeles / LAX)",
    "dates": "Dates? Outbound and return: <code>2027-03-10 2027-03-20</code>. One date = one-way.",
    "bag": "Checked bag, 23 kg per passenger? yes / no",
    "direct": "Direct flights only? yes / no (no = up to 1 connection)",
}
USAGE = "Usage: <code>/new TLV LAX 2027-03-10 [2027-03-20] [bag] [pax=2] [stops=0] [cabin=business]</code>"
HELP = ("/new start tracking a trip (asks 5 questions), or " + USAGE[7:] + "\n/list your trips\n"
        "/status [id] lowest ever vs cheapest now\n/stop &lt;id&gt; stop tracking\n/check check prices now\n"
        "/lang en|he alert language\n/cancel abort setup")
L = {
    "en": {"first": "Tracking started. Cheapest right now:", "new_low": "New lowest price (was {was})",
           "now": "Cheapest now. Lowest ever {low} ({when})",
           "plateau": "No lower price for 14 days. Lowest seen: {low} ({when}). Book it.",
           "autostop": "Departure in {days} days, stopped tracking #{id}. Lowest seen: {low} ({when}).",
           "rt": "round trip", "ow": "one way", "pax": "{n} adult(s)", "out": "Outbound", "nonstop": "nonstop",
           "stops": "{n} stop(s) via {via}", "src": "Source", "book": "Book / open search",
           "rt_note": "Price is the round-trip total, pick the return on the booking page.",
           "yes": "Checked bag: included in price", "no": "Checked bag: NOT included",
           "unk": "Checked bag: unknown, verify before booking", "off": "No checked bag (not requested)"},
    "he": {"first": "התחלתי לעקוב. המחיר הזול ביותר כרגע:", "new_low": "מחיר נמוך חדש (קודם {was})",
           "now": "הזול ביותר כרגע. הנמוך אי פעם {low} ({when})",
           "plateau": "אין מחיר נמוך יותר כבר 14 יום. הנמוך ביותר: {low} ({when}). להזמין.",
           "autostop": "ההמראה בעוד {days} ימים, הפסקתי לעקוב אחרי #{id}. הנמוך ביותר: {low} ({when}).",
           "rt": "הלוך ושוב", "ow": "כיוון אחד", "pax": "{n} מבוגרים", "out": "הלוך", "nonstop": "ישירה",
           "stops": "{n} עצירות דרך {via}", "src": "מקור", "book": "להזמנה / לחיפוש",
           "rt_note": "המחיר כולל הלוך ושוב, את החזור בוחרים בדף ההזמנה.",
           "yes": "מזוודה: כלולה במחיר", "no": "מזוודה: לא כלולה",
           "unk": "מזוודה: לא ידוע, לבדוק לפני הזמנה", "off": "בלי מזוודה (לא התבקש)"},
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


def send(chat_id, text):
    if DRY:
        print(f"--- to {chat_id}\n{text}\n")
        return True
    r = None
    for attempt in range(3):
        try:
            r = requests.post(f"{API}/sendMessage", timeout=30, json={
                "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
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


def esc(s):
    return html.escape(str(s), quote=False)


def money(p, cur):
    return f"{p:,.0f} {SYMBOLS.get(cur, cur)}"


def day(iso):
    return datetime.fromisoformat(iso).strftime("%-d %b %Y")


def title(t):
    return f"{t['origin']} → {t['dest']} · {day(t['out'])}" + (f" – {day(t['ret'])}" if t["ret"] else "")


def format_offer(t, o, head):
    s = L[state["chats"][t["chat_id"]]["lang"]]
    segs = o["segments"]
    via = [g[1] for g in segs[:-1]]
    plus = (date.fromisoformat(segs[-1][3][:10]) - date.fromisoformat(segs[0][2][:10])).days
    route = f"{segs[0][0]} {segs[0][2][11:]} → " + "".join(f"{v} → " for v in via) + \
        f"{segs[-1][1]} {segs[-1][3][11:]}" + (f" (+{plus})" if plus else "")
    stops = s["stops"].format(n=len(via), via=", ".join(via)) if via else s["nonstop"]
    bag = "off" if not t["bag"] else {True: "yes", False: "no", None: "unk"}[o["bag_included"]]
    return "\n".join([
        f"<b>{esc(title(t))}</b> #{t['id']}",
        esc(head),
        f"<b>{money(o['price'], o['currency'])}</b> {s['rt'] if t['ret'] else s['ow']}, {s['pax'].format(n=t['pax'])}",
        esc(o["airline"]),
        f"{s['out']}: {esc(route)}, {esc(stops)}, {o['duration_min'] // 60}h {o['duration_min'] % 60:02d}m",
        s[bag],
        f"{s['src']}: {esc(o['source'])}",
        *([s["rt_note"]] if t["ret"] else []),
        f'<a href="{esc(o["link"])}">{s["book"]}</a>',
    ])


def matches(t, o):
    return (o.stops <= t["stops"] and bool(o.segments) and o.segments[0][2][:10] == t["out"]
            and o.currency == t["currency"] and not (t["bag"] and o.bag_included is False))


def cheapest(t):
    offers = []
    for name, search in SOURCES.items():
        try:
            got = [o for o in search(t) if matches(t, o)]
            log.info("trip %s %s: %s offers, cheapest %s", t["id"], name, len(got), min((o.price for o in got), default=None))
            offers += got
        except Exception as e:
            log.warning("trip %s %s failed: %s", t["id"], name, (str(e).splitlines() or [repr(e)])[0][:200])
    return min(offers, key=lambda o: (t["bag"] and o.bag_included is None, o.price), default=None)


def evaluate(t, best, report):
    s = L[state["chats"][t["chat_id"]]["lang"]]
    cid, low = t["chat_id"], t["current_low"]
    if best is None:
        if report:
            send(cid, f"<b>{esc(title(t))}</b> #{t['id']}\nNo matching offers this run.")
        return
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
        if send(cid, f"<b>{esc(title(t))}</b> #{t['id']}\n" + s["plateau"].format(low=low_str, when=when) + f'\n<a href="{esc(low["link"])}">{s["book"]}</a>'):
            t["plateau_announced"] = True


def check_all(chat=None, trip_id=None, report=False):
    with LOCK:
        trips = [t for t in state["trips"] if t["active"] and chat in (None, t["chat_id"]) and trip_id in (None, t["id"])]
    for t in trips:
        try:
            days = (date.fromisoformat(t["out"]) - date.today()).days
            if days <= AUTOSTOP_DAYS:
                with LOCK:
                    t["active"] = False
                    save()
                low = t["current_low"]
                s = L[state["chats"][t["chat_id"]]["lang"]]
                send(t["chat_id"], f"<b>{esc(title(t))}</b> #{t['id']}\n" + s["autostop"].format(
                    days=days, id=t["id"], low=money(low["price"], low["currency"]) if low else "-",
                    when=day(low["found_at"]) if low else "-"))
                continue
            best = cheapest(t)
            with LOCK:
                if t["active"]:
                    evaluate(t, best, report)
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
        return "Can't read the dates. " + ASK["dates"]
    if ds[0] - date.today() <= timedelta(days=AUTOSTOP_DAYS):
        return f"Outbound must be more than {AUTOSTOP_DAYS} days from today."
    if len(ds) == 2 and ds[1] < ds[0]:
        return "Return is before outbound."
    t["out"], t["ret"] = ds[0].isoformat(), ds[1].isoformat() if len(ds) == 2 else None


def parse_oneliner(arg):
    t, pos = dict(DEFAULTS, bag=False, ret=None), []
    for tok in arg.split():
        k, eq, v = tok.partition("=")
        if eq:
            if k == "cabin" and v in CABINS:
                t[k] = v
            elif k in ("pax", "stops") and v.isdigit() and int(v) <= (9 if k == "pax" else 2) and (k == "stops" or int(v) >= 1):
                t[k] = int(v)
            else:
                return None, f"Bad option {esc(tok)}. " + USAGE
        elif tok.lower() in ("bag", "nobag"):
            t["bag"] = tok.lower() == "bag"
        else:
            pos.append(tok)
    if len(pos) not in (3, 4):
        return None, USAGE
    t["origin"], t["dest"] = resolve(pos[0]), resolve(pos[1])
    if not t["origin"] or not t["dest"] or t["origin"] == t["dest"]:
        return None, "Unknown or identical airports, use 3-letter codes. " + USAGE
    err = set_dates(t, " ".join(pos[2:]))
    return (None, err) if err else (t, None)


def summary(t):
    return "\n".join([
        f"{t['origin']} ({AIRPORTS.get(t['origin'], '?')}) → {t['dest']} ({AIRPORTS.get(t['dest'], '?')})",
        f"{day(t['out'])}" + (f" – {day(t['ret'])}" if t["ret"] else " one-way"),
        f"Checked bag: {'yes, 23 kg per passenger' if t['bag'] else 'no'}",
        f"{t['pax']} adult(s), {t['cabin']}, " + (f"max {t['stops']} stop(s)" if t["stops"] else "direct only") + f", {t['currency']}",
        f"Alert on a drop of at least {money(t['min_drop'], t['currency'])} or {t['min_drop_pct']}%",
        "Start tracking? yes / no",
    ])


def setup_step(chat, cid, text):
    s = chat["setup"]
    t, step = s["trip"], s["step"]
    if step in ("from", "to"):
        code = resolve(text)
        if not code or code == t.get("origin"):
            return "Don't know that one. Send the 3-letter airport code (e.g. LAX)."
        t["origin" if step == "from" else "dest"] = code
        s["step"] = "to" if step == "from" else "dates"
        return f"{code} ({AIRPORTS.get(code, 'code not in my list, using as is')})\n" + ASK[s["step"]]
    if step == "dates":
        err = set_dates(t, text)
        if err:
            return err
        s["step"] = "bag"
        return ASK["bag"]
    a = text.lower()
    if a not in YES | NO:
        return "yes or no?"
    if step == "bag":
        t["bag"] = a in YES
        s["step"] = "direct"
        return ASK["direct"]
    if step == "direct":
        t["stops"] = 0 if a in YES else DEFAULTS["stops"]
        s["step"] = "confirm"
        return summary(t)
    chat["setup"] = None
    if a in NO:
        return "Cancelled. /new to start over."
    t |= {"id": state["next_id"], "chat_id": cid, "active": True, "created_at": now(), "current_low": None,
          "history": [], "last_run_cheapest": None, "plateau_announced": False, "low_gone": False}
    state["next_id"] += 1
    state["trips"].append(t)
    PENDING.append({"trip_id": t["id"]})
    return f"Tracking #{t['id']}. First price in a moment, then I check every {INTERVAL // 3600} hours."


def status(t):
    low, last = t["current_low"], t["last_run_cheapest"]
    lines = [f"<b>{esc(title(t))}</b> #{t['id']}"]
    if low:
        lines.append(f"Lowest ever {money(low['price'], low['currency'])} ({day(low['found_at'])}, {esc(low['airline'])}, {esc(low['source'])})")
    if last:
        lines.append(f"Cheapest now {money(last['price'], last['currency'])} (checked {datetime.fromisoformat(last['at']).strftime('%-d %b %H:%M')} UTC)")
    lines.append(f"Departure in {(date.fromisoformat(t['out']) - date.today()).days} days" if low or last else "No price yet.")
    return "\n".join(lines)


def handle(msg):
    cid = str(msg["chat"]["id"])
    text = (msg.get("text") or "").strip()
    chat = state["chats"].setdefault(cid, {"lang": "en", "setup": None})
    cmd, _, arg = text.partition(" ")
    cmd, arg = cmd.split("@")[0].lower(), arg.strip()
    mine = [t for t in state["trips"] if t["chat_id"] == cid and t["active"]]
    log.info("chat %s: %s", cid, cmd if cmd.startswith("/") else "(reply)")
    if cmd in ("/new", "/start") and not arg and (cmd == "/new" or not mine):
        chat["setup"] = {"step": "from", "trip": dict(DEFAULTS, bag=False, ret=None)}
        return ("I watch a flight price and tell you when it drops. 5 quick questions.\n" if cmd == "/start" else "") + ASK["from"]
    if cmd == "/new":
        t, err = parse_oneliner(arg)
        if err:
            return err
        chat["setup"] = {"step": "confirm", "trip": t}
        return summary(t)
    if cmd in ("/start", "/help"):
        return HELP
    if cmd == "/cancel":
        chat["setup"] = None
        return "Setup cancelled."
    if cmd == "/lang":
        if arg not in L:
            return "/lang en or /lang he"
        chat["lang"] = arg
        return f"Alert language: {arg}"
    if cmd == "/list":
        return "\n".join(f"#{t['id']} {esc(title(t))}" + (f" · low {money(t['current_low']['price'], t['currency'])}" if t["current_low"] else "")
                         for t in mine) or "No trips. /new to add one."
    if cmd == "/status":
        picked = [t for t in mine if not arg or str(t["id"]) == arg.lstrip("#")]
        return "\n\n".join(status(t) for t in picked) or "No such trip. /list"
    if cmd == "/stop":
        for t in mine:
            if str(t["id"]) == arg.lstrip("#"):
                t["active"] = False
                return f"Stopped #{t['id']}."
        return "Usage: /stop &lt;id&gt; (see /list)"
    if cmd == "/check":
        if not mine:
            return "No trips. /new to add one."
        PENDING.append({"chat": cid, "report": True})
        return f"Checking {len(mine)} trip(s)…"
    if chat["setup"] and not cmd.startswith("/"):
        return setup_step(chat, cid, text)
    return HELP


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
                                  json={"offset": state["offset"], "timeout": 50, "allowed_updates": ["message"]})
                r.raise_for_status()
                updates, backoff = r.json()["result"], 5
            except Exception as e:
                log.warning("getUpdates failed: %s, retry in %ss", str(e).replace(TOKEN, "***")[:200], backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            for u in updates:
                reply = None
                with LOCK:
                    state["offset"] = u["update_id"] + 1
                    try:
                        if "message" in u:
                            reply = handle(u["message"])
                    except Exception:
                        log.exception("update %s failed", u["update_id"])
                    save()
                if reply:
                    send(str(u["message"]["chat"]["id"]), reply)
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
