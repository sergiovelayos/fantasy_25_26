# etl/insert_mercado.py

import os
import json
import psycopg2
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

# --- Cargar variables desde .env ---
load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")

JSON_FILE = "data/futmondo_market.json"


def connect_db():
    """Crea conexión con Postgres usando las variables del .env"""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )


def load_json():
    """Carga el JSON acumulado de jugadores y lo convierte en DataFrame."""
    with open(JSON_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_players = []
    for run in data:  # cada ejecución guardada en el JSON
        for player in run.get("jugadores", []):
            # Extraer average principal si es un dict
            avg_value = (
                player.get("average", {}).get("average")
                if isinstance(player.get("average"), dict)
                else player.get("average")
            )

            player_data = {
                "id": player.get("id"),
                "name": player.get("name"),
                "role": player.get("role"),
                "points": player.get("points"),
                "value": player.get("value"),
                "team": player.get("team"),
                "creation_date": player.get("creationDate"),
                "expiration_date": player.get("expirationDate"),
                "price": player.get("price"),
                "computer": player.get("computer"),
                "change": player.get("change"),
                "average": avg_value,
                "number_of_bids": player.get("numberOfBids"),
                "user_team": player.get("userTeam") if not player.get("computer", True) else None,
            }
            all_players.append(player_data)

    return pd.DataFrame(all_players)


def insert_players(df):
    """Inserta jugadores en Postgres permitiendo múltiples apariciones en el mercado."""
    conn = connect_db()
    cur = conn.cursor()

    insert_sql = """
    INSERT INTO public.futmondo_market_24_25 
    (player_id, name, role, points, value, team, creation_date, expiration_date,
     price, computer, change, average, number_of_bids, user_team, inserted_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
    ON CONFLICT ON CONSTRAINT unique_market_entry DO NOTHING;
    """

    for _, row in df.iterrows():
        cur.execute(insert_sql, (
            row.get("id"),
            row.get("name"),
            row.get("role"),
            row.get("points"),
            row.get("value"),
            row.get("team"),
            pd.to_datetime(row.get("creation_date"), errors="coerce"),
            pd.to_datetime(row.get("expiration_date"), errors="coerce"),
            row.get("price"),
            row.get("computer"),
            row.get("change"),
            row.get("average"),
            row.get("number_of_bids"),
            row.get("user_team")
        ))

    conn.commit()
    cur.close()
    conn.close()
    print(f"{len(df)} registros procesados e insertados (sin duplicados exactos).")


def run_etl():
    print("=== ETL Futmondo Market ===")
    df = load_json()
    if df.empty:
        print("No hay datos en el JSON.")
        return
    insert_players(df)


if __name__ == "__main__":
    run_etl()
