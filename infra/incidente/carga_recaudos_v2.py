"""Carga diaria del recaudo en dw.fact_recaudos. Versión corregida de carga_recaudos.py.

Qué cierra respecto al original (detalle en docs/seccion6_operacion.md):
  * Toma TODAS las partes del día (_parte1, _parte2...) y la versión más reciente si el
    proveedor reenvía (_v2). El original tomaba solo el último archivo en orden alfabético.
  * Valida contra el archivo de control (.ctl) ANTES de tocar la tabla: partes, número de
    registros y valor total. Si algo no cuadra, falla y la tabla queda como estaba.
  * DELETE + INSERT + verificación en UNA transacción: Power BI nunca ve una carga a medias.
  * Deja una fila en dw.control_cargas por día, con lo que el ctl decía y lo que se cargó.
  * La fecha se puede pasar por parámetro, para recargar un día sin cambiar código.
  * Termina con código distinto de 0 ante cualquier falla, así Jenkins se pone rojo.

Uso:
    python carga_recaudos_v2.py                      # el día anterior (hora Colombia)
    python carga_recaudos_v2.py --fecha 2026-10-26   # recarga de un día afectado

Variables de entorno: DB_CONN (cadena ODBC; la inyecta Jenkins desde sus credenciales).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

# Carpeta donde el proveedor deja los archivos (se puede cambiar con --entrada).
ENTRADA = "/data/entrada/recaudos"
# Solo estos estados se cargan, igual que en el reporte de ventas.
ESTADOS_VALIDOS = ("APROBADA", "REVERSADA")
TZ_COLOMBIA = timezone(timedelta(hours=-5), "America/Bogota")
# Nombre esperado: recaudos_AAAAMMDD[_vN][_parteN].csv. Los grupos capturan la fecha,
# la versión (si el proveedor reenvía) y el número de parte (si el archivo viene partido).
RE_ARCHIVO = re.compile(r"^recaudos_(\d{8})(?:_v(\d+))?(?:_parte(\d+))?\.csv$")

log = logging.getLogger("carga_recaudos")


class ControlError(Exception):
    """Lo recibido no coincide con lo que el proveedor declaró en el .ctl."""


def leer_control(ruta: Path) -> dict:
    """Lee el archivo .ctl (líneas clave=valor) con lo que el proveedor declara: partes, registros y valor total."""
    datos = dict(l.strip().split("=", 1) for l in Path(ruta).read_text(encoding="utf-8").splitlines() if "=" in l)
    # Si el .ctl no trae "registros" o "valor_total" falla con KeyError: sin control no se carga nada.
    return {
        "partes": int(datos.get("partes", 1)),
        "registros": int(datos["registros"]),
        "valor_total": Decimal(datos["valor_total"]),
    }


def archivos_del_dia(carpeta, fecha: date) -> list[Path]:
    """Todas las partes de la versión más reciente del día, ordenadas."""
    # Cada candidato es (versión, parte, ruta). Sin _vN la versión es 1; sin _parteN la parte es 0.
    candidatos = []
    for p in Path(carpeta).glob(f"recaudos_{fecha:%Y%m%d}*.csv"):
        m = RE_ARCHIVO.match(p.name)
        if not m:
            log.warning("Archivo con nombre inesperado, se ignora: %s", p.name)
            continue
        candidatos.append((int(m.group(2) or 1), int(m.group(3) or 0), p))
    if not candidatos:
        raise FileNotFoundError(f"No hay archivos de recaudo para {fecha.isoformat()} en {carpeta}")
    # Si el proveedor reenvió (_v2, _v3...), solo cuenta la versión más alta; las anteriores se ignoran.
    ultima = max(v for v, _, _ in candidatos)
    elegidos = sorted((parte, p) for v, parte, p in candidatos if v == ultima)
    log.info("Archivos del día: %s (versión %d)", [p.name for _, p in elegidos], ultima)
    return [p for _, p in elegidos]


def leer_archivos(archivos: list[Path], ctl: dict) -> pd.DataFrame:
    """Lee y une las partes. Falla si faltan partes o si un id aparece en dos partes."""
    # Se compara lo que llegó con lo que el .ctl declaró. El original tomaba solo el
    # último archivo y no notaba que faltaban partes.
    esperadas = ctl["partes"]
    if esperadas > 1:
        numeros = sorted(int(RE_ARCHIVO.match(p.name).group(3) or 0) for p in archivos)
        if numeros != list(range(1, esperadas + 1)):
            raise ControlError(f"El control declara {esperadas} partes y llegaron {numeros}")
    elif len(archivos) != 1:
        raise ControlError(f"El control declara 1 archivo y llegaron {len(archivos)}")
    # Códigos y valor se leen como texto: así no se pierden ceros ni se redondea el dinero.
    df = pd.concat(
        [pd.read_csv(p, sep=";", dtype={"cod_punto": str, "cod_convenio": str, "valor": str}) for p in archivos],
        ignore_index=True,
    )
    if df["id_recaudo"].duplicated().any():
        raise ControlError("Hay id_recaudo repetidos entre las partes")
    return df


def validar_contra_control(df: pd.DataFrame, ctl: dict) -> Decimal:
    """Compara registros y valor total del archivo COMPLETO (sin filtrar por estado) con el .ctl."""
    # Se valida el archivo COMPLETO, antes de filtrar por estado, porque el proveedor
    # calcula sus totales sobre todo lo que envía. Decimal suma sin error de redondeo.
    valor = sum((Decimal(v) for v in df["valor"]), Decimal(0))
    if len(df) != ctl["registros"] or valor != ctl["valor_total"]:
        raise ControlError(
            f"No cuadra con el control: registros {len(df)} vs {ctl['registros']}, "
            f"valor {valor} vs {ctl['valor_total']}"
        )
    return valor


def cargar(conn, validos: pd.DataFrame, fecha: date, ctl: dict, leidos: int, partes: int) -> None:
    """Reemplaza el día en una sola transacción y verifica antes de confirmar."""
    cur = conn.cursor()
    try:
        # Reemplazo del día: borrar y volver a insertar. Como todo ocurre en una sola
        # transacción (el commit está al final), si algo falla no queda un día a medias.
        cur.execute("DELETE FROM dw.fact_recaudos WHERE fecha = ?", (fecha.isoformat(),))
        log.info("Registros eliminados para %s: %s", fecha, cur.rowcount)
        if hasattr(cur, "fast_executemany"):
            cur.fast_executemany = True  # pyodbc: un solo viaje por lote en vez de uno por fila
        filas = [(r.id_recaudo, fecha.isoformat(), r.cod_punto, r.cod_convenio, r.valor, r.estado)
                 for r in validos.itertuples(index=False)]
        cur.executemany(
            "INSERT INTO dw.fact_recaudos (id_recaudo, fecha, cod_punto, cod_convenio, valor, estado) "
            "VALUES (?, ?, ?, ?, ?, ?)", filas)
        cur.execute("SELECT COUNT(*), COALESCE(SUM(valor), 0) FROM dw.fact_recaudos WHERE fecha = ?",
                    (fecha.isoformat(),))
        # Verificación antes de confirmar: lo que quedó en la tabla debe ser igual a lo que se cargó.
        n, total = cur.fetchone()
        esperado = sum((Decimal(v) for v in validos["valor"]), Decimal(0))
        if n != len(validos) or Decimal(str(total)) != esperado:
            raise ControlError(f"La tabla quedó distinta de lo cargado: {n} filas y {total} vs {len(validos)} y {esperado}")
        # Auditoría: una fila por día con lo que el .ctl decía y lo que realmente se cargó.
        cur.execute(
            "INSERT INTO dw.control_cargas (fecha, partes, registros_ctl, valor_ctl, registros_leidos, "
            "registros_validos, valor_cargado, cargado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (fecha.isoformat(), partes, ctl["registros"], int(ctl["valor_total"]), leidos, len(validos),
             int(esperado), datetime.now(TZ_COLOMBIA).strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    except Exception:
        conn.rollback()  # la tabla queda exactamente como estaba
        raise
    finally:
        cur.close()


def ejecutar(fecha: date, carpeta: str, conn) -> None:
    """Orden de la carga: buscar archivos, leer el .ctl, validar, filtrar por estado y cargar."""
    archivos = archivos_del_dia(carpeta, fecha)
    ctl = leer_control(Path(carpeta) / f"recaudos_{fecha:%Y%m%d}.ctl")
    df = leer_archivos(archivos, ctl)
    validar_contra_control(df, ctl)
    log.info("Control OK: %d registros y valor %s en %d archivo(s)", len(df), ctl["valor_total"], len(archivos))
    # El filtro por estado va DESPUÉS de validar contra el control (que cuenta todas las filas).
    validos = df[df["estado"].isin(ESTADOS_VALIDOS)].copy()
    log.info("Registros leídos: %d | válidos (APROBADA/REVERSADA): %d", len(df), len(validos))
    cargar(conn, validos, fecha, ctl, len(df), len(archivos))
    log.info("Carga finalizada: %d filas, valor %s", len(validos), sum((Decimal(v) for v in validos["valor"]), Decimal(0)))


def main(argv=None) -> int:
    """Punto de entrada: devuelve 0 si cargó y 1 si falló (Jenkins se pone rojo con 1)."""
    p = argparse.ArgumentParser(description="Carga el recaudo diario en dw.fact_recaudos.")
    p.add_argument("--fecha", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(), default=None)
    p.add_argument("--entrada", default=ENTRADA)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    fecha = args.fecha or (datetime.now(TZ_COLOMBIA).date() - timedelta(days=1))
    conn = None
    try:
        import pyodbc  # import perezoso: las pruebas no necesitan el driver

        # La cadena de conexión viene de la variable DB_CONN (la inyecta Jenkins), no del código.
        # autocommit=False: nada se confirma hasta el commit explícito de cargar().
        conn = pyodbc.connect(os.environ["DB_CONN"], autocommit=False)
        ejecutar(fecha, args.entrada, conn)
        return 0
    except Exception:
        log.exception("La carga de recaudos falló")
        return 1
    finally:
        if conn is not None:  # se cierra la conexión aunque haya fallado
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
