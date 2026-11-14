"""Pruebas del reporte: qué estados cuentan como venta, el comparativo con zonas nuevas y el Excel de tres hojas."""
from datetime import date

import numpy as np
from openpyxl import load_workbook

from etl import reporte
from tests.conftest import cargar, tx

DIA = date(2026, 10, 27)


def test_solo_cuentan_aprobadas_y_reversadas(conn):
    cargar(conn, [
        tx("1", "2026-10-27 15:00:00", valor="1000"),
        tx("2", "2026-10-27 15:00:00", valor="2000", estado="REVERSADA"),
        tx("3", "2026-10-27 15:00:00", valor="4000", estado="ANULADA"),
        tx("4", "2026-10-27 15:00:00", valor="8000", estado="RECHAZADA"),
    ])
    comp = reporte.datos_comparativo(conn, DIA).set_index("zona")
    assert comp.loc["Valle de Aburrá", "ventas_actual"] == 3000
    assert comp.loc["Valle de Aburrá", "trx_actual"] == 2


def test_zona_nueva_sin_ventas_previas_no_rompe_el_comparativo(conn):
    cargar(conn, [
        tx("1", "2026-10-26 15:00:00", valor="1000"),
        tx("2", "2026-10-27 15:00:00", valor="3000"),
        tx("3", "2026-10-27 15:00:00", valor="5000", cod="00136"),  # Urabá abre el 27/10
    ])
    comp = reporte.datos_comparativo(conn, DIA).set_index("zona")
    assert comp.loc["Valle de Aburrá", "variacion_pct"] == 2.0       # +200 %
    assert np.isnan(comp.loc["Urabá", "variacion_pct"])               # sin base: vacío, no error ni infinito
    assert "Sin ventas el día anterior" in comp.loc["Urabá", "nota"]
    assert comp.loc["Oriente", "ventas_actual"] == 0                  # sin operación: cero, no desaparece


def test_el_resumen_trae_todas_las_combinaciones_zona_producto(conn):
    cargar(conn, [tx("1", "2026-10-27 15:00:00")])
    res = reporte.datos_resumen(conn, DIA)
    assert len(res) == 5 * 3                       # 5 zonas x 3 productos, con ceros explícitos
    assert res["ventas"].sum() == 10000
    assert (res["ventas"] == 0).sum() == 14


def test_excel_con_las_tres_hojas_pedidas(conn, tmp_path):
    cargar(conn, [tx("1", "2026-10-27 15:00:00")])
    ruta = reporte.generar_excel(conn, DIA, str(tmp_path))
    assert ruta.name == "reporte_ventas_20261027.xlsx"
    assert load_workbook(ruta).sheetnames == ["Resumen", "Comparativo", "Calidad"]
