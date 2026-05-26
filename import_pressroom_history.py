import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from export_pressroom import flatten_signing


DEFAULT_OUTPUT_DIR = Path("data/exports")


def infer_season_from_news(news):
    years = []
    for item in news:
        created = item.get("created")
        if not created:
            continue
        try:
            years.append(datetime.fromisoformat(created.replace("Z", "+00:00")).year)
        except ValueError:
            continue

    if len(set(years)) >= 2:
        return f"{min(years)}_{max(years)}"
    if years:
        year = years[0]
        return f"{year - 1}_{year}" if datetime.now().month <= 7 else f"{year}_{year + 1}"
    return "unknown_season"


def load_news(source_path):
    data = json.load(source_path.open(encoding="utf-8"))
    news = data.get("answer", {}).get("news", [])
    if not isinstance(news, list):
        raise RuntimeError(f"El fichero no parece un pressroom valido: {source_path}")
    return data, news


def normalize_pressrooms(source_paths):
    all_news = []
    first_data = None
    for source_path in source_paths:
        data, news = load_news(source_path)
        if first_data is None:
            first_data = data
        all_news.extend(news)

    unique_news = []
    seen_ids = set()
    for item in all_news:
        item_id = item.get("_id")
        if item_id and item_id in seen_ids:
            continue
        if item_id:
            seen_ids.add(item_id)
        unique_news.append(item)
    return {
        "answer": {
            "news": unique_news,
            "isAdmin": first_data.get("answer", {}).get("isAdmin", False),
        },
        "query": first_data.get("query", {}),
        "header": {
            "token": "<redacted>",
            "userid": first_data.get("header", {}).get("userid"),
        },
        "importedAt": datetime.now().isoformat(timespec="seconds"),
        "sources": [str(path) for path in source_paths],
        "sourceRows": len(all_news),
        "duplicateRowsRemoved": len(all_news) - len(unique_news),
    }


def write_exports(data, output_dir, season):
    season_dir = output_dir / season
    season_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = season_dir / f"pressroom_all_{stamp}.json"
    csv_path = season_dir / f"fichajes_all_{stamp}.csv"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    rows = [flatten_signing(item) for item in data["answer"]["news"]]
    fieldnames = list(rows[0].keys()) if rows else list(flatten_signing({}).keys())
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return json_path, csv_path, len(rows)


def main():
    parser = argparse.ArgumentParser(description="Importa un pressroom historico ya descargado.")
    parser.add_argument("sources", nargs="+", type=Path, help="Rutas a JSON pressroom historicos.")
    parser.add_argument("--season", help="Temporada de salida, por ejemplo 2024_2025.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, type=Path)
    args = parser.parse_args()

    data = normalize_pressrooms(args.sources)
    season = args.season or infer_season_from_news(data["answer"]["news"])
    json_path, csv_path, count = write_exports(data, args.output_dir, season)
    print(f"Importados {count} movimientos historicos")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")


if __name__ == "__main__":
    main()
