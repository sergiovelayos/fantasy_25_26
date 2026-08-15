import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


DEFAULT_EXPORTS_DIR = Path("data/exports")
DEFAULT_OUTPUT = Path("docs/index.html")
LEGACY_MARKET_FILE = Path("data/futmondo_market.json")
LEGACY_SIGNINGS_FILE = Path("data/fichajes_hist_file/pressroom_2025_2026.json")
ASSETS_DIR = Path("docs/assets")

plt.style.use("ggplot")
sns.set_palette("husl")


def latest_file(directory, pattern):
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def esc(value):
    if pd.isna(value):
        return ""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_int(value):
    return f"{int(round(value)):,}".replace(",", ".")


def format_money(value):
    return f"{format_int(value)} €"


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
    return market.drop_duplicates(subset=["player_id", "creation_date"])


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

    if signings.empty:
        matched_sales = pd.DataFrame(columns=["creation_date"])
    else:
        merged = pd.merge(market, signings, on="player_id", suffixes=("_m", "_s"), how="left")
        merged["time_diff"] = merged["signed_date"] - merged["creation_date"]
        matched_sales = merged[
            (merged["time_diff"] >= timedelta(seconds=0)) & (merged["time_diff"] <= timedelta(hours=48))
        ].copy()

    total_offers = len(market)
    total_sales = matched_sales["creation_date"].nunique() if not matched_sales.empty else 0
    rate = (total_sales / total_offers) * 100 if total_offers else 0

    market = market.copy()
    market["date"] = market["creation_date"].dt.date
    daily_market = market.groupby("date").size().rename("Ofertas")
    if matched_sales.empty:
        daily_sales = pd.Series(dtype="float64", name="Ventas")
    else:
        matched_sales["date"] = matched_sales["creation_date"].dt.date
        daily_sales = matched_sales.groupby("date")["creation_date"].nunique().rename("Ventas")

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
    if market.empty or signings.empty:
        return pd.DataFrame(columns=["buyer", "count", "avg_overbid"])

    market_comp = market[market["computer"] == True].copy()
    market_comp = market_comp.dropna(subset=["player_id", "creation_date", "price"])
    if market_comp.empty:
        return pd.DataFrame(columns=["buyer", "count", "avg_overbid"])

    market_comp = market_comp.rename(columns={"price": "price_m"}).sort_values("creation_date")
    signed = signings.rename(columns={"price": "price_s"}).dropna(subset=["player_id", "signed_date", "price_s"])
    signed = signed.sort_values("signed_date")

    merged = pd.merge_asof(
        signed,
        market_comp,
        left_on="signed_date",
        right_on="creation_date",
        by="player_id",
        direction="backward",
        tolerance=pd.Timedelta(days=4),
    )
    matched = merged.dropna(subset=["price_m"]).copy()
    if matched.empty:
        return pd.DataFrame(columns=["buyer", "count", "avg_overbid"])
    matched["overbid_pct"] = ((matched["price_s"] - matched["price_m"]) / matched["price_m"]) * 100
    return (
        matched.groupby("buyer", as_index=False)
        .agg(count=("player_id", "count"), avg_overbid=("overbid_pct", "mean"))
        .sort_values("avg_overbid", ascending=False)
    )


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
    return current.sort_values(["expiration_date", "points"], ascending=[True, False])


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
    assistant_path = DEFAULT_OUTPUT.parent / f"asistente_alineacion_{season}_j1.html"
    if assistant_path.exists():
        links.append(("Asistente", assistant_path.name, active == "assistant"))

    rendered = []
    for label, href, is_active in links:
        cls = "bg-indigo-700 text-white" if is_active else "border border-gray-400 text-gray-800 hover:bg-white"
        rendered.append(
            f'<a class="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold {cls}" href="{href}">{label}</a>'
        )
    return "\n".join(rendered)


def table_empty(colspan, text):
    return f'<tr><td class="py-3 px-4 text-gray-500" colspan="{colspan}">{text}</td></tr>'


def generate_html(season, seasons, conversion_rate, offers, sales, buyer_stats, top_signings, current_market, chart_filename):
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
        top_rows = table_empty(4, "Sin fichajes registrados.")
    else:
        for _, row in top_signings.iterrows():
            date = row["signed_date"].strftime("%Y-%m-%d") if pd.notna(row["signed_date"]) else "-"
            top_rows += f"""
            <tr>
                <td class="py-3 px-4 font-medium">{esc(row['player_name'])}</td>
                <td class="py-3 px-4">{esc(row['buyer'])}</td>
                <td class="py-3 px-4 text-right whitespace-nowrap tabular-nums">{format_money(row['price'])}</td>
                <td class="py-3 px-4 text-right text-sm text-gray-500 whitespace-nowrap">{date}</td>
            </tr>
            """

    market_rows = ""
    if current_market.empty:
        market_rows = table_empty(6, "Sin jugadores de mercado para esta temporada.")
    else:
        for _, row in current_market.iterrows():
            exp_str = row["expiration_date"].strftime("%d/%m %H:%M") if pd.notna(row["expiration_date"]) else "-"
            avg_str = f"{row['average']:.1f}" if pd.notna(row["average"]) else "-"
            price_str = format_money(row["price"]) if pd.notna(row["price"]) else "-"
            points_str = f"{int(row['points'])}" if pd.notna(row["points"]) else "-"
            market_rows += f"""
            <tr>
                <td class="py-2 px-3 font-medium">{esc(row['name'])}</td>
                <td class="py-2 px-3 text-gray-500">{esc(row['team'])}</td>
                <td class="py-2 px-3 whitespace-nowrap tabular-nums">{price_str}</td>
                <td class="py-2 px-3 text-center tabular-nums">{avg_str}</td>
                <td class="py-2 px-3 text-center tabular-nums">{points_str}</td>
                <td class="py-2 px-3 text-sm text-orange-500 whitespace-nowrap">{exp_str}</td>
            </tr>
            """

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
            <div class="mt-4 flex justify-center">
                <img src="assets/{chart_filename}" alt="Gráfico de conversión" class="max-w-full">
            </div>
        </section>

        <section class="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-2">
            <div class="bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold text-indigo-700">Comportamiento de los Compradores</h2>
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
                <div class="mt-4 overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="border-b bg-gray-50 text-xs uppercase text-gray-500">
                            <tr><th class="py-3 px-4">Jugador</th><th class="py-3 px-4">Comprador</th><th class="py-3 px-4 text-right">Precio</th><th class="py-3 px-4 text-right">Fecha</th></tr>
                        </thead>
                        <tbody class="divide-y divide-gray-100">{top_rows}</tbody>
                    </table>
                </div>
            </div>
        </section>

        <section class="mt-8 bg-white p-5 shadow-sm">
            <h2 class="text-xl font-bold">Mercado Actual ({len(current_market)} jugadores)</h2>
            <div class="mt-4 overflow-x-auto">
                <table class="min-w-full text-left text-sm">
                    <thead class="border-b bg-gray-50 text-xs uppercase text-gray-500">
                        <tr><th class="py-2 px-3">Jugador</th><th class="py-2 px-3">Equipo</th><th class="py-2 px-3">Precio</th><th class="py-2 px-3 text-center">Media</th><th class="py-2 px-3 text-center">Puntos</th><th class="py-2 px-3">Expira</th></tr>
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
    top_signings = signings.sort_values("price", ascending=False).head(10) if not signings.empty else signings
    current_market = get_current_market(market)
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
    )
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
