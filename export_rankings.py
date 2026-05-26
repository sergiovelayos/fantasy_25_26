import argparse
import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv


LOGIN_API_URL = "https://api.futmondo.com/5/login/with_mail"
ACTIVE_CHAMPIONSHIPS_API_URL = "https://api.futmondo.com/2/user/activechampionships"
ROUNDS_API_URL = "https://api.futmondo.com/1/userteam/rounds"
ROUND_RANKING_API_URL = "https://api.futmondo.com/1/ranking/round"
GENERAL_RANKING_API_URL = "https://api.futmondo.com/1/ranking/general"
DEFAULT_OUTPUT_DIR = Path("data/exports")

REQUEST_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "es-ES,es;q=0.5",
    "Content-Type": "application/json; charset=utf-8",
    "Origin": "https://app.futmondo.com",
    "Referer": "https://app.futmondo.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Sec-GPC": "1",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": '"Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Falta la variable de entorno {name}.")
    return value


def login(session):
    payload = {
        "header": {"token": None, "userid": ""},
        "query": {
            "mail": require_env("FUTMONDO_USER"),
            "pwd": require_env("FUTMONDO_PASS"),
        },
        "answer": {},
    }
    response = session.post(LOGIN_API_URL, headers=REQUEST_HEADERS, json=payload, timeout=30)
    response.raise_for_status()
    mobile = response.json().get("answer", {}).get("mobile", {})
    token = mobile.get("token")
    userid = mobile.get("userid")
    if not token or not userid:
        raise RuntimeError("Login correcto, pero la respuesta no contiene token/userid.")
    return token, userid


def post_api(session, url, token, userid, query):
    payload = {
        "header": {"token": token, "userid": userid},
        "query": query,
        "answer": {},
    }
    response = session.post(url, headers=REQUEST_HEADERS, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def get_season_from_active_championships(session, token, userid, championship_id):
    data = post_api(session, ACTIVE_CHAMPIONSHIPS_API_URL, token, userid, {})
    answer = data.get("answer", {})
    championships = answer.get("championships", [])
    leagues = answer.get("leagues", [])
    championship = next((item for item in championships if item.get("id") == championship_id), None)
    if not championship:
        return None
    league_id = championship.get("league")
    league = next((item for item in leagues if item.get("_id") == league_id), None)
    if not league:
        return None
    season = league.get("season")
    return season.replace("-", "_") if season else None


def fetch_rankings(delay):
    load_dotenv(".env")
    championship_id = require_env("FUTMONDO_CHAMPIONSHIPID")
    userteam_id = require_env("FUTMONDO_USERTEAMID")

    with requests.Session() as session:
        token, userid = login(session)
        season = get_season_from_active_championships(session, token, userid, championship_id)
        base_query = {"championshipId": championship_id, "userteamId": userteam_id}

        rounds_data = post_api(session, ROUNDS_API_URL, token, userid, base_query)
        rounds = sorted(rounds_data.get("answer", []), key=lambda item: item.get("number", 0))
        print(f"Jornadas encontradas: {len(rounds)}")

        general_data = post_api(session, GENERAL_RANKING_API_URL, token, userid, base_query)
        general = general_data.get("answer", {})
        print(f"Clasificacion general: {len(general.get('ranking', []))} equipos")

        round_rankings = []
        for item in rounds:
            round_id = item.get("id")
            round_number = item.get("number")
            query = base_query | {"roundNumber": round_id}
            ranking_data = post_api(session, ROUND_RANKING_API_URL, token, userid, query)
            answer = ranking_data.get("answer", {})
            if not isinstance(answer, dict):
                raise RuntimeError(f"Respuesta inesperada para jornada {round_number}: {answer!r}")

            ranking = answer.get("ranking", [])
            print(f"Jornada {round_number}: {len(ranking)} equipos")
            round_rankings.append(
                {
                    "round": item,
                    "ranking": ranking,
                    "metadata": answer.get("metadata", {}),
                    "type": answer.get("type"),
                }
            )
            if delay:
                time.sleep(delay)

    return {
        "season": season,
        "championshipId": championship_id,
        "userteamId": userteam_id,
        "exportedAt": datetime.now().isoformat(timespec="seconds"),
        "general": {
            "ranking": general.get("ranking", []),
            "metadata": general.get("metadata", {}),
            "type": general.get("type"),
        },
        "rounds": round_rankings,
    }


def flatten_round_rankings(data):
    rows = []
    for round_item in data["rounds"]:
        round_info = round_item["round"]
        metadata = round_item.get("metadata", {})
        for team in round_item.get("ranking", []):
            rows.append(
                {
                    "season": data.get("season"),
                    "round_number": round_info.get("number"),
                    "round_id": round_info.get("id"),
                    "round_status": round_info.get("status"),
                    "round_user_points": round_info.get("points"),
                    "metadata_status": metadata.get("status"),
                    "userteam_id": team.get("id"),
                    "userteam_name": team.get("name"),
                    "points": team.get("points"),
                    "position": team.get("position"),
                    "trend": team.get("trend"),
                    "cwl": team.get("cwl"),
                }
            )
    return rows


def flatten_general_ranking(data):
    rows = []
    metadata = data.get("general", {}).get("metadata", {})
    for team in data.get("general", {}).get("ranking", []):
        rows.append(
            {
                "season": data.get("season"),
                "metadata_status": metadata.get("status"),
                "userteam_id": team.get("id"),
                "userteam_name": team.get("name"),
                "points": team.get("points"),
                "position": team.get("position"),
                "trend": team.get("trend"),
                "cwl": team.get("cwl"),
            }
        )
    return rows


def write_csv(path, rows):
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_exports(data, output_dir, season=None):
    season_dir = season or data.get("season") or "unknown_season"
    output_dir = output_dir / season_dir / "clasificacion"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    json_path = output_dir / f"clasificacion_all_{stamp}.json"
    rounds_csv_path = output_dir / f"clasificacion_jornadas_{stamp}.csv"
    general_csv_path = output_dir / f"clasificacion_general_{stamp}.csv"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    round_rows = flatten_round_rankings(data)
    general_rows = flatten_general_ranking(data)
    write_csv(rounds_csv_path, round_rows)
    write_csv(general_csv_path, general_rows)

    return json_path, rounds_csv_path, general_csv_path, len(round_rows), len(general_rows)


def main():
    parser = argparse.ArgumentParser(description="Exporta la clasificacion de Futmondo por jornadas.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, type=Path)
    parser.add_argument("--season", help="Temporada de salida, por ejemplo 2025_2026. Si se omite, se infiere.")
    parser.add_argument("--delay", default=0.2, type=float, help="Pausa entre jornadas, en segundos.")
    args = parser.parse_args()

    data = fetch_rankings(delay=args.delay)
    json_path, rounds_csv_path, general_csv_path, round_rows, general_rows = write_exports(
        data,
        args.output_dir,
        season=args.season,
    )
    print(f"Exportadas {len(data['rounds'])} jornadas")
    print(f"Filas de clasificacion por jornada: {round_rows}")
    print(f"Filas de clasificacion general: {general_rows}")
    print(f"JSON: {json_path}")
    print(f"CSV jornadas: {rounds_csv_path}")
    print(f"CSV general: {general_csv_path}")


if __name__ == "__main__":
    main()
