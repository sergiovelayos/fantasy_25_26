import argparse
import html
import json
from datetime import datetime
from pathlib import Path

import pandas as pd


DEFAULT_SEASON = "all"
DEFAULT_EXPORTS_DIR = Path("data/exports")
DEFAULT_OUTPUT = Path("docs/resumen_liga.html")
DEFAULT_MARKET_FILE = Path("data/futmondo_market.json")


def latest_file(directory, pattern):
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No se encontro ningun fichero {pattern} en {directory}")
    return files[0]


def format_int(value):
    return f"{int(round(value)):,}".replace(",", ".")


def format_points(value):
    return f"{float(value):.1f}".replace(".", ",")


def format_money(value):
    return f"{format_int(value)} €"


def format_money_compact(value):
    return f"{format_int(value / 1_000_000)} M€"


def esc(value):
    if pd.isna(value):
        return ""
    return html.escape(str(value))


def table_rows(rows, columns):
    rendered = []
    for row in rows:
        cells = []
        for key, align in columns:
            cls = "px-4 py-3"
            if align == "right":
                cls += " whitespace-nowrap text-right tabular-nums"
            elif align == "center":
                cls += " text-center tabular-nums"
            cells.append(f'<td class="{cls}">{row[key]}</td>')
        rendered.append(f"<tr>{''.join(cells)}</tr>")
    return "\n".join(rendered)


def load_market_history(path):
    if not path.exists():
        return pd.DataFrame(columns=["player_id", "name", "creation_date", "price", "computer"])

    raw = json.load(path.open(encoding="utf-8"))
    rows = []
    for entry in raw:
        players = entry.get("jugadores") if isinstance(entry, dict) else None
        if players is None:
            players = [entry]
        for player in players:
            if not isinstance(player, dict):
                continue
            rows.append(
                {
                    "player_id": player.get("id"),
                    "name": player.get("name"),
                    "creation_date": player.get("creationDate"),
                    "price": player.get("price"),
                    "computer": player.get("computer", False) or (player.get("userTeam") is None),
                }
            )

    market = pd.DataFrame(rows)
    if market.empty:
        return pd.DataFrame(columns=["player_id", "name", "creation_date", "price", "computer"])
    market["creation_date"] = pd.to_datetime(market["creation_date"], utc=True, errors="coerce")
    market["price"] = pd.to_numeric(market["price"], errors="coerce")
    market = market.dropna(subset=["player_id", "creation_date", "price"])
    return market.drop_duplicates(subset=["player_id", "creation_date"])


def build_market_insights(signings, season, market_file=DEFAULT_MARKET_FILE):
    top_signings = signings.sort_values(["price", "created"], ascending=[False, True]).head(10)
    top_rows = [
        {
            "player": esc(row["player_name"]),
            "buyer": esc(row["buyer_name"]),
            "price": format_money(row["price"]),
            "date": pd.to_datetime(row["created"], errors="coerce").strftime("%d/%m/%Y"),
        }
        for _, row in top_signings.iterrows()
    ]

    insights = {
        "show": season == "2025_2026",
        "buyer_rows": [],
        "top_rows": top_rows,
        "top_overbid_rows": [],
    }
    if season != "2025_2026":
        return insights

    market = load_market_history(market_file)
    if market.empty:
        return insights

    market = market[market["computer"] == True].copy()
    if market.empty:
        return insights

    market = market.rename(columns={"price": "market_price"}).sort_values("creation_date")
    signed = signings.rename(columns={"price": "signed_price"}).copy()
    signed["signed_date"] = pd.to_datetime(signed["created"], utc=True, errors="coerce")
    signed = signed.dropna(subset=["player_id", "signed_date", "signed_price"]).sort_values("signed_date")

    matched = pd.merge_asof(
        signed,
        market,
        left_on="signed_date",
        right_on="creation_date",
        by="player_id",
        direction="backward",
        tolerance=pd.Timedelta(days=4),
    ).dropna(subset=["market_price"])

    if matched.empty:
        return insights

    matched["overbid_pct"] = ((matched["signed_price"] - matched["market_price"]) / matched["market_price"]) * 100
    buyer_stats = (
        matched.groupby("buyer_name", as_index=False)
        .agg(signings=("player_id", "count"), avg_overbid=("overbid_pct", "mean"))
        .sort_values(["avg_overbid", "signings"], ascending=[False, False])
    )
    insights["buyer_rows"] = [
        {
            "buyer": esc(row["buyer_name"]),
            "signings": str(int(row["signings"])),
            "overbid": f"{row['avg_overbid']:+.2f}%".replace(".", ","),
            "tone": "text-emerald-700" if row["avg_overbid"] >= 0 else "text-rose-700",
        }
        for _, row in buyer_stats.iterrows()
    ]
    top_overbids = matched.sort_values(
        ["overbid_pct", "signed_price", "signed_date"], ascending=[False, False, True]
    ).head(10)
    insights["top_overbid_rows"] = [
        {
            "player": esc(row["player_name"]),
            "buyer": esc(row["buyer_name"]),
            "market_price": format_money(row["market_price"]),
            "signed_price": format_money(row["signed_price"]),
            "overbid": f"{row['overbid_pct']:+.2f}%".replace(".", ","),
            "date": row["signed_date"].strftime("%d/%m/%Y"),
        }
        for _, row in top_overbids.iterrows()
    ]
    return insights


def build_summary(rankings_path, signings_path, season):
    has_rankings = rankings_path is not None
    if has_rankings:
        rankings = pd.read_csv(rankings_path)
        general_path = latest_file(rankings_path.parent, "clasificacion_general_*.csv")
        general = pd.read_csv(general_path)
        rankings["round_number"] = pd.to_numeric(rankings["round_number"])
        rankings["points"] = pd.to_numeric(rankings["points"])
    else:
        rankings = pd.DataFrame(columns=["round_number", "points", "userteam_name"])
        general_path = None
        general = pd.DataFrame(columns=["userteam_id", "userteam_name"])

    signings = pd.read_csv(signings_path)
    pressroom_path = latest_file(signings_path.parent, "pressroom_all_*.json")

    signings = signings[signings["buyer_name"].notna() & (signings["buyer_name"].str.strip() != "")].copy()
    signings["price"] = pd.to_numeric(signings["price"])
    market_insights = build_market_insights(signings, season)

    winner_rows = []
    winner_counts = {}
    cumulative_rankings = []
    for round_number, group in rankings.groupby("round_number"):
        max_points = group["points"].max()
        winners = group[group["points"] == max_points].sort_values("userteam_name")
        for _, winner in winners.iterrows():
            name = winner["userteam_name"]
            winner_counts[name] = winner_counts.get(name, 0) + 1
        winner_rows.append(
            {
                "round": int(round_number),
                "winners": ", ".join(esc(name) for name in winners["userteam_name"]),
                "points": format_points(max_points),
            }
        )

    if has_rankings:
        round_numbers = sorted(rankings["round_number"].unique())
        for round_number in round_numbers:
            cumulative = (
                rankings[rankings["round_number"] <= round_number]
                .groupby(["userteam_id", "userteam_name"], as_index=False)["points"]
                .sum()
                .sort_values(["points", "userteam_name"], ascending=[False, True])
                .reset_index(drop=True)
            )
            for index, row in cumulative.iterrows():
                cumulative_rankings.append(
                    {
                        "round": int(round_number),
                        "rank": index + 1,
                        "team": row["userteam_name"],
                        "points": round(float(row["points"]), 1),
                    }
                )

    winner_count_rows = [{"team": team, "wins": wins} for team, wins in winner_counts.items()]
    if winner_count_rows:
        winner_count_rows = (
            pd.DataFrame(winner_count_rows)
            .sort_values(["wins", "team"], ascending=[False, True])
            .to_dict("records")
        )

    half_winners = []
    if has_rankings:
        rankings["half"] = rankings["round_number"].apply(
            lambda value: "Primera vuelta" if value <= 19 else "Segunda vuelta"
        )
        half_totals = (
            rankings.groupby(["half", "userteam_name"], as_index=False)["points"]
            .sum()
            .sort_values(["half", "points"], ascending=[True, False])
        )
        for half, group in half_totals.groupby("half", sort=False):
            max_points = group["points"].max()
            winners = group[group["points"] == max_points]
            half_winners.append(
                {
                    "half": half,
                    "winner": ", ".join(esc(name) for name in winners["userteam_name"]),
                    "points": format_points(max_points),
                }
            )

    market = (
        signings.groupby("buyer_name", as_index=False)
        .agg(
            signings=("id", "count"),
            spend=("price", "sum"),
            avg_price=("price", "mean"),
            top_price=("price", "max"),
        )
        .sort_values(["spend", "signings"], ascending=[False, False])
    )
    top_signings = (
        signings.sort_values(["buyer_name", "price", "player_name"], ascending=[True, False, True])
        .groupby("buyer_name", as_index=False)
        .first()[["buyer_name", "player_name"]]
        .rename(columns={"player_name": "top_player"})
    )
    market = market.merge(top_signings, on="buyer_name", how="left")

    pressroom = json.load(pressroom_path.open(encoding="utf-8"))["answer"]["news"]
    sales_rows = []
    for item in pressroom:
        seller = item.get("_seller") or {}
        seller_name = seller.get("name")
        if not seller_name:
            continue
        sales_rows.append(
            {
                "seller_name": seller_name,
                "sale_id": item.get("_id"),
                "player_name": (item.get("_player") or {}).get("name"),
                "price": item.get("price") or 0,
            }
        )
    sales = pd.DataFrame(sales_rows)
    if sales.empty:
        sales = pd.DataFrame(columns=["seller_name", "sale_id", "player_name", "price"])
    sales["price"] = pd.to_numeric(sales["price"])
    sales_market = (
        sales.groupby("seller_name", as_index=False)
        .agg(
            sales=("sale_id", "count"),
            income=("price", "sum"),
            avg_sale=("price", "mean"),
            top_sale=("price", "max"),
        )
        .sort_values(["sales", "income"], ascending=[False, False])
    )
    top_sales = (
        sales.sort_values(["seller_name", "price", "player_name"], ascending=[True, False, True])
        .groupby("seller_name", as_index=False)
        .first()[["seller_name", "player_name"]]
        .rename(columns={"player_name": "top_sale_player"})
    )
    sales_market = sales_market.merge(top_sales, on="seller_name", how="left")

    team_rows = []
    max_wins = max(winner_counts.values()) if winner_counts else 1
    max_spend = market["spend"].max() if not market.empty else 1
    max_signings = market["signings"].max() if not market.empty else 1
    market_by_team = market.set_index("buyer_name").to_dict("index")
    sales_by_team = sales_market.set_index("seller_name").to_dict("index")
    all_teams = sorted(
        set(general["userteam_name"])
        | set(rankings["userteam_name"])
        | set(market["buyer_name"])
        | set(sales_market["seller_name"])
    )
    for team in all_teams:
        info = market_by_team.get(team, {})
        sale_info = sales_by_team.get(team, {})
        wins = winner_counts.get(team, 0)
        signings_count = int(info.get("signings", 0))
        spend = float(info.get("spend", 0))
        sale_count = int(sale_info.get("sales", 0))
        income = float(sale_info.get("income", 0))
        team_rows.append(
            {
                "team": esc(team),
                "wins": wins,
                "wins_bar": round((wins / max_wins) * 100, 1),
                "signings": signings_count,
                "signings_bar": round((signings_count / max_signings) * 100, 1),
                "spend": spend,
                "sales": sale_count,
                "income": income,
                "net": income - spend,
                "spend_bar": round((spend / max_spend) * 100, 1),
                "avg_price": float(info.get("avg_price", 0)),
                "top_price": float(info.get("top_price", 0)),
            }
        )
    team_rows.sort(key=lambda row: (-row["wins"], -row["spend"], row["team"]))

    return {
        "rankings_path": rankings_path,
        "signings_path": signings_path,
        "general_path": general_path,
        "pressroom_path": pressroom_path,
        "has_rankings": has_rankings,
        "winner_count_rows": winner_count_rows,
        "winner_rows": winner_rows,
        "cumulative_rankings": cumulative_rankings,
        "half_winners": half_winners,
        "market_rows": market.to_dict("records"),
        "market_insights": market_insights,
        "sales_rows": sales_market.to_dict("records"),
        "team_rows": team_rows,
        "rounds": rankings["round_number"].nunique() if has_rankings else 0,
        "teams_general": general["userteam_id"].nunique() if has_rankings else len(all_teams),
        "total_signings": len(signings),
        "total_spend": signings["price"].sum(),
        "total_sales": len(sales),
        "total_income": sales["price"].sum(),
    }


def discover_seasons(exports_dir):
    seasons = []
    for directory in exports_dir.iterdir():
        if not directory.is_dir():
            continue
        has_signings = any(directory.glob("fichajes_all_*.csv"))
        if has_signings:
            seasons.append(directory.name)
    return sorted(seasons)


def render_season_selector(seasons, current_season):
    options = []
    for season in seasons:
        selected = " selected" if season == current_season else ""
        label = season.replace("_", "-")
        options.append(f'<option value="resumen_liga_{season}.html"{selected}>{label}</option>')
    return "\n".join(options)


def render_dashboard(summary, season, seasons=None):
    has_rankings = summary["has_rankings"]
    winner_count_rows = table_rows(
        [
            {
                "team": esc(row["team"]),
                "wins": str(row["wins"]),
            }
            for row in summary["winner_count_rows"]
        ],
        [("team", "left"), ("wins", "center")],
    )

    half_rows = table_rows(
        summary["half_winners"],
        [("half", "left"), ("winner", "left"), ("points", "right")],
    )

    round_rows = table_rows(
        [
            {
                "round": str(row["round"]),
                "winners": row["winners"],
                "points": row["points"],
            }
            for row in summary["winner_rows"]
        ],
        [("round", "center"), ("winners", "left"), ("points", "right")],
    )
    cumulative_json = json.dumps(summary["cumulative_rankings"], ensure_ascii=False)
    max_round = summary["rounds"]

    market_rows = table_rows(
        [
            {
                "team": esc(row["buyer_name"]),
                "signings": str(int(row["signings"])),
                "avg": format_money(row["avg_price"]),
                "top": f"{esc(row['top_player'])} · {format_money(row['top_price'])}",
            }
            for row in summary["market_rows"]
        ],
        [("team", "left"), ("signings", "center"), ("avg", "right"), ("top", "right")],
    )

    sales_rows = table_rows(
        [
            {
                "team": esc(row["seller_name"]),
                "sales": str(int(row["sales"])),
                "avg": format_money(row["avg_sale"]),
                "top": f"{esc(row['top_sale_player'])} · {format_money(row['top_sale'])}",
            }
            for row in summary["sales_rows"]
        ],
        [("team", "left"), ("sales", "center"), ("avg", "right"), ("top", "right")],
    )

    insights = summary["market_insights"]
    buyer_behavior_rows = "\n".join(
        f"""
        <tr>
            <td class="px-4 py-3 font-semibold">{row['buyer']}</td>
            <td class="px-4 py-3 text-center tabular-nums">{row['signings']}</td>
            <td class="px-4 py-3 text-right font-bold tabular-nums {row['tone']}">{row['overbid']}</td>
        </tr>
        """
        for row in insights["buyer_rows"]
    )
    if not buyer_behavior_rows:
        buyer_behavior_rows = """
        <tr>
            <td class="px-4 py-3 text-slate-500" colspan="3">No hay cruces suficientes con el histórico diario del mercado.</td>
        </tr>
        """

    top_market_rows = table_rows(
        insights["top_rows"],
        [("player", "left"), ("buyer", "left"), ("price", "right"), ("date", "right")],
    )
    top_overbid_rows = table_rows(
        insights["top_overbid_rows"],
        [
            ("player", "left"),
            ("buyer", "left"),
            ("market_price", "right"),
            ("signed_price", "right"),
            ("overbid", "right"),
            ("date", "right"),
        ],
    )
    if not top_overbid_rows:
        top_overbid_rows = """
        <tr>
            <td class="px-4 py-3 text-slate-500" colspan="6">No hay cruces suficientes con el histórico diario del mercado.</td>
        </tr>
        """
    market_insights_section = ""
    if insights["show"]:
        market_insights_section = f"""
        <section class="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-2">
            <div class="bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold">Comportamiento de los Compradores</h2>
                <div class="mt-4 overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                            <tr><th class="px-4 py-3">Comprador</th><th class="px-4 py-3 text-center">Fichajes</th><th class="px-4 py-3 text-right">Sobrepuja media</th></tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">{buyer_behavior_rows}</tbody>
                    </table>
                </div>
            </div>
            <div class="bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold">Top 10 Fichajes Más Caros</h2>
                <div class="mt-4 overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                            <tr><th class="px-4 py-3">Jugador</th><th class="px-4 py-3">Comprador</th><th class="px-4 py-3 text-right">Precio</th><th class="px-4 py-3 text-right">Fecha</th></tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">{top_market_rows}</tbody>
                    </table>
                </div>
            </div>
        </section>
        <section class="mt-8 bg-white p-5 shadow-sm">
            <h2 class="text-xl font-bold">Top 10 Fichajes con Mayor Sobrepuja</h2>
            <div class="mt-4 overflow-x-auto">
                <table class="min-w-full text-left text-sm">
                    <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                        <tr><th class="px-4 py-3">Jugador</th><th class="px-4 py-3">Comprador</th><th class="px-4 py-3 text-right">Precio mercado</th><th class="px-4 py-3 text-right">Precio fichaje</th><th class="px-4 py-3 text-right">Sobrepuja</th><th class="px-4 py-3 text-right">Fecha</th></tr>
                    </thead>
                    <tbody class="divide-y divide-slate-100">{top_overbid_rows}</tbody>
                </table>
            </div>
        </section>
        """

    team_rows = "\n".join(
        f"""
        <tr>
            <td class="px-4 py-3 font-semibold">{row['team']}</td>
            <td class="px-4 py-3 text-center tabular-nums">{row['wins']}</td>
            <td class="px-4 py-3">
                <div class="h-2 bg-slate-200"><div class="h-2 bg-emerald-500" style="width: {row['wins_bar']}%"></div></div>
            </td>
            <td class="px-4 py-3 text-center tabular-nums">{row['signings']}</td>
            <td class="px-4 py-3 text-right tabular-nums">{format_money(row['spend'])}</td>
            <td class="px-4 py-3 text-center tabular-nums">{row['sales']}</td>
            <td class="px-4 py-3 text-right tabular-nums">{format_money(row['income'])}</td>
            <td class="px-4 py-3 text-right tabular-nums">{format_money(row['net'])}</td>
        </tr>
        """
        for row in summary["team_rows"]
    )
    team_market_rows = "\n".join(
        f"""
        <tr>
            <td class="px-4 py-3 font-semibold">{row['team']}</td>
            <td class="px-4 py-3 text-center tabular-nums">{row['signings']}</td>
            <td class="px-4 py-3 text-right tabular-nums">{format_money(row['spend'])}</td>
            <td class="px-4 py-3 text-center tabular-nums">{row['sales']}</td>
            <td class="px-4 py-3 text-right tabular-nums">{format_money(row['income'])}</td>
            <td class="px-4 py-3 text-right tabular-nums">{format_money(row['net'])}</td>
        </tr>
        """
        for row in sorted(summary["team_rows"], key=lambda item: (-item["spend"], -item["signings"], item["team"]))
    )

    classification_notice = ""
    dominance_section = ""
    half_and_combined_section = ""
    round_winners_section = ""
    if has_rankings:
        half_and_combined_section = f"""
        <section class="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-2">
            <div class="bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold">Dominio por jornada</h2>
                <div class="mt-4 overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                            <tr><th class="px-4 py-3">Equipo</th><th class="px-4 py-3 text-center">Jornadas ganadas</th></tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">{winner_count_rows}</tbody>
                    </table>
                </div>
            </div>
            <div class="bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold">Ganador por vuelta</h2>
                <div class="mt-4 overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                            <tr><th class="px-4 py-3">Vuelta</th><th class="px-4 py-3">Equipo</th><th class="px-4 py-3 text-right">Puntos</th></tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">{half_rows}</tbody>
                    </table>
                </div>
            </div>
        </section>
        <section class="mt-8 bg-white p-5 shadow-sm">
                <h2 class="text-xl font-bold">Resumen combinado</h2>
                <div class="mt-4 overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                            <tr><th class="px-4 py-3">Equipo</th><th class="px-4 py-3 text-center">Victorias</th><th class="px-4 py-3">Ritmo</th><th class="px-4 py-3 text-center">Fichajes</th><th class="px-4 py-3 text-right">Gasto</th><th class="px-4 py-3 text-center">Ventas</th><th class="px-4 py-3 text-right">Ingresos</th><th class="px-4 py-3 text-right">Neto</th></tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">{team_rows}</tbody>
                    </table>
                </div>
        </section>
        """
        round_winners_section = f"""
        <section class="mt-8 bg-white p-5 shadow-sm">
            <div class="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <h2 class="text-xl font-bold">Clasificación acumulada</h2>
                <label class="text-sm font-semibold text-slate-700">
                    Jornada
                    <select id="round-ranking-select" class="ml-2 border border-slate-400 bg-white px-3 py-2 text-sm font-semibold text-slate-900">
                        {''.join(f'<option value="{round_number}"{" selected" if round_number == max_round else ""}>{round_number}</option>' for round_number in range(1, max_round + 1))}
                    </select>
                </label>
            </div>
            <div class="mt-4 overflow-x-auto">
                <table class="min-w-full text-left text-sm">
                    <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                        <tr><th class="px-4 py-3 text-center">Ranking</th><th class="px-4 py-3">Equipo</th><th class="px-4 py-3 text-right">Puntos acumulados</th></tr>
                    </thead>
                    <tbody id="round-ranking-body" class="divide-y divide-slate-100"></tbody>
                </table>
            </div>
        </section>
        """
    else:
        classification_notice = """
        <section class="mt-8 border border-amber-300 bg-amber-50 p-5 text-sm text-amber-950">
            No hay histórico de clasificación por jornadas para esta temporada. Se muestran solo las métricas de mercado: fichajes, ventas, ingresos y balance.
        </section>
        """
        half_and_combined_section = f"""
        <section class="mt-8 bg-white p-5 shadow-sm">
            <h2 class="text-xl font-bold">Resumen de mercado</h2>
            <div class="mt-4 overflow-x-auto">
                <table class="min-w-full text-left text-sm">
                    <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                        <tr><th class="px-4 py-3">Equipo</th><th class="px-4 py-3 text-center">Fichajes</th><th class="px-4 py-3 text-right">Gasto</th><th class="px-4 py-3 text-center">Ventas</th><th class="px-4 py-3 text-right">Ingresos</th><th class="px-4 py-3 text-right">Neto</th></tr>
                    </thead>
                    <tbody class="divide-y divide-slate-100">{team_market_rows}</tbody>
                </table>
            </div>
        </section>
        """

    updated = datetime.now().strftime("%d/%m/%Y %H:%M")
    season_label = season.replace("_", "-")
    seasons = seasons or [season]
    selector_options = render_season_selector(seasons, season)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Resumen Liga Futmondo {season_label}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');
        body {{ font-family: 'Inter', sans-serif; }}
        .tabular-nums {{ font-variant-numeric: tabular-nums; }}
    </style>
</head>
<body class="bg-slate-100 text-slate-900">
    <main class="max-w-6xl mx-auto px-4 py-6 sm:px-6 lg:px-8">
        <header class="mb-8">
            <div class="flex flex-col gap-4 border-b border-slate-300 pb-6 sm:flex-row sm:items-end sm:justify-between">
                <div>
                    <p class="text-sm font-semibold uppercase tracking-wide text-emerald-700">Futmondo PALETOS · {season_label}</p>
                    <h1 class="mt-2 text-3xl font-extrabold text-slate-950 sm:text-4xl">Resumen de liga</h1>
                    <p class="mt-2 text-sm text-slate-600">Actualizado: {updated}</p>
                </div>
                <div class="flex flex-col gap-3 sm:items-end">
                    <label class="text-xs font-bold uppercase text-slate-500" for="season-select">Temporada</label>
                    <select id="season-select" class="border border-slate-400 bg-white px-3 py-2 text-sm font-semibold text-slate-900" onchange="changeSeason(this)" oninput="changeSeason(this)">
                        {selector_options}
                    </select>
                    <a class="inline-flex items-center justify-center border border-slate-400 px-4 py-2 text-sm font-semibold text-slate-800 hover:bg-white" href="index.html">Mercado</a>
                </div>
            </div>
        </header>

        <section class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div class="bg-white p-5 shadow-sm">
                <p class="text-xs font-bold uppercase text-slate-500">Jornadas</p>
                <p class="mt-2 text-3xl font-extrabold">{summary['rounds']}</p>
            </div>
            <div class="bg-white p-5 shadow-sm">
                <p class="text-xs font-bold uppercase text-slate-500">Equipos en liga</p>
                <p class="mt-2 text-3xl font-extrabold">{summary['teams_general']}</p>
            </div>
            <div class="bg-white p-5 shadow-sm">
                <p class="text-xs font-bold uppercase text-slate-500">Fichajes con comprador</p>
                <p class="mt-2 text-3xl font-extrabold">{format_int(summary['total_signings'])}</p>
            </div>
            <div class="bg-white p-5 shadow-sm">
                <p class="text-xs font-bold uppercase text-slate-500">Gasto total</p>
                <p class="mt-2 whitespace-nowrap text-3xl font-extrabold">{format_money_compact(summary['total_spend'])}</p>
            </div>
            <div class="bg-white p-5 shadow-sm">
                <p class="text-xs font-bold uppercase text-slate-500">Ventas</p>
                <p class="mt-2 text-3xl font-extrabold">{format_int(summary['total_sales'])}</p>
            </div>
            <div class="bg-white p-5 shadow-sm">
                <p class="text-xs font-bold uppercase text-slate-500">Ingresos por ventas</p>
                <p class="mt-2 whitespace-nowrap text-3xl font-extrabold">{format_money_compact(summary['total_income'])}</p>
            </div>
        </section>

        {classification_notice}
        {dominance_section}
        {half_and_combined_section}
        {market_insights_section}

        <section class="mt-8 bg-white p-5 shadow-sm">
            <h2 class="text-xl font-bold">Mercado de fichajes por equipo</h2>
            <div class="mt-4 overflow-x-auto">
                <table class="min-w-full text-left text-sm">
                    <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                        <tr><th class="px-4 py-3">Equipo</th><th class="px-4 py-3 text-center">Fichajes</th><th class="px-4 py-3 text-right">Precio fichaje medio</th><th class="px-4 py-3 text-right">Fichaje más caro</th></tr>
                    </thead>
                    <tbody class="divide-y divide-slate-100">{market_rows}</tbody>
                </table>
            </div>
        </section>

        <section class="mt-8 bg-white p-5 shadow-sm">
            <h2 class="text-xl font-bold">Ventas por equipo</h2>
            <div class="mt-4 overflow-x-auto">
                <table class="min-w-full text-left text-sm">
                    <thead class="border-b bg-slate-50 text-xs uppercase text-slate-500">
                        <tr><th class="px-4 py-3">Equipo</th><th class="px-4 py-3 text-center">Ventas</th><th class="px-4 py-3 text-right">Precio fichaje medio</th><th class="px-4 py-3 text-right">Venta más cara</th></tr>
                    </thead>
                    <tbody class="divide-y divide-slate-100">{sales_rows}</tbody>
                </table>
            </div>
        </section>

        {round_winners_section}
    </main>
    <script>
        const cumulativeRankings = {cumulative_json};

        function changeSeason(select) {{
            const target = select.value;
            const currentFile = window.location.pathname.split("/").pop();
            if (target && target !== currentFile) {{
                window.location.assign(new URL(target, window.location.href).href);
            }}
        }}
        const seasonSelect = document.getElementById("season-select");
        seasonSelect.addEventListener("change", function () {{ changeSeason(this); }});

        function formatPoints(value) {{
            return Number(value).toFixed(1).replace(".", ",");
        }}

        function renderRoundRanking(round) {{
            const body = document.getElementById("round-ranking-body");
            if (!body) return;
            const rows = cumulativeRankings.filter((item) => item.round === Number(round));
            body.innerHTML = rows.map((item) => `
                <tr>
                    <td class="px-4 py-3 text-center tabular-nums">${{item.rank}}</td>
                    <td class="px-4 py-3 font-semibold">${{item.team}}</td>
                    <td class="px-4 py-3 text-right tabular-nums">${{formatPoints(item.points)}}</td>
                </tr>
            `).join("");
        }}

        const roundRankingSelect = document.getElementById("round-ranking-select");
        if (roundRankingSelect) {{
            renderRoundRanking(roundRankingSelect.value);
            roundRankingSelect.addEventListener("change", function () {{ renderRoundRanking(this.value); }});
        }}
    </script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Genera el dashboard resumen de liga.")
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--exports-dir", default=DEFAULT_EXPORTS_DIR, type=Path)
    parser.add_argument("--output", default=DEFAULT_OUTPUT, type=Path)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)

    seasons = discover_seasons(args.exports_dir) if args.season == "all" else [args.season]
    if not seasons:
        raise RuntimeError("No hay temporadas con fichajes para generar dashboard.")

    latest_season = seasons[-1]
    latest_html = None
    for season in seasons:
        season_dir = args.exports_dir / season
        rankings_dir = season_dir / "clasificacion"
        rankings_path = (
            latest_file(rankings_dir, "clasificacion_jornadas_*.csv")
            if rankings_dir.exists() and any(rankings_dir.glob("clasificacion_jornadas_*.csv"))
            else None
        )
        signings_path = latest_file(season_dir, "fichajes_all_*.csv")
        summary = build_summary(rankings_path, signings_path, season)
        html_content = render_dashboard(summary, season, seasons=seasons)
        season_output = args.output.with_name(f"resumen_liga_{season}.html")
        season_output.write_text(html_content, encoding="utf-8")
        print(f"Dashboard generado: {season_output}")
        print(f"Clasificacion: {rankings_path if rankings_path else 'sin historico'}")
        print(f"Fichajes: {signings_path}")
        print(f"Pressroom: {summary['pressroom_path']}")
        if season == latest_season:
            latest_html = html_content

    args.output.write_text(latest_html, encoding="utf-8")
    print(f"Dashboard principal generado: {args.output}")


if __name__ == "__main__":
    main()
