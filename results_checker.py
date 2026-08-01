#!/usr/bin/env python3
"""
Recupera i risultati delle partite tramite API‑Football, aggiorna bets.json e bets_log.csv.
Invia report Telegram.
"""

import os, json, csv, logging, requests
from datetime import date

API_FOOTBALL_KEY = os.environ["API_FOOTBALL_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

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

def get_results():
    today = date.today().strftime("%Y-%m-%d")
    headers = {"x-apisports-key": API_FOOTBALL_KEY, "x-apisports-host": "v3.football.api-sports.io"}
    url = "https://v3.football.api-sports.io/fixtures"
    params = {"date": today}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            logging.error(f"API fixtures HTTP {resp.status_code}")
            return {}
        data = resp.json()
        results = {}
        for match in data.get("response", []):
            fid = match["fixture"]["id"]
            goals = match["goals"]
            if goals["home"] is not None and goals["away"] is not None:
                home_score = goals["home"]
                away_score = goals["away"]
                if home_score > away_score:
                    winner = match["teams"]["home"]["name"]
                elif away_score > home_score:
                    winner = match["teams"]["away"]["name"]
                else:
                    winner = "draw"
                results[fid] = {
                    "winner": winner,
                    "score": f"{home_score}-{away_score}"
                }
        return results
    except Exception as e:
        logging.error(f"Error fetching results: {e}")
        return {}

def update_csv_with_results(bets, results):
    filename = "bets_log.csv"
    if not os.path.isfile(filename):
        logging.info("bets_log.csv non trovato, nessun aggiornamento CSV.")
        return
    rows = []
    with open(filename, "r", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return
    header = rows[0]
    # Trova gli indici delle colonne (flessibile se cambiassero posizione)
    try:
        idx_id = header.index("fixture_id")
        idx_score = header.index("Risultato reale")
        idx_esito = header.index("Esito")
    except ValueError:
        logging.error("Colonne mancanti nel CSV, impossibile aggiornare.")
        return

    for b in bets:
        fid = str(b.get("fixture_id", ""))
        if fid not in results:
            continue
        result_info = results[fid]
        for i, row in enumerate(rows[1:], start=1):
            if row[idx_id] == fid and row[idx_esito] == "":
                rows[i][idx_score] = result_info["score"]
                rows[i][idx_esito] = b["result"]   # "won" o "lost"
                break

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    logging.info(f"CSV aggiornato con {len(bets)} risultati.")

def main():
    bets = load_json("bets.json", [])
    pending = [b for b in bets if b["result"] == "pending"]
    if not pending:
        logging.info("Nessuna scommessa pending.")
        return

    results = get_results()
    updated = 0
    for b in pending:
        fid = b["fixture_id"]
        if fid in results:
            actual = results[fid]["winner"]
            if actual == "draw":
                b["result"] = "lost"
            else:
                b["result"] = "won" if b["predicted_winner"] == actual else "lost"
            updated += 1

    if updated > 0:
        save_json("bets.json", bets)
        update_csv_with_results(pending, results)   # passa i bet aggiornati e i risultati
        won = sum(1 for b in bets if b["result"] == "won")
        lost = sum(1 for b in bets if b["result"] == "lost")
        total = won + lost
        acc = (won / total * 100) if total > 0 else 0
        report = f"📊 *Report risultati*\nPronostici verificati oggi: {updated}\n✅ Vinti: {won}\n❌ Persi: {lost}\n📈 Accuratezza: {acc:.1f}%"
        send_telegram(report)
        logging.info(report)
    else:
        logging.info("Nessun risultato disponibile per i bet pending.")

if __name__ == "__main__":
    main()
