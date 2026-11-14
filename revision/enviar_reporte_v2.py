"""Envío diario del reporte comercial. Versión corregida de enviar_reporte.py.

Los problemas del original y el porqué de cada cambio están en
docs/seccion5_revision_codigo.md.

Variables de entorno (ninguna credencial vive en el código ni se imprime):
    DB_SERVER, DB_NAME (por defecto 'ventas'), DB_USER, DB_PASSWORD
    SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_STARTTLS (1)
    REPORTE_REMITENTE, REPORTE_DESTINATARIOS (separados por coma)
    ENVIOS_DIR   carpeta PERSISTENTE con las marcas de envío (por defecto .envios).
                 En Jenkins no debe ser el workspace si este se limpia entre corridas.

Uso:
    python enviar_reporte_v2.py                    # reporte del día anterior (hora Colombia)
    python enviar_reporte_v2.py --fecha 2026-10-27
    python enviar_reporte_v2.py --dry-run          # arma el correo y lo imprime, sin enviar

Código de salida (Jenkins marca rojo con cualquiera distinto de 0):
    0  correo enviado, o ya se había enviado antes para esa fecha
    1  falla técnica (base de datos, SMTP, configuración)
    2  no hay ventas en la fecha: el archivo del core pudo no llegar
"""
from __future__ import annotations

import argparse
import logging
import os
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

# Colombia está en UTC-5 todo el año. Se calcula "ayer" en su hora, no en la del servidor.
TZ_COLOMBIA = timezone(timedelta(hours=-5), "America/Bogota")
ZONAS = ("Valle de Aburrá", "Oriente", "Suroeste", "Norte", "Urabá")

# Consulta parametrizada y agregada en la base: no se traen miles de filas para sumarlas aquí.
SQL_VENTAS = (
    "SELECT zona, COUNT(*) AS transacciones, SUM(valor) AS ventas "
    "FROM ventas WHERE fecha = ? GROUP BY zona"
)

log = logging.getLogger("enviar_reporte")


def variable(nombre: str, defecto: str | None = None) -> str:
    """Lee una variable de entorno y falla con un mensaje claro si falta (así no se envía a medias)."""
    valor = os.environ.get(nombre, defecto)
    if valor in (None, ""):
        raise RuntimeError(f"Falta la variable de entorno {nombre}")
    return valor


def conectar():
    """Abre la conexión a SQL Server con las credenciales de las variables de entorno (nada va escrito en el código)."""
    import pyodbc  # import perezoso: el módulo se puede importar y probar sin el driver ODBC

    cadena = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={variable('DB_SERVER')};DATABASE={variable('DB_NAME', 'ventas')};"
        f"UID={variable('DB_USER')};PWD={variable('DB_PASSWORD')};"
        # Conexión cifrada, con el certificado del servidor verificado, y con tiempo límite
        # para que un servidor caído no deje el job colgado.
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30"
    )
    return pyodbc.connect(cadena, timeout=30)


def obtener_ventas(conn, fecha: date) -> dict[str, tuple[int, Decimal]]:
    """Ventas por zona: {zona: (transacciones, valor)}. Las cinco zonas siempre están presentes."""
    cur = conn.cursor()
    try:
        cur.execute(SQL_VENTAS, fecha)  # la fecha va como parámetro: no se arma el SQL con texto
        # Decimal y no float para el dinero: el float acumula errores de redondeo al sumar.
        filas = {
            r[0]: (int(r[1]), Decimal(str(r[2])) if r[2] is not None else Decimal(0))
            for r in cur.fetchall()
        }
    finally:
        cur.close()
    # Una zona nueva (o escrita distinto) no se descarta en silencio: se muestra y se avisa.
    inesperadas = sorted(set(filas) - set(ZONAS))
    if inesperadas:
        log.warning("Zonas que no están en la lista esperada: %s", inesperadas)
    # Las cinco zonas salen siempre, con ceros si no vendieron, para que el correo tenga la misma forma cada día.
    resultado = {z: filas.get(z, (0, Decimal(0))) for z in ZONAS}
    resultado.update({z: filas[z] for z in inesperadas})
    return resultado


def _cop(n: Decimal) -> str:
    """Formato de pesos: 916205700 -> $916.205.700."""
    return "$" + f"{int(n):,}".replace(",", ".")


def construir_mensaje(fecha: date, ventas: dict, remitente: str, destinatarios: list[str]) -> EmailMessage:
    """Un solo correo por día, con una línea por zona. La lista de destinatarios es nueva en cada llamada."""
    msg = EmailMessage()
    msg["Subject"] = f"Reporte de ventas del {fecha.isoformat()}"
    msg["From"] = remitente
    msg["To"] = ", ".join(destinatarios)
    total_trx = sum(t for t, _ in ventas.values())
    total_val = sum((v for _, v in ventas.values()), Decimal(0))  # el 2.º argumento es el valor inicial de la suma
    # Tabla de texto con columnas alineadas: < izquierda, > derecha, y el número es el ancho.
    lineas = [f"Ventas del {fecha.isoformat()}", "", f"{'Zona':<18}{'Transacciones':>15}{'Ventas':>20}"]
    for zona, (trx, valor) in ventas.items():
        marca = "   <- sin ventas" if trx == 0 else ""
        lineas.append(f"{zona:<18}{trx:>15,}{_cop(valor):>20}{marca}".replace(",", "."))
    lineas.append(f"{'TOTAL':<18}{total_trx:>15,}{_cop(total_val):>20}".replace(",", "."))
    msg.set_content("\n".join(lineas))
    return msg


def enviar_una_vez(msg: EmailMessage, fecha: date) -> bool:
    """Envía por SMTP si no se había enviado ya esa fecha. Devuelve True si envió.

    La marca se crea de forma atómica ANTES de enviar: un reintento de Jenkins, o dos
    ejecutores a la vez, no pueden enviar dos veces. Si el envío falla, la marca se borra
    para que el reintento sí envíe.
    """
    # Las marcas de envío viven en una carpeta aparte y persistente: si estuvieran
    # en el workspace de Jenkins y este se limpia, el control de doble envío se perdería.
    carpeta = Path(os.environ.get("ENVIOS_DIR", ".envios"))
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = carpeta / f"{fecha:%Y%m%d}"
    try:
        with open(marca, "x", encoding="utf-8") as f:  # modo "x": falla si el archivo ya existe
            f.write(datetime.now(TZ_COLOMBIA).isoformat())
    except FileExistsError:
        return False  # ya se envió ese día
    try:
        with smtplib.SMTP(variable("SMTP_HOST"), int(os.environ.get("SMTP_PORT", "587")), timeout=30) as smtp:
            if os.environ.get("SMTP_STARTTLS", "1") == "1":
                smtp.starttls(context=ssl.create_default_context())
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], variable("SMTP_PASSWORD"))
            smtp.send_message(msg)
    except Exception:
        marca.unlink(missing_ok=True)  # el envío falló: se libera la marca para poder reintentar
        raise  # y el error sube a main(), que lo registra y devuelve código 1
    return True


def _fecha(texto: str) -> date:
    """Convierte --fecha a date; argparse muestra el mensaje si el formato no es AAAA-MM-DD."""
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{texto}' no es una fecha válida (use AAAA-MM-DD)")


def main(argv=None) -> int:
    """Punto de entrada. Devuelve 0 (enviado o ya enviado), 1 (falla técnica) o 2 (no hay ventas)."""
    p = argparse.ArgumentParser(description="Envía el reporte comercial de ventas por zona.")
    p.add_argument("--fecha", type=_fecha, default=None, help="AAAA-MM-DD; por defecto, el día anterior.")
    p.add_argument("--dry-run", action="store_true", help="Arma el correo y lo imprime, sin enviarlo.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    fecha = args.fecha or (datetime.now(TZ_COLOMBIA).date() - timedelta(days=1))
    try:
        # Cualquier error se captura al final y se registra: nunca se "traga" en silencio como en el script original.
        conn = conectar()
        try:
            ventas = obtener_ventas(conn, fecha)
        finally:
            conn.close()

        total_trx = sum(t for t, _ in ventas.values())
        # Cero ventas en toda la compañía casi seguro significa que el archivo no llegó:
        # se corta con código 2 para que Jenkins alerte, y no se manda un reporte vacío.
        if total_trx == 0:
            log.error("No hay ventas para %s. El archivo del core pudo no llegar; no se envía el reporte.",
                      fecha.isoformat())
            return 2
        for zona, (trx, _) in ventas.items():
            if trx == 0:
                log.warning("La zona %s no tiene ventas el %s.", zona, fecha.isoformat())

        destinatarios = [d.strip() for d in variable("REPORTE_DESTINATARIOS").split(",") if d.strip()]
        msg = construir_mensaje(fecha, ventas, variable("REPORTE_REMITENTE", "etl@pagared.com"), destinatarios)
        if args.dry_run:  # prueba sin enviar: imprime el correo y termina
            print(msg)
            return 0
        if enviar_una_vez(msg, fecha):
            log.info("Correo enviado a %d destinatario(s) con %d transacciones.", len(destinatarios), total_trx)
        else:
            log.warning("El reporte de %s ya se había enviado; no se envía de nuevo.", fecha.isoformat())
        return 0
    except Exception:
        log.exception("El envío del reporte falló")  # el traceback queda en el log, sin secretos
        return 1


if __name__ == "__main__":
    sys.exit(main())
