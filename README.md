# Pipeline de ventas diarias de PagaRed

Reemplaza el trabajo manual de Paola: lee los archivos del core, los limpia, los carga en un
modelo dimensional (SQLite) y genera el Excel que recibe Laura Restrepo cada mañana.

Probado con Python 3.12 y con pandas 2.2 y 3.0.

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate          # en Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt   # para las pruebas; en producción basta requirements.txt
```

Copie los archivos del kit en `datos/` (`transacciones.csv`, `transacciones_delta_20261028.csv`
y `puntos_venta.csv`). No se versionan porque son datos del core.

## Ejecución

```bash
# 1. Carga principal + reporte de la fecha de corte
python -m etl.pipeline --fecha-corte 2026-10-27

# 2. Aplicar el incremental sobre lo ya cargado y regenerar el reporte
python -m etl.pipeline --fecha-corte 2026-10-27 --delta datos/transacciones_delta_20261028.csv
```

Resultados: `salida/reporte_ventas_20261027.xlsx` (hojas Resumen, Comparativo y Calidad),
`salida/correo_ventas_20261027.eml` (correo con el Excel adjunto, listo para abrir y enviar) y
`salida/pagared.db` (modelo para Power BI). El log queda en `logs/pipeline.log`.

| Opción | Qué hace |
|---|---|
| `--fecha-corte AAAA-MM-DD` | Día del reporte en hora Colombia. Por defecto, el día anterior. |
| `--delta ARCHIVO` | Aplica el incremental sobre lo cargado, sin recargar el archivo principal. |
| `--maestro-desde AAAA-MM-DD` | Desde cuándo rige un cambio de zona detectado en el maestro (por defecto, hoy). |
| `--exportar-csv CARPETA` | Exporta las tablas del modelo a CSV, por si Power BI no puede leer SQLite. |
| `--parquet` | Exporta los hechos a Parquet particionado por fecha (`salida/parquet/.../fecha=AAAA-MM-DD`). |
| `--enviar` | Envía el correo por SMTP, una sola vez por fecha. Requiere `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER` y `SMTP_PASSWORD` como variables de entorno. |

Para ver las consultas de B3 con su resultado: `python sql/ejecutar_consultas.py`.
Para revisar el script de B4 sin enviar nada: `python revision/enviar_reporte_v2.py --dry-run`
(requiere las variables de entorno descritas en su encabezado).

Código de salida del pipeline: 0 si todo salió bien y distinto de 0 ante cualquier falla (archivo que no llegó,
columnas distintas, conteos que no cuadran, ninguna transacción en la fecha de corte).

## Pruebas

```bash
python -m pytest
```

39 pruebas: limpieza, carga incremental e idempotencia, atomicidad, historia de zonas,
comparativo con zona nueva, correo sin doble envío, corridas completas del orquestador, el
script corregido de B4 y la carga corregida de recaudos (B5).

## Estructura

```
etl/limpieza.py   lectura y reglas de limpieza (sin base de datos)
etl/modelo.py     esquema en estrella, carga idempotente, delta y calidad
etl/reporte.py    Excel de tres hojas
etl/correo.py     correo .eml y envío único
etl/exportar.py   CSV y Parquet
etl/pipeline.py   orquestador y línea de comandos
tests/            pruebas con pytest
sql/              consultas de B3 (consultas.sql) y ejecutar_consultas.py para verlas con resultados
revision/         enviar_reporte_v2.py, versión corregida del script de B4
infra/            Dockerfile, Jenkinsfile (+ vigilante), descargar_core.sh y la carga corregida de recaudos (B5)
powerbi/          B6: datos_modelo/ (CSV del modelo en estrella con el delta aplicado), medidas.dax,
                  tablero_ventas.pbix y los pantallazos del tablero
docs/             secciones del informe (1 teoría, 2 análisis B1, 3 pipeline, 4 SQL, 5 revisión,
                  6 operación, 7 Power BI, 8 uso de IA)
docs/img/         diagrama de arquitectura de B1 (arquitectura.mmd es la fuente Mermaid; el .png se regenera con mmdc)
```

El informe consolidado es `INFORME_<nombre>.pdf`, en la raíz.

## Contenedor

```bash
docker build -f infra/Dockerfile -t pagared/ventas-pipeline:1.0 .
```

El Dockerfile y los Jenkinsfile no se probaron en mi entorno (sin Docker ni Jenkins); ver `docs/seccion6_operacion.md`.

## Reglas de negocio (correo de Laura)

* Venta = transacción APROBADA o REVERSADA. ANULADA y RECHAZADA no cuentan.
* Día de ventas = de 00:00 a 24:00 hora Colombia. El core entrega UTC, por eso se resta 5 horas.
* El punto de pruebas (90001) no entra al modelo ni al reporte.
* Comparativo contra el día anterior por zona; sin ventas previas el porcentaje queda vacío.
* Repetir la carga no duplica nada y el correo no sale dos veces para la misma fecha.

No hay credenciales en el repositorio. Todo secreto entra por variables de entorno.
