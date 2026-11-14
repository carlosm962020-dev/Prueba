"""Reporte diario de ventas en Excel: hojas Resumen, Comparativo y Calidad.

Todos los números salen de la base de datos (vista vw_ventas), nunca de los
DataFrames de la limpieza, así el reporte siempre refleja lo realmente cargado,
incluido el delta ya aplicado.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import config as cfg

# Estilo del Excel. ORDEN_TIPO fija el orden de la hoja Calidad: primero lo leído y
# cargado, luego lo descartado y al final lo corregido.
FUENTE = "Arial"
ORDEN_TIPO = {"Leídos": 0, "Cargados": 1, "Descartado": 2, "Corregido": 3}
N_COP = "#,##0"  # miles con separador y sin decimales (pesos)
N_PCT = "0.0%"   # porcentaje con un decimal


# ------------------------------------------------------------------ consultas --
def zonas_del_modelo(conn: sqlite3.Connection) -> list[str]:
    """Las cinco zonas del negocio y, si existe, 'Sin zona'; siempre en el mismo orden."""
    # Las cinco zonas oficiales van siempre, aunque no tengan ventas ese día. Si el
    # modelo tiene otra (por ejemplo "Sin zona"), se agrega al final.
    en_modelo = {r[0] for r in conn.execute("SELECT DISTINCT zona FROM dim_punto")}
    zonas = list(cfg.ZONAS) + sorted(en_modelo - set(cfg.ZONAS))
    return zonas


def _ventas_por(conn, fecha: str, columnas: str) -> pd.DataFrame:
    """Suma ventas y cuenta transacciones de un día, agrupadas por las columnas pedidas.

    Lee de vw_ventas, que ya trae solo APROBADA y REVERSADA. `columnas` es texto
    fijo escrito en este módulo (nunca viene del usuario) y la fecha va como
    parámetro (?), así no hay riesgo de inyección SQL.
    """
    return pd.read_sql_query(
        f"SELECT {columnas}, SUM(valor) AS ventas, COUNT(*) AS transacciones "
        f"FROM vw_ventas WHERE fecha = ? GROUP BY {columnas}",
        conn, params=(fecha,),
    )


def datos_resumen(conn, fecha: date) -> pd.DataFrame:
    """Ventas y transacciones por zona y producto (con ceros explícitos)."""
    df = _ventas_por(conn, fecha.isoformat(), "zona, producto")
    # Se arma la tabla con todas las combinaciones zona x producto y se rellena con
    # 0 lo que no vendió, para que el reporte tenga siempre las mismas filas.
    indice = pd.MultiIndex.from_product([zonas_del_modelo(conn), cfg.PRODUCTOS],
                                        names=["zona", "producto"])
    return (df.set_index(["zona", "producto"]).reindex(indice, fill_value=0)
              .reset_index())


def datos_comparativo(conn, fecha: date) -> pd.DataFrame:
    """Variación contra el día anterior por zona. Sin división por cero."""
    zonas = zonas_del_modelo(conn)
    # Ventas del día y del día anterior por zona; reindex deja las zonas sin ventas en 0.
    actual = _ventas_por(conn, fecha.isoformat(), "zona").set_index("zona").reindex(zonas, fill_value=0)
    previo = _ventas_por(conn, (fecha - timedelta(days=1)).isoformat(), "zona") \
        .set_index("zona").reindex(zonas, fill_value=0)
    out = pd.DataFrame({
        "zona": zonas,
        "ventas_actual": actual["ventas"].to_numpy(),
        "ventas_previo": previo["ventas"].to_numpy(),
        "trx_actual": actual["transacciones"].to_numpy(),
        "trx_previo": previo["transacciones"].to_numpy(),
    })
    return _con_variacion(out)


def _con_variacion(out: pd.DataFrame) -> pd.DataFrame:
    """Agrega variación en pesos, variación en % y una nota cuando no hay base de comparación."""
    out = out.copy()
    out["variacion"] = out["ventas_actual"] - out["ventas_previo"]
    # where() deja NaN donde el día anterior fue 0: dividir por NaN da NaN y no un
    # error de división por cero. En el Excel esa celda queda vacía, no en 0%.
    base = out["ventas_previo"].where(out["ventas_previo"] != 0)  # NaN si no hubo ventas
    out["variacion_pct"] = out["variacion"] / base  # NaN si no hay base
    # np.select elige la nota según la primera condición que se cumpla.
    out["nota"] = np.select(
        [(out["ventas_previo"] == 0) & (out["ventas_actual"] > 0),
         (out["ventas_previo"] == 0) & (out["ventas_actual"] == 0)],
        ["Sin ventas el día anterior (zona nueva o sin operación): no hay base para el %",
         "Sin ventas en ninguno de los dos días"],
        default="",
    )
    return out


def datos_calidad(conn) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (resumen de conteos, detalle fila por fila de lo descartado) para la hoja Calidad."""
    resumen = pd.read_sql_query("SELECT archivo, tipo, motivo, registros FROM dq_resumen", conn)
    resumen["_o"] = resumen["tipo"].map(ORDEN_TIPO)
    resumen = (resumen.sort_values(["archivo", "_o", "registros"], ascending=[True, True, False])
                      .drop(columns="_o"))
    # Las versiones anteriores y los duplicados exactos son normales y muy numerosos:
    # solo se cuentan en el resumen, no se listan uno por uno en el detalle.
    marcas = ",".join("?" * len(cfg.MOTIVOS_SOLO_CONTEO))
    detalle = pd.read_sql_query(
        "SELECT archivo, fila, id_transaccion, motivo, detalle FROM dq_descartados "
        f"WHERE motivo NOT IN ({marcas}) ORDER BY archivo, fila",
        conn, params=cfg.MOTIVOS_SOLO_CONTEO,
    )
    return resumen, detalle


# --------------------------------------------------------------------- Excel --
def _estilo_encabezado(ws, fila: int, n_cols: int) -> None:
    """Pinta la fila de encabezados: fondo azul oscuro, letra blanca en negrita, centrada."""
    relleno = PatternFill("solid", start_color="1F3864")
    for c in range(1, n_cols + 1):
        celda = ws.cell(row=fila, column=c)
        celda.font = Font(name=FUENTE, bold=True, color="FFFFFF", size=10)
        celda.fill = relleno
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _escribir_tabla(ws, df: pd.DataFrame, encabezados: list[str], formatos: dict,
                    fila_inicial: int = 1, total: dict | None = None) -> int:
    """Escribe una tabla y devuelve la siguiente fila libre. `formatos`: índice de columna → formato."""
    for c, texto in enumerate(encabezados, 1):
        ws.cell(row=fila_inicial, column=c, value=texto)
    _estilo_encabezado(ws, fila_inicial, len(encabezados))
    fila = fila_inicial + 1
    for registro in df.itertuples(index=False):
        for c, valor in enumerate(registro, 1):
            # openpyxl no entiende los tipos de numpy ni NaN: se convierten a tipos
            # normales de Python, y NaN pasa a celda vacía.
            if isinstance(valor, float) and np.isnan(valor):
                valor = None
            elif isinstance(valor, (np.integer,)):
                valor = int(valor)
            elif isinstance(valor, (np.floating,)):
                valor = float(valor)
            celda = ws.cell(row=fila, column=c, value=valor)
            celda.font = Font(name=FUENTE, size=10)
            if c in formatos:
                celda.number_format = formatos[c]
        fila += 1
    # Fila de total opcional; `total` indica qué va en cada columna (por número de columna).
    if total:
        borde = Border(top=Side(style="thin"))
        for c in range(1, len(encabezados) + 1):
            celda = ws.cell(row=fila, column=c, value=total.get(c))
            celda.font = Font(name=FUENTE, bold=True, size=10)
            celda.border = borde
            if c in formatos:
                celda.number_format = formatos[c]
        fila += 1
    return fila


def _ajustar_anchos(ws, minimo=10, maximo=70) -> None:
    """Ajusta el ancho de cada columna al texto más largo, entre un mínimo y un máximo; las notas en cursiva no cuentan."""
    for col in ws.columns:
        letra = get_column_letter(col[0].column)
        largo = max((len(str(c.value)) for c in col if c.value is not None and not c.font.italic), default=0)
        ws.column_dimensions[letra].width = max(minimo, min(maximo, largo + 2))


def _nota(ws, fila: int, texto: str) -> None:
    """Escribe una nota explicativa en gris y cursiva en la columna A."""
    celda = ws.cell(row=fila, column=1, value=texto)
    celda.font = Font(name=FUENTE, italic=True, size=9, color="595959")


def generar_excel(conn, fecha: date, carpeta: str) -> Path:
    """Arma el Excel de tres hojas para `fecha` y lo guarda en `carpeta`; devuelve la ruta del archivo."""
    anterior = fecha - timedelta(days=1)
    wb = Workbook()

    # Hoja 1: Resumen ------------------------------------------------------------
    ws = wb.active
    ws.title = "Resumen"
    res = datos_resumen(conn, fecha)
    fila = _escribir_tabla(
        ws, res, ["Zona", "Producto", "Ventas (COP)", "Transacciones"], {3: N_COP, 4: N_COP},
        total={1: "TOTAL", 3: int(res["ventas"].sum()), 4: int(res["transacciones"].sum())},
    )
    _nota(ws, fila + 1, f"Ventas del {fecha.isoformat()} (00:00 a 24:00, hora Colombia).")
    _nota(ws, fila + 2, "Venta = transacción APROBADA o REVERSADA. No cuentan ANULADA ni RECHAZADA. "
                        "Se excluye el punto de pruebas de sistemas.")
    ws.freeze_panes = "A2"

    # Hoja 2: Comparativo --------------------------------------------------------
    ws = wb.create_sheet("Comparativo")
    comp = datos_comparativo(conn, fecha)
    tot = {
        "ventas_actual": int(comp["ventas_actual"].sum()),
        "ventas_previo": int(comp["ventas_previo"].sum()),
        "trx_actual": int(comp["trx_actual"].sum()),
        "trx_previo": int(comp["trx_previo"].sum()),
    }
    # El total se calcula con la misma función de variación que las zonas, así el
    # % del total sale igual de bien (y vacío si el día anterior no tuvo ventas).
    tot_df = _con_variacion(pd.DataFrame([{"zona": "TOTAL", **tot}]))
    cuerpo = comp[["zona", "ventas_actual", "ventas_previo", "variacion", "variacion_pct",
                   "trx_actual", "trx_previo", "nota"]]
    t = tot_df.iloc[0]
    fila = _escribir_tabla(
        ws, cuerpo,
        ["Zona", f"Ventas {fecha.isoformat()}", f"Ventas {anterior.isoformat()}",
         "Variación (COP)", "Variación (%)", f"Transacciones {fecha.isoformat()}",
         f"Transacciones {anterior.isoformat()}", "Nota"],
        {2: N_COP, 3: N_COP, 4: N_COP, 5: N_PCT, 6: N_COP, 7: N_COP},
        total={1: "TOTAL", 2: tot["ventas_actual"], 3: tot["ventas_previo"],
               4: int(t["variacion"]),
               5: None if np.isnan(t["variacion_pct"]) else float(t["variacion_pct"]),
               6: tot["trx_actual"], 7: tot["trx_previo"]},
    )
    _nota(ws, fila + 1, "Variación (%) = (ventas del día − ventas del día anterior) / ventas del día anterior. "
                        "Queda vacía cuando no hubo ventas el día anterior.")
    ws.freeze_panes = "A2"

    # Hoja 3: Calidad ------------------------------------------------------------
    ws = wb.create_sheet("Calidad")
    resumen, detalle = datos_calidad(conn)
    fila = _escribir_tabla(ws, resumen, ["Archivo", "Tipo", "Motivo", "Registros"], {4: N_COP})
    fila += 1
    _nota(ws, fila, "Detalle de registros descartados. Las versiones anteriores de una transacción y los "
                    "duplicados exactos solo se cuentan arriba.")
    _escribir_tabla(ws, detalle, ["Archivo", "Fila", "Id", "Motivo", "Detalle"], {}, fila_inicial=fila + 1)
    _ajustar_anchos(ws)
    ws.freeze_panes = "A2"

    # Calidad ya se ajustó arriba con el ancho máximo por defecto; las otras dos usan 45.
    for hoja in (wb["Resumen"], wb["Comparativo"]):
        _ajustar_anchos(hoja, maximo=45)

    Path(carpeta).mkdir(parents=True, exist_ok=True)
    # El nombre lleva la fecha del reporte (aaaammdd), así cada día genera su archivo.
    ruta = Path(carpeta) / f"reporte_ventas_{fecha:%Y%m%d}.xlsx"
    wb.save(ruta)
    return ruta
