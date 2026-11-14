"""Correo para la Gerente Comercial: se arma siempre, se envía solo si se pide.

No hay servidor de correo en la prueba, así que el pipeline deja un archivo
.eml (con el Excel adjunto) que se abre con Outlook o Thunderbird y se puede
enviar tal cual. Con --enviar y las variables SMTP_HOST, SMTP_PORT, SMTP_USER y
SMTP_PASSWORD el mismo mensaje sale por SMTP. Las credenciales nunca viven en
el código ni en el repositorio.

Doble envío: antes de enviar se crea de forma atómica la marca
salida/.enviados/AAAAMMDD. Si la marca ya existe, no se envía otra vez, aunque
el job se reintente o corran dos ejecutores a la vez. Si el envío falla, la
marca se borra para que el reintento sí pueda enviar.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as cfg

# Valores por defecto de la prueba. En producción se cambian con las variables de
# entorno REPORTE_REMITENTE y REPORTE_DESTINATARIOS, sin tocar el código.
REMITENTE_POR_DEFECTO = "automatizacion@pagared.com"
DESTINATARIO_POR_DEFECTO = "gerencia.comercial@pagared.com"


def _cop(n) -> str:
    """Formato de pesos colombianos: 916205700 -> $916.205.700 (punto de miles)."""
    return "$" + f"{int(n):,}".replace(",", ".")


def _pct(p) -> str:
    """Porcentaje con signo y coma decimal: 0.147 -> +14,7 %."""
    return f"{p * 100:+.1f}".replace(".", ",") + " %"


def cuerpo_texto(fecha: date, comparativo: pd.DataFrame) -> str:
    """Texto del correo: total del día, variación contra el día anterior y una línea por zona."""
    ventas = int(comparativo["ventas_actual"].sum())
    previo = int(comparativo["ventas_previo"].sum())
    trx = int(comparativo["trx_actual"].sum())
    # Solo hay porcentaje total si el día anterior tuvo ventas; si no, se dice en palabras.
    if previo > 0:
        contra = f"{_pct((ventas - previo) / previo)} contra el día anterior"
    else:
        contra = "sin ventas el día anterior para comparar"
    # El número de transacciones se formatea con punto de miles (2.444). El cambio
    # de coma a punto se hace solo sobre ese número: si se hiciera sobre toda la
    # línea, también cambiaría la coma decimal del porcentaje (+18,0 -> +18.0).
    trx_txt = f"{trx:,}".replace(",", ".")
    lineas = [
        "Hola Laura,",
        "",
        f"Ventas del {fecha.isoformat()}: {_cop(ventas)} en {trx_txt} transacciones ({contra}).",
        "",
        "Por zona:",
    ]
    for r in comparativo.itertuples(index=False):
        # Una zona sin ventas en ninguno de los dos días no aporta nada al correo.
        if r.ventas_actual == 0 and r.ventas_previo == 0:
            continue
        if np.isnan(r.variacion_pct):
            var = "sin base de comparación"
        else:
            var = _pct(r.variacion_pct)
        lineas.append(f"  - {r.zona}: {_cop(r.ventas_actual)} ({var})")
    lineas += [
        "",
        "El detalle por zona y producto, el comparativo y los registros descartados o "
        "corregidos van en el Excel adjunto.",
    ]
    return "\n".join(lineas)


def construir_correo(fecha: date, ruta_xlsx: Path, comparativo: pd.DataFrame,
                     remitente: str | None = None, destinatarios: list[str] | None = None) -> EmailMessage:
    """Arma el mensaje (asunto, cuerpo y Excel adjunto) sin enviarlo.

    Remitente y destinatarios salen de los argumentos o, si no vienen, de las
    variables de entorno y, en último caso, de los valores por defecto.
    """
    remitente = remitente or os.environ.get("REPORTE_REMITENTE", REMITENTE_POR_DEFECTO)
    if destinatarios is None:
        crudo = os.environ.get("REPORTE_DESTINATARIOS", DESTINATARIO_POR_DEFECTO)
        destinatarios = [d.strip() for d in crudo.split(",") if d.strip()]
    msg = EmailMessage()  # EmailMessage de la librería estándar arma bien las cabeceras y la codificación
    msg["Subject"] = f"Reporte de ventas del {fecha.isoformat()}"
    msg["From"] = remitente
    msg["To"] = ", ".join(destinatarios)
    msg["Date"] = format_datetime(datetime.now(cfg.TZ_COLOMBIA))  # fecha con zona horaria de Colombia
    msg["Message-ID"] = make_msgid(domain="pagared.com")  # identificador único que exigen los clientes de correo
    msg.set_content(cuerpo_texto(fecha, comparativo))
    ruta_xlsx = Path(ruta_xlsx)
    # El tipo MIME largo es el oficial de los archivos .xlsx.
    msg.add_attachment(
        ruta_xlsx.read_bytes(), maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=ruta_xlsx.name,
    )
    return msg


def guardar_eml(msg: EmailMessage, carpeta: str, fecha: date) -> Path:
    """Guarda el mensaje como archivo .eml, que Outlook y Thunderbird abren listo para enviar."""
    ruta = Path(carpeta) / f"correo_ventas_{fecha:%Y%m%d}.eml"
    ruta.write_bytes(bytes(msg))
    return ruta


def enviar_una_vez(msg: EmailMessage, fecha: date, carpeta: str) -> bool:
    """Envía por SMTP si no se había enviado ya. Devuelve True si envió."""
    # La "marca" es un archivo vacío por fecha. Crearlo es la forma de reservar el
    # envío de ese día: solo uno puede crearlo, aunque dos procesos corran a la vez.
    marcas = Path(carpeta) / ".enviados"
    marcas.mkdir(parents=True, exist_ok=True)
    marca = marcas / f"{fecha:%Y%m%d}"
    try:
        with open(marca, "x", encoding="utf-8") as f:  # 'x' falla si ya existe
            f.write(datetime.now(cfg.TZ_COLOMBIA).isoformat())
    except FileExistsError:
        return False  # ya se envió (o está enviándose): no se envía otra vez
    try:
        # Los datos de conexión vienen de variables de entorno, nunca del código.
        host = os.environ["SMTP_HOST"]
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())  # cifra la conexión antes de enviar la contraseña
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(msg)
    except Exception:
        # Si el envío falló se borra la marca para que el reintento pueda enviar,
        # y se vuelve a lanzar el error para que el pipeline falle y alerte.
        marca.unlink(missing_ok=True)
        raise
    return True
