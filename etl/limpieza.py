"""Lectura y limpieza de los archivos del core.

Las funciones de este módulo no tocan la base de datos: reciben DataFrames y
devuelven DataFrames, así se pueden probar sin infraestructura.

Orden de las reglas sobre las transacciones (cada fila se descarta por el
primer motivo que aplique, así los conteos cuadran):
  1. Formato por fila: id, estado, producto, canal, cod_punto, fechas.
  2. Versiones de una misma transacción: se conserva la de mayor
     fecha_actualizacion (el core manda el historial de cambios de estado).
  3. Reglas de negocio sobre la versión vigente: punto de pruebas, punto que
     no existe en el maestro, valor vacío o no positivo.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from . import config as cfg

RE_MILES = re.compile(r"^\d{1,3}(\.\d{3})+$")  # 1.299.000 ó 522.500
RE_DECIMAL = re.compile(r"^\d+(\.\d+)?$")  # 10000.00 ó 386800

COLS_TX = [
    "id_transaccion", "fecha_transaccion", "fecha_actualizacion",
    "cod_punto", "producto", "valor", "estado", "canal",
]
COLS_MAESTRO = ["cod_punto", "nombre", "zona", "activo", "fecha_apertura"]

C_ESTADO = "estado con espacios o minúsculas, normalizado"
C_COD = "cod_punto sin ceros a la izquierda, completado a 5 dígitos"
C_VALOR_MILES = "valor con punto de miles (1.299.000), convertido"
C_FECHA = "fecha en formato distinto al ISO (dd/mm/aaaa o con T/Z), convertida"
C_VALOR_COMPLETADO = "valor vacío completado con el de otra versión de la transacción"
C_ZONA_NORM = "zona con mayúsculas, tildes o espacios distintos, normalizada"
C_ZONA_SIN = "zona vacía o desconocida, asignada a 'Sin zona'"


@dataclass
class ResultadoLimpieza:
    archivo: str
    leidos: int
    limpio: pd.DataFrame
    descartados: pd.DataFrame
    correcciones: dict = field(default_factory=dict)

    def __post_init__(self):
        # Control de cuadre: todo lo leído se carga o se descarta, nada se pierde.
        if self.leidos != len(self.limpio) + len(self.descartados):
            raise RuntimeError(
                f"{self.archivo}: no cuadran los conteos "
                f"({self.leidos} leídos vs {len(self.limpio)} limpios + "
                f"{len(self.descartados)} descartados)"
            )

    @property
    def resumen(self) -> pd.DataFrame:
        filas = [
            ("Leídos", "Filas leídas del archivo", self.leidos),
            ("Cargados", "Filas válidas tras la limpieza", len(self.limpio)),
        ]
        if len(self.descartados):
            for motivo, n in self.descartados["motivo"].value_counts().items():
                filas.append(("Descartado", motivo, int(n)))
        for motivo, n in self.correcciones.items():
            if n:
                filas.append(("Corregido", motivo, int(n)))
        out = pd.DataFrame(filas, columns=["tipo", "motivo", "registros"])
        out.insert(0, "archivo", self.archivo)
        return out


# ---------------------------------------------------------------- lectura --
def leer_csv(ruta) -> pd.DataFrame:
    """Lee un CSV delimitado por ';' como texto puro.

    Prueba UTF-8 (con o sin BOM) y, si falla, Windows-1252: el maestro de
    puntos llega en esa codificación. Todo se lee como texto para que
    ningún '00101' pierda sus ceros ni ningún valor cambie de formato.
    """
    error = None
    for codificacion in ("utf-8-sig", "cp1252"):
        try:
            df = pd.read_csv(
                ruta, sep=";", dtype=str, keep_default_na=False,
                encoding=codificacion,
            )
            break
        except UnicodeDecodeError as exc:
            error = exc
    else:
        raise error
    df.columns = [c.strip().lower() for c in df.columns]
    df.insert(0, "fila", range(2, len(df) + 2))  # número de línea en el archivo
    return df


# ------------------------------------------------------- parseo de campos --
def parse_valor(texto) -> tuple[float, bool]:
    """Devuelve (valor, venía_con_punto_de_miles). NaN si no es número."""
    s = (texto or "").strip()
    if RE_MILES.match(s):
        return float(s.replace(".", "")), True
    if RE_DECIMAL.match(s):
        return float(s), False
    return float("nan"), False


_FORMATOS_FECHA = (
    ("%Y-%m-%d %H:%M:%S", True),   # formato canónico
    ("%Y-%m-%dT%H:%M:%SZ", False),
    ("%d/%m/%Y %H:%M", False),     # sin segundos
)


def parse_fecha(texto) -> tuple[datetime | None, bool]:
    """Devuelve (fecha, era_formato_canónico). None si no se reconoce."""
    s = (texto or "").strip()
    for formato, canonico in _FORMATOS_FECHA:
        try:
            return datetime.strptime(s, formato), canonico
        except ValueError:
            continue
    return None, False


def _sin_tildes(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalizar_zona(texto) -> tuple[str, str | None]:
    """Devuelve (zona_canónica, tipo_de_corrección: None | 'norm' | 'sin')."""
    original = texto or ""
    s = original.strip()
    if not s:
        return cfg.ZONA_SIN, "sin"
    clave = _sin_tildes(s).lower()
    for zona in cfg.ZONAS:
        if _sin_tildes(zona).lower() == clave:
            return zona, (None if zona == original else "norm")
    return cfg.ZONA_SIN, "sin"


# ----------------------------------------------------------- transacciones --
def limpiar_transacciones(raw: pd.DataFrame, cods_validos: set, archivo: str) -> ResultadoLimpieza:
    faltan = [c for c in COLS_TX if c not in raw.columns]
    if faltan:
        raise ValueError(f"{archivo}: faltan columnas {faltan}")

    df = raw.copy().reset_index(drop=True)
    leidos = len(df)
    df["motivo"] = ""
    for c in ("estado", "cod_punto", "valor"):
        df[f"{c}_raw"] = df[c]

    def marcar(mask, motivo):
        sel = mask & (df["motivo"] == "")
        df.loc[sel, "motivo"] = motivo

    # 1. Formato por fila ----------------------------------------------------
    df["id_transaccion"] = df["id_transaccion"].str.strip()
    marcar(df["id_transaccion"] == "", cfg.M_ID_VACIO)

    df["estado"] = df["estado_raw"].str.strip().str.upper()
    df["c_estado"] = df["estado"] != df["estado_raw"]
    marcar(~df["estado"].isin(cfg.ESTADOS), cfg.M_ESTADO)

    df["producto"] = df["producto"].str.strip().str.upper()
    marcar(~df["producto"].isin(cfg.PRODUCTOS), cfg.M_PRODUCTO)
    df["canal"] = df["canal"].str.strip().str.upper()
    marcar(~df["canal"].isin(cfg.CANALES), cfg.M_CANAL)

    cod = df["cod_punto"].str.strip()
    vacio = cod == ""
    invalido = ~vacio & ~cod.str.fullmatch(r"\d{1,5}")
    df["c_cod"] = ~vacio & ~invalido & (cod.str.len() < 5)
    df["cod_punto"] = cod.str.zfill(5)
    marcar(vacio, cfg.M_COD_VACIO)
    marcar(invalido, cfg.M_COD_INVALIDO)

    pv = df["valor_raw"].map(parse_valor)
    df["valor"] = pv.map(lambda t: t[0]).astype("float64")
    df["c_valor_miles"] = pv.map(lambda t: t[1]).astype(bool)

    pt = df["fecha_transaccion"].map(parse_fecha)
    pa = df["fecha_actualizacion"].map(parse_fecha)
    df["fecha_hora_utc"] = pd.to_datetime(pt.map(lambda t: t[0]), errors="coerce")
    df["fecha_actualizacion_utc"] = pd.to_datetime(pa.map(lambda t: t[0]), errors="coerce")
    df["c_fecha"] = ~(pt.map(lambda t: t[1]).astype(bool) & pa.map(lambda t: t[1]).astype(bool))
    marcar(df["fecha_hora_utc"].isna() | df["fecha_actualizacion_utc"].isna(), cfg.M_FECHA)

    # 2. Versiones de una misma transacción ----------------------------------
    cand = df[df["motivo"] == ""].sort_values(
        ["id_transaccion", "fecha_actualizacion_utc", "fila"], kind="stable"
    )
    # Último valor no vacío de cada transacción (GroupBy.last ignora los NaN).
    valor_ult = cand.groupby("id_transaccion")["valor"].transform("last")
    es_ganador = ~cand.duplicated("id_transaccion", keep="last")
    ganadores = cand[es_ganador].copy()
    perdedores = cand[~es_ganador]

    if len(perdedores):
        campos = ["estado", "cod_punto", "producto", "canal", "valor", "fecha_actualizacion_utc"]
        g = ganadores.set_index("id_transaccion").loc[perdedores["id_transaccion"], campos]
        iguales = (
            g.astype(str).to_numpy() == perdedores[campos].astype(str).to_numpy()
        ).all(axis=1)
        df.loc[perdedores.index, "motivo"] = np.where(
            iguales, cfg.M_DUPLICADO_EXACTO, cfg.M_VERSION_ANTERIOR
        )

    ganadores["c_valor_completado"] = ganadores["valor"].isna() & valor_ult.loc[ganadores.index].notna()
    ganadores.loc[ganadores["c_valor_completado"], "valor"] = valor_ult.loc[ganadores.index]

    # 3. Reglas de negocio sobre la versión vigente --------------------------
    mot = pd.Series("", index=ganadores.index)

    def marcar_g(mask, motivo):
        mot.loc[mask & (mot == "")] = motivo

    marcar_g(ganadores["cod_punto"].isin(cfg.PUNTOS_PRUEBA), cfg.M_PRUEBAS)
    marcar_g(~ganadores["cod_punto"].isin(cods_validos), cfg.M_PUNTO_INEXISTENTE)
    marcar_g(ganadores["valor"].isna(), cfg.M_VALOR)
    marcar_g(ganadores["valor"] <= 0, cfg.M_VALOR_NO_POSITIVO)
    rechazadas = mot[mot != ""]
    df.loc[rechazadas.index, "motivo"] = rechazadas

    ok = ganadores[mot == ""].copy()
    ok["fecha_hora_col"] = ok["fecha_hora_utc"] + cfg.OFFSET_COLOMBIA
    ok["fecha"] = ok["fecha_hora_col"].dt.strftime("%Y-%m-%d")
    ok["fecha_key"] = ok["fecha_hora_col"].dt.strftime("%Y%m%d").astype(int)
    ok["archivo_origen"] = archivo

    correcciones = {
        C_ESTADO: int(ok["c_estado"].sum()),
        C_COD: int(ok["c_cod"].sum()),
        C_VALOR_MILES: int(ok["c_valor_miles"].sum()),
        C_FECHA: int(ok["c_fecha"].sum()),
        C_VALOR_COMPLETADO: int(ok["c_valor_completado"].sum()),
    }

    limpio = ok[[
        "id_transaccion", "fecha_hora_utc", "fecha_hora_col", "fecha", "fecha_key",
        "fecha_actualizacion_utc", "cod_punto", "producto", "canal", "estado",
        "valor", "archivo_origen",
    ]].reset_index(drop=True)

    d = df[df["motivo"] != ""]
    descartados = pd.DataFrame({
        "archivo": archivo,
        "fila": d["fila"].to_numpy(),
        "id_transaccion": d["id_transaccion"].to_numpy(),
        "motivo": d["motivo"].to_numpy(),
        "detalle": (
            "estado=" + d["estado_raw"] + "; cod_punto=" + d["cod_punto_raw"]
            + "; valor=" + d["valor_raw"]
        ).to_numpy(),
    })
    return ResultadoLimpieza(archivo, leidos, limpio, descartados, correcciones)


# ----------------------------------------------------------------- maestro --
def limpiar_maestro(raw: pd.DataFrame, archivo: str) -> ResultadoLimpieza:
    faltan = [c for c in COLS_MAESTRO if c not in raw.columns]
    if faltan:
        raise ValueError(f"{archivo}: faltan columnas {faltan}")

    df = raw.copy().reset_index(drop=True)
    leidos = len(df)
    df["motivo"] = ""

    def marcar(mask, motivo):
        sel = mask & (df["motivo"] == "")
        df.loc[sel, "motivo"] = motivo

    cod = df["cod_punto"].str.strip()
    vacio = cod == ""
    invalido = ~vacio & ~cod.str.fullmatch(r"\d{1,5}")
    df["cod_punto"] = cod.str.zfill(5)
    df["nombre"] = df["nombre"].str.strip()
    marcar(vacio, cfg.M_COD_VACIO)
    marcar(invalido, cfg.M_COD_INVALIDO)

    df["activo"] = df["activo"].str.strip().str.upper()
    marcar(~df["activo"].isin(["S", "N"]), cfg.M_ACTIVO)
    marcar(
        df["cod_punto"].isin(cfg.PUNTOS_PRUEBA) | df["nombre"].str.upper().str.contains("PRUEBAS"),
        cfg.M_PRUEBAS,
    )

    zonas = df["zona"].map(normalizar_zona)
    df["zona_norm"] = zonas.map(lambda t: t[0])
    df["zona_tipo"] = zonas.map(lambda t: t[1])
    df["apertura"] = pd.to_datetime(df["fecha_apertura"].str.strip(), format="%Y-%m-%d", errors="coerce")

    # Código repetido: gana el punto activo y, si empatan, el más reciente.
    cand = df[df["motivo"] == ""].sort_values(
        ["cod_punto", "activo", "apertura", "fila"],
        ascending=[True, False, False, False], kind="stable",
    )
    repetidos = cand[cand.duplicated("cod_punto", keep="first")]
    df.loc[repetidos.index, "motivo"] = cfg.M_MAESTRO_DUP

    ok = df[df["motivo"] == ""].copy()
    ok["activo"] = (ok["activo"] == "S").astype(int)
    ok["fecha_apertura"] = ok["apertura"].dt.strftime("%Y-%m-%d")
    ok["zona"] = ok["zona_norm"]
    correcciones = {
        C_ZONA_NORM: int((ok["zona_tipo"] == "norm").sum()),
        C_ZONA_SIN: int((ok["zona_tipo"] == "sin").sum()),
    }
    limpio = ok[COLS_MAESTRO].sort_values("cod_punto").reset_index(drop=True)

    d = df[df["motivo"] != ""]
    descartados = pd.DataFrame({
        "archivo": archivo,
        "fila": d["fila"].to_numpy(),
        "id_transaccion": ("punto " + d["cod_punto"]).to_numpy(),
        "motivo": d["motivo"].to_numpy(),
        "detalle": ("nombre=" + d["nombre"] + "; zona=" + d["zona"]).to_numpy(),
    })
    return ResultadoLimpieza(archivo, leidos, limpio, descartados, correcciones)
