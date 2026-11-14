"""Pruebas de extremo a extremo del orquestador con archivos CSV pequeños."""
from pathlib import Path

from etl import pipeline

MAESTRO = ("cod_punto;nombre;zona;activo;fecha_apertura\r\n"
           "00101;Punto Medellín Centro;Valle de Aburrá;S;2015-02-10\r\n"
           "00118;Punto Rionegro;Oriente;S;2016-03-01\r\n")
CABECERA = "id_transaccion;fecha_transaccion;fecha_actualizacion;cod_punto;producto;valor;estado;canal\r\n"


def escribir(carpeta: Path, transacciones: str):
    carpeta.mkdir(exist_ok=True)
    (carpeta / "puntos_venta.csv").write_bytes(MAESTRO.encode("cp1252"))
    (carpeta / "transacciones.csv").write_bytes(("\ufeff" + CABECERA + transacciones).encode("utf-8"))


def correr(tmp_path, *extra):
    return pipeline.main(["--datos", str(tmp_path / "datos"), "--db", str(tmp_path / "t.db"),
                          "--salida", str(tmp_path / "salida"), "--log", str(tmp_path / "p.log"),
                          "--maestro-desde", "2026-10-28", *extra])


def test_corrida_completa_genera_reporte_y_correo(tmp_path):
    escribir(tmp_path / "datos",
             "TX1;2026-10-27 15:00:00;2026-10-27 15:00:01;00101;RECARGA;10000.00;APROBADA;POS\r\n"
             "TX2;27/10/2026 16:00;27/10/2026 16:00;118;GIRO;1.500.000;aprobada ;APP\r\n")
    assert correr(tmp_path, "--fecha-corte", "2026-10-27") == 0
    assert (tmp_path / "salida" / "reporte_ventas_20261027.xlsx").exists()
    assert (tmp_path / "salida" / "correo_ventas_20261027.eml").exists()


def test_falla_con_codigo_distinto_de_cero_si_el_archivo_no_llego(tmp_path):
    (tmp_path / "datos").mkdir()
    assert correr(tmp_path, "--fecha-corte", "2026-10-27") != 0


def test_falla_si_no_hay_ninguna_transaccion_en_la_fecha_de_corte(tmp_path):
    escribir(tmp_path / "datos",
             "TX1;2026-10-20 15:00:00;2026-10-20 15:00:01;00101;RECARGA;10000.00;APROBADA;POS\r\n")
    assert correr(tmp_path, "--fecha-corte", "2026-10-27") != 0


def test_falla_si_el_core_cambia_las_columnas(tmp_path):
    carpeta = tmp_path / "datos"
    carpeta.mkdir()
    (carpeta / "puntos_venta.csv").write_bytes(MAESTRO.encode("cp1252"))
    (carpeta / "transacciones.csv").write_text("id;fecha;monto\r\nTX1;2026-10-27;10\r\n", encoding="utf-8")
    assert correr(tmp_path, "--fecha-corte", "2026-10-27") != 0
