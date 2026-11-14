"""Constantes de negocio del pipeline de ventas de PagaRed.

Todas las reglas que salen del correo de la Gerente Comercial viven aquí,
para que cambiarlas no obligue a tocar la lógica de limpieza ni de carga.
"""
from datetime import timedelta, timezone

# Colombia está en UTC-5 todo el año (no tiene horario de verano), por eso un
# desfase fijo evita depender de la base de zonas horarias del sistema.
OFFSET_COLOMBIA = timedelta(hours=-5)
TZ_COLOMBIA = timezone(OFFSET_COLOMBIA, "America/Bogota")

ESTADOS = ("APROBADA", "RECHAZADA", "ANULADA", "REVERSADA")
# Laura: "las ventas son lo aprobado; lo anulado no cuenta, pero lo reversado sí".
ESTADOS_VENTA = ("APROBADA", "REVERSADA")
PRODUCTOS = ("RECARGA", "PAGO_SERVICIOS", "GIRO")
CANALES = ("POS", "WEB", "APP")

ZONAS = ("Valle de Aburrá", "Oriente", "Suroeste", "Norte", "Urabá")
ZONA_SIN = "Sin zona"

# Punto de pruebas de sistemas: no entra al reporte ni al modelo.
PUNTOS_PRUEBA = frozenset({"90001"})

# Vigencia de la primera versión de cada punto en la dimensión (SCD tipo 2).
VIGENTE_DESDE_INICIAL = "1900-01-01"
VIGENTE_HASTA_ABIERTA = "9999-12-31"

FMT_TS = "%Y-%m-%d %H:%M:%S"

# Motivos (texto que ve Laura en la hoja Calidad).
M_VERSION_ANTERIOR = "Versión anterior de la misma transacción (se conserva la más reciente)"
M_DUPLICADO_EXACTO = "Duplicado exacto de otra fila (se conserva una)"
M_PRUEBAS = "Punto de pruebas de sistemas (regla de negocio)"
M_PUNTO_INEXISTENTE = "cod_punto no existe en el maestro"
M_COD_VACIO = "cod_punto vacío"
M_COD_INVALIDO = "cod_punto con formato inválido"
M_VALOR = "valor vacío o no numérico"
M_VALOR_NO_POSITIVO = "valor no positivo"
M_FECHA = "fecha inválida"
M_ESTADO = "estado no reconocido"
M_PRODUCTO = "producto no reconocido"
M_CANAL = "canal no reconocido"
M_ID_VACIO = "id_transaccion vacío"
M_ACTIVO = "activo distinto de S/N"
M_MAESTRO_DUP = "cod_punto duplicado en el maestro (se conserva el activo/más reciente)"

# Motivos "normales" que no hace falta listar fila por fila en la hoja Calidad.
MOTIVOS_SOLO_CONTEO = (M_VERSION_ANTERIOR, M_DUPLICADO_EXACTO)
