import json
import pandas as pd
from datetime import datetime
import pytz

# Ruta al archivo JSON
json_path = './data/futmondo_market.json'

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
def count_players(df):
    if 'name' not in df.columns:
        print("El campo 'name' no existe en el DataFrame.")
        return {}
    player_counts = df['name'].value_counts().to_dict()
    return player_counts



# Crea una lista de tuplas que diga el nombre del jugador y su creationDate
def list_player_dates(df):    
    if 'name' in df.columns and 'creationDate' in df.columns:  
        # Convertir a datetime con UTC
        df['creationDate'] = pd.to_datetime(df["creationDate"], errors="coerce", utc=True)
        # Convertir de UTC a zona horaria de Barcelona
        df['creationDate'] = df['creationDate'].dt.tz_convert("Europe/Madrid")
        
        # Fecha de hoy en Barcelona (solo YYYY-MM-DD)
        today = datetime.now(pytz.timezone("Europe/Madrid")).date()
        
        # Filtrar solo los jugadores cuya fecha es hoy
        df_today = df[df['creationDate'].dt.date == today]
        
        if df_today.empty:
            print("No hay jugadores con creationDate de hoy.")
            return []
        
        # Formatear como string legible (ahora sí sobre df_today)
        df_today['creationDate'] = df_today['creationDate'].dt.strftime('%Y-%m-%d %H:%M:%S')
        
        # Crear lista de tuplas (name, creationDate)
        player_dates = list(zip(df_today['name'], df_today['creationDate']))
        
        print("\nLista de jugadores con sus creationDate:")
        for name, cdate in player_dates:
            print(f"{name}: {cdate}")
        
        return player_dates
    
    else:
        print("❌ Los campos 'name' o 'creationDate' no existen en el JSON. Columnas disponibles:", list(df.columns))
        return []

# print(list_player_dates(df))

df['creationDate'] = pd.to_datetime(df["creationDate"], errors="coerce", utc=True)
# Convertir de UTC a zona horaria de Barcelona
df['creationDate'] = df['creationDate'].dt.tz_convert("Europe/Madrid")
#.strftime('%Y-%m-%d')

# Fecha de hoy en Barcelona (solo YYYY-MM-DD)
today = datetime.now(pytz.timezone("Europe/Madrid")).date()

# Filtrar solo los jugadores cuya fecha es hoy
df_today = df[df['creationDate'].dt.date == today]

print(df['creationDate'].unique())