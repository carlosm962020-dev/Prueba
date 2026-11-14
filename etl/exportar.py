"""Exportaciones del modelo para BI y para un data lake.

* CSV: respaldo para conectar Power BI cuando no hay forma de leer SQLite.
* Parquet particionado por fecha: así quedaría la tabla de hechos en S3
  (s3://bucket/fact_transacciones/fecha=2026-10-27/...).
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pandas as pd

# Tablas del modelo en estrella que se exportan: las cinco dimensiones y los hechos.
TABLAS_MODELO = ["dim_fecha", "dim_punto", "dim_producto", "dim_canal", "dim_estado",
                 "fact_transacciones"]


def exportar_csv(conn: sqlite3.Connection, carpeta: str) -> list[Path]:
    """Escribe un CSV por tabla del modelo en `carpeta` y devuelve las rutas."""
    destino = Path(carpeta)
    destino.mkdir(parents=True, exist_ok=True)
    rutas = []
    for tabla in TABLAS_MODELO:
        ruta = destino / f"{tabla}.csv"
        # utf-8-sig para que Excel y Power BI reconozcan las tildes.
        # El nombre de la tabla sale de la lista fija de arriba, no de un dato externo.
        pd.read_sql_query(f"SELECT * FROM {tabla}", conn).to_csv(ruta, index=False, encoding="utf-8-sig")
        rutas.append(ruta)
    return rutas


def exportar_parquet(conn: sqlite3.Connection, carpeta: str) -> Path:
    """Exporta los hechos a Parquet, una carpeta por fecha (fecha=AAAA-MM-DD). Necesita pyarrow."""
    destino = Path(carpeta) / "fact_transacciones"
    if destino.exists():  # se reescribe completo: sin particiones viejas ni duplicadas
        shutil.rmtree(destino)
    # Se une con dim_fecha para tener la columna "fecha" legible, que es la que particiona.
    df = pd.read_sql_query(
        "SELECT f.*, d.fecha FROM fact_transacciones f JOIN dim_fecha d ON d.fecha_key = f.fecha_key",
        conn,
    )
    df.to_parquet(destino, partition_cols=["fecha"], index=False)
    return destino
