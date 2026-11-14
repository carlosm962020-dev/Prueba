"""Pruebas del correo: asunto, cuerpo y adjunto, envío único por fecha y reintento cuando el envío falla (SMTP simulado)."""
import smtplib
from datetime import date

import pytest

from etl import correo, reporte
from tests.conftest import cargar, tx

DIA = date(2026, 10, 27)


class SMTPFalso:
    enviados = []
    falla = False

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, **k):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        if SMTPFalso.falla:
            raise ConnectionError("SMTP caído")
        SMTPFalso.enviados.append(msg["Subject"])


@pytest.fixture
def mensaje(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", SMTPFalso)
    monkeypatch.setenv("SMTP_HOST", "smtp.ejemplo.test")
    SMTPFalso.enviados, SMTPFalso.falla = [], False
    cargar(conn, [tx("1", "2026-10-26 15:00:00", valor="1000"), tx("2", "2026-10-27 15:00:00", valor="3000")])
    ruta = reporte.generar_excel(conn, DIA, str(tmp_path))
    return correo.construir_correo(DIA, ruta, reporte.datos_comparativo(conn, DIA)), tmp_path


def test_el_correo_lleva_asunto_cuerpo_y_el_excel_adjunto(mensaje):
    msg, _ = mensaje
    assert msg["Subject"] == "Reporte de ventas del 2026-10-27"
    assert [a.get_filename() for a in msg.iter_attachments()] == ["reporte_ventas_20261027.xlsx"]
    cuerpo = msg.get_body().get_content()
    assert "$3.000" in cuerpo and "+200,0 %" in cuerpo
    # El porcentaje del total debe llevar coma decimal, igual que el de cada zona.
    assert "(+200,0 % contra el día anterior)" in cuerpo


def test_no_se_envia_dos_veces_el_mismo_dia(mensaje):
    msg, carpeta = mensaje
    assert correo.enviar_una_vez(msg, DIA, str(carpeta)) is True
    assert correo.enviar_una_vez(msg, DIA, str(carpeta)) is False   # reintento de Jenkins
    assert SMTPFalso.enviados == ["Reporte de ventas del 2026-10-27"]


def test_si_el_envio_falla_el_reintento_si_puede_enviar(mensaje):
    msg, carpeta = mensaje
    SMTPFalso.falla = True
    with pytest.raises(ConnectionError):
        correo.enviar_una_vez(msg, DIA, str(carpeta))
    SMTPFalso.falla = False
    assert correo.enviar_una_vez(msg, DIA, str(carpeta)) is True
