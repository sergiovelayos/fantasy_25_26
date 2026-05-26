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
PRESSROOM_API_URL = "https://api.futmondo.com/1/locker/pressroom"
DEFAULT_OUTPUT_DIR = Path("data/exports")
PAGE_SIZE = 150

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


def fetch_pressroom_page(session, token, userid, championship_id, cursor):
    payload = {
        "header": {"token": token, "userid": userid},
        "query": {"championshipId": championship_id, "from": cursor},
        "answer": {},
    }
    response = session.post(PRESSROOM_API_URL, headers=REQUEST_HEADERS, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_all_pressroom(delay):
    load_dotenv(".env")
    championship_id = require_env("FUTMONDO_CHAMPIONSHIPID")

    with requests.Session() as session:
        token, userid = login(session)
        cursor = ""
        all_news = []
        seen_ids = set()
        page = 1

        while True:
            data = fetch_pressroom_page(session, token, userid, championship_id, cursor)
            news = data.get("answer", {}).get("news", [])
            print(f"Pagina {page}: {len(news)} fichajes")

            new_items = []
            for item in news:
                item_id = item.get("_id")
                if item_id and item_id in seen_ids:
                    continue
                if item_id:
                    seen_ids.add(item_id)
                new_items.append(item)

            all_news.extend(new_items)

            if len(news) < PAGE_SIZE or not news or not new_items:
                break

            cursor = news[-1].get("_id")
            if not cursor:
                break

            page += 1
            if delay:
                time.sleep(delay)

    return {
        "answer": {
            "news": all_news,
            "isAdmin": False,
        },
        "query": {
            "championshipId": championship_id,
            "from": "",
            "pagination": "all",
        },
        "header": {
            "token": "<redacted>",
            "userid": userid,
        },
        "exportedAt": datetime.now().isoformat(timespec="seconds"),
    }


def flatten_signing(item):
    player = item.get("_player") or {}
    player_team = item.get("_playerTeam") or {}
    buyer = item.get("_buyer") or {}
    seller = item.get("_seller") or {}
    bids = item.get("bids") or []

    return {
        "id": item.get("_id"),
        "created": item.get("created"),
        "player_id": player.get("_id"),
        "player_slug": player.get("slug"),
        "player_name": player.get("name"),
        "player_team_id": player_team.get("_id"),
        "player_team_name": player_team.get("name"),
        "player_team_slug": player_team.get("slug"),
        "buyer_id": buyer.get("_id"),
        "buyer_name": buyer.get("name"),
        "seller_id": seller.get("_id"),
        "seller_name": seller.get("name"),
        "price": item.get("price"),
        "bids_count": len(bids),
        "bids_json": json.dumps(bids, ensure_ascii=False),
    }


def write_exports(data, output_dir, season=None):
    output_dir = output_dir / (season or infer_season(data))
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"pressroom_all_{stamp}.json"
    csv_path = output_dir / f"fichajes_all_{stamp}.csv"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    rows = [flatten_signing(item) for item in data["answer"]["news"]]
    fieldnames = list(rows[0].keys()) if rows else list(flatten_signing({}).keys())
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return json_path, csv_path, len(rows)


def infer_season(data):
    years = []
    for item in data["answer"]["news"]:
        created = item.get("created")
        if not created:
            continue
        try:
            years.append(datetime.fromisoformat(created.replace("Z", "+00:00")).year)
        except ValueError:
            continue

    if len(set(years)) >= 2:
        start, end = min(years), max(years)
        return f"{start}_{end}"
    if years:
        year = years[0]
        return f"{year - 1}_{year}" if datetime.now().month <= 7 else f"{year}_{year + 1}"
    return "unknown_season"


def main():
    parser = argparse.ArgumentParser(description="Exporta todos los fichajes del pressroom de Futmondo.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, type=Path)
    parser.add_argument("--season", help="Temporada de salida, por ejemplo 2025_2026. Si se omite, se infiere.")
    parser.add_argument("--delay", default=0.2, type=float, help="Pausa entre paginas, en segundos.")
    args = parser.parse_args()

    data = fetch_all_pressroom(delay=args.delay)
    json_path, csv_path, count = write_exports(data, args.output_dir, season=args.season)
    print(f"Exportados {count} fichajes")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")


if __name__ == "__main__":
    main()
