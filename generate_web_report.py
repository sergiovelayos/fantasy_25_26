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
PREVIOUS_PLAYER_FILES = {
    "2026_2027": LEGACY_MARKET_FILE,
}

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
    if pd.isna(value):
        return "-"
    return f"{value / 1_000_000:.1f} M€"


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
                "role",
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
                    "role": player.get("role"),
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
        return pd.DataFrame(columns=["player_id", "player_name", "buyer", "seller", "price", "signed_date"])

    raw = json.load(path.open(encoding="utf-8"))
    rows = []
    for item in raw.get("answer", {}).get("news", []):
        buyer = item.get("_buyer") or {}
        seller = item.get("_seller") or {}
        player = item.get("_player") or {}
        rows.append(
            {
                "player_id": player.get("_id"),
                "player_name": player.get("name"),
                "buyer": buyer.get("name"),
                "seller": seller.get("name"),
                "price": item.get("price"),
                "signed_date": item.get("created"),
            }
        )
    signings = pd.DataFrame(rows)
    if signings.empty:
        return signings
    signings["signed_date"] = pd.to_datetime(signings["signed_date"], utc=True, errors="coerce")
    signings["price"] = pd.to_numeric(signings["price"], errors="coerce")
    return signings


def load_previous_players(season):
    path = PREVIOUS_PLAYER_FILES.get(season)
    if not path or not path.exists():
        return pd.DataFrame(columns=["prev_player_id", "prev_name_norm", "prev_points", "prev_average"])

    rows = []
    if path.suffix == ".csv":
        previous = pd.read_csv(path)
        average_column = "average.average" if "average.average" in previous.columns else "average"
        matches_column = "average.matches" if "average.matches" in previous.columns else None
        required = {"id", "name", "points", average_column}
        if not required.issubset(previous.columns):
            return pd.DataFrame(columns=["prev_player_id", "prev_name_norm", "prev_points", "prev_average"])
        for _, player in previous.iterrows():
            rows.append(
                {
                    "prev_player_id": player.get("id"),
                    "prev_name_norm": normalize_name(player.get("name")),
                    "prev_points": player.get("points"),
                    "prev_matches": player.get(matches_column) if matches_column else pd.NA,
                    "prev_average_raw": player.get(average_column),
                }
            )
    else:
        raw = json.load(path.open(encoding="utf-8"))
        for entry in raw:
            players = entry.get("jugadores") if isinstance(entry, dict) else None
            if players is None:
                players = [entry]
            for player in players:
                if not isinstance(player, dict):
                    continue
                average = player.get("average") or {}
                rows.append(
                    {
                        "prev_player_id": player.get("id"),
                        "prev_name_norm": normalize_name(player.get("name")),
                        "prev_points": player.get("points"),
                        "prev_matches": average.get("matches") if isinstance(average, dict) else pd.NA,
                        "prev_average_raw": average.get("average") if isinstance(average, dict) else average,
                    }
                )

    previous = pd.DataFrame(rows)
    if previous.empty:
        return pd.DataFrame(columns=["prev_player_id", "prev_name_norm", "prev_points", "prev_average"])

    previous["prev_points"] = pd.to_numeric(previous["prev_points"], errors="coerce")
    previous["prev_matches"] = pd.to_numeric(previous["prev_matches"], errors="coerce")
    previous["prev_average_raw"] = pd.to_numeric(previous["prev_average_raw"], errors="coerce")
    previous = previous.dropna(subset=["prev_player_id", "prev_points"])
    previous = previous.sort_values(["prev_player_id", "prev_points", "prev_matches"], ascending=[True, False, False])
    previous = previous.drop_duplicates(subset=["prev_player_id"], keep="first").copy()
    previous["prev_average"] = previous["prev_points"] / previous["prev_matches"]
    previous.loc[previous["prev_matches"].isna() | (previous["prev_matches"] <= 0), "prev_average"] = previous[
        "prev_average_raw"
    ]
    return previous[["prev_player_id", "prev_name_norm", "prev_points", "prev_average"]]


def enrich_market_with_previous_season(current_market, previous_players):
    market = current_market.copy()
    market["prev_points"] = pd.NA
    market["prev_average"] = pd.NA
    if market.empty or previous_players.empty:
        return market

    by_id = previous_players.set_index("prev_player_id")
    by_name = previous_players.drop_duplicates(subset=["prev_name_norm"], keep="last").set_index("prev_name_norm")
    for index, row in market.iterrows():
        prev_row = None
        player_id = row.get("player_id")
        if player_id in by_id.index:
            prev_row = by_id.loc[player_id]
        else:
            name_norm = normalize_name(row.get("name"))
            if name_norm in by_name.index:
                prev_row = by_name.loc[name_norm]
        if prev_row is None:
            continue
        market.at[index, "prev_points"] = prev_row.get("prev_points")
        market.at[index, "prev_average"] = prev_row.get("prev_average")
    return market


def analyze_conversion(market, signings):
    if market.empty:
        return 0, 0, 0

    offers = unique_market_offers(market)
    matched_sales = match_signings_to_market(market, signings)

    total_offers = len(offers)
    total_sales = len(matched_sales)
    rate = (total_sales / total_offers) * 100 if total_offers else 0
    return rate, total_offers, total_sales


def build_activity_chart(signings, chart_path):
    if signings.empty:
        make_empty_chart(chart_path, "Sin movimientos de mercado")
        return

    movements = signings.dropna(subset=["signed_date"]).copy()
    movements = movements[(movements["buyer"].notna()) | (movements["seller"].notna())].copy()
    if movements.empty:
        make_empty_chart(chart_path, "Sin movimientos de mercado")
        return

    movements["date"] = movements["signed_date"].dt.date
    daily_movements = movements.groupby("date").size().tail(30)
    x_positions = list(range(len(daily_movements)))

    plt.figure(figsize=(10, 5))
    ax = plt.gca()
    ax.plot(x_positions, daily_movements.values, marker="o", color="#4f46e5", linewidth=2.5)
    ax.fill_between(x_positions, daily_movements.values, alpha=0.12, color="#4f46e5")
    ax.set_xticks(x_positions)
    ax.set_xticklabels([date.strftime("%d/%m") for date in daily_movements.index], rotation=45)
    plt.title("Fichajes diarios")
    plt.ylabel("Movimientos")
    plt.xlabel("Fecha")
    plt.grid(True, alpha=0.25)
    plt.tight_layout()
    plt.savefig(chart_path)
    plt.close()


def make_empty_chart(chart_path, title):
    plt.figure(figsize=(10, 5))
    plt.title(title)
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(chart_path)
    plt.close()


def analyze_overbids(market, signings, current_values=None):
    matched = match_signings_to_market(market, signings, current_values=current_values, include_fallback=True)
    if matched.empty:
        return pd.DataFrame(columns=["buyer", "count", "avg_overbid", "spend", "income"])

    buyer_stats = (
        matched.groupby("buyer", as_index=False)
        .agg(count=("player_id", "count"), avg_overbid=("overbid_pct", "mean"), spend=("price_s", "sum"))
        .sort_values("buyer")
    )
    sales = signings.dropna(subset=["seller"]).copy()
    if sales.empty:
        buyer_stats["income"] = 0
        return buyer_stats
    income = sales.groupby("seller", as_index=False).agg(income=("price", "sum")).rename(columns={"seller": "buyer"})
    buyer_stats = buyer_stats.merge(income, on="buyer", how="left")
    buyer_stats["income"] = buyer_stats["income"].fillna(0)
    return buyer_stats.sort_values("buyer")


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


def match_signings_to_market(market, signings, current_values=None, include_fallback=False):
    if market.empty or signings.empty:
        return pd.DataFrame(
            columns=[
                "buyer",
                "player_id",
                "player_name",
                "price_s",
                "market_price",
                "overbid",
                "overbid_pct",
                "signed_date",
                "price_source",
            ]
        )

    market_comp = unique_market_offers(market)
    if market_comp.empty:
        return pd.DataFrame(
            columns=[
                "buyer",
                "player_id",
                "player_name",
                "price_s",
                "market_price",
                "overbid",
                "overbid_pct",
                "signed_date",
                "price_source",
            ]
        )

    market_comp = market_comp.rename(columns={"price": "price_m"}).sort_values("creation_date")
    signed = signings.dropna(subset=["buyer"]).rename(columns={"price": "price_s"}).dropna(
        subset=["player_id", "signed_date", "price_s"]
    )
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
    matched["market_price"] = matched["price_m"]
    matched["price_source"] = "mercado"

    if include_fallback:
        matched_keys = set(zip(matched.get("player_id", pd.Series(dtype=object)), matched.get("signed_date", pd.Series(dtype=object))))
        missing = signed[~signed.apply(lambda row: (row["player_id"], row["signed_date"]) in matched_keys, axis=1)].copy()
        if current_values is not None and not current_values.empty and not missing.empty:
            missing = missing.merge(current_values, on="player_id", how="left")
            missing = missing.dropna(subset=["current_value"]).copy()
            missing["market_price"] = missing["current_value"]
            missing["price_source"] = "valor actual"
            matched = pd.concat([matched, missing], ignore_index=True, sort=False)

    if matched.empty:
        return pd.DataFrame(
            columns=[
                "buyer",
                "player_id",
                "player_name",
                "price_s",
                "market_price",
                "overbid",
                "overbid_pct",
                "signed_date",
                "price_source",
            ]
        )
    matched["overbid"] = matched["price_s"] - matched["market_price"]
    matched["overbid_pct"] = (matched["overbid"] / matched["market_price"]) * 100
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


def load_current_player_values(exports_dir, season):
    lineup_dir = exports_dir / season / "lineup_assistant"
    path = latest_file(lineup_dir, "championshipplayers_*.json") if lineup_dir.exists() else None
    if not path:
        return pd.DataFrame(columns=["player_id", "current_value"])
    raw = json.load(path.open(encoding="utf-8"))
    rows = []
    for player in raw.get("answer", {}).get("players", []):
        rows.append(
            {
                "player_id": player.get("id"),
                "current_value": player.get("value"),
            }
        )
    values = pd.DataFrame(rows)
    if values.empty:
        return values
    values["current_value"] = pd.to_numeric(values["current_value"], errors="coerce")
    return values.dropna(subset=["player_id", "current_value"]).drop_duplicates(subset=["player_id"], keep="last")


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
    show_previous_columns=False,
):
    buyer_rows = ""
    if buyer_stats.empty:
        buyer_rows = table_empty(5, "Sin fichajes registrados para calcular comportamiento.")
    else:
        for _, row in buyer_stats.iterrows():
            tone = "text-green-600" if row["avg_overbid"] > 0 else "text-red-600"
            buyer_rows += f"""
            <tr>
                <td class="py-3 px-4 font-medium">{esc(row['buyer'])}</td>
                <td class="py-3 px-4 text-center tabular-nums">{int(row['count'])}</td>
                <td class="py-3 px-4 text-right tabular-nums">{format_money(row['spend'])}</td>
                <td class="py-3 px-4 text-right tabular-nums">{format_money(row['income'])}</td>
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
    market_colspan = 11 if show_lineup_columns else 7
    if show_previous_columns:
        market_colspan += 2
    if current_market.empty:
        market_rows = table_empty(market_colspan, "Sin jugadores de mercado para esta temporada.")
    else:
        for _, row in current_market.iterrows():
            exp_str = row["expiration_date"].strftime("%d/%m %H:%M") if pd.notna(row["expiration_date"]) else "-"
            avg_str = f"{row['average']:.1f}" if pd.notna(row["average"]) else "-"
            price_str = format_money(row["price"]) if pd.notna(row["price"]) else "-"
            points_str = f"{int(row['points'])}" if pd.notna(row["points"]) else "-"
            previous_cells = ""
            if show_previous_columns:
                prev_points = f"{row['prev_points']:.1f}" if pd.notna(row.get("prev_points")) else "-"
                prev_average = f"{row['prev_average']:.1f}" if pd.notna(row.get("prev_average")) else "-"
                prev_points_sort = row["prev_points"] if pd.notna(row.get("prev_points")) else -1
                prev_average_sort = row["prev_average"] if pd.notna(row.get("prev_average")) else -1
                previous_cells = f"""
                <td class="py-2 px-3 text-center tabular-nums" data-sort-value="{prev_points_sort}">{prev_points}</td>
                <td class="py-2 px-3 text-center tabular-nums" data-sort-value="{prev_average_sort}">{prev_average}</td>
                """.strip()
            lineup_cells = ""
            if show_lineup_columns:
                ff_prob = f"{int(row['ff_probability'])}%" if row.get("ff_probability") != "" and pd.notna(row.get("ff_probability")) else "-"
                ff_context = " · ".join(part for part in [row.get("ff_match"), row.get("ff_home_away")] if part)
                ff_prob_sort = row.get("ff_probability") if row.get("ff_probability") != "" and pd.notna(row.get("ff_probability")) else -1
                lineup_cells = f"""
                <td class="py-2 px-3" data-sort-value="{esc(row.get('ff_status'))}">{esc(row.get('ff_status'))}</td>
                <td class="py-2 px-3 text-center tabular-nums" data-sort-value="{ff_prob_sort}">{ff_prob}</td>
                <td class="py-2 px-3 text-gray-500" data-sort-value="{esc(ff_context)}">{esc(ff_context)}</td>
                <td class="py-2 px-3 font-semibold" data-sort-value="{esc(row.get('ff_recommendation'))}">{esc(row.get('ff_recommendation'))}</td>
                """.strip()
            price_sort = row["price"] if pd.notna(row["price"]) else -1
            avg_sort = row["average"] if pd.notna(row["average"]) else -1
            points_sort = row["points"] if pd.notna(row["points"]) else -1
            exp_sort = row["expiration_date"].timestamp() if pd.notna(row["expiration_date"]) else 0
            market_rows += f"""
            <tr>
                <td class="py-2 px-3 font-medium" data-sort-value="{esc(row['name'])}">{esc(row['name'])}</td>
                <td class="py-2 px-3 text-gray-500" data-sort-value="{esc(row['team'])}">{esc(row['team'])}</td>
                <td class="py-2 px-3 text-gray-500" data-sort-value="{esc(row.get('role'))}">{esc(row.get('role'))}</td>
                <td class="py-2 px-3 whitespace-nowrap tabular-nums" data-sort-value="{price_sort}">{price_str}</td>
                <td class="py-2 px-3 text-center tabular-nums" data-sort-value="{avg_sort}">{avg_str}</td>
                <td class="py-2 px-3 text-center tabular-nums" data-sort-value="{points_sort}">{points_str}</td>
{previous_cells}
{lineup_cells}
                <td class="py-2 px-3 text-sm text-orange-500 whitespace-nowrap" data-sort-value="{exp_sort}">{exp_str}</td>
            </tr>
            """.strip()
    lineup_headers = ""
    if show_lineup_columns:
        lineup_headers = '<th class="py-2 px-3" data-sort-type="text">Estado FF</th><th class="py-2 px-3 text-center" data-sort-type="number">Prob.</th><th class="py-2 px-3" data-sort-type="text">Partido</th><th class="py-2 px-3" data-sort-type="text">Consejo</th>'
    previous_headers = ""
    if show_previous_columns:
        previous_headers = '<th class="py-2 px-3 text-center" data-sort-type="number">Pts 25-26</th><th class="py-2 px-3 text-center" data-sort-type="number">Prom. 25-26</th>'

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
        th[data-sort-type] {{ cursor: pointer; user-select: none; white-space: nowrap; }}
        th[data-sort-type]::after {{ content: "↕"; margin-left: 0.35rem; color: #9ca3af; font-size: 0.75rem; }}
        th[data-sort-dir="asc"]::after {{ content: "↑"; color: #4338ca; }}
        th[data-sort-dir="desc"]::after {{ content: "↓"; color: #4338ca; }}
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
                <p class="mt-2 text-xs leading-5 text-gray-600">Fichajes comprados a la máquina que se pueden cruzar con una oferta diaria, dividido entre el total de ofertas de la máquina analizadas.</p>
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

        <section class="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-2">
            <div class="bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold text-indigo-700">Comportamiento de los Compradores</h2>
                <p class="mt-1 text-sm text-gray-600">Incluye todos los fichajes con comprador desde que hay pressroom esta temporada. Si existe oferta diaria se usa su precio; si no existe, la sobrepuja se aproxima contra el valor actual del jugador. Gasto suma compras e ingresos suma ventas a la máquina.</p>
                <div class="mt-4 overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="border-b bg-gray-50 text-xs uppercase text-gray-500">
                            <tr><th class="py-3 px-4">Comprador</th><th class="py-3 px-4 text-center">Fichajes</th><th class="py-3 px-4 text-right">Gasto</th><th class="py-3 px-4 text-right">Ingresos</th><th class="py-3 px-4 text-right">Sobrepuja Media</th></tr>
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
                <table id="current-market-table" class="min-w-full text-left text-sm">
                    <thead class="border-b bg-gray-50 text-xs uppercase text-gray-500">
                        <tr><th class="py-2 px-3" data-sort-type="text">Jugador</th><th class="py-2 px-3" data-sort-type="text">Equipo</th><th class="py-2 px-3" data-sort-type="text">Posición</th><th class="py-2 px-3" data-sort-type="number">Precio</th><th class="py-2 px-3 text-center" data-sort-type="number">Promedio</th><th class="py-2 px-3 text-center" data-sort-type="number">Puntos</th>{previous_headers}{lineup_headers}<th class="py-2 px-3" data-sort-type="number">Expira</th></tr>
                    </thead>
                    <tbody class="divide-y divide-gray-100">{market_rows}</tbody>
                </table>
            </div>
        </section>

        <section class="mt-8 bg-white p-5 shadow-sm">
            <h2 class="text-xl font-bold">Actividad Diaria del Mercado</h2>
            <p class="mt-1 text-sm text-gray-600">Cuenta los movimientos diarios registrados en prensa: compras de jugadores humanos y ventas de jugadores humanos a la máquina. Es actividad real del pressroom, no ofertas publicadas.</p>
            <div class="mt-4 flex justify-center">
                <img src="assets/{chart_filename}" alt="Gráfico de actividad diaria" class="max-w-full">
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
        function cellValue(row, index, type) {{
            const cell = row.cells[index];
            const raw = cell.dataset.sortValue || cell.textContent.trim();
            if (type === "number") {{
                const parsed = Number(String(raw).replace(/[^0-9,.-]/g, "").replace(",", "."));
                return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
            }}
            return raw.toLocaleLowerCase("es");
        }}
        function sortTable(table, columnIndex, type, direction) {{
            const tbody = table.tBodies[0];
            const rows = Array.from(tbody.rows);
            rows.sort((left, right) => {{
                const leftValue = cellValue(left, columnIndex, type);
                const rightValue = cellValue(right, columnIndex, type);
                if (leftValue < rightValue) return direction === "asc" ? -1 : 1;
                if (leftValue > rightValue) return direction === "asc" ? 1 : -1;
                return 0;
            }});
            rows.forEach(row => tbody.appendChild(row));
        }}
        document.querySelectorAll("#current-market-table th[data-sort-type]").forEach((header, index) => {{
            header.addEventListener("click", () => {{
                const table = header.closest("table");
                const currentDirection = header.dataset.sortDir || "";
                const nextDirection = currentDirection === "asc" ? "desc" : "asc";
                table.querySelectorAll("th[data-sort-type]").forEach(cell => cell.removeAttribute("data-sort-dir"));
                header.dataset.sortDir = nextDirection;
                sortTable(table, index, header.dataset.sortType, nextDirection);
            }});
        }});
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
    conversion_rate, offers, converted_sales = analyze_conversion(market, signings)
    build_activity_chart(signings, ASSETS_DIR / chart_filename)
    current_values = load_current_player_values(exports_dir, season)
    buyer_stats = analyze_overbids(market, signings, current_values=current_values)
    matched_signings = match_signings_to_market(market, signings, current_values=current_values, include_fallback=True)
    if matched_signings.empty:
        top_signings = pd.DataFrame(
            columns=["player_name", "buyer", "price_s", "overbid", "overbid_pct", "signed_date"]
        )
    else:
        top_signings = matched_signings.sort_values("price_s", ascending=False).head(10)
    current_market = get_current_market(market)
    previous_players = load_previous_players(season)
    current_market = enrich_market_with_previous_season(current_market, previous_players)
    lineup_rows = load_lineup_rows(exports_dir, season)
    current_market = enrich_market_with_lineups(current_market, lineup_rows, overrides=load_matching_overrides())
    html = generate_html(
        season,
        seasons,
        conversion_rate,
        offers,
        int(signings["buyer"].notna().sum()) if not signings.empty else 0,
        buyer_stats,
        top_signings,
        current_market,
        chart_filename,
        show_lineup_columns=bool(lineup_rows),
        show_previous_columns=not previous_players.empty,
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
