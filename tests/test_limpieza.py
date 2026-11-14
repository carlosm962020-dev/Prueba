"""Pruebas de las reglas de limpieza de transacciones y del maestro de puntos (sin base de datos de archivos)."""
import math

import pandas as pd

from etl import config as cfg
from etl import limpieza
from tests.conftest import limpiar, raw, tx


def test_parse_valor_acepta_los_tres_formatos_del_core():
    assert limpieza.parse_valor("10000.00") == (10000.0, False)
    assert limpieza.parse_valor("386800") == (386800.0, False)
    assert limpieza.parse_valor("1.299.000") == (1299000.0, True)
    assert limpieza.parse_valor("522.500") == (522500.0, True)  # miles, no 522 pesos con 500 milésimas
    assert math.isnan(limpieza.parse_valor("")[0])
    assert math.isnan(limpieza.parse_valor("abc")[0])


def test_normaliza_estado_codigo_y_fechas(conn):
    r = limpiar(conn, [
        tx("A1", "27/10/2026 13:00", cod="101", estado=" aprobada "),
        tx("A2", "2026-10-27T13:05:00Z", valor="1.299.000"),
    ])
    a1 = r.limpio.set_index("id_transaccion").loc["A1"]
    assert a1["estado"] == "APROBADA"
    assert a1["cod_punto"] == "00101"
    assert a1["fecha_hora_utc"] == pd.Timestamp("2026-10-27 13:00:00")
    assert a1["fecha_hora_col"] == pd.Timestamp("2026-10-27 08:00:00")  # UTC-5
    assert r.limpio.set_index("id_transaccion").loc["A2", "valor"] == 1299000
    assert r.correcciones[limpieza.C_ESTADO] == 1
    assert r.correcciones[limpieza.C_COD] == 1
    assert r.correcciones[limpieza.C_VALOR_MILES] == 1
    assert r.correcciones[limpieza.C_FECHA] == 2


def test_el_dia_de_negocio_es_hora_colombia_no_utc(conn):
    r = limpiar(conn, [
        tx("T1", "2026-10-28 04:59:59"),  # 23:59:59 del 27/10 en Colombia
        tx("T2", "2026-10-28 05:00:00"),  # 00:00:00 del 28/10 en Colombia
    ]).limpio.set_index("id_transaccion")
    assert r.loc["T1", "fecha"] == "2026-10-27"
    assert r.loc["T2", "fecha"] == "2026-10-28"


def test_se_conserva_la_version_con_mayor_fecha_de_actualizacion(conn):
    # La versión más reciente llega primero en el archivo: no debe importar el orden.
    r = limpiar(conn, [
        tx("D1", "2026-10-27 15:00:00", fa="2026-10-27 20:00:00", estado="ANULADA"),
        tx("D1", "2026-10-27 15:00:00", fa="2026-10-27 15:00:03", estado="APROBADA"),
        tx("D2", "2026-10-27 16:00:00"),
        tx("D2", "2026-10-27 16:00:00"),  # duplicado exacto
    ])
    assert r.limpio.set_index("id_transaccion").loc["D1", "estado"] == "ANULADA"
    motivos = r.descartados.set_index("id_transaccion")["motivo"]
    assert motivos["D1"] == cfg.M_VERSION_ANTERIOR
    assert motivos["D2"] == cfg.M_DUPLICADO_EXACTO


def test_descarta_pruebas_punto_inexistente_y_datos_incompletos(conn):
    filas = [
        tx("OK", "2026-10-27 10:00:00"),
        tx("P1", "2026-10-27 10:00:00", cod="90001"),
        tx("P2", "2026-10-27 10:00:00", cod="00199"),
        tx("P3", "2026-10-27 10:00:00", cod=""),
        tx("P4", "2026-10-27 10:00:00", valor=""),
        tx("P5", "2026-10-27 10:00:00", estado="PENDIENTE"),
        tx("P6", "no es fecha"),
    ]
    r = limpiar(conn, filas)
    assert list(r.limpio["id_transaccion"]) == ["OK"]
    motivos = dict(zip(r.descartados["id_transaccion"], r.descartados["motivo"]))
    assert motivos == {
        "P1": cfg.M_PRUEBAS, "P2": cfg.M_PUNTO_INEXISTENTE, "P3": cfg.M_COD_VACIO,
        "P4": cfg.M_VALOR, "P5": cfg.M_ESTADO, "P6": cfg.M_FECHA,
    }
    # Todo lo leído se carga o se descarta; el constructor ya lo exige.
    assert r.leidos == len(r.limpio) + len(r.descartados) == 7


def test_valor_vacio_se_completa_con_otra_version_de_la_transaccion(conn):
    r = limpiar(conn, [
        tx("V1", "2026-10-27 10:00:00", fa="2026-10-27 10:00:01", valor="50000.00"),
        tx("V1", "2026-10-27 10:00:00", fa="2026-10-27 12:00:00", valor="", estado="REVERSADA"),
    ])
    fila = r.limpio.iloc[0]
    assert (fila["estado"], fila["valor"]) == ("REVERSADA", 50000)
    assert r.correcciones[limpieza.C_VALOR_COMPLETADO] == 1


def test_maestro_codigo_repetido_zonas_y_punto_de_pruebas():
    m = limpieza.limpiar_maestro(raw([
        {"cod_punto": "00117", "nombre": "Llanogrande", "zona": "Oriente", "activo": "N", "fecha_apertura": "2017-01-09"},
        {"cod_punto": "00117", "nombre": "La Magnolia", "zona": "Valle de Aburrá", "activo": "S", "fecha_apertura": "2024-03-04"},
        {"cod_punto": "00104", "nombre": "Itagüí", "zona": "VALLE DE ABURRA", "activo": "S", "fecha_apertura": "2016-04-11"},
        {"cod_punto": "00133", "nombre": "Santa Fe", "zona": "", "activo": "S", "fecha_apertura": "2025-12-01"},
        {"cod_punto": "90001", "nombre": "PUNTO PRUEBAS SISTEMAS - NO USAR", "zona": "Oriente", "activo": "S", "fecha_apertura": "2015-01-01"},
    ]), "puntos.csv").limpio.set_index("cod_punto")
    assert m.loc["00117", "nombre"] == "La Magnolia"          # gana el activo
    assert m.loc["00104", "zona"] == "Valle de Aburrá"          # normalizada
    assert m.loc["00133", "zona"] == cfg.ZONA_SIN               # no se inventa una zona
    assert "90001" not in m.index
