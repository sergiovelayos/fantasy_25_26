from datetime import datetime

# Obtiene la fecha y hora actuales
ahora = datetime.now()

# Imprime con formato
print("Fecha y hora de ejecución:", ahora.strftime("%Y-%m-%d %H:%M:%S"))
