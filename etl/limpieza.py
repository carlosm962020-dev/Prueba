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

# El core mezcla dos formas de escribir el valor: con punto de miles (1.299.000)
# y con punto decimal (10000.00). Se distinguen por la forma del texto, no por el
# tamaño del número. Supuesto: un texto como 522.500 (grupos de exactamente tres
# cifras después del punto) es un valor en miles, y los decimales traen dos cifras.
RE_MILES = re.compile(r"^\d{1,3}(\.\d{3})+$")  # 1.299.000 ó 522.500
RE_DECIMAL = re.compile(r"^\d+(\.\d+)?$")  # 10000.00 ó 386800

# Columnas que debe traer cada archivo. Si falta alguna, el pipeline se detiene
# con un error claro: es mejor fallar que cargar un archivo que no se entiende.
COLS_TX = [
    "id_transaccion", "fecha_transaccion", "fecha_actualizacion",
    "cod_punto", "producto", "valor", "estado", "canal",
]
COLS_MAESTRO = ["cod_punto", "nombre", "zona", "activo", "fecha_apertura"]

# Tipos de corrección. Corregir no es descartar: la fila se carga arreglada y se
# cuenta aquí, para que Laura vea cuánto llegó mal y se arregló.
C_ESTADO = "estado con espacios o minúsculas, normalizado"
C_COD = "cod_punto sin ceros a la izquierda, completado a 5 dígitos"
C_VALOR_MILES = "valor con punto de miles (1.299.000), convertido"
C_FECHA = "fecha en formato distinto al ISO (dd/mm/aaaa o con T/Z), convertida"
C_VALOR_COMPLETADO = "valor vacío completado con el de otra versión de la transacción"
C_ZONA_NORM = "zona con mayúsculas, tildes o espacios distintos, normalizada"
C_ZONA_SIN = "zona vacía o desconocida, asignada a 'Sin zona'"


@dataclass
class ResultadoLimpieza:
    """Lo que devuelve limpiar un archivo.

    limpio: filas listas para cargar. descartados: filas rechazadas con su
    motivo. correcciones: cuántas filas cargadas hubo que arreglar, por tipo.
    """
    archivo: str
    leidos: int
    limpio: pd.DataFrame
    descartados: pd.DataFrame
    correcciones: dict = field(default_factory=dict)

    def __post_init__(self):
        # Control de cuadre: todo lo leído se carga o se descarta, nada se pierde.
        # Si esto falla, hay un error en las reglas, y se prefiere detener la carga
        # a entregar un reporte con filas perdidas sin explicación.
        if self.leidos != len(self.limpio) + len(self.descartados):
            raise RuntimeError(
                f"{self.archivo}: no cuadran los conteos "
                f"({self.leidos} leídos vs {len(self.limpio)} limpios + "
                f"{len(self.descartados)} descartados)"
            )

    @property
    def resumen(self) -> pd.DataFrame:
        """Tabla de conteos que va a la hoja Calidad: leídos, cargados, descartados por motivo y correcciones."""
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
    # "utf-8-sig" es UTF-8 y además ignora el BOM que Excel agrega al inicio.
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
        # El else de un for solo corre si ningún intento terminó con break:
        # ninguna de las dos codificaciones sirvió, y se informa el último error.
        raise error
    # Nombres de columna sin espacios y en minúscula, por si el core cambia la capitalización.
    df.columns = [c.strip().lower() for c in df.columns]
    df.insert(0, "fila", range(2, len(df) + 2))  # número de línea en el archivo
    return df


# ------------------------------------------------------- parseo de campos --
def parse_valor(texto) -> tuple[float, bool]:
    """Devuelve (valor, venía_con_punto_de_miles). NaN si no es número."""
    s = (texto or "").strip()
    # El patrón de miles se prueba primero: 522.500 también cumpliría el decimal.
    if RE_MILES.match(s):
        return float(s.replace(".", "")), True
    if RE_DECIMAL.match(s):
        return float(s), False
    return float("nan"), False


# Formatos de fecha que acepta el core, del más común al menos común. El booleano
# dice si es el formato canónico; sirve para contar cuántas fechas se convirtieron.
_FORMATOS_FECHA = (
    ("%Y-%m-%d %H:%M:%S", True),   # formato canónico
    ("%Y-%m-%dT%H:%M:%SZ", False),
    ("%d/%m/%Y %H:%M", False),     # sin segundos
)


def parse_fecha(texto) -> tuple[datetime | None, bool]:
    """Devuelve (fecha, era_formato_canónico). None si no se reconoce."""
    s = (texto or "").strip()
    for formato, canonico in _FORMATOS_FECHA:
        # strptime lanza ValueError si el texto no cumple el formato: se prueba el siguiente.
        try:
            return datetime.strptime(s, formato), canonico
        except ValueError:
            continue
    return None, False


def _sin_tildes(texto: str) -> str:
    """Quita las tildes: NFKD separa cada letra de su tilde ('á' -> 'a' + marca) y se descartan las marcas."""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalizar_zona(texto) -> tuple[str, str | None]:
    """Devuelve (zona_canónica, tipo_de_corrección: None | 'norm' | 'sin')."""
    original = texto or ""
    s = original.strip()
    if not s:
        return cfg.ZONA_SIN, "sin"
    # Se compara sin tildes ni mayúsculas, así "oriente" y "ORIENTE" caen en "Oriente".
    clave = _sin_tildes(s).lower()
    for zona in cfg.ZONAS:
        if _sin_tildes(zona).lower() == clave:
            # None si ya venía exacta; "norm" si hubo que cambiarla.
            return zona, (None if zona == original else "norm")
    # Una zona que no existe no se inventa: queda "Sin zona" y se cuenta como corrección.
    return cfg.ZONA_SIN, "sin"


# ----------------------------------------------------------- transacciones --
def limpiar_transacciones(raw: pd.DataFrame, cods_validos: set, archivo: str) -> ResultadoLimpieza:
    """Limpia un archivo de transacciones.

    raw: el CSV leído como texto. cods_validos: códigos de punto que existen en
    el maestro. Devuelve el resultado con las filas cargables y las descartadas.
    """
    faltan = [c for c in COLS_TX if c not in raw.columns]
    if faltan:
        raise ValueError(f"{archivo}: faltan columnas {faltan}")

    # Se trabaja sobre una copia: lo leído no se modifica.
    df = raw.copy().reset_index(drop=True)
    leidos = len(df)
    # La columna "motivo" vacía significa "fila válida hasta ahora". marcar() solo
    # escribe si la fila aún no tiene motivo, así cada fila se descarta por el
    # primer motivo que le toca y los conteos del reporte suman exacto.
    df["motivo"] = ""
    # Se guarda el texto original para mostrarlo en el detalle de la hoja Calidad.
    for c in ("estado", "cod_punto", "valor"):
        df[f"{c}_raw"] = df[c]

    def marcar(mask, motivo):
        sel = mask & (df["motivo"] == "")
        df.loc[sel, "motivo"] = motivo

    # 1. Formato por fila ----------------------------------------------------
    df["id_transaccion"] = df["id_transaccion"].str.strip()
    marcar(df["id_transaccion"] == "", cfg.M_ID_VACIO)

    df["estado"] = df["estado_raw"].str.strip().str.upper()
    df["c_estado"] = df["estado"] != df["estado_raw"]  # True si hubo que arreglarlo
    marcar(~df["estado"].isin(cfg.ESTADOS), cfg.M_ESTADO)

    df["producto"] = df["producto"].str.strip().str.upper()
    marcar(~df["producto"].isin(cfg.PRODUCTOS), cfg.M_PRODUCTO)
    df["canal"] = df["canal"].str.strip().str.upper()
    marcar(~df["canal"].isin(cfg.CANALES), cfg.M_CANAL)

    # cod_punto: de 1 a 5 dígitos. A veces llega sin los ceros iniciales ("101" en
    # vez de "00101"), así que se completa a 5 con zfill.
    cod = df["cod_punto"].str.strip()
    vacio = cod == ""
    invalido = ~vacio & ~cod.str.fullmatch(r"\d{1,5}")
    # Se mide el largo ANTES de rellenar para contar cuántos códigos se corrigieron.
    df["c_cod"] = ~vacio & ~invalido & (cod.str.len() < 5)
    df["cod_punto"] = cod.str.zfill(5)
    marcar(vacio, cfg.M_COD_VACIO)
    marcar(invalido, cfg.M_COD_INVALIDO)

    # parse_valor devuelve (número, venía_con_miles): se separan en dos columnas.
    pv = df["valor_raw"].map(parse_valor)
    df["valor"] = pv.map(lambda t: t[0]).astype("float64")
    df["c_valor_miles"] = pv.map(lambda t: t[1]).astype(bool)

    # Fechas en UTC tal como llegan del core. Si alguna no se reconoce queda NaT
    # y la fila se descarta; c_fecha cuenta las que hubo que convertir de formato.
    pt = df["fecha_transaccion"].map(parse_fecha)
    pa = df["fecha_actualizacion"].map(parse_fecha)
    df["fecha_hora_utc"] = pd.to_datetime(pt.map(lambda t: t[0]), errors="coerce")
    df["fecha_actualizacion_utc"] = pd.to_datetime(pa.map(lambda t: t[0]), errors="coerce")
    df["c_fecha"] = ~(pt.map(lambda t: t[1]).astype(bool) & pa.map(lambda t: t[1]).astype(bool))
    marcar(df["fecha_hora_utc"].isna() | df["fecha_actualizacion_utc"].isna(), cfg.M_FECHA)

    # 2. Versiones de una misma transacción ----------------------------------
    # El core manda una fila por cada cambio de estado de la transacción. Se ordena
    # por id y por fecha de actualización (la fila del archivo desempata) y la
    # última de cada id es la versión vigente.
    cand = df[df["motivo"] == ""].sort_values(
        ["id_transaccion", "fecha_actualizacion_utc", "fila"], kind="stable"
    )
    # Último valor no vacío de cada transacción (GroupBy.last ignora los NaN).
    valor_ult = cand.groupby("id_transaccion")["valor"].transform("last")
    es_ganador = ~cand.duplicated("id_transaccion", keep="last")  # keep="last": se queda la más reciente
    ganadores = cand[es_ganador].copy()
    perdedores = cand[~es_ganador]

    # Las versiones viejas se descartan con uno de dos motivos: "duplicado exacto"
    # (idéntica a la vigente) o "versión anterior" (cambió algo, p. ej. el estado).
    if len(perdedores):
        campos = ["estado", "cod_punto", "producto", "canal", "valor", "fecha_actualizacion_utc"]
        # Para cada versión vieja se busca la vigente de su misma transacción y se comparan los campos.
        g = ganadores.set_index("id_transaccion").loc[perdedores["id_transaccion"], campos]
        iguales = (
            g.astype(str).to_numpy() == perdedores[campos].astype(str).to_numpy()
        ).all(axis=1)
        df.loc[perdedores.index, "motivo"] = np.where(
            iguales, cfg.M_DUPLICADO_EXACTO, cfg.M_VERSION_ANTERIOR
        )

    # Si la versión vigente trae el valor vacío pero otra versión lo tenía, se completa.
    ganadores["c_valor_completado"] = ganadores["valor"].isna() & valor_ult.loc[ganadores.index].notna()
    ganadores.loc[ganadores["c_valor_completado"], "valor"] = valor_ult.loc[ganadores.index]

    # 3. Reglas de negocio sobre la versión vigente --------------------------
    # Estas reglas se aplican solo a la versión vigente: una versión vieja ya no
    # importa. Se usa la misma idea de marcar(): el primer motivo que aplique gana.
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
    # El día de ventas va de 00:00 a 24:00 hora Colombia, y el core entrega UTC:
    # se restan 5 horas. Una venta a las 02:00 UTC del día 28 es del día 27 en Colombia.
    ok["fecha_hora_col"] = ok["fecha_hora_utc"] + cfg.OFFSET_COLOMBIA
    ok["fecha"] = ok["fecha_hora_col"].dt.strftime("%Y-%m-%d")
    ok["fecha_key"] = ok["fecha_hora_col"].dt.strftime("%Y%m%d").astype(int)
    ok["archivo_origen"] = archivo

    # Las correcciones se cuentan solo sobre las filas que quedaron cargadas.
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

    # Todo lo que tiene motivo se devuelve como descartado, con su número de fila
    # en el archivo original para que se pueda ubicar.
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
    """Limpia el maestro de puntos de venta (código, nombre, zona, activo, fecha de apertura)."""
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
    # El punto de pruebas se detecta por su código o porque el nombre dice "PRUEBAS".
    marcar(
        df["cod_punto"].isin(cfg.PUNTOS_PRUEBA) | df["nombre"].str.upper().str.contains("PRUEBAS"),
        cfg.M_PRUEBAS,
    )

    # La zona no descarta: se normaliza, y si no se reconoce queda "Sin zona".
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
    ok["activo"] = (ok["activo"] == "S").astype(int)  # S/N -> 1/0
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
