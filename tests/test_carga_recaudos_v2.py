"""Reproduce el incidente de carga_recaudos_bi y comprueba que la versión corregida lo detecta."""
import sqlite3
import sys
import types
from datetime import date

import pytest

from infra.incidente import carga_recaudos_v2 as c

DIA = date(2026, 10, 26)


def escribir(carpeta, nombre, filas):
    cuerpo = "id_recaudo;cod_punto;cod_convenio;valor;estado\n" + "\n".join(";".join(map(str, f)) for f in filas)
    (carpeta / nombre).write_text(cuerpo + "\n", encoding="utf-8")


def control(carpeta, dia, partes, registros, valor_total):
    extra = f"partes={partes}\n" if partes > 1 else ""
    (carpeta / f"recaudos_{dia}.ctl").write_text(
        f"archivo=recaudos_{dia}\n{extra}registros={registros}\nvalor_total={valor_total}\n", encoding="utf-8")


PARTE1 = [(1, "00101", "C1", 1000, "APROBADA"), (2, "00101", "C1", 500, "RECHAZADA"), (3, "00102", "C2", 2000, "REVERSADA")]
PARTE2 = [(4, "00103", "C1", 4000, "APROBADA"), (5, "00103", "C3", 300, "ANULADA")]


def nueva_base():
    k = sqlite3.connect(":memory:")
    k.execute("ATTACH DATABASE ':memory:' AS dw")
    k.execute("CREATE TABLE dw.fact_recaudos (id_recaudo INTEGER, fecha TEXT, cod_punto TEXT, cod_convenio TEXT, "
              "valor NUMERIC, estado TEXT)")
    k.execute("CREATE TABLE dw.control_cargas (fecha TEXT, partes INT, registros_ctl INT, valor_ctl INT, "
              "registros_leidos INT, registros_validos INT, valor_cargado INT, cargado_en TEXT)")
    k.commit()
    return k


@pytest.fixture
def conn():
    k = nueva_base()
    yield k
    k.close()


@pytest.fixture
def dos_partes(tmp_path):
    escribir(tmp_path, "recaudos_20261026_parte1.csv", PARTE1)
    escribir(tmp_path, "recaudos_20261026_parte2.csv", PARTE2)
    control(tmp_path, "20261026", 2, 5, 7800)   # 1000+500+2000+4000+300, todos los estados
    return tmp_path


def suma(conn):
    return conn.execute("SELECT COUNT(*), SUM(valor) FROM dw.fact_recaudos").fetchone()


def test_carga_las_dos_partes_y_cuadra_con_el_control(conn, dos_partes):
    c.ejecutar(DIA, str(dos_partes), conn)
    assert suma(conn) == (3, 7000)   # válidas: 1000 + 2000 + 4000 (el original habría cargado solo la parte 2)
    fila = conn.execute("SELECT partes, registros_ctl, registros_leidos, registros_validos FROM dw.control_cargas").fetchone()
    assert fila == (2, 5, 5, 3)


def test_si_falta_una_parte_falla_y_la_tabla_no_se_toca(conn, dos_partes):
    conn.execute("INSERT INTO dw.fact_recaudos VALUES (99, '2026-10-26', '00109', 'C9', 123, 'APROBADA')")
    conn.commit()
    (dos_partes / "recaudos_20261026_parte1.csv").unlink()
    with pytest.raises(c.ControlError, match="2 partes"):
        c.ejecutar(DIA, str(dos_partes), conn)
    assert suma(conn) == (1, 123)


def test_si_el_valor_no_cuadra_con_el_control_falla(conn, dos_partes):
    control(dos_partes, "20261026", 2, 5, 9999)
    with pytest.raises(c.ControlError, match="No cuadra con el control"):
        c.ejecutar(DIA, str(dos_partes), conn)
    assert suma(conn) == (0, None)


def test_si_faltan_registros_falla(conn, dos_partes):
    control(dos_partes, "20261026", 2, 6, 7800)
    with pytest.raises(c.ControlError, match="registros 5 vs 6"):
        c.ejecutar(DIA, str(dos_partes), conn)


def test_con_reenvio_v2_toma_solo_la_version_mas_reciente(conn, tmp_path):
    escribir(tmp_path, "recaudos_20261026.csv", [(1, "00101", "C1", 111, "APROBADA")])
    escribir(tmp_path, "recaudos_20261026_v2.csv", [(1, "00101", "C1", 222, "APROBADA")])
    control(tmp_path, "20261026", 1, 1, 222)
    c.ejecutar(DIA, str(tmp_path), conn)
    assert suma(conn) == (1, 222)


def test_recargar_el_mismo_dia_reemplaza_sin_duplicar(conn, dos_partes):
    c.ejecutar(DIA, str(dos_partes), conn)
    c.ejecutar(DIA, str(dos_partes), conn)
    assert suma(conn) == (3, 7000)


def test_si_el_insert_falla_a_mitad_se_conserva_lo_anterior(dos_partes):
    """Atomicidad: nadie ve una carga a medias ni queda la tabla vacía."""
    class Proxy:
        def __init__(self, real):
            self.real = real

        def cursor(self):
            real = self.real.cursor()

            class Cur:
                def __getattr__(self, n):
                    return getattr(real, n)

                def executemany(self, *a, **k):
                    raise RuntimeError("se cayó la conexión a mitad de carga")
            return Cur()

        def commit(self):
            self.real.commit()

        def rollback(self):
            self.real.rollback()

    k = nueva_base()
    k.execute("INSERT INTO dw.fact_recaudos VALUES (99, '2026-10-26', '00109', 'C9', 123, 'APROBADA')")
    k.commit()
    with pytest.raises(RuntimeError):
        c.ejecutar(DIA, str(dos_partes), Proxy(k))
    assert suma(k) == (1, 123)


def test_main_devuelve_codigo_distinto_de_cero_si_algo_falla(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_CONN", "no-se-usa")
    falso = types.ModuleType("pyodbc")
    falso.connect = lambda *a, **k: sqlite3.connect(":memory:")
    monkeypatch.setitem(sys.modules, "pyodbc", falso)
    assert c.main(["--fecha", "2026-10-26", "--entrada", str(tmp_path)]) == 1   # no hay archivos
