"""Fixtures y utilidades compartidas por las pruebas (todo en memoria, sin archivos)."""
import pandas as pd
import pytest

from etl import limpieza, modelo

# Maestro mínimo de prueba: tres puntos en tres zonas. Se carga en una base en memoria.
MAESTRO = pd.DataFrame(
    [
        ("00101", "Punto A", "Valle de Aburrá", 1, "2015-02-10"),
        ("00118", "Punto B", "Oriente", 1, "2016-03-01"),
        ("00136", "Punto Apartadó", "Urabá", 1, "2026-10-27"),
    ],
    columns=limpieza.COLS_MAESTRO,
)


def tx(id_, ft, fa=None, cod="00101", producto="RECARGA", valor="10000.00", estado="APROBADA", canal="POS"):
    """Una fila cruda tal como llega del core (todo texto)."""
    return {"id_transaccion": id_, "fecha_transaccion": ft, "fecha_actualizacion": fa or ft,
            "cod_punto": cod, "producto": producto, "valor": valor, "estado": estado, "canal": canal}


def raw(filas):
    """Convierte las filas de prueba en el DataFrame que produciría leer_csv (con la columna 'fila')."""
    df = pd.DataFrame(filas, dtype=str)
    df.insert(0, "fila", range(2, len(df) + 2))
    return df


def limpiar(conn, filas, archivo="t.csv"):
    """Solo limpia, sin cargar: sirve para probar las reglas de descarte y corrección."""
    return limpieza.limpiar_transacciones(raw(filas), modelo.codigos_de_puntos(conn), archivo)


def cargar(conn, filas, archivo="t.csv"):
    """Limpia y carga; devuelve (conteo, resultado_de_limpieza)."""
    r = limpiar(conn, filas, archivo)
    with conn:
        conteo = modelo.cargar_hechos(conn, r.limpio, "2026-10-28 00:00:00")
    return conteo, r


@pytest.fixture
def conn():
    """Base SQLite en memoria con el esquema y el maestro cargados; cada prueba recibe una nueva y se descarta al terminar."""
    c = modelo.conectar(":memory:")
    modelo.crear_esquema(c)
    with c:
        modelo.cargar_maestro(c, MAESTRO, "2026-01-01")
    yield c
    c.close()
