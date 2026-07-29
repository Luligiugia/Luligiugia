#!/usr/bin/env python3
"""
Market Hunter Calcio – Final Edition (con debug orari)
"""

import os, json, logging, requests, sys
from datetime import datetime, date, timedelta

API_KEY = os.environ["API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Soglie
PRE_CRASH_THRESHOLD_PERCENT = 12       # pre‑alert già al 12%
FULL_CRASH_THRESHOLD_PERCENT = 25      # alert definitivo al 25%
MAX_MINUTES_CRASH_WINDOW = 15          # confronto con 15 minuti fa (3 scansioni)
MIN_STARTING_ODD = 1.80
MAX_CRASH_ODD = 1.80
QUOTA_MINIMA_DOPO_CRASH = 1.30         # ignora crolli che schiacciano sotto 1.30
HOURS_BEFORE_KICKOFF = 2               # ignora partite che iniziano oltre 2 ore da adesso

TARGET_SPORT_KEYS = [
    "soccer_argentina_primera_division",
    "soccer_denmark_superliga",
    "soccer_finland_veikkausliiga",
    "soccer_league_of_ireland",
    "soccer_poland_ekstraklasa",
    "soccer_russia_premier_league",
    "soccer_sweden_allsvenskan",
]

def is_monitoring_window():
    now = datetime.utcnow()
    if now.weekday() != 6:   # solo domenica
        return False
    if not (14 <= now.hour <= 21):
        return False
    return True

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        logging.error(f"Telegram error: {e}")

def load_json(filename, default=None):
    try:
        with open(filename) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def fetch_odds():
    url = "https://api.the-odds-api.com/v4/sports/soccer/odds/"
    params = {
        "apiKey": API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "dateFormat": "iso",
        "oddsFormat": "decimal",
        "includeLinks": "false",
        "includeSids": "false"
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            logging.error(f"HTTP {resp.status_code}: {resp.text}")
            return []
        data = resp.json()
        matches = []
        min_start = None
        max_start = None
        for game in data:
            sport_key = game.get("sport_key")
            if sport_key not in TARGET_SPORT_KEYS:
                continue
            home = game["home_team"]
            away = game["away_team"]
            commence_time = game.get("commence_time")

            if commence_time:
                try:
                    kickoff = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
                    if min_start is None or kickoff < min_start:
                        min_start = kickoff
                    if max_start is None or kickoff > max_start:
                        max_start = kickoff
                except:
                    pass

            bookmakers = game.get("bookmakers", [])
            if not bookmakers:
                continue
            bk = bookmakers[0]

            odd_home = odd_away = None
            for market in bk.get("markets", []):
                if market["key"] == "h2h":
                    outcomes = market["outcomes"]
                    odd_home = next((o["price"] for o in outcomes if o["name"] == home), None)
                    odd_away = next((o["price"] for o in outcomes if o["name"] == away), None)
                    break
            if not odd_home or not odd_away:
                continue

            matches.append({
                "fixture_id": game["id"],
                "home": home,
                "away": away,
                "league": game["sport_title"] + " - " + sport_key,
                "commence_time": commence_time,
                "odd_home": odd_home,
                "odd_away": odd_away,
                "bookmaker_used": bk["key"]
            })

        if min_start and max_start:
            logging.info(f"ORARI PARTITE: dalle {min_start.strftime('%H:%M')} alle {max_start.strftime('%H:%M')} UTC")
        else:
            logging.info("ORARI PARTITE: nessuna partita trovata")

        bk_used = set(m["bookmaker_used"] for m in matches)
        logging.info(f"Bookmaker utilizzati oggi: {bk_used}")
        return matches
    except Exception as e:
        logging.error(f"API call failed: {e}")
        return []

def check_crashes(state, current_matches, now):
    alerts = []
    new_state = {}
    threshold_time = now - timedelta(minutes=MAX_MINUTES_CRASH_WINDOW)

    for m in current_matches:
        if m.get("commence_time"):
            try:
                kickoff = datetime.fromisoformat(m["commence_time"].replace("Z", "+00:00"))
                if (kickoff - now).total_seconds() > HOURS_BEFORE_KICKOFF * 3600:
                    continue
            except:
                pass

        fid = m["fixture_id"]
        new_state[fid] = {
            "home": m["home"],
            "away": m["away"],
            "league": m["league"],
            "odd_home": m["odd_home"],
            "odd_away": m["odd_away"],
            "timestamp": now.isoformat()
        }

        if fid not in state:
            continue

        prev = state[fid]
        try:
            prev_time = datetime.fromisoformat(prev["timestamp"])
        except:
            continue
        if (now - prev_time) > timedelta(minutes=MAX_MINUTES_CRASH_WINDOW):
            continue

        old_home = prev["odd_home"]
        old_away = prev["odd_away"]

        if old_home > MIN_STARTING_ODD and m["odd_home"] < MAX_CRASH_ODD:
            drop = (old_home - m["odd_home"]) / old_home
            if drop >= CRASH_THRESHOLD_PERCENT / 100.0:
                alerts.append({
                    "fixture_id": fid,
                    "home": m["home"],
                    "away": m["away"],
                    "league": m["league"],
                    "side": "Home",
                    "old_odd": old_home,
                    "new_odd": m["odd_home"],
                    "drop": round(drop * 100, 2),
                    "predicted": m["home"],
                    "time": now.strftime("%H:%M:%S")
                })

        if old_away > MIN_STARTING_ODD and m["odd_away"] < MAX_CRASH_ODD:
            drop = (old_away - m["odd_away"]) / old_away
            if drop >= CRASH_THRESHOLD_PERCENT / 100.0:
                alerts.append({
                    "fixture_id": fid,
                    "home": m["home"],
                    "away": m["away"],
                    "league": m["league"],
                    "side": "Away",
                    "old_odd": old_away,
                    "new_odd": m["odd_away"],
                    "drop": round(drop * 100, 2),
                    "predicted": m["away"],
                    "time": now.strftime("%H:%M:%S")
                })
    return alerts, new_state

def save_bet(bets, alert):
    fid = alert["fixture_id"]
    for b in bets:
        if b["fixture_id"] == fid and b["side"] == alert["side"]:
            return bets
    bets.append({
        "fixture_id": fid,
        "home_team": alert["home"],
        "away_team": alert["away"],
        "predicted_winner": alert["predicted"],
        "odd_at_crash": alert["new_odd"],
        "crash_percent": alert["drop"],
        "timestamp": datetime.now().isoformat(),
        "result": "pending"
    })
    return bets

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not is_monitoring_window():
        logging.info("Fuori dalla finestra di monitoraggio. Esco.")
        sys.exit(0)

    logging.info("Market Hunter Calcio (Final) started")

    state = load_json("state.json")
    bets = load_json("bets.json", [])

    matches = fetch_odds()
    logging.info(f"Trovate {len(matches)} partite nei campionati target")

    now = datetime.now()
    alerts, new_state = check_crashes(state, matches, now)

    for alert in alerts:
        message = (
            f"🚨 *CRASH CALCIO*\n"
            f"⚽ {alert['league']}\n"
            f"⚔️ {alert['home']} vs {alert['away']}\n"
            f"📉 Quota {alert['predicted']}: {alert['old_odd']:.2f} → {alert['new_odd']:.2f} (-{alert['drop']}%)\n"
            f"⏱️ Rilevato alle {alert['time']}\n"
            f"🔮 Pronostico: *{alert['predicted']}* vincitore"
        )
        send_telegram(message)
        bets = save_bet(bets, alert)

    save_json("state.json", new_state)
    save_json("bets.json", bets)

    solved = [b for b in bets if b["result"] != "pending"]
    if solved:
        won = sum(1 for b in solved if b["result"] == "won")
        logging.info(f"RIEPILOGO: {won}/{len(solved)} vinti ({100*won/len(solved):.1f}%)")
    else:
        logging.info("Nessun bet risolto ancora.")

    logging.info(f"Inviate {len(alerts)} notifiche. Stato salvato.")
