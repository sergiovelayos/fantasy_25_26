# -*- coding: utf-8 -*-

import os
import json
import requests
from bs4 import BeautifulSoup  # por si lo necesitas en el futuro
from dotenv import load_dotenv
from datetime import datetime

# --- CONFIGURACIÓN ---
load_dotenv()

USERNAME = os.getenv("FUTMONDO_USER")
PASSWORD = os.getenv("FUTMONDO_PASS")
API_URL = "https://api.futmondo.com/1/market/players"
LOGIN_API_URL = "https://api.futmondo.com/5/login/with_mail"
FILE_PATH = "futmondo_market.json"


def futmondo_market_scraper_api():
    if not all([USERNAME, PASSWORD]):
        print("Error: faltan variables de entorno FUTMONDO_USER y FUTMONDO_PASS.")
        return

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
                    "championshipId": "64f45b87f0ee1105e1ea0e9a",
                    "userteamId": "64f49cfa016d860e1faabe35",
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
            if os.path.exists(FILE_PATH):
                with open(FILE_PATH, "r", encoding="utf-8") as f:
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
                "jugadores": players_list
            }
            existing_data.append(new_entry)

            # Guardar todo de nuevo
            with open(FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, ensure_ascii=False, indent=2)

            print(f"Datos añadidos correctamente a '{FILE_PATH}'.")

        except Exception as e:
            print(f"Ocurrió un error: {e}")


if __name__ == "__main__":
    futmondo_market_scraper_api()
