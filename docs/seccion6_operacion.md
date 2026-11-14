# Sección 6. Operación (B5)

## 6.1 Incidente en `carga_recaudos_bi`

Los tres síntomas salen de dos fallas distintas que se montaron una encima de la otra: el script carga solo una de las
dos partes del archivo, y el 26/10 la carga llegó tarde y a medias a Power BI. Jenkins marcó verde porque nada en el
job compara lo cargado contra lo que el proveedor declaró.

### Causa raíz de cada síntoma

| Síntoma | Causa | Evidencia |
|---|---|---|
| 26/10, tablero a las 6:00 a. m.: $211 M | La carga arrancó 1 h 10 min tarde y Power BI refrescó cuando iba por la mitad. | `configuracion_agente-etl.txt`: un solo ejecutor para tres jobs, y `backup_reportes_mensual` con cron `H 3 27 * *`. `historial_agente-etl.csv`: el backup corrió el 27/10 de 03:41:15 a 05:21:44 (1 h 40 min). `carga_recaudos_bi_211.log`, líneas 4-6: la carga esperó ejecutor de 04:12:02 a 05:21:47. Línea 22 de `carga_recaudos.py`: `autocommit=True`, así que cada lote de 1.000 filas queda visible apenas se inserta. `powerbi_historial_actualizaciones.csv`: refresco programado a las 05:30:01. En el log, el lote 2.000 entró a las 05:29:41 y el 3.000 a las 05:33:37. Con 2.000 de 4.744 filas, 2.000/4.744 × $498,1 M = $210,0 M, que es lo que mostró el tablero. |
| 26/10, tras el refresco manual de Diana (12:04): $498 M contra $1.247 M del core | El script carga solo `_parte2`. | `carga_recaudos.py`, línea 18: `archivos[-1]`, el último archivo en orden alfabético. `carga_recaudos_bi_211.log`, líneas 17-20: «Archivos encontrados: 2», «Procesando ..._parte2.csv», «Registros leídos: 5260». `recaudos_20261026.ctl`: `partes=2` y `registros=13164`. 5.260 de 13.164 es el 40,0 %, y $498 M de $1.247 M es el 39,9 %. |
| 27/10, tablero el 28/10: $512 M contra $1.302 M | La misma causa, sin demora. | Build 212: la carga empezó a tiempo y terminó a las 04:30, antes del refresco de las 05:30, pero también procesó solo `_parte2` (log 212, línea 17). `recaudos_20261027.ctl`: 13.533 registros; el log leyó 5.418 (40,0 %). $512,7 M es el 39,4 % de $1.302 M. |
| Jenkins en verde los tres días | Nada valida la carga y el job solo avisa si hay una excepción. | `Jenkinsfile`: solo hay bloque `post { failure }`. `carga_recaudos.py`: el `.ctl` se descarga (los logs dicen «Descargado ...ctl») pero el script nunca lo abre ni compara; no hay `sys.exit` distinto de 0. Los tres logs terminan en «Finished: SUCCESS». |

Por qué no se vio antes: hasta el 25/10 el proveedor mandaba un solo archivo por día, y `archivos[-1]` era correcto. El
listado del SFTP muestra que el 26/10 es el primer día con `_parte1` y `_parte2`, y el `.ctl` pasa de 118 a 127 bytes
porque trae la línea nueva `partes=`. El 12/10 hubo un reenvío (`_v2`) y el script lo resolvía bien por casualidad: en
orden alfabético `_v2` queda al final. El comentario del código habla de ese caso y no del de las partes.

Cosas que aparecen en los logs y no explican el incidente: el «Connection reset by peer» del build 212 (se
reintentó y conectó 11 segundos después), el aviso del driver ODBC 17 y el `FutureWarning` de pandas. Sí quedan
pendientes de atender: el driver, y el disco de `/data`, que subió de 79 % a 81 % y a 82 % en tres días.

Hay otro problema de fondo: el margen es corto incluso un día normal. 11.702 filas tardaron 46 minutos (4,2 filas por segundo)
porque `executemany` no usa `fast_executemany`. Arrancando a las 4:12, termina a las 4:58 y el refresco es a las 5:30.

### Cómo corregir los datos

1. Revisar todos los días desde el 26/10 hasta hoy, no solo los dos que reportó el Director. Si el proveedor siguió
   enviando dos partes, cada carga desde el 26/10 quedó con el 40 % del valor.
2. Desplegar `infra/incidente/carga_recaudos_v2.py` y recargar cada día afectado con
   `python carga_recaudos_v2.py --fecha AAAA-MM-DD`, empezando por el 26/10 y el 27/10 (los archivos siguen en el SFTP).
   No usar el script original para recargar: borraría el día y volvería a insertar solo `_parte2`.
3. La recarga hace `DELETE`, `INSERT` y verificación en una sola transacción, así que el tablero nunca ve un día a medias
   y se puede repetir sin duplicar.
4. Dispararla fuera de la ventana de las 4:00 a las 6:00 y refrescar Power BI al terminar.

Cómo verificar que quedó bien, día por día:

* Que `dw.control_cargas` tenga `registros_leidos = registros_ctl` y que el valor del archivo completo coincida con
  `valor_ctl`. La carga ya falla si no cuadra.
* Que el total de `dw.fact_recaudos` del día coincida exacto, al peso, con lo que reporta el core: $1.247 M el 26/10 y
  $1.302 M el 27/10 según el Director. Como referencia previa, aplicando la proporción del 25/10 (cargado sobre
  `valor_total` del `.ctl` = 88,85 %) se esperarían unos $1.251 M y $1.299 M; sirve de alerta, no de prueba.
* `SELECT id_recaudo, COUNT(*) FROM dw.fact_recaudos GROUP BY id_recaudo HAVING COUNT(*) > 1` debe devolver cero filas.
* Que el 25/10 no haya cambiado, y que el tablero, ya refrescado, muestre las mismas cifras que la tabla.

### Cómo hacer que falle en vez de terminar en verde

| Cambio | Qué evita |
|---|---|
| Leer el `.ctl` y comparar partes, registros y valor total antes de tocar la tabla (hecho en `carga_recaudos_v2.py`). Si no cuadra, código de salida 1. | Cargar una parte y creerla completa. |
| Tomar todas las partes de la versión más reciente del día, y fallar si el `.ctl` declara 2 y llegó 1. | El `archivos[-1]`. |
| Una sola transacción con verificación al final. | Que Power BI lea una carga a medias. |
| Tabla `dw.control_cargas` con una fila por día: lo que decía el `.ctl` y lo que se cargó. | No poder comprobar nada después. También alimenta una tarjeta «última carga válida» en el tablero. |
| Disparar el refresco de Power BI por API al terminar la carga, y quitar el programado de las 5:30. | Que un reloj decida cuándo leer. Si se deja el programado, puede volver a leer a mitad de carga. |
| Agente propio para estas cargas, o al menos cambiar «Usar este nodo tanto como sea posible» por una etiqueta exclusiva, y sacar el backup mensual de ese nodo. | Que un backup de 1 h 40 min deje el job en cola. |
| Aviso si el valor cargado se aleja mucho de los días anteriores. Una advertencia, no un fallo, porque un día atípico puede ser real. | Descubrirlo por el Director Financiero. |
| `fast_executemany` (ya incluido en la versión corregida). | Dependencia de un margen de 32 minutos. |

La versión corregida tiene 8 pruebas en `tests/test_carga_recaudos_v2.py` que reproducen el incidente: falta una parte,
el valor no cuadra con el control, faltan registros, reenvío `_v2`, recarga del mismo día, una falla a mitad de la
inserción (la tabla queda como estaba) y código de salida distinto de 0. Usan SQLite como doble de la base. No las
corrí contra SQL Server, y la tabla `dw.control_cargas` es nueva, así que hay que crearla:

```sql
CREATE TABLE dw.control_cargas (
    fecha DATE NOT NULL, partes INT, registros_ctl INT, valor_ctl BIGINT,
    registros_leidos INT, registros_validos INT, valor_cargado BIGINT, cargado_en DATETIME2 NOT NULL
);
```

## 6.2 Despliegue del pipeline de ventas

Archivos: `infra/Dockerfile`, `infra/Jenkinsfile` (job diario), `infra/Jenkinsfile.vigilante` y
`infra/descargar_core.sh`. No tengo Docker ni Jenkins en mi entorno: probé el script de descarga con un SFTP simulado
(archivo de hoy, de ayer, antes de las 00:30 y ausente) y el resto está sin ejecutar. Hay que correr un primer
`docker build` y revisar el Jenkinsfile con «Replay» antes de activarlo.

### Dónde correrlo: Jenkins, con condiciones

Propongo Jenkins. Ya existe y el equipo lo opera, guarda credenciales, ya llega al SFTP del proveedor, y los permisos
de red hacia Azure SQL se piden una sola vez para una IP conocida. Con el plazo del lunes es lo que menos riesgo mete.

El incidente de recaudos muestra el costo: es la misma plataforma que dejó una carga en cola 70 minutos. Por eso la
propuesta lleva tres condiciones: un agente con etiqueta propia (`etl-ventas`) y dos ejecutores, el disparador a las
4:30 y no a las 6:00, y un job vigilante a las 5:45.

AWS queda como segundo paso si Jenkins sigue dando problemas o el servidor on-premise se retira. Sería EventBridge
Scheduler (acepta zona horaria `America/Bogota` en el propio horario), una tarea en ECS Fargate con la misma imagen,
S3 para los reportes, Secrets Manager y una alarma de CloudWatch. La conexión a Azure SQL saldría por una IP elástica
fija. No escribí el Terraform opcional porque no tengo cómo validarlo aquí.

### Cómo cubre cada punto

| Tema | Propuesta |
|---|---|
| Hora | `cron('TZ=America/Bogota\nH(30-40) 4 * * *')`. Sin cuentas de UTC. El correo sale en cuanto el reporte está listo, antes de las 6:00. |
| Credenciales | Almacén de credenciales de Jenkins (`credentials()`), enmascaradas en el log; nada en el repositorio ni en la imagen. En Azure SQL, un usuario con permisos mínimos y no `admin_etl`. La contraseña que aparece escrita en `enviar_reporte.py` hay que rotarla. |
| Logs | `logs/pipeline.log` con lo leído, descartado y cargado, más la consola de Jenkins. Se archivan por build (60 builds) y no contienen secretos (hay una prueba que lo comprueba). |
| Alertas por falla | El pipeline sale con código distinto de 0 ante cualquier falla y el `post { failure }` envía correo. |
| Si no llega el archivo | `descargar_core.sh` solo da por buena la descarga si `transacciones.csv` es de hoy. El job reintenta cada 5 minutos hasta unos 45 minutos y luego falla, a las 5:15. A las 5:45 el vigilante revisa si existe la marca de envío y, si no, escribe también a la Gerencia Comercial: Laura se entera antes de las 6:00. |
| Si el job ni arrancó | Lo cubre el vigilante, que corre aparte. |
| Doble envío | Marca de envío creada de forma atómica antes de enviar, en `salida/.enviados/AAAAMMDD`. Tiene que vivir en un disco que persista entre builds. |
| Power BI | El refresco se dispara por API al final del job, solo si todo salió bien (etapa «Actualizar Power BI»), y se desactiva el programado. El token de la API es un supuesto: falta definir el principal de servicio. El correo de Laura no depende de Power BI. |
| Dónde se guardan los reportes | Excel archivado en Jenkins, copia en un bucket S3 con retención (supuesto: hay AWS CLI en el agente) y, sobre todo, la base: el Excel de cualquier día se vuelve a generar con `--fecha-corte`. La retención la define Finanzas. |

## 6.3 Conectividad con Azure

El error 40615 quiere decir que la conexión llegó al servidor, o sea que el DNS y la red funcionan y que no es un
problema de usuario o contraseña. El firewall de Azure SQL rechazó la IP pública de salida de la empresa
(`190.85.xx.xx`) porque no está en ninguna regla.

De la más rápida a la más segura:

1. **Regla de firewall con la IP pública de salida de Jenkins.** Se crea en el portal o con
   `az sql server firewall-rule create`, y funciona en minutos. Conviene preferir una regla a nivel de base de datos y
   confirmar con redes que la IP es fija: si cambia, el job vuelve a fallar sin aviso. Y esa IP la comparte toda la
   empresa y el servidor sigue expuesto a internet.
2. **IP de salida fija y dedicada, con autenticación mejor.** Que el tráfico de este job salga por una IP reservada solo
   para él, abrir el firewall únicamente para ella, y cambiar el usuario SQL con contraseña por una identidad de
   Microsoft Entra (principal de servicio) con permisos mínimos y `Encrypt=yes`. Toma días, y el servidor sigue siendo
   público.
3. **VPN sitio a sitio o ExpressRoute más Private Endpoint**, con el acceso público del servidor desactivado. El tráfico
   no pasa por internet. Es lo más seguro y lo más lento: requiere redes, un DNS que resuelva
   `privatelink.database.windows.net` y semanas de coordinación.

Recomiendo la 1 hoy, con dueño y fecha de revisión, y la 3 como destino.
