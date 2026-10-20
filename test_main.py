import os
from datetime import datetime, timedelta, timezone

os.environ["FLIGHTBOT_STATE"] = "/nonexistent/trips.json"
import main
from sources import Offer

sent, prices = [], []
main.send = lambda cid, text: sent.append(text) or True
main.save = lambda: None
main.SOURCES = {"fake": lambda t: [Offer(p, "ILS", "X", [["TLV", "LAX", t["out"] + " 05:00", t["out"] + " 20:00", "X1"]],
                                         0, 900, b, "fake", "https://x") for p, b in prices]}
main.state = main.load()


def chat(text):
    return main.handle({"chat": {"id": 7}, "text": text})


out = (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d")
assert "Flying from" in chat("/start")
assert "code" in chat("Atlantis")
assert "TLV" in chat("tel aviv") and "LAX" in chat("LA")
assert "Can't read" in chat("tomorrow")
assert "bag" in chat(out).lower()
assert "Direct" in chat("yes")
assert "direct only" in chat("yes")
assert "Tracking #1" in chat("yes")
t = main.state["trips"][0]
assert t["bag"] and t["ret"] is None and t["stops"] == 0 and main.PENDING.pop() == {"trip_id": 1}

main.check_all(); main.check_all()
assert len(sent) == 1 and "No matching offers" in sent[0], "empty first check announced once"
sent.clear()
prices[:] = [(3000, None), (3100, True), (2000, False)]
main.check_all()
assert t["current_low"]["price"] == 3100 and len(sent) == 1, "bag trip: known-bag beats unknown, no-bag excluded"
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
main.check_all(chat="7", report=True)
assert "Cheapest now" in sent[-1] and "3,500" in sent[-1]
assert "Lowest ever 3,038" in chat("/status")
t["out"] = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
main.check_all()
assert not t["active"] and "stopped tracking #1" in sent[-1]

t["active"] = True
t["out"] = out
assert "Usage" in chat("/edit 1") and "Usage" in chat("/edit 9 stops=1")
assert "Bad option" in chat("/edit 1 foo=1") and "Return is before" in chat("/edit 1 back=2020-01-01")
assert "price tracking restarted" in chat("/edit 1 stops=1 nobag back=" + out) and main.PENDING.pop() == {"trip_id": 1}
assert t["stops"] == 1 and not t["bag"] and t["ret"] == out and t["current_low"] is None and "edited_at" in t["history"][-1]
assert "one way" in chat("/edit 1 back=none") and t["ret"] is None
main.PENDING.clear()
t["active"] = False
assert "Bad option" in chat("/new TLV LAX 2030-01-01 pax=0")
assert "Start tracking" in chat(f"/new TLV JFK {out} {out} bag pax=2 stops=0")
assert "Tracking #2" in chat("yes") and main.state["trips"][1]["pax"] == 2
assert "Stopped #2" in chat("/stop 2") and "No trips" in chat("/list")
assert "Tracking #3" in chat(f"/new TLV LAX {out}") + chat("yes")
other = lambda text: main.handle({"chat": {"id": 8}, "text": text})
assert "No such" in other("/status 3") and "Usage" in other("/stop 3") and main.state["trips"][2]["active"]
print("ok")
