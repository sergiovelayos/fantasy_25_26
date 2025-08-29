from sqlalchemy import create_engine, text
import pandas as pd
import os
from dotenv import load_dotenv

# Cargar variables desde el fichero .env
load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")

# Crear engine de SQLAlchemy
engine = create_engine(f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

# Ruta relativa del CSV
csv_path = "fichajes_hist_file/fichajes_24_25.csv"

# Leer el CSV con pandas
df = pd.read_csv(csv_path)

# Insertar en PostgreSQL
# ⚠️ Cambia "jugadores" por el nombre de tu tabla
df.to_sql("fichajes_futmondo_24_25", engine, if_exists="replace", index=False)