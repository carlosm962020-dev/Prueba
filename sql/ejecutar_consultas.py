"""Ejecuta sql/consultas.sql sobre la base del pipeline y muestra cada resultado.

Uso:  python sql/ejecutar_consultas.py [salida/pagared.db]
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).parent  # la carpeta sql/, donde está consultas.sql
# Base SQLite: la que se pasa por argumento o, si no, la que dejó el pipeline.
db = sys.argv[1] if len(sys.argv) > 1 else AQUI.parent / "salida" / "pagared.db"

# Se quitan los comentarios (la consulta 4 es T-SQL y va comentada) y se separa por ';'.
texto = "\n".join(l for l in (AQUI / "consultas.sql").read_text(encoding="utf-8").splitlines()
                  if not l.strip().startswith("--"))
consultas = [c.strip() for c in texto.split(";") if c.strip()]

conn = sqlite3.connect(db)
# Opciones de pandas para que las tablas se vean completas en la consola.
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)
# Se ejecuta cada consulta y se imprime su resultado, para ver las cifras que van en la sección 4 del informe.
for i, sql in enumerate(consultas, 1):
    df = pd.read_sql_query(sql, conn)
    print(f"--- Consulta {i}: {len(df)} filas")
    print(df.to_string(index=False) if len(df) else "(sin filas)")
    print()
