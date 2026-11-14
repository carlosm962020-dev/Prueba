-- =============================================================================
-- Parte B3. Consultas sobre el modelo cargado por el pipeline (SQLite).
-- Base: salida/pagared.db, con el delta ya aplicado.
-- Fecha de corte: 2026-10-27. Está escrita una sola vez al inicio de cada consulta
-- (CTE "params"), así cambiarla es tocar un solo literal.
-- Todas las consultas parten de vw_ventas: venta = APROBADA o REVERSADA, con el
-- día en hora Colombia y sin el punto de pruebas, que el pipeline nunca carga.
-- Cada consulta termina en punto y coma y los comentarios no llevan ninguno, para
-- que sql/ejecutar_consultas.py pueda correrlas una por una.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Top 3 por zona (2 puntos)
-- Enfoque: primero se suman las ventas por punto y después se numera cada punto
-- dentro de su zona con una función de ventana. ROW_NUMBER con desempate por
-- código garantiza exactamente 3 filas por zona y un resultado estable. Con RANK
-- un empate en el tercer lugar traería un cuarto punto. Una zona con menos de 3
-- puntos con ventas devuelve solo los que tiene (por ejemplo "Sin zona").
-- -----------------------------------------------------------------------------
WITH params AS (SELECT '2026-10-27' AS fecha_corte),
ventas_punto AS (
    SELECT v.zona, v.cod_punto, v.punto,
           SUM(v.valor) AS ventas,
           COUNT(*)     AS transacciones
    FROM vw_ventas v
    JOIN params p ON v.fecha = p.fecha_corte
    GROUP BY v.zona, v.cod_punto, v.punto
),
ranking AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY zona ORDER BY ventas DESC, cod_punto) AS posicion
    FROM ventas_punto
)
SELECT zona, posicion, cod_punto, punto, ventas, transacciones
FROM ranking
WHERE posicion <= 3
ORDER BY zona, posicion;


-- -----------------------------------------------------------------------------
-- 2. Puntos activos sin ventas válidas en la fecha de corte (2 puntos)
-- Enfoque: se parte del maestro (dim_punto, versión vigente) y se excluyen los
-- que tienen alguna venta ese día con NOT EXISTS. NOT EXISTS es más seguro que
-- NOT IN, que devuelve cero filas si la subconsulta trae algún NULL. Un punto que
-- solo tuvo transacciones ANULADAS o RECHAZADAS aparece aquí, porque no tiene
-- ventas válidas. Se descartan los puntos que aún no habían abierto en esa fecha,
-- que no pueden estar "sin ventas".
-- -----------------------------------------------------------------------------
WITH params AS (SELECT '2026-10-27' AS fecha_corte)
SELECT d.cod_punto, d.nombre, d.zona, d.fecha_apertura
FROM dim_punto d
CROSS JOIN params p
WHERE d.es_actual = 1
  AND d.activo = 1
  AND (d.fecha_apertura IS NULL OR d.fecha_apertura <= p.fecha_corte)
  AND NOT EXISTS (
      SELECT 1
      FROM vw_ventas v
      WHERE v.cod_punto = d.cod_punto
        AND v.fecha = p.fecha_corte
  )
ORDER BY d.zona, d.cod_punto;


-- -----------------------------------------------------------------------------
-- 3. Variación por zona (2 puntos)
-- Enfoque: una sola pasada sobre los dos días con agregación condicional, en vez
-- de unir la tabla consigo misma. Las zonas salen del maestro, así una zona sin
-- ventas en ambos días aparece con ceros en lugar de desaparecer. La variación
-- porcentual usa NULLIF: si el día anterior no hubo ventas (zona nueva) el
-- resultado es NULL y no un error de división por cero ni un infinito.
-- -----------------------------------------------------------------------------
WITH params AS (
    SELECT '2026-10-27' AS fecha_corte,
           date('2026-10-27', '-1 day') AS fecha_previa
),
zonas AS (
    SELECT DISTINCT zona FROM dim_punto
),
ventas AS (
    SELECT v.zona,
           SUM(CASE WHEN v.fecha = p.fecha_corte  THEN v.valor ELSE 0 END) AS ventas_corte,
           SUM(CASE WHEN v.fecha = p.fecha_previa THEN v.valor ELSE 0 END) AS ventas_previo
    FROM vw_ventas v
    JOIN params p ON v.fecha IN (p.fecha_corte, p.fecha_previa)
    GROUP BY v.zona
)
SELECT z.zona,
       COALESCE(v.ventas_corte, 0)  AS ventas_corte,
       COALESCE(v.ventas_previo, 0) AS ventas_dia_anterior,
       COALESCE(v.ventas_corte, 0) - COALESCE(v.ventas_previo, 0) AS variacion,
       ROUND(100.0 * (COALESCE(v.ventas_corte, 0) - COALESCE(v.ventas_previo, 0))
             / NULLIF(COALESCE(v.ventas_previo, 0), 0), 2) AS variacion_pct
FROM zonas z
LEFT JOIN ventas v ON v.zona = z.zona
ORDER BY z.zona;


-- -----------------------------------------------------------------------------
-- 4. Optimización en SQL Server / Azure SQL (4 puntos)
-- Esta consulta es T-SQL y no se ejecuta en SQLite: va comentada.
--
-- Consulta original (40 minutos, DTU al 100 %):
--
--   SELECT *
--   FROM transacciones
--   WHERE CONVERT(VARCHAR(10), fecha_transaccion, 120) = '2026-10-27'
--     AND UPPER(estado) = 'APROBADA'
--   ORDER BY fecha_transaccion;
--
-- Causa. Las dos condiciones aplican una función a la columna (CONVERT sobre
-- fecha_transaccion y UPPER sobre estado). El optimizador no puede usar el índice
-- para buscar, porque tendría que calcular la función para cada fila y ver si
-- coincide. Por eso no hace un Index Seek: recorre las 80 millones de filas (Index
-- o Clustered Index Scan), convierte cada fecha a texto y compara. Eso es lo que
-- consume el DTU. A eso se suman dos costos menores. SELECT * trae todas las
-- columnas y obliga a leer la tabla completa o a hacer miles de Key Lookups desde
-- el índice. Y el ORDER BY obliga a un Sort sobre las filas que quedan, cuando un
-- índice bien definido ya las entrega ordenadas.
--
-- Reescritura. La fecha se compara como rango semiabierto, sin tocar la columna,
-- y UPPER sobra: la intercalación por defecto de SQL Server (CI, insensible a
-- mayúsculas) ya hace que 'aprobada' = 'APROBADA'. Se piden solo las columnas
-- necesarias.
--
--   SELECT id_transaccion, fecha_transaccion, cod_punto, producto, valor, canal
--   FROM dbo.transacciones
--   WHERE fecha_transaccion >= '2026-10-27'
--     AND fecha_transaccion <  '2026-10-28'
--     AND estado = 'APROBADA'
--   ORDER BY fecha_transaccion;
--
-- Índice propuesto. Primero la columna con igualdad y después la del rango y el
-- ordenamiento: así se hace un Seek solo sobre las filas APROBADAS del día, ya
-- ordenadas, sin Sort. Las columnas del SELECT van en INCLUDE para que el índice
-- cubra la consulta y no haga Key Lookups.
--
--   CREATE NONCLUSTERED INDEX IX_transacciones_estado_fecha
--       ON dbo.transacciones (estado, fecha_transaccion)
--       INCLUDE (id_transaccion, cod_punto, producto, valor, canal)
--       WITH (ONLINE = ON);
--
-- Con el índice actual solo por fecha_transaccion la reescritura ya haría un Seek
-- sobre el día y filtraría estado fila por fila. El índice nuevo mejora eso. Si
-- esta consulta solo se hace para APROBADA, un índice filtrado
-- (WHERE estado = 'APROBADA') queda más pequeño todavía.
--
-- Dos advertencias. Primero, si "2026-10-27" es el día en Colombia y la columna
-- está en UTC como en el core, el rango correcto es
-- >= '2026-10-27 05:00' y < '2026-10-28 05:00'. Segundo, el parámetro debe tener
-- el mismo tipo que la columna (datetime2): si no, SQL Server convierte la columna
-- a cada fila y se pierde otra vez el Seek.
-- Para comprobarlo: SET STATISTICS IO ON y comparar lecturas lógicas y el plan
-- real antes y después. Debe pasar de un Scan a un Index Seek.
-- -----------------------------------------------------------------------------
