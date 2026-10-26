#!/usr/bin/env python3
"""
Recupera i risultati tramite API-Football, li inserisce nel CSV e invia il report.
"""

import os, json, csv, logging, requests, unicodedata
from datetime import date, timedelta

API_FOOTBALL_KEY = os.environ["API_FOOTBALL_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
        if resp.status_code != 200:
            logging.error(f"TELEGRAM ERROR {resp.status_code}")
    except Exception as e:
        logging.error(f"TELEGRAM EXCEPTION: {e}")

def normalize_name(name):
    """Rimuove accenti e caratteri speciali per facilitare il matching."""
    if not name: return ""
    name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8')
    return name.lower().replace(" ", "").replace("-", "").replace(".", "").strip()

def get_results_last_days():
    headers = {"x-apisports-key": API_FOOTBALL_KEY, "x-apisports-host": "v3.football.api-sports.io"}
    url = "https://v3.football.api-sports.io/fixtures"
    results = {}
    
    # Cerchiamo i risultati da oggi fino a 3 giorni fa
    for delta in range(0, 4):
        target_date = (date.today() - timedelta(days=delta)).strftime("%Y-%m-%d")
        try:
            resp = requests.get(url, headers=headers, params={"date": target_date}, timeout=15)
            if resp.status_code != 200:
                continue
            data = resp.json()
            for match in data.get("response", []):
                goals = match["goals"]
                if goals["home"] is None or goals["away"] is None:
                    continue
                
                home_team = normalize_name(match["teams"]["home"]["name"])
                away_team = normalize_name(match["teams"]["away"]["name"])
                hs = goals["home"]
                aws = goals["away"]
                
                if hs > aws: winner = "HOME"
                elif aws > hs: winner = "AWAY"
                else: winner = "DRAW"
                
                # Chiave univoca: "squadracasa_squadraospite"
                key = f"{home_team}_{away_team}"
                results[key] = {"winner": winner, "score": f"{hs}-{aws}"}
        except Exception as e:
            logging.error(f"Errore fetch {target_date}: {e}")
            
    return results

def update_csv(results):
    filename = "bets_log.csv"
    if not os.path.isfile(filename):
        logging.info("File CSV non trovato.")
        return 0, 0, 0 # <-- CORRETTO QUI (restituisce una tupla invece di un singolo numero)

    with open(filename, "r", newline="", encoding='utf-8') as f:
        rows = list(csv.reader(f))
        
    if not rows: return 0, 0, 0

    header = rows[0]
    try:
        idx_home = header.index("Squadra casa")
        idx_away = header.index("Squadra ospite")
        idx_pred = header.index("Pronostico")
        idx_score = header.index("Risultato reale")
        idx_esito = header.index("Esito")
    except ValueError:
        logging.error("Colonne non trovate nel CSV.")
        return 0, 0, 0

    updated_count = 0
    won = 0
    lost = 0

    # Scorriamo il CSV (saltando l'intestazione)
    for i in range(1, len(rows)):
        row = rows[i]
        
        # Se l'esito è già compilato, saltiamo (non sovrascriviamo)
        if row[idx_esito] != "":
            if row[idx_esito] == "won": won += 1
            elif row[idx_esito] == "lost": lost += 1
            continue

        # Normalizziamo i nomi presi dal CSV per cercarli nei risultati
        home_norm = normalize_name(row[idx_home])
        away_norm = normalize_name(row[idx_away])
        key = f"{home_norm}_{away_norm}"

        if key in results:
            info = results[key]
            row[idx_score] = info["score"]
            
            if info["winner"] == "DRAW":
                row[idx_esito] = "lost"
                lost += 1
            else:
                pred_norm = normalize_name(row[idx_pred])
                actual_winner_name = home_norm if info["winner"] == "HOME" else away_norm
                
                if pred_norm == actual_winner_name:
                    row[idx_esito] = "won"
                    won += 1
                else:
                    row[idx_esito] = "lost"
                    lost += 1
            updated_count += 1

    if updated_count > 0:
        with open(filename, "w", newline="", encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(rows)

    return won, lost, updated_count

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.info("Avvio controllo risultati...")
    
    results = get_results_last_days()
    logging.info(f"Risultati scaricati da API-Football: {len(results)}")

    won, lost, updated = update_csv(results)
    
    if updated > 0:
        total = won + lost
        acc = (won / total * 100) if total > 0 else 0
        report = f"📊 Report Risultati Aggiornati\nNuovi aggiornamenti: {updated}\n✅ Vinti: {won}\n❌ Persi: {lost}\n📈 Accuratezza CSV: {acc:.1f}%"
        send_telegram(report)
        logging.info(report)
    else:
        logging.info("Nessun nuovo risultato trovato o CSV già aggiornato.")

if __name__ == "__main__":
    main()
