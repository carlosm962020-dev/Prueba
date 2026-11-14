"""Pipeline diario de ventas de PagaRed.

Uso (desde la raíz del repositorio):

    # Carga principal + reporte del día anterior
    python -m etl.pipeline

    # Reporte de una fecha concreta
    python -m etl.pipeline --fecha-corte 2026-10-27

    # Aplicar el incremental sobre lo ya cargado y regenerar el reporte
    python -m etl.pipeline --fecha-corte 2026-10-27 --delta datos/transacciones_delta_20261028.csv

Código de salida: 0 si todo salió bien; distinto de 0 ante cualquier falla
(archivo que no llegó, columnas que cambiaron, conteos que no cuadran, base
inalcanzable, ninguna transacción en la fecha de corte...). Jenkins marca rojo
solo si el código no es 0, por eso este módulo nunca se traga una excepción.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config as cfg
from . import correo, exportar, limpieza, modelo, reporte

# Logger con nombre propio. Escribe en consola y en logs/pipeline.log (ver configurar_log).
log = logging.getLogger("pipeline")


def _fecha(texto: str) -> date:
    """Convierte el texto de --fecha-corte a fecha; argparse muestra el mensaje si el formato es inválido."""
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{texto}' no es una fecha válida (use AAAA-MM-DD)")


def hoy_colombia() -> date:
    """Fecha de hoy en Colombia (no la del servidor, que puede estar en UTC)."""
    return datetime.now(cfg.TZ_COLOMBIA).date()


def parse_args(argv=None) -> argparse.Namespace:
    """Define las opciones de la línea de comandos. Todas son opcionales y tienen un valor por defecto."""
    p = argparse.ArgumentParser(
        prog="python -m etl.pipeline",
        description="Carga las transacciones del core, las deja en un modelo dimensional y "
                    "genera el reporte diario de ventas.",
    )
    p.add_argument("--fecha-corte", type=_fecha, default=None, metavar="AAAA-MM-DD",
                   help="Día del reporte (hora Colombia). Por defecto, el día anterior.")
    p.add_argument("--datos", default="datos", help="Carpeta con transacciones.csv y puntos_venta.csv.")
    p.add_argument("--db", default="salida/pagared.db", help="Base SQLite del modelo.")
    p.add_argument("--salida", default="salida", help="Carpeta del reporte Excel y del correo.")
    p.add_argument("--delta", default=None, metavar="ARCHIVO",
                   help="Aplica este incremental sobre lo ya cargado (no recarga el archivo principal).")
    p.add_argument("--maestro-desde", type=_fecha, default=None, metavar="AAAA-MM-DD",
                   help="Desde cuándo rige un cambio de zona detectado en el maestro. Por defecto, hoy.")
    p.add_argument("--parquet", action="store_true",
                   help="Exporta la tabla de hechos a Parquet particionado por fecha.")
    p.add_argument("--exportar-csv", default=None, metavar="CARPETA",
                   help="Exporta las tablas del modelo a CSV (respaldo para Power BI).")
    p.add_argument("--enviar", action="store_true",
                   help="Envía el correo por SMTP (una sola vez por fecha). Sin esta opción solo se arma el .eml.")
    p.add_argument("--log", default="logs/pipeline.log", help="Archivo de log.")
    return p.parse_args(argv)


def configurar_log(ruta: str) -> None:
    """Configura el log para que cada mensaje salga por consola y quede en el archivo."""
    # Se quitan los manejadores de una ejecución anterior (por ejemplo, en las
    # pruebas) para no repetir cada línea dos veces.
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    log.setLevel(logging.INFO)
    log.propagate = False  # no pasar los mensajes al logger raíz: evita duplicados
    formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    log.addHandler(consola)
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    archivo = logging.FileHandler(ruta, encoding="utf-8")
    archivo.setFormatter(formato)
    log.addHandler(archivo)


def _exigir(ruta: Path) -> Path:
    """Si el archivo no existe, falla con un mensaje claro: es la alerta de "no llegó el archivo"."""
    if not ruta.is_file():
        raise FileNotFoundError(f"No llegó el archivo esperado: {ruta}")
    return ruta


def _log_limpieza(r: limpieza.ResultadoLimpieza) -> None:
    """Escribe en el log cuántas filas se leyeron, cargaron, descartaron y corrigieron."""
    log.info("[%s] leídos=%d | válidos=%d | descartados=%d",
             r.archivo, r.leidos, len(r.limpio), len(r.descartados))
    if len(r.descartados):
        for motivo, n in r.descartados["motivo"].value_counts().items():
            log.info("    descartados (%d): %s", n, motivo)
    for motivo, n in r.correcciones.items():
        if n:
            log.info("    corregidos (%d): %s", n, motivo)


def ejecutar(args: argparse.Namespace) -> dict:
    """Corre el pipeline completo: leer, limpiar, cargar, validar, reporte y correo.

    Si algo falla lanza una excepción; main() la convierte en código de salida 1.
    """
    # Sin --fecha-corte el reporte es el del día anterior, que es el que Laura necesita cada mañana.
    fecha = args.fecha_corte or (hoy_colombia() - timedelta(days=1))
    vigente_desde = args.maestro_desde or hoy_colombia()
    datos = Path(args.datos)
    # En modo delta se lee el archivo incremental en lugar del principal.
    modo = "delta" if args.delta else "principal"
    log.info("Inicio | modo=%s | fecha_corte=%s", modo, fecha.isoformat())

    # Primero se verifica que lleguen los archivos: si falta alguno, se corta aquí.
    ruta_maestro = _exigir(datos / "puntos_venta.csv")
    ruta_tx = _exigir(Path(args.delta) if args.delta else datos / "transacciones.csv")
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)

    conn = modelo.conectar(args.db)
    try:
        modelo.crear_esquema(conn)
        # El maestro va primero porque las transacciones se validan contra sus códigos.
        maestro = limpieza.limpiar_maestro(limpieza.leer_csv(ruta_maestro), ruta_maestro.name)
        _log_limpieza(maestro)

        marca_carga = datetime.now(cfg.TZ_COLOMBIA).strftime(cfg.FMT_TS)
        # "with conn" hace commit si todo sale bien y rollback si algo lanza un error:
        # una sola transacción para maestro, hechos, calidad y bitácora. Si algo falla
        # a mitad, la base queda como estaba y se puede reintentar sin duplicados.
        with conn:
            cambios = modelo.cargar_maestro(conn, maestro.limpio, vigente_desde.isoformat())
            log.info("[maestro] puntos nuevos=%d | cambios de zona=%d | otros cambios=%d",
                     cambios["nuevos"], cambios["cambios_zona"], cambios["cambios_otros"])
            modelo.registrar_calidad(conn, maestro)

            tx = limpieza.limpiar_transacciones(
                limpieza.leer_csv(ruta_tx), modelo.codigos_de_puntos(conn), ruta_tx.name)
            _log_limpieza(tx)
            conteo = modelo.cargar_hechos(conn, tx.limpio, marca_carga)
            modelo.registrar_calidad(conn, tx)
            modelo.registrar_carga(conn, ruta_tx.name, modo, tx, conteo)
        # Mismo conteo, dos redacciones: en la carga principal "ignorados" son filas
        # que ya estaban cargadas; en el delta, filas que llegaron más viejas que lo guardado.
        if modo == "delta":
            log.info("[%s] insertados=%d | actualizados=%d | ignorados=%d", ruta_tx.name,
                     conteo["insertados"], conteo["actualizados"], conteo["ignorados"])
        else:
            log.info("[%s] insertados=%d | actualizados=%d | ya cargados (ignorados)=%d", ruta_tx.name,
                     conteo["insertados"], conteo["actualizados"], conteo["ignorados"])

        # Validación de negocio: si la fecha del reporte no tiene ninguna transacción,
        # lo más probable es que el archivo llegó incompleto. Es mejor fallar y avisar
        # que enviar a Laura un reporte en ceros.
        hay = conn.execute(
            "SELECT COUNT(*) FROM fact_transacciones WHERE fecha_key = ?", (int(fecha.strftime("%Y%m%d")),)
        ).fetchone()[0]
        if hay == 0:
            raise RuntimeError(
                f"No hay ninguna transacción para la fecha de corte {fecha.isoformat()}: "
                "el archivo del core pudo no llegar o llegar incompleto.")

        # El reporte y el correo se generan después de cargar, y leen de la base (no de los DataFrames).
        ruta_excel = reporte.generar_excel(conn, fecha, args.salida)
        comparativo = reporte.datos_comparativo(conn, fecha)
        log.info("Reporte generado: %s", ruta_excel)

        msg = correo.construir_correo(fecha, ruta_excel, comparativo)
        eml = correo.guardar_eml(msg, args.salida, fecha)
        log.info("Correo listo para enviar: %s", eml)
        # Por defecto solo se deja el .eml; el envío real por SMTP es opcional (--enviar).
        if args.enviar:
            if correo.enviar_una_vez(msg, fecha, args.salida):
                log.info("Correo enviado a %s", msg["To"])
            else:
                log.warning("El correo de %s ya se había enviado; no se envía de nuevo.", fecha.isoformat())

        if args.exportar_csv:
            n = len(exportar.exportar_csv(conn, args.exportar_csv))
            log.info("Exportadas %d tablas a CSV en %s", n, args.exportar_csv)
        if args.parquet:
            log.info("Parquet particionado por fecha en %s", exportar.exportar_parquet(conn, args.salida + "/parquet"))
    finally:
        conn.close()  # se cierra la conexión haya o no haya errores
    log.info("Fin OK")
    return {"excel": ruta_excel, "conteo": conteo}


def main(argv=None) -> int:
    """Punto de entrada. Devuelve 0 si todo salió bien y 1 si hubo cualquier falla."""
    args = parse_args(argv)
    configurar_log(args.log)
    try:
        ejecutar(args)
        return 0
    except Exception:
        # log.exception guarda el error completo (con su traza) en el log, y el
        # código 1 es lo que Jenkins usa para marcar la ejecución como fallida.
        log.exception("El pipeline falló")
        return 1


if __name__ == "__main__":
    sys.exit(main())
