# Futmondo Fantasy 25-26 Analytics

Este proyecto automatiza la extracción, el almacenamiento y el análisis de datos del mercado de fichajes de **Futmondo**.

## 🚀 Funcionalidades Principales

*   **Scraping Automatizado:** Obtención de datos del mercado de jugadores en tiempo real.
*   **Pipeline ETL con carga delta:** Procesamiento incremental de datos desde JSON a PostgreSQL. Solo inserta registros nuevos (detectados por `scrape_fecha`) evitando duplicados mediante constraint `UNIQUE(player_id, creation_date, expiration_date)`.
*   **Analíticas Avanzadas:**
    *   Tasa de conversión de jugadores del mercado (¿cuántos se compran realmente?).
    *   Comportamiento de los usuarios (porcentaje de sobrepuja respecto al valor de mercado).
    *   Ranking de los fichajes más caros.
*   **Mercado Actual:** Tabla con los jugadores disponibles hoy ofertados por la máquina (`computer=true`), ordenados por fecha de expiración y puntos.
*   **Reporte Web:** Generación automática de un dashboard en HTML publicado en GitHub Pages.
*   **Notificaciones:** Alertas vía Telegram.

## 📊 Visualización de Resultados

Los resultados del análisis se publican automáticamente en:
👉 **[https://sergiovelayos.github.io/fantasy_25_26/](https://sergiovelayos.github.io/fantasy_25_26/)**

## ⏰ Automatización

El proceso completo se ejecuta cada día a las **7:01 AM** mediante cron job en el Mac Mini:

```
1 7 * * * /ruta/run_futmondo.sh
```

El script `run_futmondo.sh` encadena:
1. Scraping del mercado de Futmondo
2. ETL delta → PostgreSQL
3. Generación del reporte web
4. Push automático a GitHub Pages vía SSH

## 🗄️ Base de Datos (PostgreSQL)

*   Tabla: `public.futmondo_market_25_26`
*   Constraint única: `(player_id, creation_date, expiration_date)` — permite registrar al mismo jugador en distintos periodos de mercado.
*   Campo `scrape_fecha`: fecha del primer scrape que capturó cada listing.

## 🛠️ Instalación y Configuración

1.  **Clonar el repositorio:**
    ```bash
    git clone https://github.com/sergiovelayos/fantasy_25_26.git
    cd fantasy_25_26
    ```

2.  **Preparar el entorno:**
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```

3.  **Configurar variables de entorno:**
    Copia `.env.example` a `.env` y completa tus credenciales de Futmondo, PostgreSQL y Telegram.

## 💻 Uso de los Scripts

*   **Extraer datos del mercado:**
    `python futmondo_market_scraper.py`
*   **Exportar todos los fichajes de prensa por temporada:**
    `python export_pressroom.py --season 2025_2026`
    - Si no se indica `--season`, el script intenta inferir la temporada a partir de las fechas descargadas.
    - Guarda el JSON completo y un CSV limpio en `data/exports/<temporada>/`.
*   **Importar un pressroom histórico ya descargado:**
    `python import_pressroom_history.py "/ruta/pressroom_2024_2025.json" --season 2024_2025`
    - Normaliza el JSON, elimina duplicados por `_id` y genera el CSV compatible con los dashboards.
    - También acepta varios snapshots parciales: `python import_pressroom_history.py "/ruta/pressroom_1.json" "/ruta/pressroom_2.json" --season 2023_2024`.
*   **Importar clasificaciones históricas ya descargadas:**
    `python import_rankings_history.py "/ruta/clasificacion_todas_las_jornadas.json" "/ruta/ids_jornadas.json" --season 2024_2025`
    - Cruza los IDs internos de jornada, genera la clasificación por jornada y deriva la general por suma de puntos.
*   **Exportar la clasificación por jornadas:**
    `python export_rankings.py --season 2025_2026`
    - Descarga la clasificación general y las clasificaciones de las 38 jornadas.
    - Guarda el JSON completo y CSVs en `data/exports/<temporada>/clasificacion/`.
*   **Cargar datos en Base de Datos (delta):**
    `python etl/insert_mercado.py`
*   **Generar el reporte web:**
    `python generate_web_report.py` (Actualiza `docs/index.html`)
*   **Generar el dashboard resumen de liga:**
    `python generate_league_dashboard.py` (Actualiza `docs/resumen_liga.html` y crea una página por temporada disponible)
*   **Pipeline completo:**
    `./run_futmondo.sh`

## 📂 Estructura del Proyecto

*   `docs/`: Contiene el dashboard web estático (GitHub Pages).
*   `etl/`: Scripts de procesamiento e inserción en base de datos.
*   `data/`: Almacenamiento local de datos en JSON y CSV.
*   `generate_web_report.py`: Script principal de análisis y generación de reportes.
*   `run_futmondo.sh`: Script de automatización completo (scraper + ETL + web + push).
*   `telegram_utils.py`: Utilidades de notificación.
