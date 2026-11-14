"""Modelo dimensional (esquema en estrella) en SQLite y carga idempotente.

            dim_fecha ─┐
            dim_punto ─┤
        dim_producto ─┼── fact_transacciones
           dim_canal ─┤
          dim_estado ─┘

Decisiones de diseño:
  * La tabla de hechos guarda TODOS los estados de una transacción (no solo las
    ventas). Si llegara solo lo aprobado, una anulación posterior nunca podría
    reflejarse. La regla "venta = APROBADA o REVERSADA" vive en
    dim_estado.cuenta_como_venta y en la vista vw_ventas.
  * La llave de la tabla de hechos es el id de la transacción en el core. Con
    eso el cargue se puede repetir N veces sin duplicar.
  * Una fila existente solo se pisa si la nueva trae una fecha_actualizacion
    MÁS RECIENTE; así el orden en que lleguen los archivos no importa.
  * dim_punto guarda historia de la zona (SCD tipo 2): si un punto cambia de
    zona se cierra su versión anterior y se abre otra. Los hechos ya cargados
    conservan la zona de su época.
  * El día de negocio es el día en hora Colombia (UTC-5), no el día UTC.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pandas as pd

from . import config as cfg

# Nombres en español para dim_fecha, así Power BI no depende del idioma del equipo.
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Todo el esquema usa IF NOT EXISTS: ejecutarlo de nuevo sobre una base que ya
# existe no borra ni duplica nada. Es parte de lo que hace la carga repetible.
ESQUEMA = """
-- Dimensiones: describen el "quién, cuándo, qué y cómo" de cada transacción.
CREATE TABLE IF NOT EXISTS dim_fecha (
    fecha_key        INTEGER PRIMARY KEY,          -- aaaammdd
    fecha            TEXT    NOT NULL UNIQUE,      -- aaaa-mm-dd
    anio             INTEGER NOT NULL,
    mes              INTEGER NOT NULL,
    nombre_mes       TEXT    NOT NULL,
    dia              INTEGER NOT NULL,
    dia_semana       INTEGER NOT NULL,             -- 1 = lunes
    nombre_dia       TEXT    NOT NULL,
    es_fin_de_semana INTEGER NOT NULL
);
-- Un punto puede tener varias filas, una por cada zona que ha tenido (SCD tipo 2).
-- La llave de negocio es cod_punto; punto_key identifica cada versión.
CREATE TABLE IF NOT EXISTS dim_punto (
    punto_key      INTEGER PRIMARY KEY AUTOINCREMENT,
    cod_punto      TEXT    NOT NULL,
    nombre         TEXT    NOT NULL,
    zona           TEXT    NOT NULL,
    activo         INTEGER NOT NULL,
    fecha_apertura TEXT,
    vigente_desde  TEXT    NOT NULL,
    vigente_hasta  TEXT    NOT NULL,
    es_actual      INTEGER NOT NULL,
    UNIQUE (cod_punto, vigente_desde)
);
CREATE TABLE IF NOT EXISTS dim_producto (
    producto_key INTEGER PRIMARY KEY,
    producto     TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS dim_canal (
    canal_key INTEGER PRIMARY KEY,
    canal     TEXT NOT NULL UNIQUE
);
-- cuenta_como_venta es la regla de Laura en un solo lugar: 1 para APROBADA y REVERSADA.
CREATE TABLE IF NOT EXISTS dim_estado (
    estado_key        INTEGER PRIMARY KEY,
    estado            TEXT    NOT NULL UNIQUE,
    cuenta_como_venta INTEGER NOT NULL
);
-- Hechos: una fila por transacción. La llave primaria es el id del core, por eso
-- la misma transacción no se puede insertar dos veces.
CREATE TABLE IF NOT EXISTS fact_transacciones (
    id_transaccion          TEXT    PRIMARY KEY,
    fecha_key               INTEGER NOT NULL REFERENCES dim_fecha (fecha_key),
    punto_key               INTEGER NOT NULL REFERENCES dim_punto (punto_key),
    producto_key            INTEGER NOT NULL REFERENCES dim_producto (producto_key),
    canal_key               INTEGER NOT NULL REFERENCES dim_canal (canal_key),
    estado_key              INTEGER NOT NULL REFERENCES dim_estado (estado_key),
    valor                   NUMERIC NOT NULL,      -- pesos colombianos (COP)
    fecha_hora_utc          TEXT    NOT NULL,
    fecha_hora_col          TEXT    NOT NULL,
    fecha_actualizacion_utc TEXT    NOT NULL,
    archivo_origen          TEXT    NOT NULL,
    cargado_en              TEXT    NOT NULL
);
-- Índices para las consultas del reporte, que filtran por fecha y estado.
CREATE INDEX IF NOT EXISTS ix_fact_fecha ON fact_transacciones (fecha_key, estado_key);
CREATE INDEX IF NOT EXISTS ix_fact_punto ON fact_transacciones (punto_key);

-- Calidad de datos y bitácora: lo que se descartó, lo que se corrigió y qué hizo cada carga.
CREATE TABLE IF NOT EXISTS dq_resumen (
    archivo TEXT NOT NULL, tipo TEXT NOT NULL, motivo TEXT NOT NULL, registros INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dq_descartados (
    archivo TEXT NOT NULL, fila INTEGER, id_transaccion TEXT, motivo TEXT NOT NULL, detalle TEXT
);
CREATE TABLE IF NOT EXISTS etl_cargas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ejecutado_en TEXT NOT NULL, archivo TEXT NOT NULL, modo TEXT NOT NULL,
    leidos INTEGER, descartados INTEGER, cargados INTEGER,
    insertados INTEGER, actualizados INTEGER, ignorados INTEGER
);

-- Vistas: unen los hechos con sus dimensiones para no repetir los JOIN en cada consulta.
CREATE VIEW IF NOT EXISTS vw_transacciones AS
SELECT f.id_transaccion, d.fecha, f.fecha_hora_col, p.cod_punto, p.nombre AS punto,
       p.zona, pr.producto, c.canal, e.estado, e.cuenta_como_venta, f.valor
FROM fact_transacciones f
JOIN dim_fecha    d  ON d.fecha_key     = f.fecha_key
JOIN dim_punto    p  ON p.punto_key     = f.punto_key
JOIN dim_producto pr ON pr.producto_key = f.producto_key
JOIN dim_canal    c  ON c.canal_key     = f.canal_key
JOIN dim_estado   e  ON e.estado_key    = f.estado_key;

-- vw_ventas es la fuente del reporte: solo las transacciones que cuentan como venta.
CREATE VIEW IF NOT EXISTS vw_ventas AS
SELECT * FROM vw_transacciones WHERE cuenta_como_venta = 1;
"""


def conectar(ruta_db: str) -> sqlite3.Connection:
    """Abre la base SQLite. Las llaves foráneas vienen apagadas por defecto: se activan en cada conexión."""
    conn = sqlite3.connect(ruta_db)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def crear_esquema(conn: sqlite3.Connection) -> None:
    """Crea las tablas y llena las dimensiones que no dependen de los archivos.

    Se puede ejecutar todas las veces que se quiera: INSERT OR IGNORE no repite
    las filas que ya existen.
    """
    conn.executescript(ESQUEMA)
    conn.executemany(
        "INSERT OR IGNORE INTO dim_estado (estado_key, estado, cuenta_como_venta) VALUES (?, ?, ?)",
        [(i, e, int(e in cfg.ESTADOS_VENTA)) for i, e in enumerate(cfg.ESTADOS, 1)],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO dim_producto (producto_key, producto) VALUES (?, ?)",
        list(enumerate(cfg.PRODUCTOS, 1)),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO dim_canal (canal_key, canal) VALUES (?, ?)",
        list(enumerate(cfg.CANALES, 1)),
    )
    # dim_fecha es un calendario precargado (2025 a 2027), no se arma con las
    # fechas de los archivos. Así el tablero muestra también los días sin ventas.
    filas, dia = [], date(2025, 1, 1)
    while dia <= date(2027, 12, 31):
        filas.append((
            int(dia.strftime("%Y%m%d")), dia.isoformat(), dia.year, dia.month,
            MESES[dia.month - 1], dia.day, dia.isoweekday(), DIAS[dia.weekday()],
            int(dia.weekday() >= 5),
        ))
        dia += timedelta(days=1)
    conn.executemany("INSERT OR IGNORE INTO dim_fecha VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", filas)
    conn.commit()


# ------------------------------------------------------------------ maestro --
def cargar_maestro(conn: sqlite3.Connection, maestro: pd.DataFrame, vigente_desde: str) -> dict:
    """Aplica el maestro limpio sobre dim_punto (SCD2 solo para la zona).

    vigente_desde es la fecha desde la que rige un cambio de zona. Devuelve
    cuántos puntos son nuevos y cuántos cambiaron.
    """
    # La versión actual de cada punto, indexada por cod_punto (r[1]) para buscarla rápido.
    actuales = {
        r[1]: r for r in conn.execute(
            "SELECT punto_key, cod_punto, nombre, zona, activo, fecha_apertura, vigente_desde "
            "FROM dim_punto WHERE es_actual = 1"
        )
    }
    # La versión vieja se cierra el día anterior a la nueva, para que no se solapen.
    cierre = (date.fromisoformat(vigente_desde) - timedelta(days=1)).isoformat()
    conteo = {"nuevos": 0, "cambios_zona": 0, "cambios_otros": 0}

    insertar = (
        "INSERT INTO dim_punto (cod_punto, nombre, zona, activo, fecha_apertura, "
        "vigente_desde, vigente_hasta, es_actual) VALUES (?, ?, ?, ?, ?, ?, ?, 1)"
    )
    for r in maestro.itertuples(index=False):
        apertura = None if pd.isna(r.fecha_apertura) else r.fecha_apertura
        previo = actuales.get(r.cod_punto)
        # Caso 1: punto nuevo. Su primera versión rige "desde siempre" (1900-01-01),
        # así las transacciones antiguas encuentran punto aunque el maestro sea nuevo.
        if previo is None:
            conn.execute(insertar, (r.cod_punto, r.nombre, r.zona, int(r.activo), apertura,
                                    cfg.VIGENTE_DESDE_INICIAL, cfg.VIGENTE_HASTA_ABIERTA))
            conteo["nuevos"] += 1
            continue
        key, _, nombre, zona, activo, apertura_previa, desde_previo = previo
        # Caso 2: cambió la zona. Se cierra la versión vieja y se abre una nueva; los
        # hechos ya cargados siguen apuntando a la versión vieja (conservan su zona).
        # Solo si la fecha es posterior al inicio de la versión actual: si no, el
        # rango de vigencia quedaría al revés y se actualiza en el lugar (caso 3).
        if zona != r.zona and vigente_desde > desde_previo:
            conn.execute("UPDATE dim_punto SET vigente_hasta = ?, es_actual = 0 WHERE punto_key = ?",
                         (cierre, key))
            conn.execute(insertar, (r.cod_punto, r.nombre, r.zona, int(r.activo), apertura,
                                    vigente_desde, cfg.VIGENTE_HASTA_ABIERTA))
            conteo["cambios_zona"] += 1
        # Caso 3: cambió otro dato (nombre, activo, apertura). No es historia que
        # importe al reporte: se corrige sobre la misma fila.
        elif (nombre, zona, int(activo), apertura_previa) != (r.nombre, r.zona, int(r.activo), apertura):
            conn.execute(
                "UPDATE dim_punto SET nombre = ?, zona = ?, activo = ?, fecha_apertura = ? "
                "WHERE punto_key = ?",
                (r.nombre, r.zona, int(r.activo), apertura, key),
            )
            conteo["cambios_otros"] += 1
    return conteo


def codigos_de_puntos(conn: sqlite3.Connection) -> set:
    """Códigos de punto que existen en el maestro; la limpieza los usa para descartar puntos desconocidos."""
    return {r[0] for r in conn.execute("SELECT DISTINCT cod_punto FROM dim_punto")}


# ------------------------------------------------------------------- hechos --
def _fa_existentes(conn: sqlite3.Connection, ids) -> dict:
    """Devuelve {id: fecha_actualizacion} de lo que ya está cargado, para decidir qué insertar o actualizar."""
    ids, previas = list(ids), {}
    for i in range(0, len(ids), 500):  # SQLite limita los parámetros por consulta
        lote = ids[i:i + 500]
        sql = ("SELECT id_transaccion, fecha_actualizacion_utc FROM fact_transacciones "
               f"WHERE id_transaccion IN ({','.join('?' * len(lote))})")
        previas.update(conn.execute(sql, lote).fetchall())
    return previas


def _con_punto_key(conn: sqlite3.Connection, df: pd.DataFrame) -> pd.DataFrame:
    """Asigna el punto_key vigente en la fecha de cada transacción."""
    versiones = pd.read_sql_query(
        "SELECT punto_key, cod_punto, vigente_desde, vigente_hasta FROM dim_punto", conn
    )
    m = df.merge(versiones, on="cod_punto", how="left")
    # Las fechas son texto aaaa-mm-dd, y en ese formato el orden alfabético es el
    # cronológico, por eso se pueden comparar directamente.
    m = m[(m["vigente_desde"] <= m["fecha"]) & (m["fecha"] <= m["vigente_hasta"])]
    m = m.drop_duplicates("id_transaccion")
    # Si alguna transacción se quedó sin punto, algo está mal en el maestro: se detiene todo.
    if len(m) != len(df):
        raise RuntimeError("Hay transacciones sin punto vigente en dim_punto")
    return m


def _valor(v):
    """Guarda los pesos como entero cuando no tienen decimales (10000, no 10000.0)."""
    v = float(v)
    return int(v) if v.is_integer() else v


def cargar_hechos(conn: sqlite3.Connection, df: pd.DataFrame, cargado_en: str) -> dict:
    """Inserta lo nuevo, actualiza lo que llegó más reciente e ignora el resto.

    No hace commit: el llamador decide, así la carga completa es atómica.
    """
    conteo = {"insertados": 0, "actualizados": 0, "ignorados": 0}
    if df.empty:
        return conteo

    fa = df["fecha_actualizacion_utc"].dt.strftime(cfg.FMT_TS)
    previas = _fa_existentes(conn, df["id_transaccion"])
    # Para cada fila se decide qué hacer: "nuevo" si el id no existe, "actualiza" si
    # la fila trae una fecha de actualización más reciente que la guardada, e
    # "ignora" si es igual o más vieja. Por eso repetir una carga no cambia nada,
    # y el orden en que lleguen los archivos tampoco.
    accion = []
    for id_, f in zip(df["id_transaccion"], fa):
        p = previas.get(id_)
        accion.append("nuevo" if p is None else ("actualiza" if f > p else "ignora"))
    accion = pd.Series(accion, index=df.index)

    # Diccionarios texto -> llave, para convertir cada fila a las llaves de las dimensiones.
    est = dict(conn.execute("SELECT estado, estado_key FROM dim_estado"))
    pro = dict(conn.execute("SELECT producto, producto_key FROM dim_producto"))
    can = dict(conn.execute("SELECT canal, canal_key FROM dim_canal"))
    ts = lambda x: x.strftime(cfg.FMT_TS)  # noqa: E731

    nuevos = df[accion == "nuevo"]
    if len(nuevos):
        nuevos = _con_punto_key(conn, nuevos)
        conn.executemany(
            # OR IGNORE es un seguro extra: si el id ya existiera, no se duplica.
            "INSERT OR IGNORE INTO fact_transacciones (id_transaccion, fecha_key, punto_key, "
            "producto_key, canal_key, estado_key, valor, fecha_hora_utc, fecha_hora_col, "
            "fecha_actualizacion_utc, archivo_origen, cargado_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(r.id_transaccion, int(r.fecha_key), int(r.punto_key), pro[r.producto], can[r.canal],
              est[r.estado], _valor(r.valor), ts(r.fecha_hora_utc), ts(r.fecha_hora_col),
              ts(r.fecha_actualizacion_utc), r.archivo_origen, cargado_en)
             for r in nuevos.itertuples(index=False)],
        )
    conteo["insertados"] = len(nuevos)

    cambios = df[accion == "actualiza"]
    if len(cambios):
        # Se actualiza lo que cambia con el tiempo (estado, valor, producto, canal).
        # La fecha de la transacción y su punto son parte de su identidad y no se tocan.
        conn.executemany(
            "UPDATE fact_transacciones SET estado_key = ?, producto_key = ?, canal_key = ?, "
            "valor = ?, fecha_actualizacion_utc = ?, archivo_origen = ?, cargado_en = ? "
            # La condición < ? repite la regla en SQL: nunca se pisa una versión más reciente.
            "WHERE id_transaccion = ? AND fecha_actualizacion_utc < ?",
            [(est[r.estado], pro[r.producto], can[r.canal], _valor(r.valor),
              ts(r.fecha_actualizacion_utc), r.archivo_origen, cargado_en,
              r.id_transaccion, ts(r.fecha_actualizacion_utc))
             for r in cambios.itertuples(index=False)],
        )
    conteo["actualizados"] = len(cambios)
    conteo["ignorados"] = int((accion == "ignora").sum())
    return conteo


# ------------------------------------------------------- calidad y bitácora --
def registrar_calidad(conn: sqlite3.Connection, resultado) -> None:
    """Guarda la calidad del archivo; reprocesar el mismo archivo reemplaza lo anterior."""
    # Se borra lo de este archivo antes de insertar: así recargar no acumula filas repetidas.
    conn.execute("DELETE FROM dq_resumen WHERE archivo = ?", (resultado.archivo,))
    conn.execute("DELETE FROM dq_descartados WHERE archivo = ?", (resultado.archivo,))
    conn.executemany(
        "INSERT INTO dq_resumen (archivo, tipo, motivo, registros) VALUES (?, ?, ?, ?)",
        [(r.archivo, r.tipo, r.motivo, int(r.registros)) for r in resultado.resumen.itertuples()],
    )
    conn.executemany(
        "INSERT INTO dq_descartados (archivo, fila, id_transaccion, motivo, detalle) "
        "VALUES (?, ?, ?, ?, ?)",
        [(r.archivo, int(r.fila), r.id_transaccion, r.motivo, r.detalle)
         for r in resultado.descartados.itertuples()],
    )


def registrar_carga(conn, archivo, modo, resultado, conteo) -> None:
    """Deja una fila en la bitácora etl_cargas: qué archivo, cuándo (hora Colombia) y cuántas filas hizo cada cosa."""
    conn.execute(
        "INSERT INTO etl_cargas (ejecutado_en, archivo, modo, leidos, descartados, cargados, "
        "insertados, actualizados, ignorados) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.now(cfg.TZ_COLOMBIA).strftime(cfg.FMT_TS), archivo, modo, resultado.leidos,
         len(resultado.descartados), len(resultado.limpio), conteo["insertados"],
         conteo["actualizados"], conteo["ignorados"]),
    )
