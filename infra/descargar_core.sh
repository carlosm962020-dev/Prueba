#!/usr/bin/env bash
# Descarga del SFTP del core los archivos de ventas y comprueba que sean de HOY.
# Sale con 0 solo si ambos llegaron, no están vacíos y transacciones.csv se generó hoy
# (hora Colombia, después de las 00:30). Si el archivo es el de ayer, el de hoy aún no llegó.
#
# Uso: infra/descargar_core.sh [carpeta_destino]     (por defecto: datos)
# Requiere SFTP_KEY (ruta de la llave privada; la inyecta Jenkins desde sus credenciales).
# SFTP_HOST y SFTP_USER salen de los logs del incidente; SFTP_ORIGEN es un supuesto
# (/salida/ventas): hay que confirmarlo con el proveedor del core.
set -euo pipefail

SFTP_HOST="${SFTP_HOST:-sftp.proveedor-core.com}"
SFTP_USER="${SFTP_USER:-pagared_etl}"
ORIGEN="${SFTP_ORIGEN:-/salida/ventas}"
DESTINO="${1:-datos}"
TMP="${DESTINO}.tmp"

rm -rf "$TMP" && mkdir -p "$TMP"
sftp -i "$SFTP_KEY" -o BatchMode=yes -o StrictHostKeyChecking=yes -b - "$SFTP_USER@$SFTP_HOST" <<SFTP
lcd $TMP
get -p $ORIGEN/transacciones.csv
get -p $ORIGEN/puntos_venta.csv
SFTP

for f in transacciones.csv puntos_venta.csv; do
    [ -s "$TMP/$f" ] || { echo "Falta o está vacío: $f" >&2; exit 1; }
done

hoy="$(TZ=America/Bogota date +%F)"
# La asignación propaga el error de find (con set -e); no se oculta nada.
recientes="$(find "$TMP/transacciones.csv" -newermt "TZ=\"America/Bogota\" $hoy 00:30")"
if [ -z "$recientes" ]; then
    echo "transacciones.csv es de un día anterior: el core todavía no entregó el de hoy" >&2
    exit 1
fi

rm -rf "$DESTINO" && mv "$TMP" "$DESTINO"
echo "Archivos de $hoy descargados en $DESTINO"
