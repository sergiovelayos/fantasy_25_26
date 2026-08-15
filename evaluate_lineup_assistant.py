import argparse
import csv
import html
import json
import os
import re
import time
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


LOGIN_API_URL = "https://api.futmondo.com/5/login/with_mail"
CHAMPIONSHIP_PLAYERS_API_URL = "https://api.futmondo.com/5/league/championshipplayers"
FUTBOLFANTASY_LINEUPS_URL = "https://www.futbolfantasy.com/laliga/posibles-alineaciones"
DEFAULT_SEASON = "2026_2027"
DEFAULT_EXPORTS_DIR = Path("data/exports")
DEFAULT_DOCS_DIR = Path("docs")

REQUEST_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Content-Type": "application/json; charset=utf-8",
    "Origin": "https://app.futmondo.com",
    "Referer": "https://app.futmondo.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/148 Safari/537.36",
}

FF_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/148 Safari/537.36",
}


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Falta la variable de entorno {name}.")
    return value


def normalize_name(value):
    value = value or ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower()
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def esc(value):
    return html.escape("" if value is None else str(value))


def login(session):
    payload = {
        "header": {"token": None, "userid": ""},
        "query": {"mail": require_env("FUTMONDO_USER"), "pwd": require_env("FUTMONDO_PASS")},
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


def fetch_championship_players():
    load_dotenv(".env")
    championship_id = require_env("FUTMONDO_CHAMPIONSHIPID")
    with requests.Session() as session:
        token, userid = login(session)
        payload = {
            "header": {"token": token, "userid": userid},
            "query": {"championshipId": championship_id},
            "answer": {},
        }
        response = session.post(CHAMPIONSHIP_PLAYERS_API_URL, headers=REQUEST_HEADERS, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
    return data


def lineup_url(round_number):
    if int(round_number) == 1:
        return FUTBOLFANTASY_LINEUPS_URL
    return f"{FUTBOLFANTASY_LINEUPS_URL}/{round_number}"


def fetch_html(url):
    response = requests.get(url, headers=FF_HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def parse_matches(round_number):
    url = lineup_url(round_number)
    soup = BeautifulSoup(fetch_html(url), "html.parser")
    matches_section = soup.select_one("section.mod.proxjornada .matches")
    if not matches_section:
        raise RuntimeError(f"No encuentro la seccion de partidos en {url}")

    matches = []
    current = {}
    for child in matches_section.children:
        if getattr(child, "name", None) == "meta" and child.get("itemprop") == "name":
            current = {"name": child.get("content", "").replace("⚽", "").strip()}
        elif getattr(child, "name", None) == "time" and child.get("itemprop") == "startDate":
            current["start"] = child.get("content")
        elif getattr(child, "name", None) == "div" and "partido-container" in (child.get("class") or []):
            link = child.select_one("a.partido[href]")
            if not link:
                continue
            teams = [img.get("alt") for img in link.select("img.escudo")]
            if len(teams) >= 2:
                current["home_team"] = teams[0]
                current["away_team"] = teams[1]
            elif current.get("name") and " - " in current["name"]:
                current["home_team"], current["away_team"] = current["name"].split(" - ", 1)
            current["url"] = link.get("href")
            matches.append(current)
            current = {}
    return matches


def player_probability(node):
    text = node.get_text(" ", strip=True)
    match = re.search(r"(\d{1,3})\s*%", text)
    return int(match.group(1)) if match else None


def extract_players_from(container, match, side, lineup_status):
    if not container:
        return []
    club = match["home_team"] if side == "local" else match["away_team"]
    opponent = match["away_team"] if side == "local" else match["home_team"]
    rows = []
    for node in container.select(".camiseta-wrapper"):
        name_el = node.select_one(".truncate-name")
        if not name_el:
            continue
        name = name_el.get_text(" ", strip=True)
        rows.append(
            {
                "ff_name": name,
                "ff_name_norm": normalize_name(name),
                "club": club,
                "opponent": opponent,
                "home_away": "casa" if side == "local" else "fuera",
                "match": f"{match['home_team']} - {match['away_team']}",
                "match_start": match.get("start"),
                "match_url": match.get("url"),
                "lineup_status": lineup_status,
                "probability": player_probability(node),
                "availability": "",
            }
        )
    return rows


def extract_absences(soup, match, side, availability):
    rows = []
    club = match["home_team"] if side == "local" else match["away_team"]
    opponent = match["away_team"] if side == "local" else match["home_team"]
    selector = f".alineacion_superwrapper.{side}.mod"
    for section in soup.select(selector):
        classes = set(section.get("class") or [])
        if availability == "lesionado" and "lesionados" not in classes:
            continue
        if availability == "sancionado" and "sancionados" not in classes:
            continue
        if availability == "sancionado" and "lesionados" in classes:
            continue
        for link in section.select("a.jugador"):
            name = link.get_text(" ", strip=True)
            if not name:
                continue
            rows.append(
                {
                    "ff_name": name,
                    "ff_name_norm": normalize_name(name),
                    "club": club,
                    "opponent": opponent,
                    "home_away": "casa" if side == "local" else "fuera",
                    "match": f"{match['home_team']} - {match['away_team']}",
                    "match_start": match.get("start"),
                    "match_url": match.get("url"),
                    "lineup_status": "baja",
                    "probability": 0,
                    "availability": availability,
                }
            )
    return rows


def parse_match_lineups(match):
    soup = BeautifulSoup(fetch_html(match["url"]), "html.parser")
    rows = []
    for side in ["local", "visitante"]:
        rows.extend(extract_players_from(soup.select_one(f".campo-wrapper.{side}.liga"), match, side, "once probable"))
        rows.extend(extract_players_from(soup.select_one(f".campo-wrapper.{side}.suplentes"), match, side, "alternativa"))
        rows.extend(extract_absences(soup, match, side, "lesionado"))
        rows.extend(extract_absences(soup, match, side, "sancionado"))
    return rows


def fetch_futbolfantasy_round(round_number, delay=0.2):
    matches = parse_matches(round_number)
    rows = []
    for index, match in enumerate(matches, start=1):
        print(f"FutbolFantasy partido {index}/{len(matches)}: {match.get('name') or match.get('match')}")
        rows.extend(parse_match_lineups(match))
        if delay:
            time.sleep(delay)
    return matches, rows


def best_ff_match(player, ff_by_name, ff_rows):
    name_norm = normalize_name(player.get("name"))
    if name_norm in ff_by_name:
        return ff_by_name[name_norm], 1.0
    candidates = []
    for row in ff_rows:
        score = SequenceMatcher(None, name_norm, row["ff_name_norm"]).ratio()
        if score >= 0.88:
            candidates.append((score, row))
    if not candidates:
        return None, 0
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1], candidates[0][0]


def recommendation(row):
    if row["availability"] == "sancionado":
        return "Evitar: sancionado"
    if row["availability"] == "lesionado":
        return "Evitar/revisar: parte medico"
    if row["lineup_status"] == "once probable":
        probability = row["probability"] or 0
        if probability >= 80 and row["home_away"] == "casa":
            return "Alinear fuerte"
        if probability >= 80:
            return "Alinear"
        if probability >= 60:
            return "Alinear con cautela"
        return "Duda de once"
    if row["lineup_status"] == "alternativa":
        probability = row["probability"] or 0
        if probability >= 50:
            return "Banquillo util / posible entrada"
        return "Solo emergencia"
    return "Sin dato FutbolFantasy"


def recommendation_score(row):
    if row["availability"] == "sancionado":
        return -100
    if row["availability"] == "lesionado":
        return -80
    score = 0
    probability = row["probability"] or 0
    if row["lineup_status"] == "once probable":
        score += 40 + probability
    elif row["lineup_status"] == "alternativa":
        score += 10 + probability
    else:
        score -= 10
    if row["home_away"] == "casa":
        score += 10
    return score


def build_assessment(championship_data, ff_rows):
    ff_by_name = {}
    for row in sorted(ff_rows, key=lambda item: item.get("probability") or 0, reverse=True):
        ff_by_name.setdefault(row["ff_name_norm"], row)

    owned = [player for player in championship_data.get("answer", {}).get("players", []) if player.get("userteamId")]
    assessed = []
    for player in owned:
        ff_row, match_score = best_ff_match(player, ff_by_name, ff_rows)
        base = {
            "fantasy_team": player.get("userteam"),
            "player_name": player.get("name"),
            "role": player.get("role"),
            "futmondo_status": player.get("status"),
            "value": player.get("value"),
            "points": player.get("points"),
            "club": "",
            "opponent": "",
            "home_away": "",
            "match": "",
            "match_start": "",
            "lineup_status": "sin dato",
            "probability": "",
            "availability": "",
            "match_name_score": round(match_score, 3),
            "match_url": "",
        }
        if ff_row:
            base.update(
                {
                    "club": ff_row["club"],
                    "opponent": ff_row["opponent"],
                    "home_away": ff_row["home_away"],
                    "match": ff_row["match"],
                    "match_start": ff_row["match_start"],
                    "lineup_status": ff_row["lineup_status"],
                    "probability": ff_row["probability"],
                    "availability": ff_row["availability"],
                    "match_url": ff_row["match_url"],
                }
            )
        base["recommendation"] = recommendation(base)
        base["score"] = recommendation_score(base)
        assessed.append(base)
    assessed.sort(key=lambda row: (row["fantasy_team"] or "", -row["score"], row["player_name"] or ""))
    return assessed


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def format_money(value):
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return ""


def render_html(rows, season, round_number, output_path):
    updated = datetime.now().strftime("%d/%m/%Y %H:%M")
    season_label = season.replace("_", "-")
    grouped = {}
    for row in rows:
        grouped.setdefault(row["fantasy_team"] or "Sin equipo", []).append(row)

    sections = []
    for team, team_rows in grouped.items():
        table_rows = []
        for row in team_rows:
            probability = f"{row['probability']}%" if row["probability"] != "" and row["probability"] is not None else "-"
            table_rows.append(
                f"""
                <tr>
                    <td class="px-3 py-2 font-semibold">{esc(row['player_name'])}</td>
                    <td class="px-3 py-2">{esc(row['role'])}</td>
                    <td class="px-3 py-2">{esc(row['club'])}</td>
                    <td class="px-3 py-2">{esc(row['match'])}</td>
                    <td class="px-3 py-2">{esc(row['home_away'])}</td>
                    <td class="px-3 py-2 whitespace-nowrap">{esc(row['match_start'])}</td>
                    <td class="px-3 py-2">{esc(row['lineup_status'])}</td>
                    <td class="px-3 py-2 text-right tabular-nums">{probability}</td>
                    <td class="px-3 py-2 font-semibold">{esc(row['recommendation'])}</td>
                </tr>
                """
            )
        sections.append(
            f"""
            <section class="mt-8 bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold">{esc(team)}</h2>
                <div class="mt-4 overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                            <tr>
                                <th class="px-3 py-2">Jugador</th><th class="px-3 py-2">Rol</th><th class="px-3 py-2">Club</th><th class="px-3 py-2">Partido</th><th class="px-3 py-2">Campo</th><th class="px-3 py-2">Hora</th><th class="px-3 py-2">Estado FF</th><th class="px-3 py-2 text-right">Prob.</th><th class="px-3 py-2">Recomendación</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">{''.join(table_rows)}</tbody>
                    </table>
                </div>
            </section>
            """
        )

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Asistente Alineación Futmondo {season} J{round_number}</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='0.9em' font-size='90'%3E%E2%9A%BD%3C/text%3E%3C/svg%3E">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');
        body {{ font-family: 'Inter', sans-serif; }}
        .tabular-nums {{ font-variant-numeric: tabular-nums; }}
    </style>
</head>
<body class="bg-slate-100 text-slate-900">
    <main class="max-w-7xl mx-auto px-4 py-6 sm:px-6 lg:px-8">
        <header class="border-b border-slate-300 pb-6">
            <div class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                <div>
                    <p class="text-sm font-semibold uppercase tracking-wide text-emerald-700">Futmondo PALETOS · {esc(season_label)} · Jornada {round_number}</p>
                    <h1 class="mt-2 text-3xl font-extrabold text-slate-950 sm:text-4xl">Asistente de alineación</h1>
                    <p class="mt-2 text-sm text-slate-600">Actualizado: {updated}. Fuente: Futmondo + FutbolFantasy.</p>
                    <p class="mt-3 max-w-3xl text-sm text-slate-700">Informe orientativo. No modifica alineaciones en Futmondo.</p>
                </div>
                <nav class="flex gap-2">
                    <a class="inline-flex items-center justify-center border border-slate-400 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-white" href="index_{season}.html">Mercado</a>
                    <a class="inline-flex items-center justify-center border border-slate-400 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-white" href="resumen_liga_{season}.html">Resumen</a>
                    <a class="inline-flex items-center justify-center bg-emerald-700 px-4 py-2 text-sm font-semibold text-white" href="asistente_alineacion_{season}_j{round_number}.html">Asistente</a>
                </nav>
            </div>
        </header>
        {''.join(sections)}
    </main>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Evalua plantillas Futmondo contra alineaciones probables de FutbolFantasy.")
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--round", default=1, type=int, dest="round_number")
    parser.add_argument("--exports-dir", default=DEFAULT_EXPORTS_DIR, type=Path)
    parser.add_argument("--docs-dir", default=DEFAULT_DOCS_DIR, type=Path)
    parser.add_argument("--delay", default=0.2, type=float)
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = args.exports_dir / args.season / "lineup_assistant"
    export_dir.mkdir(parents=True, exist_ok=True)

    championship_data = fetch_championship_players()
    championship_path = export_dir / f"championshipplayers_{stamp}.json"
    championship_path.write_text(json.dumps(championship_data, ensure_ascii=False, indent=2), encoding="utf-8")

    matches, ff_rows = fetch_futbolfantasy_round(args.round_number, delay=args.delay)
    ff_path = export_dir / f"futbolfantasy_jornada_{args.round_number}_{stamp}.json"
    ff_path.write_text(json.dumps({"matches": matches, "players": ff_rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    assessment = build_assessment(championship_data, ff_rows)
    csv_path = export_dir / f"alineacion_asistente_jornada_{args.round_number}_{stamp}.csv"
    html_path = args.docs_dir / f"asistente_alineacion_{args.season}_j{args.round_number}.html"
    write_csv(csv_path, assessment)
    render_html(assessment, args.season, args.round_number, html_path)

    print(f"Jugadores evaluados: {len(assessment)}")
    print(f"Futmondo JSON: {championship_path}")
    print(f"FutbolFantasy JSON: {ff_path}")
    print(f"CSV: {csv_path}")
    print(f"HTML: {html_path}")


if __name__ == "__main__":
    main()
