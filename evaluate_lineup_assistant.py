import argparse
import csv
import html
import json
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


LOGIN_API_URL = "https://api.futmondo.com/5/login/with_mail"
CHAMPIONSHIP_PLAYERS_API_URL = "https://api.futmondo.com/5/league/championshipplayers"
FUTBOLFANTASY_LINEUPS_URL = "https://www.futbolfantasy.com/laliga/posibles-alineaciones"
FUTBOLFANTASY_TEAM_URL = "https://www.futbolfantasy.com/laliga/equipos/{slug}/plantilla"
DEFAULT_SEASON = "2026_2027"
DEFAULT_EXPORTS_DIR = Path("data/exports")
DEFAULT_DOCS_DIR = Path("docs")
DEFAULT_MATCHING_OVERRIDES = Path("config/player_name_overrides.json")

FF_TEAM_SLUGS = {
    "Alavés": "alaves",
    "Athletic": "athletic",
    "Atlético": "atletico",
    "Barcelona": "barcelona",
    "Betis": "betis",
    "Celta": "celta",
    "Deportivo": "deportivo",
    "Elche": "elche",
    "Espanyol": "espanyol",
    "Getafe": "getafe",
    "Levante": "levante",
    "Málaga": "malaga",
    "Osasuna": "osasuna",
    "Racing": "racing",
    "Rayo": "rayo-vallecano",
    "Real Madrid": "real-madrid",
    "Real Sociedad": "real-sociedad",
    "Sevilla": "sevilla",
    "Valencia": "valencia",
    "Villarreal": "villarreal",
}

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


def anchor_id(value):
    value = normalize_name(value)
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-") or "jugador"


def clean_player_name(value):
    value = value or ""
    value = re.sub(r"^\s*\d+\.\s*", "", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_role(value):
    value = normalize_name(value)
    mapping = {
        "portero": "portero",
        "defensa": "defensa",
        "centrocampista": "centrocampista",
        "mediocampista": "centrocampista",
        "delantero": "delantero",
    }
    return mapping.get(value, value)


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
    for attempt in range(3):
        response = requests.get(url, headers=FF_HEADERS, timeout=30)
        if response.status_code != 429:
            response.raise_for_status()
            return response.text
        wait_seconds = 3 * (attempt + 1)
        print(f"FutbolFantasy limita la peticion; reintento en {wait_seconds}s: {url}")
        time.sleep(wait_seconds)
    response.raise_for_status()
    return response.text


def parse_local_datetime(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def filter_delayed_matches(matches, max_days_from_start):
    if max_days_from_start is None:
        return matches
    dated = [(match, parse_local_datetime(match.get("start"))) for match in matches]
    starts = [start for _, start in dated if start]
    if not starts:
        return matches
    first_start = min(starts)
    cutoff = first_start + timedelta(days=max_days_from_start)
    filtered = [match for match, start in dated if not start or start <= cutoff]
    excluded = [match for match, start in dated if start and start > cutoff]
    for match in excluded:
        print(f"Partido retrasado excluido: {match.get('name')} ({match.get('start')})")
    return filtered


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


def load_schedule(path):
    if not path:
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_matching_overrides(path):
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def schedule_action(schedule, today):
    for entry in schedule:
        starts_on = date.fromisoformat(entry["starts_on"])
        active_until = date.fromisoformat(entry.get("active_until", entry["starts_on"]))
        if today == starts_on:
            return "run", int(entry["round"])
        if starts_on < today <= active_until:
            return "active", int(entry["round"])
    return "suspend", None


def player_probability(node):
    text = node.get_text(" ", strip=True)
    match = re.search(r"(\d{1,3})\s*%", text)
    return int(match.group(1)) if match else None


def player_role(node):
    classes = set(node.get("class") or [])
    for role in ["portero", "defensa", "centrocampista", "delantero"]:
        if role in classes:
            return role
    return ""


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
                "role": player_role(node),
                "availability": "",
                "source": "alineacion",
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
                    "role": "",
                    "availability": availability,
                    "source": "alineacion",
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


def parse_team_squad(club, delay=0):
    slug = FF_TEAM_SLUGS.get(club)
    if not slug:
        print(f"Sin slug de FutbolFantasy para plantilla: {club}")
        return []

    try:
        soup = BeautifulSoup(fetch_html(FUTBOLFANTASY_TEAM_URL.format(slug=slug)), "html.parser")
    except requests.HTTPError as exc:
        print(f"No se pudo descargar plantilla FF de {club}: {exc}")
        return []
    rows = []
    for node in soup.select(".elemento.wjugador"):
        link = node.select_one("a.jugador")
        if not link:
            continue
        role_el = node.select_one(".comentario .posicion")
        role = normalize_role(role_el.get_text(" ", strip=True) if role_el else "")
        detail = ""
        comment = node.select_one(".comentario")
        if comment:
            detail = comment.get_text(" ", strip=True)
            if role_el:
                detail = detail.replace(role_el.get_text(" ", strip=True), "", 1)
            detail = re.sub(r"\s+", " ", detail).strip()
        name = clean_player_name(link.get_text(" ", strip=True))
        if not name:
            continue
        rows.append(
            {
                "ff_name": name,
                "ff_name_norm": normalize_name(name),
                "club": club,
                "opponent": "",
                "home_away": "",
                "match": "",
                "match_start": "",
                "match_url": link.get("href"),
                "lineup_status": "plantilla",
                "probability": "",
                "role": role,
                "role_detail": detail,
                "availability": "",
                "source": "plantilla",
            }
        )
    if delay:
        time.sleep(delay)
    return rows


def fetch_futbolfantasy_squads(matches, delay=0.1):
    clubs = sorted({match.get("home_team") for match in matches} | {match.get("away_team") for match in matches})
    rows = []
    for index, club in enumerate([club for club in clubs if club], start=1):
        print(f"FutbolFantasy plantilla {index}/{len(clubs)}: {club}")
        rows.extend(parse_team_squad(club, delay=delay))
    return rows


def fetch_futbolfantasy_round(round_number, delay=0.2, max_days_from_start=4, return_all_rows=False):
    all_matches = parse_matches(round_number)
    all_rows = []
    for index, match in enumerate(all_matches, start=1):
        print(f"FutbolFantasy partido {index}/{len(all_matches)}: {match.get('name') or match.get('match')}")
        all_rows.extend(parse_match_lineups(match))
        if delay:
            time.sleep(delay)

    matches = filter_delayed_matches(all_matches, max_days_from_start)
    included_urls = {match.get("url") for match in matches}
    rows = [row for row in all_rows if row.get("match_url") in included_urls]
    if return_all_rows:
        return matches, rows, all_rows
    return matches, rows


def build_team_id_map(championship_data, ff_rows):
    ff_by_name = {}
    for row in ff_rows:
        ff_by_name.setdefault(row["ff_name_norm"], row)

    team_votes = {}
    for player in championship_data.get("answer", {}).get("players", []):
        team_id = player.get("teamId")
        if not team_id:
            continue
        ff_row = ff_by_name.get(normalize_name(player.get("name")))
        if not ff_row or not ff_row.get("club"):
            continue
        team_votes.setdefault(team_id, {})
        club = ff_row["club"]
        team_votes[team_id][club] = team_votes[team_id].get(club, 0) + 1

    team_map = {}
    for team_id, votes in team_votes.items():
        team_map[team_id] = sorted(votes.items(), key=lambda item: item[1], reverse=True)[0][0]
    return team_map


def best_ff_match(player, ff_by_name, ff_rows, overrides=None, team_id_map=None):
    overrides = overrides or {}
    team_id_map = team_id_map or {}
    mapped_name = overrides.get(player.get("id")) or overrides.get(normalize_name(player.get("name")))
    if mapped_name:
        mapped_norm = normalize_name(mapped_name)
        if mapped_norm in ff_by_name:
            return ff_by_name[mapped_norm], 1.0

    name_norm = normalize_name(player.get("name"))
    player_club = player.get("team") or team_id_map.get(player.get("teamId"), "")
    player_role = player.get("role")
    candidates = []
    for row in ff_rows:
        name_score = SequenceMatcher(None, name_norm, row["ff_name_norm"]).ratio()
        exact_score = 0.4 if name_norm == row["ff_name_norm"] else 0
        score = (
            name_score
            + exact_score
            + club_score(player_club, row.get("club"))
            + role_score(player_role, row.get("role"))
            + source_score(row)
        )
        same_club = player_club and normalize_name(player_club) == normalize_name(row.get("club"))
        same_role = player_role and normalize_name(player_role) == normalize_name(row.get("role"))
        if name_score >= 0.88 or (same_club and name_score >= 0.55) or (same_club and same_role and name_score >= 0.5):
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
    if row["lineup_status"] == "plantilla":
        return "Sin dato alineacion probable"
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
    elif row["lineup_status"] == "plantilla":
        score -= 5
    else:
        score -= 10
    if row["home_away"] == "casa":
        score += 10
    return score


def build_assessment(championship_data, ff_rows, overrides=None, team_id_map=None, matching_rows=None):
    team_id_map = team_id_map or {}
    matching_rows = matching_rows or ff_rows
    ff_by_name = {}
    for row in sorted(matching_rows, key=lambda item: int(item.get("probability") or 0), reverse=True):
        ff_by_name.setdefault(row["ff_name_norm"], row)

    owned = [player for player in championship_data.get("answer", {}).get("players", []) if player.get("userteamId")]
    assessed = []
    for player in owned:
        ff_row, match_score = best_ff_match(
            player,
            ff_by_name,
            matching_rows,
            overrides=overrides,
            team_id_map=team_id_map,
        )
        base = {
            "player_id": player.get("id"),
            "fantasy_team": player.get("userteam"),
            "player_name": player.get("name"),
            "role": player.get("role"),
            "futmondo_team_id": player.get("teamId"),
            "futmondo_club": player.get("team") or team_id_map.get(player.get("teamId"), ""),
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
    team_links = []
    for team, team_rows in grouped.items():
        team_anchor = f"team-{anchor_id(team)}"
        team_links.append(
            f'<a class="inline-flex border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-white" href="#{team_anchor}">{esc(team)}</a>'
        )
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
                """.strip()
            )
        sections.append(
            f"""
            <section id="{team_anchor}" class="mt-8 bg-white p-5 shadow-sm">
                <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <h2 class="text-xl font-bold">{esc(team)}</h2>
                    <a class="text-sm font-semibold text-emerald-700 hover:underline" href="#top">Arriba</a>
                </div>
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
    <main id="top" class="max-w-7xl mx-auto px-4 py-6 sm:px-6 lg:px-8">
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
                    <a class="inline-flex items-center justify-center bg-emerald-700 px-4 py-2 text-sm font-semibold text-white" href="asistente_alineacion.html">Asistente</a>
                    <a class="inline-flex items-center justify-center border border-slate-400 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-white" href="matching_futbolfantasy.html">Matching</a>
                </nav>
            </div>
        </header>
        <section class="mt-6 bg-white p-5 shadow-sm">
            <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <h2 class="text-lg font-bold">Índice de equipos</h2>
                <span class="text-sm font-semibold text-slate-500">{len(grouped)} equipos</span>
            </div>
            <div class="mt-4 flex flex-wrap gap-2">
                {''.join(team_links)}
            </div>
        </section>
        {''.join(sections)}
    </main>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")


def render_suspended_html(season, output_path, message):
    updated = datetime.now().strftime("%d/%m/%Y %H:%M")
    season_label = season.replace("_", "-")
    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Asistente Alineación Futmondo {season}</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='0.9em' font-size='90'%3E%E2%9A%BD%3C/text%3E%3C/svg%3E">
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 text-slate-900">
    <main class="max-w-4xl mx-auto px-4 py-10 sm:px-6 lg:px-8">
        <header class="border-b border-slate-300 pb-6">
            <p class="text-sm font-semibold uppercase tracking-wide text-emerald-700">Futmondo PALETOS · {esc(season_label)}</p>
            <h1 class="mt-2 text-3xl font-extrabold text-slate-950 sm:text-4xl">Asistente en suspenso</h1>
            <p class="mt-2 text-sm text-slate-600">Actualizado: {updated}</p>
            <p class="mt-4 text-slate-700">{esc(message)}</p>
            <nav class="mt-6 flex gap-2">
                <a class="inline-flex items-center justify-center border border-slate-400 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-white" href="index_{season}.html">Mercado</a>
                <a class="inline-flex items-center justify-center border border-slate-400 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-white" href="resumen_liga_{season}.html">Resumen</a>
            </nav>
        </header>
    </main>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")


def role_score(futmondo_role, ff_role):
    if not futmondo_role or not ff_role:
        return 0
    return 0.15 if normalize_name(futmondo_role) == normalize_name(ff_role) else -0.15


def club_score(futmondo_club, ff_club):
    if not futmondo_club or not ff_club:
        return 0
    return 0.3 if normalize_name(futmondo_club) == normalize_name(ff_club) else -0.2


def source_score(row):
    if row.get("source") == "alineacion":
        return 1.25
    return 0


def candidate_matches(player_name, ff_rows, futmondo_club="", futmondo_role="", limit=8):
    name_norm = normalize_name(player_name)
    scoped_rows = ff_rows
    if futmondo_club:
        same_club = [row for row in scoped_rows if normalize_name(row.get("club")) == normalize_name(futmondo_club)]
        if same_club:
            scoped_rows = same_club
    if futmondo_role:
        same_role = [row for row in scoped_rows if normalize_name(row.get("role")) == normalize_name(futmondo_role)]
        if same_role:
            scoped_rows = same_role

    candidates = []
    for row in scoped_rows:
        name_score = SequenceMatcher(None, name_norm, row["ff_name_norm"]).ratio()
        score = (
            name_score
            + club_score(futmondo_club, row.get("club"))
            + role_score(futmondo_role, row.get("role"))
            + source_score(row)
        )
        candidates.append((score, row))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[:limit]


def render_matching_review_html(assessment, ff_rows, season, round_number, output_path):
    updated = datetime.now().strftime("%d/%m/%Y %H:%M")
    season_label = season.replace("_", "-")
    ff_options = sorted(
        ff_rows,
        key=lambda row: (row.get("club") or "", row.get("ff_name") or ""),
    )
    unmatched = [row for row in assessment if row["lineup_status"] == "sin dato"]
    datalist_options = []
    seen_options = set()
    for candidate in ff_options:
        value = candidate["ff_name"]
        if value in seen_options:
            continue
        seen_options.add(value)
        label = f"{candidate.get('club', '')} · {candidate.get('lineup_status', '')}"
        datalist_options.append(f'<option value="{esc(value)}" label="{esc(label)}"></option>')

    rows_html = []
    for row in unmatched:
        candidates = candidate_matches(
            row["player_name"],
            ff_options,
            futmondo_club=row.get("futmondo_club"),
            futmondo_role=row.get("role"),
        )
        candidate_text = ", ".join(
            f"{candidate['ff_name']} ({candidate.get('club', '')})" for _, candidate in candidates[:4]
        )

        rows_html.append(
            f"""
            <tr data-player-id="{esc(row['player_id'])}" data-player-name="{esc(row['player_name'])}">
                <td class="px-3 py-2 font-semibold">{esc(row['player_name'])}</td>
                <td class="px-3 py-2">{esc(row['fantasy_team'])}</td>
                <td class="px-3 py-2 font-semibold text-slate-700">{esc(row.get('futmondo_club'))}</td>
                <td class="px-3 py-2">{esc(row['role'])}</td>
                <td class="px-3 py-2">
                    <input class="mapping-input w-full border border-slate-300 bg-white px-2 py-1 text-sm" list="ff-player-options" placeholder="Escribe o elige nombre FF">
                    <p class="mt-1 text-xs text-slate-500">Sugerencias: {esc(candidate_text)}</p>
                </td>
            </tr>
            """.strip()
        )

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Matching FutbolFantasy {season_label} J{round_number}</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='0.9em' font-size='90'%3E%E2%9A%BD%3C/text%3E%3C/svg%3E">
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 text-slate-900">
    <main class="max-w-7xl mx-auto px-4 py-6 sm:px-6 lg:px-8">
        <header class="border-b border-slate-300 pb-6">
            <p class="text-sm font-semibold uppercase tracking-wide text-emerald-700">Futmondo PALETOS · {season_label} · Jornada {round_number}</p>
            <h1 class="mt-2 text-3xl font-extrabold text-slate-950 sm:text-4xl">Ayuda al matching</h1>
            <p class="mt-2 text-sm text-slate-600">Actualizado: {updated}. Jugadores sin cruce: {len(unmatched)}.</p>
            <nav class="mt-4 flex flex-wrap gap-2">
                <a class="inline-flex items-center justify-center border border-slate-400 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-white" href="index_{season}.html">Mercado</a>
                <a class="inline-flex items-center justify-center border border-slate-400 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-white" href="asistente_alineacion.html">Asistente</a>
                <button id="copy-json" class="inline-flex items-center justify-center bg-emerald-700 px-4 py-2 text-sm font-semibold text-white">Copiar JSON</button>
                <button id="download-json" class="inline-flex items-center justify-center border border-slate-400 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-white">Descargar JSON</button>
            </nav>
        </header>

        <section class="mt-6 bg-white p-5 shadow-sm">
            <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <input id="filter" class="border border-slate-300 px-3 py-2 text-sm" placeholder="Filtrar jugador o equipo">
                <p id="status" class="text-sm font-semibold text-slate-600">Selecciona equivalencias y copia el JSON.</p>
            </div>
            <div class="overflow-x-auto">
                <table class="min-w-full text-left text-sm">
                    <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                        <tr><th class="px-3 py-2">Futmondo</th><th class="px-3 py-2">Equipo fantasy</th><th class="px-3 py-2">Club Futmondo</th><th class="px-3 py-2">Rol</th><th class="px-3 py-2">Nombre en FutbolFantasy</th></tr>
                    </thead>
                    <tbody id="mapping-body" class="divide-y divide-slate-100">{''.join(rows_html)}</tbody>
                </table>
                <datalist id="ff-player-options">{''.join(datalist_options)}</datalist>
            </div>
        </section>
    </main>
    <script>
        const storageKey = "futmondo_ff_matching_{season}_{round_number}";
        const saved = JSON.parse(localStorage.getItem(storageKey) || "{{}}");
        const inputs = Array.from(document.querySelectorAll(".mapping-input"));
        function currentMappings() {{
            const mappings = {{}};
            inputs.forEach((input) => {{
                const row = input.closest("tr");
                if (input.value.trim()) {{
                    mappings[row.dataset.playerId] = input.value.trim();
                }}
            }});
            return mappings;
        }}
        function persist() {{
            localStorage.setItem(storageKey, JSON.stringify(currentMappings()));
            document.getElementById("status").textContent = `${{Object.keys(currentMappings()).length}} equivalencias preparadas.`;
        }}
        inputs.forEach((input) => {{
            const row = input.closest("tr");
            if (saved[row.dataset.playerId]) input.value = saved[row.dataset.playerId];
            input.addEventListener("input", persist);
        }});
        persist();
        document.getElementById("filter").addEventListener("input", (event) => {{
            const term = event.target.value.toLowerCase();
            document.querySelectorAll("#mapping-body tr").forEach((row) => {{
                row.style.display = row.textContent.toLowerCase().includes(term) ? "" : "none";
            }});
        }});
        function payloadText() {{
            return JSON.stringify(currentMappings(), null, 2);
        }}
        document.getElementById("copy-json").addEventListener("click", async () => {{
            await navigator.clipboard.writeText(payloadText());
            document.getElementById("status").textContent = "JSON copiado. Pegalo en config/player_name_overrides.json.";
        }});
        document.getElementById("download-json").addEventListener("click", () => {{
            const blob = new Blob([payloadText()], {{ type: "application/json" }});
            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            link.download = "player_name_overrides.json";
            link.click();
            URL.revokeObjectURL(link.href);
        }});
    </script>
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
    parser.add_argument("--schedule", type=Path)
    parser.add_argument("--today")
    parser.add_argument("--skip-if-not-scheduled", action="store_true")
    parser.add_argument("--suspend-if-not-scheduled", action="store_true")
    parser.add_argument("--max-days-from-start", default=4, type=int)
    parser.add_argument("--matching-overrides", default=DEFAULT_MATCHING_OVERRIDES, type=Path)
    args = parser.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    if args.schedule:
        action, scheduled_round = schedule_action(load_schedule(args.schedule), today)
        if action == "run":
            args.round_number = scheduled_round
        elif args.skip_if_not_scheduled:
            if action == "active":
                print(f"Asistente activo para jornada {scheduled_round}; no se actualiza hoy ({today}).")
            else:
                print(f"No hay jornada programada para actualizar el asistente hoy ({today}).")
                if args.suspend_if_not_scheduled:
                    render_suspended_html(
                        args.season,
                        args.docs_dir / "asistente_alineacion.html",
                        "Se activara de nuevo el dia que empiece la siguiente jornada programada.",
                    )
            return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = args.exports_dir / args.season / "lineup_assistant"
    export_dir.mkdir(parents=True, exist_ok=True)

    championship_data = fetch_championship_players()
    championship_path = export_dir / f"championshipplayers_{stamp}.json"
    championship_path.write_text(json.dumps(championship_data, ensure_ascii=False, indent=2), encoding="utf-8")

    matches, ff_rows, all_ff_rows = fetch_futbolfantasy_round(
        args.round_number,
        delay=args.delay,
        max_days_from_start=args.max_days_from_start,
        return_all_rows=True,
    )
    squad_rows = fetch_futbolfantasy_squads(parse_matches(args.round_number), delay=args.delay)
    matching_rows = all_ff_rows + squad_rows
    ff_path = export_dir / f"futbolfantasy_jornada_{args.round_number}_{stamp}.json"
    ff_path.write_text(json.dumps({"matches": matches, "players": ff_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    squads_path = export_dir / f"futbolfantasy_plantillas_jornada_{args.round_number}_{stamp}.json"
    squads_path.write_text(json.dumps({"players": squad_rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    overrides = load_matching_overrides(args.matching_overrides)
    team_id_map = build_team_id_map(championship_data, matching_rows)
    assessment = build_assessment(
        championship_data,
        ff_rows,
        overrides=overrides,
        team_id_map=team_id_map,
        matching_rows=matching_rows,
    )
    csv_path = export_dir / f"alineacion_asistente_jornada_{args.round_number}_{stamp}.csv"
    html_path = args.docs_dir / f"asistente_alineacion_{args.season}_j{args.round_number}.html"
    stable_html_path = args.docs_dir / "asistente_alineacion.html"
    matching_html_path = args.docs_dir / "matching_futbolfantasy.html"
    write_csv(csv_path, assessment)
    render_html(assessment, args.season, args.round_number, html_path)
    render_html(assessment, args.season, args.round_number, stable_html_path)
    render_matching_review_html(assessment, matching_rows, args.season, args.round_number, matching_html_path)

    print(f"Jugadores evaluados: {len(assessment)}")
    print(f"Futmondo JSON: {championship_path}")
    print(f"FutbolFantasy JSON: {ff_path}")
    print(f"Plantillas FF JSON: {squads_path}")
    print(f"CSV: {csv_path}")
    print(f"HTML: {html_path}")
    print(f"HTML estable: {stable_html_path}")
    print(f"Matching: {matching_html_path}")


if __name__ == "__main__":
    main()
