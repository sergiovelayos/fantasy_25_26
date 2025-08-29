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


# ----------- Ejemplo de consulta -----------
query = """
SELECT *
FROM jugadores_futmondo_25_26
where userteam = '⭐⭐Heraso 🤴🏻'
;
"""

# Ejecutar la query y traer resultados a un DataFrame
with engine.connect() as conn:
    df = pd.read_sql(text(query), conn)

print(df)
