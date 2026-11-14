# Sección 1. Componente teórico (Parte A)

## A1. SQL

No cumple. El filtro `t.estado = 'APROBADA'` está en el WHERE, que se evalúa después del LEFT JOIN: en un punto sin ventas t.estado es NULL, la condición falla y la fila se descarta, así que el LEFT JOIN se comporta como INNER JOIN. Y SUM sobre cero filas devuelve NULL, no 0. Solución: pasar la condición al ON y usar COALESCE. Lo probé con tres puntos (con ventas, sin transacciones y solo anuladas): el original da 1 fila y el corregido 3.

```sql
SELECT p.cod_punto, p.nombre, COALESCE(SUM(t.valor), 0) AS ventas FROM puntos_venta p
LEFT JOIN transacciones t ON t.cod_punto = p.cod_punto AND t.estado = 'APROBADA'
GROUP BY p.cod_punto, p.nombre;
```

## A2. SQL

La primera cuenta 700 y la segunda 0. Con NULL, `NULL <> 'ANULADA'` da UNKNOWN y el WHERE solo deja pasar TRUE, así que las 100 filas NULL se pierden. `NOT IN ('ANULADA', NULL)` equivale a `estado <> 'ANULADA' AND estado <> NULL`, y la segunda parte nunca es TRUE: las APROBADAS dan UNKNOWN y las ANULADAS FALSE, así que no pasa ninguna fila. Para contar las 800 que no están anuladas hay que escribir `estado IS NULL OR estado <> 'ANULADA'`. Lo comprobé en SQLite con una tabla de 1.000 filas armada igual.

## A3. Python

El float guarda las fracciones en binario y casi ningún decimal queda exacto: 0.1 + 0.2 da 0.30000000000000004. Cada error es minúsculo, pero aparece al redondear o comparar: `round(2.675, 2)` da 2.67, mientras que un sistema contable que redondea half-up sobre decimales exactos da 2.68. Solución: leer el valor como texto, usar Decimal (o enteros en centavos) y redondear una sola vez, con la misma regla y en el mismo punto que contabilidad. Pasa también en columnas FLOAT o REAL de la base (usar DECIMAL), en Excel, en el tipo «Número decimal» de Power BI (usar «Decimal fijo»), en JSON leído como double y en agregaciones sobre double en pandas o Spark.

## A4. RPA

Causas probables: `sleep(5)` es una espera fija y el agente, más lento y en otra red, a veces necesita más; en headless la ventana es pequeña y cambia el diseño; iframes o ids dinámicos; un captcha o segundo factor que el banco activa según la IP; Chrome y driver con versiones distintas. Robustez: `WebDriverWait` con `element_to_be_clickable`, localizadores estables, ventana de tamaño fijo, reintentos, captura y HTML al fallar, versiones fijadas en una imagen Docker y código de salida distinto de 0 si la descarga no termina. No usaría RPA si el banco ofrece API o SFTP, si exige un captcha o doble factor que no corresponde saltarse, o si sus términos prohíben automatizar la interfaz.

## A5. Jenkins

Corre a la 1:00 a. m. de Colombia: 06:00 UTC menos 5 horas (Colombia es UTC-5 todo el año). Dos formas de corregirlo: 1) fijar la zona en el disparador, `cron('TZ=America/Bogota\n0 6 * * *')`, que sigue bien aunque cambie la zona del servidor; 2) escribir la hora en UTC, `0 11 * * *`, que funciona porque Colombia no tiene horario de verano, pero deja la hora del negocio escondida en una cuenta. Prefiero la primera. Si el reporte debe estar listo a las 6, el job tiene que arrancar antes, con margen para esperar el archivo.

## A6. ETL

El cargue solo inserta y no tiene cómo reconocer lo que ya cargó: el primer intento dejó el 60 % confirmado y el reintento lo volvió a insertar. Diseño idempotente: 1) llave natural única (el id de la transacción) y carga con MERGE o UPSERT; 2) o reemplazo por partición en una sola transacción: `DELETE WHERE fecha = X`, `INSERT` y `COMMIT`; 3) cargar primero a una tabla de paso, validar conteo y valor contra el archivo de control y pasar a la final en una transacción; 4) sin autocommit por lote. Así correrlo N veces da el mismo resultado. En mi pipeline es UPSERT por id, y hay una prueba que carga el mismo archivo dos veces sin que cambie un dato.
