# etl/insert_mercado.py

import os
import json
import time
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


def connect_db(retries=5, delay=30):
    """Crea conexión con Postgres. Reintenta hasta `retries` veces con `delay` segundos entre intentos."""
    for attempt in range(1, retries + 1):
        try:
            return psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASS
            )
        except psycopg2.OperationalError as e:
            print(f"[Intento {attempt}/{retries}] Error conectando a DB: {e}")
            if attempt < retries:
                print(f"Reintentando en {delay} segundos...")
                time.sleep(delay)
            else:
                raise


def get_last_checkpoint(conn):
    """Devuelve la fecha de la última ejecución cargada en la DB, o None si está vacía."""
    cur = conn.cursor()
    cur.execute("SELECT MAX(scrape_fecha) FROM public.futmondo_market_25_26;")
    result = cur.fetchone()[0]
    cur.close()
    return result


def load_json_delta(since=None):
    """Carga solo las ejecuciones del JSON más nuevas que 'since' (datetime o None).
    Si since=None, carga todo (carga inicial).
    """
    with open(JSON_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_players = []
    runs_procesadas = 0

    for run in data:
        fecha_str = run.get("fecha")
        try:
            fecha_run = datetime.strptime(fecha_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue

        if since is not None and fecha_run <= since:
            continue  # ya estaba cargada

        runs_procesadas += 1
        for player in run.get("jugadores", []):
            avg_value = (
                player.get("average", {}).get("average")
                if isinstance(player.get("average"), dict)
                else player.get("average")
            )
            player_data = {
                "scrape_fecha": fecha_run,
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

    print(f"Ejecuciones nuevas encontradas en JSON: {runs_procesadas}")
    return pd.DataFrame(all_players)


def insert_players(df, conn):
    """Inserta jugadores en Postgres. La columna scrape_fecha permite distinguir ejecuciones."""
    cur = conn.cursor()

    insert_sql = """
    INSERT INTO public.futmondo_market_25_26
    (player_id, name, role, points, value, team, creation_date, expiration_date,
     price, computer, change, average, number_of_bids, user_team, scrape_fecha, inserted_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
    ON CONFLICT ON CONSTRAINT unique_market_entry DO NOTHING;
    """

    inserted = 0
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
            row.get("user_team"),
            row.get("scrape_fecha"),
        ))
        inserted += cur.rowcount

    conn.commit()
    cur.close()
    print(f"Filas nuevas insertadas en DB: {inserted} (de {len(df)} registros procesados).")


def run_etl():
    print(f"=== ETL Futmondo Market — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    conn = connect_db()

    checkpoint = get_last_checkpoint(conn)
    if checkpoint:
        print(f"Checkpoint detectado: cargando datos posteriores a {checkpoint}")
    else:
        print("Sin checkpoint: carga inicial completa.")

    df = load_json_delta(since=checkpoint)

    if df.empty:
        print("No hay ejecuciones nuevas para cargar.")
        conn.close()
        return

    insert_players(df, conn)
    conn.close()


if __name__ == "__main__":
    run_etl()
