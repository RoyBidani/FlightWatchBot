# FlightWatch Telegram Bot

Watches the price of a flight and tells you on Telegram when it finds a new lowest price.
You set up a trip from the chat (from, to, dates, checked bag, direct only), the bot checks every 3 hours and messages you
only when the price drops by at least 50 ₪ or 2%, whichever is larger. Any number of trips, any number of people:
everyone who starts the bot gets their own trips.

Runs as one always-on Python process (systemd service) on a small Linux server, e.g. an AWS EC2 `t4g.nano`.

---

## How it works

One process, forever:

1. Long-polls Telegram (`getUpdates`, 50 s) and answers commands instantly.
2. Every 3 hours (and right after you add a trip) runs a price check in a background thread: every active trip,
   every enabled source, cheapest offer that matches the trip (exact dates, stops, cabin, passengers, bag).
3. First result of a trip is sent and recorded as the current low. After that a message goes out only on a real
   drop. Every new low is kept in the trip's history with time, price, source, airline, itinerary and link.
4. 14 days without a new low: one "Book it" message. 3 days before departure: the trip stops itself.

State is one JSON file (`/var/lib/flightbot/trips.json`), written atomically after every change.

Price sources and what was tried: [sources.md](sources.md). Today: Google Flights (no key, no browser) plus
SerpApi when you give it a key. Amadeus Self-Service, the original plan, was shut down by Amadeus on 2026-07-17.

---

## Setup, end to end

### 1. Create your Telegram bot

1. In Telegram open [@BotFather](https://t.me/BotFather), send `/newbot`.
2. Pick a display name, then a username ending in `bot` (e.g. `MyFlightWatch_bot`).
3. BotFather replies with an **HTTP API token** like `123456789:AAH...`. Keep it private, it controls the bot.

No chat id needed: the bot answers whoever writes to it.

### 2. Try it on your laptop (optional)

```bash
git clone https://github.com/RoyBidani/FlightWatchBot.git && cd FlightWatchBot
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python main.py --dry-run --trip "TLV LAX 2027-03-10 2027-03-20 bag"
```

Prints the message the bot would send for that trip. Sends nothing, writes nothing. Python 3.10+.
To run the real bot locally put `TELEGRAM_BOT_TOKEN=...` in `.env` and run `./venv/bin/python main.py`
(state goes to `./trips.json`). Stop it before starting the server copy: one token, one poller.

### 3. Server (AWS EC2)

Smallest instance is enough: `t4g.nano` (arm64, 0.5 GB), **Ubuntu 24.04** (Amazon Linux 2023 ships Python 3.9, too old).
No inbound ports. Outbound 443 (Telegram, Google, SerpApi, SSM) and 80 (Ubuntu apt mirrors). You get a shell through
SSM Session Manager, so no SSH key or port 22.

One-time, with your own AWS profile and region:

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

### 4. Install

On the server:

```bash
sudo git clone https://github.com/RoyBidani/FlightWatchBot.git /opt/flightbot
sudo chown -R $USER /opt/flightbot && cd /opt/flightbot
./deploy/install.sh                       # first run creates /etc/flightbot.env and stops
sudo nano /etc/flightbot.env              # paste TELEGRAM_BOT_TOKEN (and SERPAPI_KEY if you have one)
./deploy/install.sh                       # venv, deps, systemd unit, enable + start
```

`install.sh` is safe to re-run (also how you deploy an update after `git pull`). It never touches
`/etc/flightbot.env` or the state file. Any clone path works; the unit is rewritten to point at it.

### 5. Add a trip from Telegram

Open your bot, press **Start**. It asks five things, one at a time:

1. From where (`Tel Aviv` or `TLV`)
2. To where (`Los Angeles`, `LA` or `LAX`)
3. Dates: `2027-03-10 2027-03-20`, or one date for one-way (`10.3.2027` works too)
4. Checked bag: `yes` (one 23 kg bag per passenger, fee included in the compared price) or `no`
5. Direct flights only: `yes` (nonstop) or `no` (up to 1 connection; `stops=2` on the one-liner for 2)

It shows a summary, you answer `yes`, and the current cheapest price arrives within seconds.
Defaults: 1 adult, economy, max 1 stop, ILS. Power users skip the questions:

```
/new TLV LAX 2027-03-10 2027-03-20 bag pax=2 stops=0 cabin=business
```

City names the bot knows are in `AIRPORTS` / `CITIES` in `main.py`; anything else, send the 3-letter code.

### Commands

| command | does |
|---|---|
| `/new` | add a trip (questions, or the one-liner above) |
| `/list` | your active trips with their current low |
| `/status [id]` | lowest ever vs cheapest in the last check, days to departure |
| `/stop <id>` | stop tracking a trip |
| `/check` | check your trips now, reply with the cheapest per trip even if it is not a new low |
| `/lang en` / `/lang he` | language of the price alerts (default English) |
| `/cancel` | abort a setup in progress |

### Alerts

```
TLV → LAX · 10 Mar 2027 – 20 Mar 2027 #1
New lowest price (was 3,161 ₪)
2,980 ₪ round trip, 1 adult(s)
Iberia
Outbound: TLV 05:30 → MAD → LAX 16:40, 1 stop(s) via MAD, 21h 10m
Checked bag: included in price
Source: Google Flights
Price is the round-trip total, pick the return on the booking page.
Book / open search
```

Alert threshold, per trip in `trips.json`: `min_drop` (₪, default 50) and `min_drop_pct` (default 2).
A smaller drop is not recorded either, so lows can't creep down 10 ₪ at a time unnoticed.
If the recorded low disappears (prices went up) it is noted once in the trip history, no message.

---

## Running it

```bash
sudo systemctl status flightbot
journalctl -u flightbot -f                                   # live log
journalctl -u flightbot --since -1d | grep 'check done'      # health: ~8 lines a day
sudo systemctl restart flightbot                             # after editing /etc/flightbot.env
sudo -u flightbot /opt/flightbot/venv/bin/python /opt/flightbot/main.py --dry-run   # check all saved trips, print only
```

| flag | does |
|---|---|
| none | daemon: Telegram + a check every 3 hours |
| `--once` | one real check of every trip (sends alerts, saves), then exit |
| `--dry-run` | one check, print what would be sent, write nothing. Uses `FLIGHTBOT_STATE` (default `./trips.json`) |
| `--dry-run --trip "TLV LAX 2027-03-10 bag"` | same, for a throwaway trip instead of the saved ones |

`--once` and `--dry-run` read the state file. On the server prefix them with
`sudo -u flightbot env FLIGHTBOT_STATE=/var/lib/flightbot/trips.json` and stop the service first for `--once`.

Survives: reboots (`enable`), crashes (`Restart=always`, 10 s), Telegram/network outages (retries 5→60 s, never
exits), a failing source (logged, skipped). `systemctl stop` exits cleanly; the state file is never half-written.

Backups: `trips.json` is the only state. Copy it off the box now and then if the history matters to you.

---

## Adding a price source

One file in `sources/`, one `search(trip) -> list[Offer]` function, registered in `sources/__init__.py` behind its
env key. Return every offer; `main.py` filters by trip constraints and picks the cheapest. Raise on failure, the
bot logs one warning and carries on. Write down what you learned in [sources.md](sources.md).

---

## Troubleshooting

| log line / symptom | meaning / fix |
|---|---|
| `TELEGRAM_BOT_TOKEN missing` | `/etc/flightbot.env` empty or not readable, or no `.env` on a laptop |
| `getUpdates failed: 401` | wrong token. Fix `/etc/flightbot.env`, `sudo systemctl restart flightbot` |
| `getUpdates failed: 409 Conflict` | the same token is polled elsewhere (your laptop?). Stop the other copy |
| `getUpdates failed: ... retry in 60s` repeating | no network / DNS. Check the security group allows outbound 443 |
| `google failed: no results script in page (consent/captcha?)` | Google refused this IP or changed the page. Set `SERPAPI_KEY`; see [sources.md](sources.md) |
| `google failed: IndexError ...` / `TypeError ...` | Google changed the payload layout. Update `sources/google.py` |
| `source serpapi disabled: SERPAPI_KEY not set` | expected without a key |
| `trip N google: 0 offers` | nothing matches (stops too strict, dates sold out, bag filter). Try `/new ... stops=2` |
| `telegram <id> -> 403` | that user blocked the bot. Their trips stay in `trips.json`; `/stop` them or ignore |
| no `check done:` for > 3 hours | service down: `systemctl status flightbot`, `journalctl -u flightbot -n 50` |
| bot answers but alerts never come | prices did not drop enough. `/check` shows the current cheapest |
