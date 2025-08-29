import json
import pandas as pd

# === Cargar JSON desde archivo ===
with open("championshipplayers_2025_08_16.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# print(type(data))
# print(list(data.keys())[:10] if isinstance(data, dict) else data[:1])


# === Función para aplanar diccionarios anidados ===
def flatten_dict(d, parent_key="", sep="."):
    items = {}
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(flatten_dict(v, new_key, sep=sep))
        elif isinstance(v, list):
            if all(not isinstance(i, (dict, list)) for i in v):
                # Guardar listas simples como lista
                items[new_key] = v
            else:
                # Guardar elementos complejos de listas con índices
                for i, elem in enumerate(v):
                    if isinstance(elem, dict):
                        items.update(flatten_dict(elem, f"{new_key}[{i}]", sep=sep))
                    else:
                        items[f"{new_key}[{i}]"] = elem
        else:
            items[new_key] = v
    return items

# === Crear DataFrame por jugador ===
players = data["answer"]["players"]
rows = []

for player in players:
    flat_player = flatten_dict(player)
    rows.append(flat_player)

df = pd.DataFrame(rows)

df["clause.date"] = pd.to_datetime(df["clause.date"], errors="coerce", utc=True).dt.strftime('%Y-%m-%d %H:%M:%S')

# Mostrar DataFrame
# print(df.head())

# # Guardar a CSV
df.to_csv("championshipplayers_liga_25_26.csv", index=False, encoding="utf-8")

