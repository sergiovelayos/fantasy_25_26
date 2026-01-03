
import json
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, timedelta
import os

# --- Configuration ---
MARKET_FILE = "data/futmondo_market.json"
SIGNINGS_FILE = "data/fichajes_hist_file/pressroom_2025_2026.json"
OUTPUT_HTML = "docs/index.html"
ASSETS_DIR = "docs/assets"
CHART_FILENAME = "conversion_chart.png"

# Style configuration
plt.style.use('ggplot')
sns.set_palette("husl")

def load_data():
    print("Loading data...")
    # Load Market
    with open(MARKET_FILE, "r", encoding="utf-8") as f:
        market_raw = json.load(f)
    
    market_rows = []
    for entry in market_raw:
        for p in entry.get("jugadores", []):
            market_rows.append({
                "player_id": p.get("id"),
                "name": p.get("name"),
                "creation_date": p.get("creationDate"),
                "price": p.get("price"),
                "computer": p.get("computer", False) or (p.get("userTeam") is None)
            })
    df_market = pd.DataFrame(market_rows)
    df_market["creation_date"] = pd.to_datetime(df_market["creation_date"], utc=True)
    df_market.drop_duplicates(subset=["player_id", "creation_date"], inplace=True)

    # Load Signings
    with open(SIGNINGS_FILE, "r", encoding="utf-8") as f:
        signings_raw = json.load(f)
        news = signings_raw.get("answer", {}).get("news", [])

    signings_rows = []
    for item in news:
        buyer = item.get("_buyer", {}).get("name")
        signings_rows.append({
            "player_id": item.get("_player", {}).get("_id"),
            "player_name": item.get("_player", {}).get("name"),
            "buyer": buyer,
            "price": item.get("price"),
            "signed_date": item.get("created")
        })
    df_signings = pd.DataFrame(signings_rows)
    df_signings["signed_date"] = pd.to_datetime(df_signings["signed_date"], utc=True)

    return df_market, df_signings

def analyze_conversion(df_market, df_signings):
    print("Analyzing conversion...")
    # Merge logic
    merged = pd.merge(df_market, df_signings, on="player_id", suffixes=("_m", "_s"), how="left")
    merged["time_diff"] = merged["signed_date"] - merged["creation_date"]
    
    # Filter valid conversions (within 48h)
    valid = merged[
        (merged["time_diff"] >= timedelta(seconds=0)) & 
        (merged["time_diff"] <= timedelta(hours=48))
    ].copy()
    
    # Stats
    total_offers = len(df_market)
    total_sales = valid["creation_date"].nunique() # approx
    rate = (total_sales / total_offers) * 100 if total_offers > 0 else 0
    
    # Chart
    df_market["date"] = df_market["creation_date"].dt.date
    valid["date"] = valid["creation_date"].dt.date
    
    daily_market = df_market.groupby("date").size().rename("Ofertas")
    daily_sales = valid.groupby("date")["creation_date"].nunique().rename("Ventas")
    
    stats = pd.concat([daily_market, daily_sales], axis=1).fillna(0)
    # Filter last 15 days for cleaner chart
    stats = stats.tail(15)
    
    plt.figure(figsize=(10, 5))
    ax = stats["Ofertas"].plot(kind='bar', color='#3498db', alpha=0.7, label="Jugadores en Mercado")
    stats["Ventas"].plot(kind='bar', color='#2ecc71', ax=ax, label="Fichados")
    
    plt.title("Actividad del Mercado (Últimos 15 días)")
    plt.ylabel("Cantidad de Jugadores")
    plt.xlabel("Fecha")
    plt.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(ASSETS_DIR, CHART_FILENAME))
    plt.close()
    
    return rate, total_offers, total_sales

def analyze_overbids(df_market, df_signings):
    print("Analyzing overbids...")
    # Computer market only
    df_m_comp = df_market[df_market["computer"] == True].copy()
    df_m_comp.rename(columns={"price": "price_m"}, inplace=True)
    df_m_comp.sort_values("creation_date", inplace=True)
    
    df_s_sorted = df_signings.sort_values("signed_date").copy()
    df_s_sorted.rename(columns={"price": "price_s"}, inplace=True)
    
    # Merge asof
    merged = pd.merge_asof(
        df_s_sorted,
        df_m_comp,
        left_on="signed_date",
        right_on="creation_date",
        by="player_id",
        direction="backward",
        tolerance=pd.Timedelta(days=4)
    )
    
    matched = merged.dropna(subset=["price_m"]).copy()
    matched["overbid_pct"] = ((matched["price_s"] - matched["price_m"]) / matched["price_m"]) * 100
    
    stats = matched.groupby("buyer").agg(
        count=("player_id", "count"),
        avg_overbid=("overbid_pct", "mean")
    ).sort_values("avg_overbid", ascending=False)
    
    return stats

def get_top_signings(df_signings):
    top = df_signings.sort_values("price", ascending=False).head(10)
    return top

def generate_html(conversion_rate, offers, sales, buyer_stats, top_signings):
    print("Generating HTML...")
    
    # Buyer Table Rows
    buyer_rows = ""
    for buyer, row in buyer_stats.iterrows():
        buyer_rows += f"""
        <tr>
            <td>{buyer}</td>
            <td>{row['count']}</td>
            <td class="font-bold {'text-green-600' if row['avg_overbid'] > 0 else 'text-red-600'}">
                +{row['avg_overbid']:.2f}%
            </td>
        </tr>
        """
        
    # Top Signings Rows
    top_rows = ""
    for _, row in top_signings.iterrows():
        top_rows += f"""
        <tr>
            <td>{row['player_name']}</td>
            <td>{row['buyer']}</td>
            <td>{row['price']:,.0f} €</td>
            <td class="text-sm text-gray-500">{row['signed_date'].strftime('%Y-%m-%d')}</td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Futmondo Fantasy Analytics</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
        body {{ font-family: 'Inter', sans-serif; }}
    </style>
</head>
<body class="bg-gray-100 text-gray-800">

    <div class="max-w-5xl mx-auto p-6">
        
        <!-- Header -->
        <header class="mb-10 text-center">
            <h1 class="text-4xl font-bold text-indigo-700 mb-2">Futmondo Market Analytics</h1>
            <p class="text-gray-600">Última actualización: {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
        </header>

        <!-- KPI Cards -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-10">
            <div class="bg-white p-6 rounded-lg shadow-md border-l-4 border-indigo-500">
                <h3 class="text-sm font-semibold text-gray-500 uppercase">Tasa de Conversión</h3>
                <p class="text-3xl font-bold">{conversion_rate:.2f}%</p>
                <p class="text-xs text-gray-400">De jugadores en mercado a fichados</p>
            </div>
            <div class="bg-white p-6 rounded-lg shadow-md border-l-4 border-blue-500">
                <h3 class="text-sm font-semibold text-gray-500 uppercase">Ofertas Analizadas</h3>
                <p class="text-3xl font-bold">{offers:,}</p>
                <p class="text-xs text-gray-400">Apariciones en mercado</p>
            </div>
            <div class="bg-white p-6 rounded-lg shadow-md border-l-4 border-green-500">
                <h3 class="text-sm font-semibold text-gray-500 uppercase">Fichajes Totales</h3>
                <p class="text-3xl font-bold">{sales:,}</p>
                <p class="text-xs text-gray-400">Confirmados (Sistema)</p>
            </div>
        </div>

        <!-- Section 1: Chart -->
        <section class="bg-white p-6 rounded-lg shadow-md mb-10">
            <h2 class="text-2xl font-bold mb-6 border-b pb-2">Actividad Diaria del Mercado</h2>
            <div class="flex justify-center">
                <img src="assets/{CHART_FILENAME}" alt="Gráfico de Conversión" class="rounded max-w-full h-auto">
            </div>
            <p class="mt-4 text-sm text-gray-500 text-center">Muestra el volumen de jugadores que salen al mercado vs los que son comprados cada día.</p>
        </section>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
            
            <!-- Section 2: Buyer Stats -->
            <section class="bg-white p-6 rounded-lg shadow-md">
                <h2 class="text-xl font-bold mb-4 text-indigo-600">Comportamiento de los Compradores</h2>
                <div class="overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="bg-gray-50 border-b">
                            <tr>
                                <th class="py-3 px-4">Comprador</th>
                                <th class="py-3 px-4">Fichajes</th>
                                <th class="py-3 px-4">Sobrepuja Media</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-gray-100">
                            {buyer_rows}
                        </tbody>
                    </table>
                </div>
                <p class="mt-4 text-xs text-gray-400">* Solo incluye fichajes al "Computer" identificados correctamente.</p>
            </section>

            <!-- Section 3: Top Signings -->
            <section class="bg-white p-6 rounded-lg shadow-md">
                <h2 class="text-xl font-bold mb-4 text-indigo-600">Top 10 Fichajes Más Caros</h2>
                <div class="overflow-x-auto">
                    <table class="min-w-full text-left text-sm">
                        <thead class="bg-gray-50 border-b">
                            <tr>
                                <th class="py-3 px-4">Jugador</th>
                                <th class="py-3 px-4">Comprador</th>
                                <th class="py-3 px-4">Precio</th>
                                <th class="py-3 px-4">Fecha</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-gray-100">
                            {top_rows}
                        </tbody>
                    </table>
                </div>
            </section>
        </div>

        <footer class="mt-12 text-center text-gray-500 text-sm">
            <p>Generado automáticamente con Python & Gemini</p>
        </footer>
    </div>
</body>
</html>
    """
    
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Report generated at {OUTPUT_HTML}")

def main():
    df_m, df_s = load_data()
    
    # 1. Conversion
    rate, offers, sales = analyze_conversion(df_m, df_s)
    
    # 2. Overbids
    buyer_stats = analyze_overbids(df_m, df_s)
    
    # 3. Top Signings
    top_signings = get_top_signings(df_s)
    
    # 4. Generate HTML
    generate_html(rate, offers, sales, buyer_stats, top_signings)

if __name__ == "__main__":
    main()
