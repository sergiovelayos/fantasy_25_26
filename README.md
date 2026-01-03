# Futmondo Fantasy 25-26 Analytics

Este proyecto automatiza la extracción, el almacenamiento y el análisis de datos del mercado de fichajes de **Futmondo**.

## 🚀 Funcionalidades Principales

*   **Scraping Automatizado:** Obtención de datos del mercado de jugadores en tiempo real.
*   **Pipeline ETL:** Procesamiento de datos desde JSON a PostgreSQL para análisis a largo plazo.
*   **Analíticas Avanzadas:** 
    *   Tasa de conversión de jugadores del mercado (¿cuántos se compran realmente?).
    *   Comportamiento de los usuarios (porcentaje de sobrepuja respecto al valor de mercado).
    *   Ranking de los fichajes más caros.
*   **Reporte Web:** Generación automática de un dashboard en HTML para visualización pública.
*   **Notificaciones:** Alertas vía Telegram.

## 📊 Visualización de Resultados

Los resultados del análisis se publican automáticamente en:
👉 **[https://sergiovelayos.github.io/fantasy_25_26/](https://sergiovelayos.github.io/fantasy_25_26/)**

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
*   **Cargar datos en Base de Datos:**
    `python etl/insert_mercado.py`
*   **Generar el reporte web:**
    `python generate_web_report.py` (Actualiza `docs/index.html`)

## 📂 Estructura del Proyecto

*   `docs/`: Contiene el dashboard web estático (GitHub Pages).
*   `etl/`: Scripts de procesamiento e inserción en base de datos.
*   `data/`: Almacenamiento local de datos en JSON y CSV.
*   `generate_web_report.py`: Script principal de análisis y generación de reportes.
*   `telegram_utils.py`: Utilidades de notificación.
