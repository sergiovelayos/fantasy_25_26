import json
import pandas as pd
from datetime import datetime

# Ruta al archivo JSON
json_path = 'futmondo_market.json'

# Cargar el JSON
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

# Crear DataFrame
df = pd.DataFrame(data)

# if 'creationDate' in df.columns:
#     ser = df['creationDate']

#     # Convertir a datetime de forma robusta (ISO o epoch en s/ms)
#     if pd.api.types.is_numeric_dtype(ser):
#         unit = 'ms' if ser.max() > 1_000_000_000_000 else 's'
#         dt = pd.to_datetime(ser, unit=unit, utc=True)
#     else:
#         dt = pd.to_datetime(ser, errors='coerce', utc=True)

#     # Añadir columnas al DataFrame
#     df['creation_dt'] = dt
#     df['creation_hour'] = dt.dt.hour
#     df['creation_date'] = dt.dt.date   # solo fecha

#     # Valores únicos
#     unique_hours = sorted(int(h) for h in df['creation_hour'].dropna().unique())
#     unique_dates = sorted(df['creation_date'].dropna().unique())
#     unique_dates_str = [d.strftime("%Y-%m-%d %H:%M:%S") for d in unique_dates]

#     print("Horas únicas de creationDate:", unique_hours)
#     print("Fechas únicas de creationDate:", unique_dates_str)
# else:
#     print("El campo 'creationDate' no existe en el JSON. Columnas disponibles:", list(df.columns))

# ¿cuántas veces ha salido cada jugador?
if 'name' in df.columns:
    player_counts = df['name'].value_counts()
    print("\nJugadores y sus conteos:")
    for player, count in player_counts.items():
        print(f"{player}: {count} veces")
else:
    print("El campo 'name' no existe en el JSON. Columnas disponibles:", list(df.columns))



