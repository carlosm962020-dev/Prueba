"""Pruebas de la carga al modelo: repetir una carga, el delta, el orden de llegada, la atomicidad y la historia de zonas."""
import pytest

from etl import modelo
from tests.conftest import cargar, tx


def n_hechos(conn):
    return conn.execute("SELECT COUNT(*) FROM fact_transacciones").fetchone()[0]


def estado_de(conn, id_):
    return conn.execute(
        "SELECT e.estado FROM fact_transacciones f JOIN dim_estado e USING (estado_key) "
        "WHERE id_transaccion = ?", (id_,)).fetchone()[0]


BASE = [tx("A", "2026-10-27 15:00:00", fa="2026-10-27 15:00:02"),
        tx("B", "2026-10-27 16:00:00", cod="00118", producto="GIRO", valor="500000")]


def test_cargar_dos_veces_el_mismo_archivo_no_duplica(conn):
    primero, _ = cargar(conn, BASE)
    segundo, _ = cargar(conn, BASE)
    assert primero == {"insertados": 2, "actualizados": 0, "ignorados": 0}
    assert segundo == {"insertados": 0, "actualizados": 0, "ignorados": 2}
    assert n_hechos(conn) == 2


def test_delta_actualiza_estado_inserta_nuevos_e_ignora_viejos(conn):
    cargar(conn, BASE)
    conteo, _ = cargar(conn, [
        tx("A", "2026-10-27 15:00:00", fa="2026-10-27 22:00:00", estado="ANULADA"),   # cambio de estado
        tx("B", "2026-10-27 16:00:00", fa="2026-10-27 15:59:00", cod="00118",
           producto="GIRO", valor="500000", estado="ANULADA"),                         # más viejo que lo cargado
        tx("C", "2026-10-28 09:00:00"),                                                # nuevo
    ], archivo="delta.csv")
    assert conteo == {"insertados": 1, "actualizados": 1, "ignorados": 1}
    assert estado_de(conn, "A") == "ANULADA"
    assert estado_de(conn, "B") == "APROBADA"   # el dato viejo no pisó al vigente
    assert n_hechos(conn) == 3


def test_el_orden_de_llegada_de_los_archivos_no_cambia_el_resultado(conn):
    delta = [tx("A", "2026-10-27 15:00:00", fa="2026-10-27 22:00:00", estado="REVERSADA")]
    cargar(conn, delta, archivo="delta.csv")
    cargar(conn, BASE)  # el principal "viejo" llega después del delta
    assert estado_de(conn, "A") == "REVERSADA"


def test_si_la_carga_falla_no_queda_nada_a_medias(conn):
    r_filas = BASE + [tx("C", "2026-10-28 09:00:00")]
    from tests.conftest import limpiar
    r = limpiar(conn, r_filas)
    with pytest.raises(RuntimeError):
        with conn:
            modelo.cargar_hechos(conn, r.limpio, "2026-10-28 00:00:00")
            raise RuntimeError("falla simulada después de insertar")
    assert n_hechos(conn) == 0


def test_cambio_de_zona_conserva_la_historia_de_los_hechos(conn):
    import pandas as pd
    cargar(conn, [tx("H1", "2026-10-26 15:00:00")])           # 26/10, punto 00101 en Valle de Aburrá
    nuevo_maestro = pd.DataFrame(
        [("00101", "Punto A", "Sur", 1, "2015-02-10")],
        columns=["cod_punto", "nombre", "zona", "activo", "fecha_apertura"])
    with conn:
        c = modelo.cargar_maestro(conn, nuevo_maestro, "2026-10-27")  # rige desde el 27/10
    assert c["cambios_zona"] == 1
    cargar(conn, [tx("H2", "2026-10-27 15:00:00")])
    zonas = dict(conn.execute("SELECT id_transaccion, zona FROM vw_ventas"))
    assert zonas == {"H1": "Valle de Aburrá", "H2": "Sur"}    # el pasado no se mueve
