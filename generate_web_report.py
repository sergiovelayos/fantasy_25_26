import argparse
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


DEFAULT_EXPORTS_DIR = Path("data/exports")
DEFAULT_OUTPUT = Path("docs/index.html")
LEGACY_MARKET_FILE = Path("data/futmondo_market.json")
LEGACY_SIGNINGS_FILE = Path("data/fichajes_hist_file/pressroom_2025_2026.json")
ASSETS_DIR = Path("docs/assets")
MATCHING_OVERRIDES_FILE = Path("config/player_name_overrides.json")
LOCAL_TZ = ZoneInfo("Europe/Madrid")

plt.style.use("ggplot")
sns.set_palette("husl")


def latest_file(directory, pattern):
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def esc(value):
    if pd.isna(value):
        return ""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def normalize_name(value):
    value = value or ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower()
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def format_int(value):
    return f"{int(round(value)):,}".replace(",", ".")


def format_money(value):
    return f"{format_int(value)} €"


def format_delta_money(value):
    if pd.isna(value):
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{format_money(value)}"


def format_pct(value):
    if pd.isna(value):
        return "-"
    return f"{value:+.1f}%"


def discover_seasons(exports_dir):
    seasons = []
    for directory in exports_dir.iterdir():
        if directory.is_dir() and any(directory.glob("pressroom_all_*.json")):
            seasons.append(directory.name)
    return sorted(seasons)


def season_label(season):
    return season.replace("_", "-")


def season_market_path(exports_dir, season):
    path = exports_dir / season / "market_snapshots.json"
    if path.exists():
        return path
    if season == "2025_2026" and LEGACY_MARKET_FILE.exists():
        return LEGACY_MARKET_FILE
    return None


def season_signings_path(exports_dir, season):
    path = latest_file(exports_dir / season, "pressroom_all_*.json")
    if path:
        return path
    if season == "2025_2026" and LEGACY_SIGNINGS_FILE.exists():
        return LEGACY_SIGNINGS_FILE
    return None


def load_market(path):
    if not path:
        return pd.DataFrame(
            columns=[
                "player_id",
                "name",
                "creation_date",
                "price",
                "computer",
                "team",
                "points",
                "average",
                "expiration_date",
                "snapshot_at",
            ]
        )

    raw = json.load(path.open(encoding="utf-8"))
    rows = []
    for entry in raw:
        players = entry.get("jugadores") if isinstance(entry, dict) else None
        snapshot_at = entry.get("fecha") if isinstance(entry, dict) else None
        if players is None:
            players = [entry]
        for player in players:
            if not isinstance(player, dict):
                continue
            average = player.get("average")
            avg_value = average.get("average") if isinstance(average, dict) else average
            rows.append(
                {
                    "player_id": player.get("id"),
                    "name": player.get("name"),
                    "creation_date": player.get("creationDate"),
                    "price": player.get("price"),
                    "computer": player.get("computer", False) or (player.get("userTeam") is None),
                    "team": player.get("team"),
                    "points": player.get("points"),
                    "average": avg_value,
                    "expiration_date": player.get("expirationDate"),
                    "snapshot_at": snapshot_at,
                }
            )

    market = pd.DataFrame(rows)
    if market.empty:
        return market
    market["creation_date"] = pd.to_datetime(market["creation_date"], utc=True, errors="coerce")
    market["expiration_date"] = pd.to_datetime(market["expiration_date"], utc=True, errors="coerce")
    market["snapshot_at"] = pd.to_datetime(market["snapshot_at"], utc=True, errors="coerce")
    market["price"] = pd.to_numeric(market["price"], errors="coerce")
    market["points"] = pd.to_numeric(market["points"], errors="coerce")
    market["average"] = pd.to_numeric(market["average"], errors="coerce")
    return market.drop_duplicates(subset=["player_id", "creation_date", "snapshot_at"])


def load_signings(path):
    if not path:
        return pd.DataFrame(columns=["player_id", "player_name", "buyer", "price", "signed_date"])

    raw = json.load(path.open(encoding="utf-8"))
    rows = []
    for item in raw.get("answer", {}).get("news", []):
        buyer = item.get("_buyer") or {}
        player = item.get("_player") or {}
        rows.append(
            {
                "player_id": player.get("_id"),
                "player_name": player.get("name"),
                "buyer": buyer.get("name"),
                "price": item.get("price"),
                "signed_date": item.get("created"),
            }
        )
    signings = pd.DataFrame(rows)
    if signings.empty:
        return signings
    signings["signed_date"] = pd.to_datetime(signings["signed_date"], utc=True, errors="coerce")
    signings["price"] = pd.to_numeric(signings["price"], errors="coerce")
    return signings.dropna(subset=["buyer"])


def analyze_conversion(market, signings, chart_path):
    if market.empty:
        make_empty_chart(chart_path, "Sin datos de mercado")
        return 0, 0, 0

    offers = unique_market_offers(market)
    matched_sales = match_signings_to_market(market, signings)

    total_offers = len(offers)
    total_sales = len(matched_sales)
    rate = (total_sales / total_offers) * 100 if total_offers else 0

    offers = offers.copy()
    offers["date"] = offers["creation_date"].dt.date
    daily_market = offers.groupby("date").size().rename("Ofertas")
    if matched_sales.empty:
        daily_sales = pd.Series(dtype="float64", name="Ventas")
    else:
        matched_sales = matched_sales.copy()
        matched_sales["date"] = matched_sales["signed_date"].dt.date
        daily_sales = matched_sales.groupby("date").size().rename("Ventas")

    stats = pd.concat([daily_market, daily_sales], axis=1).fillna(0).tail(15)
    plt.figure(figsize=(10, 5))
    ax = stats["Ofertas"].plot(kind="bar", color="#3498db", alpha=0.7, label="Jugadores en Mercado")
    stats["Ventas"].plot(kind="bar", color="#2ecc71", ax=ax, label="Fichados")
    plt.title("Actividad del Mercado")
    plt.ylabel("Cantidad de jugadores")
    plt.xlabel("Fecha")
    plt.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(chart_path)
    plt.close()
    return rate, total_offers, total_sales


def make_empty_chart(chart_path, title):
    plt.figure(figsize=(10, 5))
    plt.title(title)
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(chart_path)
    plt.close()


def analyze_overbids(market, signings):
    matched = match_signings_to_market(market, signings)
    if matched.empty:
        return pd.DataFrame(columns=["buyer", "count", "avg_overbid"])
    return (
        matched.groupby("buyer", as_index=False)
        .agg(count=("player_id", "count"), avg_overbid=("overbid_pct", "mean"))
        .sort_values("avg_overbid", ascending=False)
    )


def unique_market_offers(market):
    market_comp = market[market["computer"] == True].copy()
    market_comp = market_comp.dropna(subset=["player_id", "creation_date", "price"])
    if market_comp.empty:
        return market_comp
    return (
        market_comp.sort_values(["player_id", "creation_date", "snapshot_at"])
        .drop_duplicates(subset=["player_id", "creation_date"], keep="last")
        .copy()
    )


def match_signings_to_market(market, signings):
    if market.empty or signings.empty:
        return pd.DataFrame(
            columns=["buyer", "player_id", "player_name", "price_s", "market_price", "overbid", "overbid_pct", "signed_date"]
        )

    market_comp = unique_market_offers(market)
    if market_comp.empty:
        return pd.DataFrame(
            columns=["buyer", "player_id", "player_name", "price_s", "market_price", "overbid", "overbid_pct", "signed_date"]
        )

    market_comp = market_comp.rename(columns={"price": "price_m"}).sort_values("creation_date")
    signed = signings.rename(columns={"price": "price_s"}).dropna(subset=["player_id", "signed_date", "price_s"])
    signed = signed.sort_values("signed_date")

    merged = signed.merge(market_comp, on="player_id", how="left", suffixes=("_s", "_m"))
    merged = merged[
        (merged["signed_date"] >= merged["creation_date"])
        & (merged["signed_date"] <= merged["expiration_date"] + pd.Timedelta(hours=2))
    ].copy()
    merged["distance_to_expiration"] = (merged["signed_date"] - merged["expiration_date"]).abs()
    merged = (
        merged.sort_values(["player_id", "signed_date", "distance_to_expiration"])
        .drop_duplicates(subset=["player_id", "signed_date"], keep="first")
        .copy()
    )
    matched = merged.dropna(subset=["price_m"]).copy()
    if matched.empty:
        return pd.DataFrame(
            columns=["buyer", "player_id", "player_name", "price_s", "market_price", "overbid", "overbid_pct", "signed_date"]
        )
    matched["market_price"] = matched["price_m"]
    matched["overbid"] = matched["price_s"] - matched["market_price"]
    matched["overbid_pct"] = ((matched["price_s"] - matched["price_m"]) / matched["price_m"]) * 100
    return matched


def get_current_market(market):
    if market.empty:
        return market
    if "snapshot_at" in market.columns and market["snapshot_at"].notna().any():
        latest_snapshot = market["snapshot_at"].max()
        current = market[market["snapshot_at"] == latest_snapshot].copy()
    else:
        latest_date = market["creation_date"].max()
        current = market[market["creation_date"] == latest_date].copy()
    current = current[current["computer"] == True].copy()
    return current.sort_values(["expiration_date", "price"], ascending=[True, False])


def recommendation(row):
    availability = row.get("availability", "")
    lineup_status = row.get("lineup_status", "sin dato")
    probability = row.get("probability") or 0
    home_away = row.get("home_away", "")
    if availability == "sancionado":
        return "Evitar: sancionado"
    if availability == "lesionado":
        return "Evitar/revisar: parte medico"
    if lineup_status == "once probable":
        if probability >= 80 and home_away == "casa":
            return "Alinear fuerte"
        if probability >= 80:
            return "Alinear"
        if probability >= 60:
            return "Alinear con cautela"
        return "Duda de once"
    if lineup_status == "alternativa":
        if probability >= 50:
            return "Banquillo util / posible entrada"
        return "Solo emergencia"
    return "Sin dato FutbolFantasy"


def load_lineup_rows(exports_dir, season):
    lineup_dir = exports_dir / season / "lineup_assistant"
    path = latest_file(lineup_dir, "futbolfantasy_jornada_*.json") if lineup_dir.exists() else None
    if not path:
        return []
    raw = json.load(path.open(encoding="utf-8"))
    rows = raw.get("players", [])
    return rows if isinstance(rows, list) else []


def load_matching_overrides(path=MATCHING_OVERRIDES_FILE):
    if not path.exists():
        return {}
    return json.load(path.open(encoding="utf-8"))


def best_lineup_match(name, ff_by_name, ff_rows, player_id=None, overrides=None):
    overrides = overrides or {}
    mapped_name = overrides.get(player_id) or overrides.get(normalize_name(name))
    if mapped_name:
        mapped_norm = normalize_name(mapped_name)
        if mapped_norm in ff_by_name:
            return ff_by_name[mapped_norm], 1.0

    name_norm = normalize_name(name)
    if name_norm in ff_by_name:
        return ff_by_name[name_norm], 1.0
    candidates = []
    for row in ff_rows:
        score = SequenceMatcher(None, name_norm, row.get("ff_name_norm") or normalize_name(row.get("ff_name"))).ratio()
        if score >= 0.88:
            candidates.append((score, row))
    if not candidates:
        return None, 0
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1], candidates[0][0]


def parse_ff_match_start(value):
    if not value:
        return pd.NaT
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return pd.NaT
    if parsed.tzinfo is None:
        return parsed.tz_localize(LOCAL_TZ).tz_convert("UTC")
    return parsed.tz_convert("UTC")


def enrich_market_with_lineups(current_market, ff_rows, overrides=None):
    if current_market.empty:
        return current_market
    market = current_market.copy()
    defaults = {
        "ff_status": "sin dato",
        "ff_probability": "",
        "ff_match": "",
        "ff_home_away": "",
        "ff_recommendation": "Sin dato FutbolFantasy",
        "ff_match_start": "",
    }
    for key, value in defaults.items():
        market[key] = value
    if not ff_rows:
        return market

    ff_by_name = {}
    for row in sorted(ff_rows, key=lambda item: item.get("probability") or 0, reverse=True):
        ff_by_name.setdefault(row.get("ff_name_norm") or normalize_name(row.get("ff_name")), row)

    for index, row in market.iterrows():
        ff_row, _ = best_lineup_match(row.get("name"), ff_by_name, ff_rows, player_id=row.get("player_id"), overrides=overrides)
        if not ff_row:
            continue
        market.at[index, "ff_status"] = ff_row.get("lineup_status", "sin dato")
        market.at[index, "ff_probability"] = ff_row.get("probability") if ff_row.get("probability") is not None else ""
        market.at[index, "ff_match"] = ff_row.get("match", "")
        market.at[index, "ff_home_away"] = ff_row.get("home_away", "")
        market.at[index, "ff_recommendation"] = recommendation(ff_row)
        market.at[index, "ff_match_start"] = ff_row.get("match_start", "")
        match_start = parse_ff_match_start(ff_row.get("match_start"))
        expiration = row.get("expiration_date")
        if pd.notna(match_start) and pd.notna(expiration) and expiration > match_start:
            market.at[index, "ff_status"] = "proxima jornada"
            market.at[index, "ff_probability"] = ""
            market.at[index, "ff_match"] = "Siguiente partido pendiente"
            market.at[index, "ff_home_away"] = ""
            market.at[index, "ff_recommendation"] = "No alineable en el partido ya iniciado"
    return market


def render_season_selector(seasons, current_season):
    options = []
    for season in seasons:
        selected = " selected" if season == current_season else ""
        options.append(f'<option value="index_{season}.html"{selected}>{season_label(season)}</option>')
    return "\n".join(options)


def render_nav_links(season, active):
    links = [
        ("Mercado", f"index_{season}.html", active == "market"),
        ("Resumen", f"resumen_liga_{season}.html", active == "summary"),
    ]
    assistant_path = DEFAULT_OUTPUT.parent / "asistente_alineacion.html"
    if assistant_path.exists():
        links.append(("Asistente", assistant_path.name, active == "assistant"))
    matching_path = DEFAULT_OUTPUT.parent / "matching_futbolfantasy.html"
    if matching_path.exists():
        links.append(("Matching", matching_path.name, active == "matching"))

    rendered = []
    for label, href, is_active in links:
        cls = "bg-indigo-700 text-white" if is_active else "border border-gray-400 text-gray-800 hover:bg-white"
        rendered.append(
            f'<a class="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold {cls}" href="{href}">{label}</a>'
        )
    return "\n".join(rendered)


def table_empty(colspan, text):
    return f'<tr><td class="py-3 px-4 text-gray-500" colspan="{colspan}">{text}</td></tr>'


def generate_html(
    season,
    seasons,
    conversion_rate,
    offers,
    sales,
    buyer_stats,
    top_signings,
    current_market,
    chart_filename,
    show_lineup_columns=False,
):
    buyer_rows = ""
    if buyer_stats.empty:
        buyer_rows = table_empty(3, "Sin cruces suficientes entre mercado y fichajes.")
    else:
        for _, row in buyer_stats.iterrows():
            tone = "text-green-600" if row["avg_overbid"] > 0 else "text-red-600"
            buyer_rows += f"""
            <tr>
                <td class="py-3 px-4 font-medium">{esc(row['buyer'])}</td>
                <td class="py-3 px-4 text-center tabular-nums">{int(row['count'])}</td>
                <td class="py-3 px-4 text-right font-bold tabular-nums {tone}">{row['avg_overbid']:+.2f}%</td>
            </tr>
            """

    top_rows = ""
    if top_signings.empty:
        top_rows = table_empty(6, "Sin fichajes registrados.")
    else:
        for _, row in top_signings.iterrows():
            date = row["signed_date"].strftime("%Y-%m-%d") if pd.notna(row["signed_date"]) else "-"
            overbid = format_delta_money(row["overbid"]) if pd.notna(row.get("overbid")) else "-"
            overbid_pct = format_pct(row.get("overbid_pct"))
            top_rows += f"""
            <tr>
                <td class="py-3 px-4 font-medium">{esc(row['player_name'])}</td>
                <td class="py-3 px-4">{esc(row['buyer'])}</td>
                <td class="py-3 px-4 text-right whitespace-nowrap tabular-nums">{format_money(row['price_s'])}</td>
                <td class="py-3 px-4 text-right whitespace-nowrap tabular-nums">{overbid}</td>
                <td class="py-3 px-4 text-right whitespace-nowrap tabular-nums">{overbid_pct}</td>
                <td class="py-3 px-4 text-right text-sm text-gray-500 whitespace-nowrap">{date}</td>
            </tr>
            """

    market_rows = ""
    market_colspan = 10 if show_lineup_columns else 6
    if current_market.empty:
        market_rows = table_empty(market_colspan, "Sin jugadores de mercado para esta temporada.")
    else:
        for _, row in current_market.iterrows():
            exp_str = row["expiration_date"].strftime("%d/%m %H:%M") if pd.notna(row["expiration_date"]) else "-"
            avg_str = f"{row['average']:.1f}" if pd.notna(row["average"]) else "-"
            price_str = format_money(row["price"]) if pd.notna(row["price"]) else "-"
            points_str = f"{int(row['points'])}" if pd.notna(row["points"]) else "-"
            lineup_cells = ""
            if show_lineup_columns:
                ff_prob = f"{int(row['ff_probability'])}%" if row.get("ff_probability") != "" and pd.notna(row.get("ff_probability")) else "-"
                ff_context = " · ".join(part for part in [row.get("ff_match"), row.get("ff_home_away")] if part)
                lineup_cells = f"""
                <td class="py-2 px-3">{esc(row.get('ff_status'))}</td>
                <td class="py-2 px-3 text-center tabular-nums">{ff_prob}</td>
                <td class="py-2 px-3 text-gray-500">{esc(ff_context)}</td>
                <td class="py-2 px-3 font-semibold">{esc(row.get('ff_recommendation'))}</td>
                """.strip()
            market_rows += f"""
            <tr>
                <td class="py-2 px-3 font-medium">{esc(row['name'])}</td>
                <td class="py-2 px-3 text-gray-500">{esc(row['team'])}</td>
                <td class="py-2 px-3 whitespace-nowrap tabular-nums">{price_str}</td>
                <td class="py-2 px-3 text-center tabular-nums">{avg_str}</td>
                <td class="py-2 px-3 text-center tabular-nums">{points_str}</td>
{lineup_cells}
                <td class="py-2 px-3 text-sm text-orange-500 whitespace-nowrap">{exp_str}</td>
            </tr>
            """.strip()
    lineup_headers = ""
    if show_lineup_columns:
        lineup_headers = '<th class="py-2 px-3">Estado FF</th><th class="py-2 px-3 text-center">Prob.</th><th class="py-2 px-3">Partido</th><th class="py-2 px-3">Consejo</th>'

    selector_options = render_season_selector(seasons, season)
    nav_links = render_nav_links(season, "market")
    label = season_label(season)
    updated = datetime.now().strftime("%d/%m/%Y %H:%M")

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mercado Futmondo {label}</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='0.9em' font-size='90'%3E%E2%9A%BD%3C/text%3E%3C/svg%3E">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');
        body {{ font-family: 'Inter', sans-serif; }}
        .tabular-nums {{ font-variant-numeric: tabular-nums; }}
    </style>
</head>
<body class="bg-gray-100 text-gray-800">
    <main class="max-w-6xl mx-auto px-4 py-6 sm:px-6 lg:px-8">
        <header class="mb-8">
            <div class="flex flex-col gap-4 border-b border-gray-300 pb-6 sm:flex-row sm:items-end sm:justify-between">
                <div>
                    <p class="text-sm font-semibold uppercase tracking-wide text-indigo-700">Futmondo PALETOS · {label}</p>
                    <h1 class="mt-2 text-3xl font-extrabold text-gray-950 sm:text-4xl">Mercado</h1>
                    <p class="mt-2 text-sm text-gray-600">Actualizado: {updated}</p>
                </div>
                <div class="flex flex-col gap-3 sm:items-end">
                    <label class="text-xs font-bold uppercase text-gray-500" for="season-select">Temporada</label>
                    <select id="season-select" class="border border-gray-400 bg-white px-3 py-2 text-sm font-semibold text-gray-900" onchange="changeSeason(this)" oninput="changeSeason(this)">
                        {selector_options}
                    </select>
                    <nav class="flex gap-2">
                        {nav_links}
                    </nav>
                </div>
            </div>
        </header>

        <section class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div class="bg-white p-5 shadow-sm border-l-4 border-indigo-500">
                <p class="text-xs font-bold uppercase text-gray-500">Tasa de Conversión</p>
                <p class="mt-2 text-3xl font-extrabold">{conversion_rate:.2f}%</p>
            </div>
            <div class="bg-white p-5 shadow-sm border-l-4 border-blue-500">
                <p class="text-xs font-bold uppercase text-gray-500">Ofertas Analizadas</p>
                <p class="mt-2 text-3xl font-extrabold">{format_int(offers)}</p>
            </div>
            <div class="bg-white p-5 shadow-sm border-l-4 border-green-500">
                <p class="text-xs font-bold uppercase text-gray-500">Fichajes Totales</p>
                <p class="mt-2 text-3xl font-extrabold">{format_int(sales)}</p>
            </div>
        </section>

        <section class="mt-8 bg-white p-5 shadow-sm">
            <h2 class="text-xl font-bold">Actividad Diaria del Mercado</h2>
            <p class="mt-1 text-sm text-gray-600">Compara las ofertas nuevas publicadas por la máquina cada día con los fichajes cerrados ese mismo día. El cruce usa la ventana real de subasta, desde creación hasta expiración con un pequeño margen por el cierre de Futmondo.</p>
            <div class="mt-4 flex justify-center">
                <img src="assets/{chart_filename}" alt="Gráfico de conversión" class="max-w-full">
            </div>
        </section>

        <section class="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-2">
            <div class="bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold text-indigo-700">Comportamiento de los Compradores</h2>
                <p class="mt-1 text-sm text-gray-600">Resume todos los fichajes de la temporada que se han podido cruzar con una oferta de la máquina. La sobrepuja media mide cuánto pagó cada comprador por encima o por debajo del precio de salida.</p>
                <div class="mt-4 overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="border-b bg-gray-50 text-xs uppercase text-gray-500">
                            <tr><th class="py-3 px-4">Comprador</th><th class="py-3 px-4 text-center">Fichajes</th><th class="py-3 px-4 text-right">Sobrepuja Media</th></tr>
                        </thead>
                        <tbody class="divide-y divide-gray-100">{buyer_rows}</tbody>
                    </table>
                </div>
            </div>
            <div class="bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold text-indigo-700">Top 10 Fichajes Más Caros</h2>
                <p class="mt-1 text-sm text-gray-600">Lista los fichajes más caros de la temporada y compara el precio pagado contra el precio de mercado que tenía el jugador al salir.</p>
                <div class="mt-4 overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="border-b bg-gray-50 text-xs uppercase text-gray-500">
                            <tr><th class="py-3 px-4">Jugador</th><th class="py-3 px-4">Comprador</th><th class="py-3 px-4 text-right">Precio</th><th class="py-3 px-4 text-right">Sobrepuja €</th><th class="py-3 px-4 text-right">Sobrepuja %</th><th class="py-3 px-4 text-right">Fecha</th></tr>
                        </thead>
                        <tbody class="divide-y divide-gray-100">{top_rows}</tbody>
                    </table>
                </div>
            </div>
        </section>

        <section class="mt-8 bg-white p-5 shadow-sm">
            <h2 class="text-xl font-bold">Mercado Actual ({len(current_market)} jugadores)</h2>
            <p class="mt-1 text-sm text-gray-600">Muestra todos los jugadores que siguen en el último snapshot del mercado y que vende la máquina, ordenados por plazo de compra más cercano y, en empate, por precio más alto. El partido mostrado es útil solo si el fichaje se puede cerrar antes del inicio de ese partido; si no, se marca como pendiente de la siguiente jornada.</p>
            <div class="mt-4 overflow-x-auto">
                <table class="min-w-full text-left text-sm">
                    <thead class="border-b bg-gray-50 text-xs uppercase text-gray-500">
                        <tr><th class="py-2 px-3">Jugador</th><th class="py-2 px-3">Equipo</th><th class="py-2 px-3">Precio</th><th class="py-2 px-3 text-center">Promedio</th><th class="py-2 px-3 text-center">Puntos</th>{lineup_headers}<th class="py-2 px-3">Expira</th></tr>
                    </thead>
                    <tbody class="divide-y divide-gray-100">{market_rows}</tbody>
                </table>
            </div>
        </section>
    </main>
    <script>
        function changeSeason(select) {{
            const target = select.value;
            const currentFile = window.location.pathname.split("/").pop();
            if (target && target !== currentFile) {{
                window.location.assign(new URL(target, window.location.href).href);
            }}
        }}
        document.getElementById("season-select").addEventListener("change", function () {{ changeSeason(this); }});
    </script>
</body>
</html>
"""

def build_market_dashboard(exports_dir, output, season, seasons):
    market_path = season_market_path(exports_dir, season)
    signings_path = season_signings_path(exports_dir, season)
    market = load_market(market_path)
    signings = load_signings(signings_path)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    chart_filename = f"conversion_chart_{season}.png"
    conversion_rate, offers, converted_sales = analyze_conversion(market, signings, ASSETS_DIR / chart_filename)
    buyer_stats = analyze_overbids(market, signings)
    matched_signings = match_signings_to_market(market, signings)
    if matched_signings.empty:
        top_signings = pd.DataFrame(
            columns=["player_name", "buyer", "price_s", "overbid", "overbid_pct", "signed_date"]
        )
    else:
        top_signings = matched_signings.sort_values("price_s", ascending=False).head(10)
    current_market = get_current_market(market)
    lineup_rows = load_lineup_rows(exports_dir, season)
    current_market = enrich_market_with_lineups(current_market, lineup_rows, overrides=load_matching_overrides())
    html = generate_html(
        season,
        seasons,
        conversion_rate,
        offers,
        len(signings),
        buyer_stats,
        top_signings,
        current_market,
        chart_filename,
        show_lineup_columns=bool(lineup_rows),
    )
    html = "\n".join(line.rstrip() for line in html.splitlines()) + "\n"
    output.write_text(html, encoding="utf-8")
    print(f"Mercado generado: {output}")
    print(f"Temporada: {season}")
    print(f"Mercado: {market_path if market_path else 'sin historico'}")
    print(f"Fichajes: {signings_path if signings_path else 'sin historico'}")
    return html


def main():
    parser = argparse.ArgumentParser(description="Genera el dashboard de mercado de Futmondo.")
    parser.add_argument("--season", default="all", help="Temporada, por ejemplo 2026_2027, o all.")
    parser.add_argument("--exports-dir", default=DEFAULT_EXPORTS_DIR, type=Path)
    parser.add_argument("--output", default=DEFAULT_OUTPUT, type=Path)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    seasons = discover_seasons(args.exports_dir) if args.season == "all" else [args.season]
    if not seasons:
        raise RuntimeError("No hay temporadas con pressroom para generar el mercado.")

    latest_season = seasons[-1]
    latest_html = None
    for season in seasons:
        season_output = args.output.with_name(f"index_{season}.html")
        html = build_market_dashboard(args.exports_dir, season_output, season, seasons)
        if season == latest_season:
            latest_html = html

    args.output.write_text(latest_html, encoding="utf-8")
    print(f"Mercado principal generado: {args.output}")


if __name__ == "__main__":
    main()
