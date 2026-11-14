"""Pruebas de revision/enviar_reporte_v2.py: cada una cierra un defecto del script original."""
import logging
import smtplib
from datetime import date
from decimal import Decimal

import pytest

from revision import enviar_reporte_v2 as m

DIA = date(2026, 10, 27)
CLAVE = "clave-falsa-solo-para-pruebas"


class CursorFalso:
    def __init__(self, filas, ejecuciones):
        self.filas, self.ejecuciones = filas, ejecuciones

    def execute(self, sql, *params):
        self.ejecuciones.append((sql, params))

    def fetchall(self):
        return self.filas

    def close(self):
        pass


class ConexionFalsa:
    def __init__(self, filas):
        self.filas, self.ejecuciones, self.cerrada = filas, [], False

    def cursor(self):
        return CursorFalso(self.filas, self.ejecuciones)

    def close(self):
        self.cerrada = True


class SMTPFalso:
    enviados = []

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
        SMTPFalso.enviados.append(msg)


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    monkeypatch.setenv("REPORTE_DESTINATARIOS", "gerencia.comercial@pagared.com")
    monkeypatch.setenv("SMTP_HOST", "smtp.ejemplo.test")
    monkeypatch.setenv("SMTP_PASSWORD", CLAVE)
    monkeypatch.setenv("ENVIOS_DIR", str(tmp_path / "envios"))
    monkeypatch.setattr(smtplib, "SMTP", SMTPFalso)
    SMTPFalso.enviados = []


def con_filas(monkeypatch, filas):
    conn = ConexionFalsa(filas)
    monkeypatch.setattr(m, "conectar", lambda: conn)
    return conn


FILAS = [("Valle de Aburrá", 10, Decimal("1000000")), ("Oriente", 5, Decimal("250000.50"))]


def test_los_destinatarios_no_se_acumulan_entre_llamadas():
    # En el original, destinatarios=[] se compartía entre zonas: la zona 5 salía con 5 copias.
    for _ in range(5):
        msg = m.construir_mensaje(DIA, {z: (0, Decimal(0)) for z in m.ZONAS}, "etl@pagared.com",
                                  ["gerencia.comercial@pagared.com"])
        assert msg["To"] == "gerencia.comercial@pagared.com"


def test_la_consulta_va_parametrizada_y_la_conexion_se_cierra(entorno, monkeypatch):
    conn = con_filas(monkeypatch, FILAS)
    assert m.main(["--fecha", "2026-10-27"]) == 0
    sql, params = conn.ejecuciones[0]
    assert "?" in sql and "2026" not in sql and params == (DIA,)
    assert conn.cerrada


def test_sin_ventas_termina_con_codigo_2_y_no_envia(entorno, monkeypatch):
    con_filas(monkeypatch, [])
    assert m.main(["--fecha", "2026-10-27"]) == 2
    assert SMTPFalso.enviados == []


def test_una_falla_de_base_de_datos_ya_no_se_traga_y_devuelve_1(entorno, monkeypatch):
    def caida():
        raise ConnectionError("Client with IP address is not allowed to access the server")
    monkeypatch.setattr(m, "conectar", caida)
    assert m.main(["--fecha", "2026-10-27"]) == 1


def test_no_se_envia_dos_veces_el_mismo_dia(entorno, monkeypatch):
    con_filas(monkeypatch, FILAS)
    assert m.main(["--fecha", "2026-10-27"]) == 0
    assert m.main(["--fecha", "2026-10-27"]) == 0   # reintento de Jenkins
    assert len(SMTPFalso.enviados) == 1


def test_el_reporte_incluye_las_cinco_zonas_y_avisa_la_zona_inesperada(entorno, monkeypatch, caplog):
    con_filas(monkeypatch, FILAS + [("Uraba", 3, Decimal("90000"))])   # zona escrita sin tilde
    with caplog.at_level(logging.WARNING):
        assert m.main(["--fecha", "2026-10-27"]) == 0
    cuerpo = SMTPFalso.enviados[0].get_content()
    assert all(z in cuerpo for z in m.ZONAS) and "Uraba" in cuerpo and "TOTAL" in cuerpo
    assert "Zonas que no están en la lista esperada" in caplog.text


def test_nunca_se_imprime_la_clave(entorno, monkeypatch, caplog, capsys):
    con_filas(monkeypatch, FILAS)
    with caplog.at_level(logging.INFO):
        m.main(["--fecha", "2026-10-27"])
    assert CLAVE not in caplog.text and CLAVE not in capsys.readouterr().out


def test_el_modulo_no_contiene_credenciales():
    import re
    codigo = open(m.__file__, encoding="utf-8").read()
    # Ninguna asignación de usuario o clave a un texto literal (el original tenía USER = "..." y PASSWORD = "...").
    assert not re.search(r"(?i)\b(user|password|pwd|clave)\s*=\s*[\"']", codigo)
