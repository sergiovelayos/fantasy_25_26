# -*- coding: utf-8 -*-

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup  # por si lo necesitas en el futuro
from dotenv import load_dotenv

# --- CONFIGURACIÓN ---
load_dotenv()

USERNAME = os.getenv("FUTMONDO_USER")
PASSWORD = os.getenv("FUTMONDO_PASS")
CHAMPIONSHIPID = os.getenv("FUTMONDO_CHAMPIONSHIPID")
USERTEAMID = os.getenv("FUTMONDO_USERTEAMID")
API_URL = "https://api.futmondo.com/1/market/players"
LOGIN_API_URL = "https://api.futmondo.com/5/login/with_mail"
FILE_PATH = "data/futmondo_market.json"
DEFAULT_EXPORTS_DIR = Path("data/exports")


def resolve_output_path(season=None, output=None, legacy=False):
    if output:
        return Path(output)
    if legacy or not season:
        return Path(FILE_PATH)
    return DEFAULT_EXPORTS_DIR / season / "market_snapshots.json"


def futmondo_market_scraper_api(season=None, output=None, legacy=False):
    if not all([USERNAME, PASSWORD]):
        print("Error: faltan variables de entorno FUTMONDO_USER y FUTMONDO_PASS.")
        return

    if not all([CHAMPIONSHIPID, USERTEAMID]):
        print("Error: faltan variables de entorno FUTMONDO_CHAMPIONSHIPID y FUTMONDO_USERTEAMID.")
        return

    output_path = resolve_output_path(season=season, output=output, legacy=legacy)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with requests.Session() as session:
        try:
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print("Fecha y hora de ejecución:", ahora)

            # --- LOGIN ---
            login_payload = {
                "header": {"token": None, "userid": ""},
                "query": {"mail": USERNAME, "pwd": PASSWORD},
                "answer": {}
            }
            login_headers = {"Content-Type": "application/json; charset=utf-8"}

            login_response = session.post(LOGIN_API_URL, headers=login_headers, data=json.dumps(login_payload))
            login_response.raise_for_status()
            login_data = login_response.json()

            session_token = login_data.get("answer", {}).get("mobile", {}).get("token")
            session_userid = login_data.get("answer", {}).get("mobile", {}).get("userid")

            if not session_token or not session_userid:
                print("Error: No se pudo obtener el token o userid.")
                return

            # --- OBTENER DATOS DE MERCADO ---
            headers = {
                "Accept": "*/*",
                "Content-Type": "application/json; charset=utf-8",
                "Origin": "https://app.futmondo.com",
                "Referer": "https://app.futmondo.com/",
                "User-Agent": "Mozilla/5.0"
            }
            payload = {
                "header": {"token": session_token, "userid": session_userid},
                "query": {
                    "championshipId": CHAMPIONSHIPID,
                    "userteamId": USERTEAMID,
                    "type": "market"
                },
                "answer": {}
            }

            api_response = session.post(API_URL, headers=headers, data=json.dumps(payload))
            api_response.raise_for_status()
            market_data = api_response.json()
            players_list = market_data.get("answer", [])

            # --- GUARDAR EN JSON ---
            # Cargar datos previos
            if output_path.exists():
                with output_path.open("r", encoding="utf-8") as f:
                    try:
                        existing_data = json.load(f)
                    except json.JSONDecodeError:
                        existing_data = []
            else:
                existing_data = []

            # Nos aseguramos de que sea una lista
            if not isinstance(existing_data, list):
                existing_data = [existing_data]

            # Añadir nueva ejecución con fecha
            new_entry = {
                "fecha": ahora,
                "season": season,
                "championshipId": CHAMPIONSHIPID,
                "userteamId": USERTEAMID,
                "jugadores": players_list
            }
            existing_data.append(new_entry)

            # Guardar todo de nuevo
            with output_path.open("w", encoding="utf-8") as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)

            print(f"Jugadores de mercado descargados: {len(players_list)}")
            print(f"Datos añadidos correctamente a '{output_path}'.")

        except Exception as e:
            print(f"Ocurrió un error: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extrae el mercado actual de Futmondo.")
    parser.add_argument("--season", help="Temporada de salida, por ejemplo 2026_2027.")
    parser.add_argument("--output", help="Ruta JSON de salida. Si se omite con --season usa data/exports/<temporada>/market_snapshots.json.")
    parser.add_argument("--legacy", action="store_true", help=f"Guardar en la ruta historica {FILE_PATH}.")
    args = parser.parse_args()
    futmondo_market_scraper_api(season=args.season, output=args.output, legacy=args.legacy)
