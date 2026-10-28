# FlightWatch Telegram Bot

A Telegram bot that watches the price of a flight and tells you when it finds a new lowest price.

You add a trip in the chat (from, to, dates, checked bag, direct only). The bot checks Google Flights and Kiwi.com every 3 hours
and messages you only when the price drops by at least 50 ₪ or 2%, whichever is larger. Any number of trips,
any number of people: everyone who starts the bot gets their own trips.

It was built for one trip, Tel Aviv → Los Angeles for a friend's wedding, but works for any route.
Bot: [@RoyFlightWatch_bot](https://t.me/RoyFlightWatch_bot).

- [Quick start](#quick-start)
- [The big picture](#the-big-picture)
- [How the bot thinks](#how-the-bot-thinks)
- [What is in the repo](#what-is-in-the-repo)
- [How this project was built, step by step](#how-this-project-was-built-step-by-step)
- [AWS deployment](#aws-deployment)
- [Set it up yourself](#set-it-up-yourself)
- [Using the bot](#using-the-bot)
- [Running and operating it](#running-and-operating-it)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

---

## Quick start

Want your own copy? About 15 minutes, no paid services, no API keys except Telegram's.

1. **Telegram bot**: in Telegram open [@BotFather](https://t.me/BotFather), send `/newbot`, copy the token.
2. **A Linux machine that is always on**: any VPS, an AWS EC2 `t4g.nano`, a Raspberry Pi or a home server.
   Ubuntu 24.04 (or anything with Python 3.10+ and systemd). It only needs outbound internet, no open ports.
   AWS recipe: [AWS deployment](#aws-deployment).
3. **Install** on that machine:
   ```bash
   sudo git clone https://github.com/RoyBidani/FlightWatchBot.git /opt/flightbot
   sudo chown -R $USER /opt/flightbot && cd /opt/flightbot
   ./deploy/install.sh                  # creates /etc/flightbot.env and stops
   sudo nano /etc/flightbot.env         # TELEGRAM_BOT_TOKEN=<your token>
   ./deploy/install.sh                  # starts the bot as a service
   ```
4. **Use it**: open your bot in Telegram, press **Start**, tap through the questions. The first price arrives in seconds.

Check it is alive: `journalctl -u flightbot -f`. Try it on a laptop first without Telegram:
`./venv/bin/python main.py --dry-run --trip "TLV ATH 2026-12-29 2027-01-05 bag"`.

---

## The big picture

```
   You (Telegram app)
        │  /new, answers, /status, /check ...          ▲  price alerts
        ▼                                               │
 ┌──────────────────────────────┐                       │
 │   Telegram Bot API (cloud)   │◄──────────────────────┘
 └──────────────┬───────────────┘
                │ HTTPS, outbound only: the bot asks Telegram
                │ "anything new?" (long-poll), Telegram never calls in
 ┌──────────────┼──────────────────────────── AWS, us-east-1 ───────────┐
 │              │                                                       │
 │   ┌──────────┴─────────────── private subnet ─────────────────────┐  │
 │   │  EC2 t3a.micro, Ubuntu 24.04  (no public IP, no inbound port) │  │
 │   │                                                               │  │
 │   │   systemd: flightbot.service  (Restart=always, starts on boot)│  │
 │   │      └─ python main.py   (one process, runs forever)          │  │
 │   │            ├─ /etc/flightbot.env      Telegram token          │  │
 │   │            └─ /var/lib/flightbot/trips.json   all state       │  │
 │   └──────────┬────────────────────────────────────────────────────┘  │
 │              │ all outbound traffic                                 │
 │       ┌──────┴──────┐                                               │
 │       │ NAT gateway │  one public IP for the whole subnet           │
 │       └──────┬──────┘                                               │
 └──────────────┼──────────────────────────────────────────────────────┘
                │ HTTPS
                ▼
     Google Flights (www.google.com/travel/flights)     Kiwi.com (api.skypicker.com GraphQL)
     cheapest fares, bag fee unknown                    total price with the checked bag
     plain HTTP, no browser, no API key                 plain HTTP, no browser, no API key

 Admin access: AWS SSM (Session Manager / Run Command). No SSH, no open ports.
```

---

## How the bot thinks

### One process, two threads

```
 main thread (never blocks for long)                 check thread (one at a time)
 ───────────────────────────────────                 ────────────────────────────
 loop forever:                                        for every active trip:
   every 3 h passed?  ──► queue a full check ───┐       departure ≤ 3 days?  → stop it, say goodbye
   something queued?  ──► start check thread ───┼──►    ask every price source (Google today)
   getUpdates (wait up to 50 s for messages)    │       keep offers that match the trip
   for each message or button tap:              │       pick the cheapest
     handle it (setup / command)                │       compare with the recorded low (below)
     save trips.json                            │       save trips.json
     reply instantly                            │     log "check done"
     new trip or /check? ──► queue a check ─────┘

 Both threads change the same state, so every change happens under one lock,
 and every save is atomic (write a temp file, then rename it over trips.json).
```

### Adding a trip (the conversation)

```
 /start or /new                         (every question has buttons; typing also works)
      │
      ▼
 "Flying from?"   [Tel Aviv (TLV)]                         → TLV
      ▼
 "Flying to?"     [Los Angeles] [New York] [London] ...    → LAX
      ▼
 "Going date?"    calendar  ‹ September 2027 ›             → Thu 9 Sep 2027
      ▼
 "Return date?"   calendar (only days after going) [One way]  → Fri 24 Sep 2027
      ▼
 "Checked bag?"   [Yes, with a bag] [No bag]
      ▼
 "Direct only?"   [Direct only] [Connections OK]
      ▼
 "Dates move?"    [Exact dates] [±1 day] [±2 days] [±3 days]
      ▼
 summary card     [Start tracking] [Cancel]
      │
      ▼
 trip saved, first check runs now

 Shortcut: /new TLV LAX 2027-09-09 2027-09-24 bag stops=0 pax=2  → straight to the summary.
 The step you are on is saved in trips.json, so a restart in the middle loses nothing.
```

### Every check, for every trip

```
 offers from Google + Kiwi ──► keep only: exact going date, stops ≤ allowed, same currency
                                   │
                                   ▼
                          cheapest matching offer
                                   │
          ┌────────────────────────┼─────────────────────────────────────┐
          │ none found             │ first price for this trip            │ we already have a low
          ▼                        ▼                                      ▼
 tell you once:            send it, record it as the low        dropped ≥ max(50 ₪, 2%)?
 "no offers yet"                                                    │yes                 │no
                                                                    ▼                    ▼
                                                        send "New lowest price",   price went up above the low?
                                                        record it in history         → note it once in history, no message
                                                                                    14 days with no new low?
                                                                                     → one "Book it" message
```

A message counts as delivered only if Telegram answered 200 (after up to 3 retries). If it didn't, the low is not
recorded and the next check tries again.

---

## What is in the repo

```
FlightWatchBot/
├── main.py               the whole bot: Telegram loop, setup chat, price logic, state (~480 lines)
├── sources/
│   ├── __init__.py       Offer dataclass + list of enabled sources
│   ├── google.py         Google Flights, no key: cheapest fares, bag fee unknown
│   ├── kiwi.py           Kiwi.com website API, no key: total price with the checked bag, bag weight
│   └── serpapi.py        Google Flights through SerpApi, only if SERPAPI_KEY is set (backup)
├── sources.md            every price source we looked at: date, result, cost, quirks
├── test_main.py          self-check of the setup chat and price logic, no network needed
├── deploy/
│   ├── flightbot.service systemd unit
│   ├── install.sh        venv + deps + unit + enable/start, safe to re-run
│   └── flightbot.env.example
└── requirements.txt      requests, python-dotenv, fast-flights (pinned), typing_extensions
```

Design rules: smallest thing that works, plain `requests` against the Telegram Bot API (no bot framework),
one JSON file instead of a database, no cron (the process keeps its own 3-hour timer).

---

## How this project was built, step by step

The bot follows the patterns of an earlier project, [Yad2SearchBot](https://github.com/RoyBidani/Yad2SearchBot)
(apartment listings to Telegram), and the lessons it taught.

| # | step | what happened / why |
|---|---|---|
| 1 | Read Yad2SearchBot | Reused its Telegram send-with-retry, HTML message style, README layout. Left behind Playwright and the Mac launchd timer: a bot that must answer chats needs to run all the time, on a server |
| 2 | Pick a price source | The plan was the Amadeus API. **Amadeus shut down its free self-service API on 2026-07-17**, so it was out. Duffel charges per search without bookings, SerpApi is paid above 250 searches/month, travel sites (Kayak, Skyscanner) block bots. Details in [sources.md](sources.md) |
| 3 | Probe Google Flights | The `fast-flights` library calls Google Flights with plain HTTP. TLV→LAX returned real prices in ~1.2 s. Its own parser skipped Google's "best flights" section, which held the cheapest fare, so `sources/google.py` reads the page data itself |
| 4 | Bags | The plan was to let Google add its checked-bag fee to every price (`checked_bags=1`). Tested later (step 15): **Google ignores it**, prices are identical with and without. Prices are fares without the bag, and alerts say so |
| 5 | Write the bot | `main.py`: Telegram long-poll, the setup chat, the price rules (first price, ≥ 50 ₪ / 2% drop, 14-day "book it", stop 3 days before departure), atomic `trips.json`, safe shutdown |
| 6 | Self-check | `test_main.py` drives the whole flow with fake prices: setup, thresholds, bag rules, per-user isolation, auto-stop |
| 7 | Safety pass | Found the bot token leaking into logs through error messages that include the URL; it is now masked as `***` |
| 8 | Push to GitHub | This repo |
| 9 | AWS | Launched an EC2 instance in a private subnet (below), installed through SSM, proved Google answers from AWS too: 9 offers |
| 10 | Telegram | Created @RoyFlightWatch_bot with BotFather, token into `/etc/flightbot.env`, service started |
| 11 | First real trip | TLV→LAX, going Thu 9 Sep 2027, returning Fri 24 Sep 2027, bag, direct. Got 0 offers: airlines sell seats only **330 days ahead**, and the return date was not on sale yet (it opens around 29 Oct 2026). Changed the bot to say "no offers yet" once instead of staying silent |
| 12 | Requests from use | Added "Direct flights only?" as a 5th setup question. Every message now shows "Going: Thu 9 Sep 2027 / Returning: Fri 24 Sep 2027" |
| 13 | Live tests | First-price message, new-low alert (faked a higher low in `trips.json`), `/check`, `/status`, instance reboot: all passed |
| 14 | Managing trips | `/edit` to change dates, bag, stops or passengers of a trip; `/` command menu in Telegram |
| 15 | Bug: "no offers" for a trip 2 months out | TLV→ATH in Dec said "no offers". From AWS, Google sometimes answers with a temporary error (status 13, ~1 in 6 calls) and sometimes lists one flight with no price; the bot read the first as "no flights" and crashed on the second. Now: retry on errors, only status 3 (dates not on sale) means "no flights", unpriced flights are skipped, and a failed source never produces a "no offers" message |
| 16 | Buttons and design | Calendar date picker, tap-to-answer buttons, trip buttons in *My trips*, stop confirmation; every message redesigned with icons, bold dates and prices |
| 17 | Real bag prices | Google's Bags filter turned out to be carry-on only (checked in a real browser). Tried Duffel (great data, but going live needs company details) and SerpApi booking pages (no checked-bag fees). Kiwi.com's own website API gives the total price **with** the bag and its weight, no key, works from AWS. TLV→ATH with a bag: 978 ₪, where Google's "cheapest" 543 ₪ had no bag |
| 18 | Bag prices too high | TLV→AMS on Passover said 5,916 ₪: the bot let Kiwi's full price (Kiwi's markup + Kiwi's bag) beat Google's cheaper fare. Now Kiwi's bag fee for the same flight is added to Google's fare (4,038 + 484 = 4,522 ₪); Kiwi's own price still competes |
| 19 | Flexible dates | A shared Google link for TLV→AMS was ~1,000 ₪ cheaper than the bot: same flights, one day earlier out and later back, around Passover. Added "Can your dates move?" (±1–3 days): Google gets one request per date pair, Kiwi one range request. The AMS trip dropped from 4,243 ₪ to 3,256 ₪ with a bag |
| 20 | Hidden cheaper fare | TLV→LAX said 8,757 ₪ (Kiwi, bag included) while Google had the same El Al flights at 6,212 ₪ with the bag unknown. Alerts now add "💡 Cheaper fare: … bag not confirmed" whenever a fare below the bag-included price exists. *My trips* buttons show the destination (`#1 LAX · Dates`) |

---

## AWS deployment

### What was built

| item | value | why |
|---|---|---|
| Account / region | a dev AWS account, `us-east-1` | an account that already had the network pieces below |
| Instance | `t3a.micro`, Ubuntu 24.04 (amd64), name `RoysTestingEnv` | the bot uses ~50 MB RAM and almost no CPU. Ubuntu 24.04 has Python 3.12; Amazon Linux 2023 has 3.9, too old for `fast-flights` |
| Disk | 8 GB gp3, 3000 IOPS, 125 MB/s | same as the account's other small utility instances |
| Network | existing VPC, **private subnet**, outbound through a NAT gateway | nothing on the internet can reach the instance; the bot only makes outgoing HTTPS calls |
| Security group | the VPC's standard group | no inbound port is needed: Telegram long-polling is an outgoing call |
| Access | IAM instance profile with `AmazonSSMManagedInstanceCore` | shell and remote commands through AWS Systems Manager, no SSH key, no port 22 |
| Metadata | IMDSv2 required | AWS hardening default |
| Tags | `Name` only | no scheduler tags, so nothing stops it at night: it runs 24/7 |

### How it was deployed

```
 laptop                                      AWS                                   instance
 ──────                                      ───                                   ────────
 aws ec2 run-instances ...  ───────────────► new EC2 in private subnet
 aws ssm send-command:  ─────────────────────────────────────────────────────────► git clone this repo to /opt/flightbot
                                                                                   ./deploy/install.sh
                                                                                     ├ apt: python3-venv
                                                                                     ├ user "flightbot" (no login)
                                                                                     ├ venv + requirements
                                                                                     ├ creates empty /etc/flightbot.env (600)
                                                                                     └ stops: no token yet
 aws ssm send-command:  ─────────────────────────────────────────────────────────► write token to /etc/flightbot.env
                                                                                   ./deploy/install.sh again
                                                                                     ├ /etc/systemd/system/flightbot.service
                                                                                     └ systemctl enable + restart

 Every later update:  git push  →  aws ssm send-command "cd /opt/flightbot && git pull && ./deploy/install.sh"
```

The service runs as the unprivileged `flightbot` user. systemd creates `/var/lib/flightbot/` for the state file
(`StateDirectory=`), reads the token from `/etc/flightbot.env`, restarts the bot 10 s after any crash, starts it on
boot, and sends its output to journald (`journalctl -u flightbot`).

### Build your own (no existing network)

Smallest option: `t4g.nano` (arm64, 0.5 GB) in a default VPC with a public IP. The security group allows **no inbound**,
outbound 443 (Telegram, Google, SSM) and 80 (Ubuntu package mirrors).

```bash
P=personal R=eu-central-1
aws iam create-role --profile $P --role-name flightbot-ssm --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --profile $P --role-name flightbot-ssm --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam create-instance-profile --profile $P --instance-profile-name flightbot-ssm
aws iam add-role-to-instance-profile --profile $P --instance-profile-name flightbot-ssm --role-name flightbot-ssm
SG=$(aws ec2 create-security-group --profile $P --region $R --group-name flightbot --description "flightbot: no inbound" --query GroupId --output text)
aws ec2 revoke-security-group-egress --profile $P --region $R --group-id $SG --ip-permissions '[{"IpProtocol":"-1","IpRanges":[{"CidrIp":"0.0.0.0/0"}]}]'
aws ec2 authorize-security-group-egress --profile $P --region $R --group-id $SG --ip-permissions \
  '[{"IpProtocol":"tcp","FromPort":443,"ToPort":443,"IpRanges":[{"CidrIp":"0.0.0.0/0"}]},{"IpProtocol":"tcp","FromPort":80,"ToPort":80,"IpRanges":[{"CidrIp":"0.0.0.0/0"}]}]'
AMI=$(aws ssm get-parameter --profile $P --region $R --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)
aws ec2 run-instances --profile $P --region $R --image-id $AMI --instance-type t4g.nano --security-group-ids $SG \
  --iam-instance-profile Name=flightbot-ssm --metadata-options HttpTokens=required \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=flightbot}]' --query 'Instances[0].InstanceId' --output text
```

Wait two minutes, then open a shell: `aws ssm start-session --profile $P --region $R --target <instance-id>`
(needs the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)).

---

## Set it up yourself

### 1. Create your Telegram bot

1. In Telegram open [@BotFather](https://t.me/BotFather), send `/newbot`.
2. Pick a display name, then a username ending in `bot` (e.g. `MyFlightWatch_bot`).
3. BotFather replies with an **HTTP API token** like `123456789:AAH...`. Keep it private, it controls the bot.
   Leaked? Send `/revoke` to BotFather and put the new token in `/etc/flightbot.env`.

No chat id needed: the bot answers whoever writes to it.

### 2. Try it on your laptop (optional)

```bash
git clone https://github.com/RoyBidani/FlightWatchBot.git && cd FlightWatchBot
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python main.py --dry-run --trip "TLV LAX 2027-08-20 2027-08-30 bag"
```

Prints the message the bot would send for that trip. Sends nothing, writes nothing. Python 3.10+.
To run the real bot locally put `TELEGRAM_BOT_TOKEN=...` in `.env` and run `./venv/bin/python main.py`
(state goes to `./trips.json`). Stop it before starting the server copy: one token, one poller.

### 3. Install on the server

```bash
sudo git clone https://github.com/RoyBidani/FlightWatchBot.git /opt/flightbot
sudo chown -R $USER /opt/flightbot && cd /opt/flightbot
./deploy/install.sh                       # first run creates /etc/flightbot.env and stops
sudo nano /etc/flightbot.env              # paste TELEGRAM_BOT_TOKEN
./deploy/install.sh                       # venv, deps, systemd unit, enable + start
```

`install.sh` is safe to re-run (it is also how you deploy an update after `git pull`). It never touches
`/etc/flightbot.env` or the state file. Any clone path works; the unit is rewritten to point at it.

---

## Using the bot

Open the bot, press **Start**. Everything is buttons; typing works too:

1. **From**: tap *Tel Aviv (TLV)*, or type any city / 3-letter code
2. **To**: tap one of the popular destinations, or type it
3. **Going date**: tap a day in the calendar (‹ › switch months; days too close to today are greyed out `·`)
4. **Return date**: tap a day (only days after the going date), or *One way*
5. **Checked bag**: *Yes, with a bag* / *No bag*. With a bag, prices come from Kiwi.com with the airline's checked bag
   already added (the alert shows its weight, 15–23 kg depending on the airline); if only Google has a price, the
   alert says the bag is not in it
6. **Direct only**: *Direct only* / *Connections OK* (up to 1 connection)
7. **Flexible dates**: *Exact dates* / *±1 day* / *±2 days* / *±3 days*. With ±1 the bot checks all 9 going / return
   combinations (±3 = 49) and sends the cheapest, marked e.g. `Going Wed 14 Apr 2027 (1 day earlier)`
8. A summary card, tap **Start tracking**

After each tap the question is updated with your answer (`→ Tue 29 Dec 2026`), so the chat reads as a clean record.
The first price arrives within seconds (or "no matching offers yet" if the dates are not on sale yet).

The main buttons **New trip · My trips · Check now** come with every menu message. **My trips** lists each trip with
three buttons labelled with its destination: **#1 LAX · Status**, **· Dates** (pick new dates in the calendar) and
**· Stop** (asks before stopping). With a bag, if a cheaper fare exists whose bag Google can't confirm, the alert adds a
`💡 Cheaper fare` line with its link.

Defaults: 1 adult, economy, max 1 connection, ILS. Power users skip the buttons:

```
/new TLV LAX 2027-09-09 2027-09-24 bag pax=2 stops=0 flex=1 cabin=business
```

`flex=0..3` flexible dates (± days), `stops=0` direct only, `stops=1` up to one connection, `stops=2` up to two. City names the bot knows are in
`AIRPORTS` / `CITIES` in `main.py`; for anything else send the 3-letter airport code.
Type `/` in the chat to get the command menu (the bot registers it on startup).

| command | does |
|---|---|
| `/new` | add a trip (buttons, or the one-liner above) |
| `/dates <id>` | pick new dates for a trip in the calendar (same as the *Dates* button) |
| `/list` | your active trips with their current low |
| `/status [id]` | lowest ever vs cheapest in the last check, days to departure |
| `/edit <id> ...` | change a trip, e.g. `/edit 1 stops=1`, `/edit 1 go=2027-09-10 back=2027-09-24`, `/edit 1 nobag`, `/edit 1 flex=2`, `back=none` = one way. Only what you write changes; the trip keeps its number but its price history restarts (new dates = different flight) |
| `/stop <id>` | stop tracking a trip |
| `/check` | check your trips now, reply with the cheapest per trip even if it is not a new low |
| `/lang en` / `/lang he` | language of the price alerts (default English) |
| `/cancel` | abort a setup in progress |

### What an alert looks like

```
✈️ TLV → LAX  ·  #1
🛫 Going  Fri 20 Aug 2027
🛬 Return  Mon 30 Aug 2027

📉 New lowest price! (was 3,640 ₪)
💰 3,490 ₪  round trip · 1 adult

🛩 SWISS
🕐 04:50 TLV → ZRH → 16:30 LAX
↔️ 1 stop in ZRH · 21h 40m
🧳 Bag (23 kg): +544 ₪, already in the price

🔎 Open on Google Flights             ← link to the same search
The price includes the return flight; you pick it on Google.
```

Price rules, per trip in `trips.json`: `min_drop` (₪, default 50) and `min_drop_pct` (default 2).
A smaller drop is not recorded either, so a low can't creep down 10 ₪ at a time without you hearing about it.

**Booking window:** airlines sell seats about **330 days** ahead. If your return date is further than that,
Google shows nothing for the round trip yet. The bot says so once, keeps checking every 3 hours, and sends
the first price on the day the dates go on sale.

---

## Running and operating it

On the server (or through `aws ssm start-session`):

```bash
sudo systemctl status flightbot
journalctl -u flightbot -f                                   # live log
journalctl -u flightbot --since -1d | grep 'check done'      # health: ~8 lines a day
sudo systemctl restart flightbot                             # after editing /etc/flightbot.env
cd /opt/flightbot && git pull && ./deploy/install.sh         # deploy a new version
```

| flag | does |
|---|---|
| none | daemon: Telegram + a check every 3 hours (what systemd runs) |
| `--once` | one real check of every trip (sends alerts, saves), then exit |
| `--dry-run` | one check, print what would be sent, write nothing. Uses `FLIGHTBOT_STATE` (default `./trips.json`) |
| `--dry-run --trip "TLV LAX 2027-08-20 bag"` | same, for a throwaway trip instead of the saved ones |

On the server, prefix `--once` / `--dry-run` with `sudo -u flightbot env FLIGHTBOT_STATE=/var/lib/flightbot/trips.json`
and stop the service first for `--once`.

What it survives: reboots (service is enabled), crashes (`Restart=always`, 10 s), Telegram or network outages
(retries every 5→60 s, never exits), a failing price source (one warning, skipped). `systemctl stop` exits
cleanly and the state file is never half-written.

Backups: `trips.json` is the only state. Copy it off the box now and then if the price history matters to you.

### Adding a price source

One file in `sources/`, one `search(trip) -> list[Offer]` function, registered in `sources/__init__.py` behind its
env key. Return every offer; `main.py` filters by the trip's rules and picks the cheapest. Raise on failure, the
bot logs one warning and carries on. Write down what you learned in [sources.md](sources.md).

---

## Testing

```bash
./venv/bin/python test_main.py      # prints "ok"
```

No network: fake prices drive the setup chat, the 50 ₪ / 2% rule, bag rules, "no offers yet", the 14-day
message, auto-stop and per-user isolation.

Live checks on the running bot:

| check | how | status |
|---|---|---|
| Google answers from AWS | `--dry-run --trip ...` on the instance | done, 9 offers |
| Bot answers in Telegram | `/start`, buttons and calendar | done |
| Clean restart, state kept | `systemctl restart`, trip still there | done |
| First price message | a trip with dates on sale | done, 3,473 ₪ |
| New-low alert | raise the recorded low in `trips.json`, restart or `/check` | done |
| Survives reboot | reboot the instance, then `/status` | done, back in 25 s |
| Two 3-hour checks | `journalctl -u flightbot \| grep 'check done'` | done, 8 in a row exactly 3 h apart (Oct 20–21), surviving Telegram 429 / 502 / timeouts |

---

## Troubleshooting

| log line / symptom | meaning / fix |
|---|---|
| `TELEGRAM_BOT_TOKEN missing` | `/etc/flightbot.env` empty or not readable, or no `.env` on a laptop |
| `getUpdates failed: 401` | wrong token. Fix `/etc/flightbot.env`, `sudo systemctl restart flightbot` |
| `getUpdates failed: 409 Conflict` | the same token is polled elsewhere (your laptop?). Stop the other copy |
| `getUpdates failed: ... retry in 60s` repeating | no network / DNS. Check outbound 443 (security group, NAT) |
| `google failed: no results script in page (consent/captcha?)` | Google refused this IP or changed the page. Set `SERPAPI_KEY`; see [sources.md](sources.md) |
| `google failed: IndexError ...` / `TypeError ...` | Google changed the page data layout. Update `sources/google.py` |
| `source serpapi disabled: SERPAPI_KEY not set` | expected without a key |
| `trip N google: 0 offers` | dates more than ~330 days out (not on sale yet), or rules too strict (direct only). Wait, or try `stops=1` |
| `trip N google failed: google error status 13 three times in a row` | Google had a temporary problem; no message is sent, the next check retries |
| `telegram <id> -> 403` | that user blocked the bot. Their trips stay in `trips.json`; `/stop` them or ignore |
| no `check done:` for > 3 hours | service down: `systemctl status flightbot`, `journalctl -u flightbot -n 50` |
| bot answers but alerts never come | prices did not drop enough. `/check` shows the current cheapest |
| `stopped` + `flightbot up` around 06:00–07:00 UTC, no deploy | Ubuntu's automatic security updates restarted the service. Normal; a check runs right after |
