import os
from datetime import datetime, timedelta, timezone

os.environ["FLIGHTBOT_STATE"] = "/nonexistent/trips.json"
import main
from sources import Offer

sent, prices = [], []
main.send = lambda cid, text: sent.append(text) or True
main.save = lambda: None
main.SOURCES = {"fake": lambda t: [Offer(p, "ILS", "X", [["TLV", "LAX", t["out"] + " 05:00", t["out"] + " 20:00", "X1"]],
                                         0, 900, b, "fake", "https://x", ret_date=t["ret"] or "") for p, b in prices]}
main.state = main.load()


def chat(text, cid=7):
    r = main.handle({"chat": {"id": cid}, "text": text})
    return r[0] if isinstance(r, tuple) else r


def tap(question, data):
    reply, buttons = question
    msg = {"chat": {"id": 7}, "message_id": 1, "text": reply, "reply_markup": {"inline_keyboard": buttons}}
    r, edit = main.handle_callback({"id": "q", "message": msg, "data": data})
    return r, edit


out = (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d")
assert "Flying from" in chat("/start")
assert "code" in chat("Atlantis")
assert "Flying to" in chat("tel aviv") and "Going date" in chat("LA")
assert "Can't read" in chat("tomorrow")
assert "Returning" in chat(out)
assert "bag" in chat("one way").lower()
assert "Direct" in chat("yes")
assert "Can your dates move" in chat("yes")
assert "Tap one" in chat("maybe") and "Exact dates" in chat("exact")
assert "Tracking #1" in chat("yes")
t = main.state["trips"][0]
assert t["bag"] and t["ret"] is None and t["stops"] == 0 and main.PENDING.pop() == {"trip_id": 1}

real = main.SOURCES["fake"]
main.SOURCES["fake"] = lambda t: 1 / 0
main.check_all()
assert not sent and not t.get("empty_announced"), "a failing source is not 'no offers'"
main.check_all(chat="7", report=True)
assert "Couldn't reach" in sent.pop()
main.SOURCES["fake"] = real
main.check_all(); main.check_all()
assert len(sent) == 1 and "No matching offers" in sent[0], "empty first check announced once"
sent.clear()
prices[:] = [(3000, None), (3100, True), (2000, False)]
main.check_all()
assert t["current_low"]["price"] == 3100 and len(sent) == 1, "bag trip: known-bag beats unknown, no-bag excluded"
assert "Cheaper fare: <b>3,000 ₪</b>" in sent[-1], "cheaper bag-unknown fare shown; the known no-bag fare is not"
prices[:] = [(3060, True)]
main.check_all()
assert len(sent) == 1, "drop of 40 is under max(50, 2%)"
prices[:] = [(3038, True)]
main.check_all()
assert len(sent) == 2 and t["current_low"]["price"] == 3038 and "was 3,100" in sent[-1]
prices[:] = [(3500, True)]
main.check_all(); main.check_all()
assert len(sent) == 2 and sum("gone_at" in h for h in t["history"]) == 1
t["current_low"]["found_at"] = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
main.check_all(); main.check_all()
assert len(sent) == 3 and "Book it" in sent[-1]
t["current_low"]["bag_included"] = None
prices[:] = [(3600, True)]
main.check_all()
assert len(sent) == 4 and "Tracking started" in sent[-1] and t["current_low"]["price"] == 3600, "real bag price replaces a no-bag low"
prices[:] = [(3500, True)]
t["current_low"]["found_at"] = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
main.check_all(); sent.pop()
main.check_all(chat="7", report=True)
assert "Cheapest right now" in sent[-1] and "3,500" in sent[-1]
assert "Lowest ever: <b>3,500" in chat("/status")
t["out"] = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
main.check_all()
assert not t["active"] and "stopped tracking #1" in sent[-1]

t["active"] = True
t["out"] = out
assert "Usage" in chat("/edit 1") and "Usage" in chat("/edit 9 stops=1")
assert "Bad option" in chat("/edit 1 foo=1") and "before the going date" in chat("/edit 1 back=2020-01-01")
assert "price tracking restarted" in chat("/edit 1 stops=1 nobag back=" + out) and main.PENDING.pop() == {"trip_id": 1}
assert t["stops"] == 1 and not t["bag"] and t["ret"] == out and t["current_low"] is None and "edited_at" in t["history"][-1]
assert "one way" in chat("/edit 1 back=none") and t["ret"] is None
main.PENDING.clear()
t["active"] = False
assert "Bad option" in chat("/new TLV LAX 2030-01-01 pax=0")
assert "Your trip" in chat(f"/new TLV JFK {out} {out} bag pax=2 stops=0")
assert "Tracking #2" in chat("yes") and main.state["trips"][1]["pax"] == 2
assert "Stopped #2" in chat("/stop 2") and "No trips" in chat("/list")
assert "Tracking #3" in chat(f"/new TLV LAX {out}") + chat("yes")
other = lambda text: main.handle({"chat": {"id": 8}, "text": text})
assert "No such" in other("/status 3") and "Usage" in other("/stop 3") and main.state["trips"][2]["active"]
q = main.handle({"chat": {"id": 7}, "text": "/new"})
r, edit = tap(q, "TLV")
assert "Flying to" in r[0] and edit[1]["text"].endswith("→ Tel Aviv (TLV)")
assert tap(q, "x") == (None, None)
cal = main.handle({"chat": {"id": 7}, "text": "ATH"})[1]
nav = cal[0][2]["callback_data"]
_, edit = tap(("", cal), nav)
assert edit[0] == "editMessageReplyMarkup"
pick = next(b["callback_data"] for row in cal[2:] for b in row if b["callback_data"] != "x")
r, edit = tap(("Going date?", cal), pick)
assert "Returning" in r[0] and main.wday(pick) in edit[1]["text"]
r, _ = tap(r, "one way")
assert "bag" in r[0]
main.state["chats"]["7"]["setup"] = None
r = main.handle({"chat": {"id": 7}, "text": "/list"})
assert any(b["callback_data"] == "/dates 3" and "LAX" in b["text"] for row in r[1] for b in row)
r, _ = tap(r, "/stop 3 ?")
assert "Stop tracking #3" in r[0] and main.state["trips"][2]["active"]
r, _ = tap(main.handle({"chat": {"id": 7}, "text": "/list"}), "/dates 3")
assert "New dates for #3" in r[0]
go2 = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
r = main.handle({"chat": {"id": 7}, "text": go2})
assert "Returning" in r[0]
r = main.handle({"chat": {"id": 7}, "text": "one way"})
assert "Save changes" in str(r[1])
r = main.handle({"chat": {"id": 7}, "text": "yes"})
assert "Updated #3" in r[0] and main.state["trips"][2]["out"] == go2 and main.state["trips"][2]["ret"] is None
main.PENDING.clear()
g = Offer(4038, "ILS", "El Al", [["TLV", "AMS", "2027-04-20 16:10", "2027-04-20 20:15", "LY339"]], 0, 305, None, "Google Flights", "g")
k1 = Offer(5217, "ILS", "El Al", [["TLV", "AMS", "2027-04-20 16:10", "2027-04-20 20:15", "LY339"]], 0, 305, True, "Kiwi.com", "k", bag_fee=537, bag_kg=23)
k2 = Offer(4819, "ILS", "El Al", [["TLV", "AMS", "2027-04-20 16:10", "2027-04-20 20:15", "LY339"]], 0, 305, True, "Kiwi.com", "k", bag_fee=484, bag_kg=23)
other = Offer(3000, "ILS", "Wizz", [["TLV", "AMS", "2027-04-20 09:00", "2027-04-20 13:00", "W41"]], 0, 240, None, "Google Flights", "g")
main.add_bag_prices([g, k1, k2, other])
assert (g.price, g.bag_included, g.bag_fee, g.bag_kg) == (4522, True, 484, 23) and g.source == "Google Flights", "cheapest bag fee for the same flight"
assert other.price == 3000 and other.bag_included is None, "no bag price for an unmatched flight"
from sources import date_pairs
base = {"out": out, "ret": (datetime.now() + timedelta(days=70)).strftime("%Y-%m-%d")}
assert len(list(date_pairs(base))) == 1 and len(list(date_pairs(base | {"flex": 1}))) == 9 and len(list(date_pairs(base | {"flex": 3}))) == 49
assert len(list(date_pairs({"out": out, "ret": out, "flex": 1}))) == 6, "never return before going"
ft = dict(main.DEFAULTS, origin="TLV", dest="AMS", out=base["out"], ret=base["ret"], flex=1, bag=False, stops=1, currency="ILS")
d1 = lambda d, n: (datetime.fromisoformat(d) + timedelta(days=n)).strftime("%Y-%m-%d")
mk = lambda o, r: Offer(1, "ILS", "X", [["TLV", "AMS", o + " 06:00", o + " 10:00", "X1"]], 0, 240, False, "f", "l", ret_date=r)
assert main.matches(ft, mk(d1(out, -1), d1(base["ret"], 1))) and not main.matches(ft, mk(d1(out, -2), base["ret"]))
assert not main.matches(dict(ft, flex=0), mk(d1(out, -1), base["ret"]))
ft["id"] = 9
txt = main.dates(ft, main.L["en"], main.asdict(mk(d1(out, -1), d1(base["ret"], 2))))
assert "(1 day earlier)" in txt and "(2 days later)" in txt, txt
assert "±1" in main.dates(ft) and "Flexible ±1 day (9 date combinations)" in main.summary(ft)
r = main.handle({"chat": {"id": 7}, "text": f"/new TLV AMS {out} {base['ret']} flex=2"})
assert "Flexible ±2 days" in r[0]
print("ok")
