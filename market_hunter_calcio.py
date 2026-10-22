#!/usr/bin/env python3
"""
Market Hunter Calcio – Venerdì Edition (con salvataggio CSV, filtro anti‑contraddittorio, no pareggi)
"""

import os, json, csv, logging, requests, sys
from datetime import datetime, date, timedelta

API_KEY = os.environ["API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

PRE_CRASH_THRESHOLD_PERCENT = 12
FULL_CRASH_THRESHOLD_PERCENT = 25
MAX_MINUTES_CRASH_WINDOW = 10
MIN_STARTING_ODD = 1.80
MAX_CRASH_ODD = 1.80
# AGGIORNATO: alzata la quota minima per evitare scommesse poco redditizie
QUOTA_MINIMA_DOPO_CRASH = 1.45 
HOURS_BEFORE_KICKOFF = 2
AMBIGUOUS_MINUTES = 30

# AGGIORNATO: Liste ottimizzate per i campionati più affidabili
TARGET_SPORT_KEYS = [
    # Campionati con alta accuratezza (Europa del Nord & altro)
    "soccer_sweden_allsvenskan",
    "soccer_sweden_superettan",
    "soccer_norway_eliteserien",
    "soccer_denmark_superliga",
    "soccer_finland_veikkausliiga",
    "soccer_saudi_arabia_pro_league",
    "soccer_spain_segunda_division",
    "soccer_russia_premier_league",
    
    # Sud America
    "soccer_argentina_primera_division",
    "soccer_brazil_campeonato",
    
    # Altre coppe/minori
    "soccer_league_of_ireland",
    "soccer_germany_dfb_pokal",
    
    # NOTA: La Polonia (soccer_poland_ekstraklasa) è stata rimossa perché 
    # generava molti falsi positivi (~55% di accuratezza). Puoi riaggiungerla se vuoi.
]

def is_monitoring_window():
    now = datetime.utcnow()
    # CAMBIA QUESTO NUMERO: 4=Venerdì, 5=Sabato, 6=Domenica
    if now.weekday() != 6:   
        return False
    if not (12 <= now.hour < 20):
        return False
    return True

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        if resp.status_code != 200:
            logging.error(f"TELEGRAM ERROR {resp.status_code}: {resp.text[:300]}")
        else:
            logging.info("Telegram OK: messaggio inviato")
    except Exception as e:
        logging.error(f"TELEGRAM EXCEPTION: {e}")

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
        for game in data:
            sport_key = game.get("sport_key")
            if sport_key not in TARGET_SPORT_KEYS:
                continue
            home = game["home_team"]
            away = game["away_team"]
            commence_time = game.get("commence_time")

            bookmakers = game.get("bookmakers", [])
            if not bookmakers:
                continue
            bk = bookmakers[0]

            odd_home = odd_away = odd_draw = None
            for market in bk.get("markets", []):
                if market["key"] == "h2h":
                    outcomes = market["outcomes"]
                    odd_home = next((o["price"] for o in outcomes if o["name"] == home), None)
                    odd_away = next((o["price"] for o in outcomes if o["name"] == away), None)
                    odd_draw = next((o["price"] for o in outcomes if o["name"] == "Draw"), None)
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
                "odd_draw": odd_draw,
            })
        return matches
    except Exception as e:
        logging.error(f"API call failed: {e}")
        return []

def check_crashes(state, current_matches, now):
    alerts = []
    new_state = {}

    for m in current_matches:
        if m.get("commence_time"):
            try:
                kickoff = datetime.fromisoformat(m["commence_time"].replace("Z", "+00:00"))
                if (kickoff - now).total_seconds() > HOURS_BEFORE_KICKOFF * 3600:
                    continue
            except:
                pass

        fid = m["fixture_id"]
        prev = state.get(fid, {})
        new_state[fid] = {
            "home": m["home"],
            "away": m["away"],
            "league": m["league"],
            "odd_home": m["odd_home"],
            "odd_away": m["odd_away"],
            "timestamp": now.isoformat(),
            "pre_alert_sent": prev.get("pre_alert_sent", False),
            "alert_sent": prev.get("alert_sent", False),
            "last_side": prev.get("last_side"),
            "last_alert_time": prev.get("last_alert_time")
        }

        if fid not in state:
            continue

        try:
            prev_time = datetime.fromisoformat(prev["timestamp"])
        except:
            continue
        if (now - prev_time) > timedelta(minutes=MAX_MINUTES_CRASH_WINDOW):
            continue

        old_home = prev["odd_home"]
        old_away = prev["odd_away"]

        def append_alert(side, odd_old, odd_new, drop, predicted, alert_type):
            ambiguous = False
            last_side = prev.get("last_side")
            last_time_str = prev.get("last_alert_time")
            if last_side and last_side != side:
                if last_time_str:
                    try:
                        last_time = datetime.fromisoformat(last_time_str)
                        if (now - last_time) <= timedelta(minutes=AMBIGUOUS_MINUTES):
                            ambiguous = True
                    except:
                        pass
            alert = {
                "fixture_id": fid,
                "home": m["home"],
                "away": m["away"],
                "league": m["league"],
                "side": side,
                "old_odd": odd_old,
                "new_odd": odd_new,
                "drop": round(drop * 100, 2),
                "predicted": predicted,
                "time": now.strftime("%H:%M:%S"),
                "alert_type": alert_type,
                "odd_draw": m.get("odd_draw"),
                "ambiguous": ambiguous
            }
            alerts.append(alert)
            new_state[fid]["last_side"] = side
            new_state[fid]["last_alert_time"] = now.isoformat()

        # Lato Home
        if old_home > MIN_STARTING_ODD and m["odd_home"] < MAX_CRASH_ODD:
            drop = (old_home - m["odd_home"]) / old_home
            if drop >= FULL_CRASH_THRESHOLD_PERCENT / 100.0:
                if m["odd_home"] >= QUOTA_MINIMA_DOPO_CRASH:
                    append_alert("Home", old_home, m["odd_home"], drop, m["home"], "definitive")
            elif drop >= PRE_CRASH_THRESHOLD_PERCENT / 100.0:
                if not prev.get("pre_alert_sent"):
                    if m["odd_home"] >= QUOTA_MINIMA_DOPO_CRASH:
                        append_alert("Home", old_home, m["odd_home"], drop, m["home"], "pre_alert")
                        new_state[fid]["pre_alert_sent"] = True

        # Lato Away
        if old_away > MIN_STARTING_ODD and m["odd_away"] < MAX_CRASH_ODD:
            drop = (old_away - m["odd_away"]) / old_away
            if drop >= FULL_CRASH_THRESHOLD_PERCENT / 100.0:
                if m["odd_away"] >= QUOTA_MINIMA_DOPO_CRASH:
                    append_alert("Away", old_away, m["odd_away"], drop, m["away"], "definitive")
            elif drop >= PRE_CRASH_THRESHOLD_PERCENT / 100.0:
                if not prev.get("pre_alert_sent"):
                    if m["odd_away"] >= QUOTA_MINIMA_DOPO_CRASH:
                        append_alert("Away", old_away, m["odd_away"], drop, m["away"], "pre_alert")
                        new_state[fid]["pre_alert_sent"] = True

    return alerts, new_state

def save_bet(bets, alert):
    fid = alert["fixture_id"]
    side = alert.get("side")
    for b in bets:
        if b["fixture_id"] == fid and b["side"] == side and b["predicted_winner"] == alert["predicted"]:
            return bets
    
    bets.append({
        "fixture_id": fid,
        "home_team": alert["home"],
        "away_team": alert["away"],
        "predicted_winner": alert["predicted"],
        "odd_at_crash": alert["new_odd"],
        "crash_percent": alert["drop"],
        "timestamp": datetime.now().isoformat(),
        "result": "pending",
        "tipo": alert["alert_type"],
        "ambiguous": alert.get("ambiguous", False)
    })
    return bets

def log_bet_to_csv(alert, filename="bets_log.csv"):
    """Salva una riga nel file CSV per ogni alert."""
    file_exists = os.path.isfile(filename)
    with open(filename, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "fixture_id", "Data", "Ora", "Tipo", "Campionato",
                "Squadra casa", "Squadra ospite", "Pronostico",
                "Quota prima", "Quota dopo", "Calo %",
                "Quota pareggio", "Risultato reale", "Esito", "Ambiguità"
            ])
        writer.writerow([
            alert.get("fixture_id", ""),
            datetime.now().strftime("%Y-%m-%d"),
            alert["time"],
            alert["alert_type"],
            alert["league"],
            alert["home"],
            alert["away"],
            alert["predicted"],
            f'{alert["old_odd"]:.2f}',
            f'{alert["new_odd"]:.2f}',
            alert["drop"],
            alert.get("odd_draw", "N/D"),
            "",   # risultato reale
            "",   # esito
            "Sì" if alert.get("ambiguous") else "No"
        ])

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not is_monitoring_window():
        logging.info("Fuori dalla finestra di monitoraggio. Esco.")
        sys.exit(0)

    # CAMBIA IL NOME DEL GIORNO QUI SOTTO (Venerdì / Sabato / Domenica)
    logging.info("Market Hunter Calcio (Venerdì) started")

    state = load_json("state.json")
    bets = load_json("bets.json", [])

    matches = fetch_odds()
    logging.info(f"Trovate {len(matches)} partite nei campionati target")

    now = datetime.now()
    alerts, new_state = check_crashes(state, matches, now)

    for alert in alerts:
        if alert.get("ambiguous"):
            message = (
                f"⚠️ *SEGNALE CONTRADDITTORIO*\n"
                f"⚽ {alert['league']}\n"
                f"⚔️ {alert['home']} vs {alert['away']}\n"
                f"📉 Quota {alert['predicted']}: {alert['old_odd']:.2f} → {alert['new_odd']:.2f} (-{alert['drop']}%)\n"
                f"🤝 Quota pareggio: {alert.get('odd_draw', 'N/D')}\n"
                f"⏱️ Rilevato alle {alert['time']}\n"
                f"🔮 Valuta con cautela"
            )
        elif alert["alert_type"] == "pre_alert":
            message = (
                f"⚠️ *MOVIMENTO SOSPETTO*\n"
                f"⚽ {alert['league']}\n"
                f"⚔️ {alert['home']} vs {alert['away']}\n"
                f"📉 Quota {alert['predicted']}: {alert['old_odd']:.2f} → {alert['new_odd']:.2f} (-{alert['drop']}%)\n"
                f"🤝 Quota pareggio: {alert.get('odd_draw', 'N/D')}\n"
                f"⏱️ Rilevato alle {alert['time']}\n"
                f"🔮 Possibile crollo su *{alert['predicted']}*"
            )
        else:
            message = (
                f"🚨 *CRASH CALCIO*\n"
                f"⚽ {alert['league']}\n"
                f"⚔️ {alert['home']} vs {alert['away']}\n"
                f"📉 Quota {alert['predicted']}: {alert['old_odd']:.2f} → {alert['new_odd']:.2f} (-{alert['drop']}%)\n"
                f"🤝 Quota pareggio: {alert.get('odd_draw', 'N/D')}\n"
                f"⏱️ Rilevato alle {alert['time']}\n"
                f"🔮 Pronostico: *{alert['predicted']}* vincitore"
            )
        send_telegram(message)
        bets = save_bet(bets, alert)
        log_bet_to_csv(alert)

    save_json("state.json", new_state)
    save_json("bets.json", bets)

    solved = [b for b in bets if b["result"] != "pending"]
    if solved:
        won = sum(1 for b in solved if b["result"] == "won")
        logging.info(f"RIEPILOGO: {won}/{len(solved)} vinti ({100*won/len(solved):.1f}%)")
    else:
        logging.info("Nessun bet risolto ancora.")

    logging.info(f"Inviate {len(alerts)} notifiche. Stato salvato.")
