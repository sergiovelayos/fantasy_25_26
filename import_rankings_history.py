import argparse
import csv
import json
from datetime import datetime
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path("data/exports")


def load_rounds(rounds_path):
    data = json.load(rounds_path.open(encoding="utf-8"))
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        rounds = data[0].get("answer", [])
    elif isinstance(data, dict):
        rounds = data.get("answer", [])
    else:
        rounds = data
    if not isinstance(rounds, list):
        raise RuntimeError("El fichero de jornadas no contiene una lista valida.")
    return sorted(rounds, key=lambda item: item.get("number", 0))


def load_rankings(rankings_path):
    rankings = json.load(rankings_path.open(encoding="utf-8"))
    if not isinstance(rankings, list):
        raise RuntimeError("El fichero de clasificaciones no contiene una lista valida.")
    return rankings


def build_export(rankings_path, rounds_path, season):
    rounds = load_rounds(rounds_path)
    round_by_id = {item.get("id"): item for item in rounds}
    rankings = load_rankings(rankings_path)

    exported_rounds = []
    for item in rankings:
        query = item.get("query", {})
        round_id = query.get("roundNumber")
        round_info = round_by_id.get(round_id)
        if not round_info:
            raise RuntimeError(f"No se encontro la jornada para round id {round_id!r}.")
        answer = item.get("answer", {})
        exported_rounds.append(
            {
                "round": round_info,
                "ranking": answer.get("ranking", []),
                "metadata": answer.get("metadata", {}),
                "type": answer.get("type"),
            }
        )

    exported_rounds.sort(key=lambda item: item["round"].get("number", 0))
    return {
        "season": season,
        "championshipId": rankings[0].get("query", {}).get("championshipId") if rankings else None,
        "userteamId": rankings[0].get("query", {}).get("userteamId") if rankings else None,
        "importedAt": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "rankings": str(rankings_path),
            "rounds": str(rounds_path),
        },
        "rounds": exported_rounds,
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


def build_general_from_rounds(round_rows, season):
    totals = {}
    for row in round_rows:
        team_id = row["userteam_id"]
        if team_id not in totals:
            totals[team_id] = {
                "season": season,
                "metadata_status": "",
                "userteam_id": team_id,
                "userteam_name": row["userteam_name"],
                "points": 0.0,
                "position": 0,
                "trend": "",
                "cwl": row["cwl"],
            }
        totals[team_id]["points"] += float(row["points"])

    rows = sorted(totals.values(), key=lambda item: item["points"], reverse=True)
    for index, row in enumerate(rows, start=1):
        row["position"] = index
        row["points"] = f"{row['points']:.1f}"
    return rows


def write_csv(path, rows):
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_exports(data, output_dir, season):
    output_dir = output_dir / season / "clasificacion"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"clasificacion_all_{stamp}.json"
    rounds_csv_path = output_dir / f"clasificacion_jornadas_{stamp}.csv"
    general_csv_path = output_dir / f"clasificacion_general_{stamp}.csv"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    round_rows = flatten_round_rankings(data)
    general_rows = build_general_from_rounds(round_rows, season)
    write_csv(rounds_csv_path, round_rows)
    write_csv(general_csv_path, general_rows)

    return json_path, rounds_csv_path, general_csv_path, len(round_rows), len(general_rows)


def main():
    parser = argparse.ArgumentParser(description="Importa clasificaciones historicas de Futmondo.")
    parser.add_argument("rankings", type=Path, help="JSON con las clasificaciones de todas las jornadas.")
    parser.add_argument("rounds", type=Path, help="JSON con los ids de jornadas.")
    parser.add_argument("--season", required=True, help="Temporada de salida, por ejemplo 2024_2025.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, type=Path)
    args = parser.parse_args()

    data = build_export(args.rankings, args.rounds, args.season)
    json_path, rounds_csv_path, general_csv_path, round_rows, general_rows = write_exports(
        data,
        args.output_dir,
        args.season,
    )
    print(f"Importadas {len(data['rounds'])} jornadas")
    print(f"Filas de clasificacion por jornada: {round_rows}")
    print(f"Filas de clasificacion general derivada: {general_rows}")
    print(f"JSON: {json_path}")
    print(f"CSV jornadas: {rounds_csv_path}")
    print(f"CSV general: {general_csv_path}")


if __name__ == "__main__":
    main()
