# Sección 4. SQL (B3)

Las tres primeras consultas corren sobre la base que deja el pipeline (SQLite, con el delta aplicado) y la fecha
de corte es el 27/10/2026. El archivo completo, con el comentario de enfoque de cada una, es `sql/consultas.sql`;
`python sql/ejecutar_consultas.py` las ejecuta y muestra estos mismos resultados.

Todas parten de `vw_ventas`: venta = APROBADA o REVERSADA, día en hora Colombia, sin el punto de pruebas.

## 4.1 Top 3 por zona

Primero se suman las ventas por punto y luego se numera cada punto dentro de su zona con una función de ventana.
`ROW_NUMBER` con desempate por código garantiza exactamente tres filas por zona. Con `RANK`, un empate en el tercer
lugar traería un cuarto punto.

```sql
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
```

| zona | posicion | cod_punto | punto | ventas | transacciones |
|---|---:|---|---|---:|---:|
| Norte | 1 | 00135 | Punto Entrerríos | 42.299.500 | 104 |
| Norte | 2 | 00132 | Punto Donmatías | 35.977.600 | 104 |
| Norte | 3 | 00134 | Punto San Pedro de los Milagros | 22.719.100 | 61 |
| Oriente | 1 | 00124 | Punto La Unión | 36.038.000 | 82 |
| Oriente | 2 | 00123 | Punto El Santuario | 35.400.300 | 64 |
| Oriente | 3 | 00120 | Punto La Ceja | 23.776.200 | 80 |
| Sin zona | 1 | 00133 | Punto Santa Fe de Antioquia | 31.837.400 | 97 |
| Suroeste | 1 | 00128 | Punto Jericó | 40.730.800 | 93 |
| Suroeste | 2 | 00129 | Punto Fredonia | 36.574.400 | 89 |
| Suroeste | 3 | 00127 | Punto Ciudad Bolívar | 7.428.500 | 29 |
| Urabá | 1 | 00137 | Punto Turbo | 34.302.900 | 78 |
| Urabá | 2 | 00136 | Punto Apartadó | 27.937.400 | 86 |
| Urabá | 3 | 00139 | Punto Carepa | 24.084.400 | 83 |
| Valle de Aburrá | 1 | 00103 | Punto Belén | 44.218.900 | 114 |
| Valle de Aburrá | 2 | 00112 | Punto Caldas | 40.458.200 | 97 |
| Valle de Aburrá | 3 | 00114 | Punto Buenos Aires | 37.500.200 | 107 |

«Sin zona» solo tiene un punto (00133), por eso devuelve una fila.

## 4.2 Puntos activos sin ventas válidas

Se parte del maestro y se excluyen los puntos con alguna venta ese día usando `NOT EXISTS`, que es más seguro que
`NOT IN`: este último devuelve cero filas si la subconsulta trae un `NULL`. Un punto que solo tuvo transacciones
anuladas o rechazadas aparece aquí, porque no tiene ventas válidas. Se descartan los puntos que aún no habían abierto.

```sql
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
```

| cod_punto | nombre | zona | fecha_apertura |
|---|---|---|---|
| 00131 | Punto Yarumal | Norte | 2019-02-25 |
| 00125 | Punto Andes | Suroeste | 2017-10-02 |
| 00109 | Punto Castilla | Valle de Aburrá | 2018-05-30 |

El 00138 (Valdivia) tampoco vendió, pero está inactivo y no entra en la consulta.

## 4.3 Variación por zona

Una sola pasada sobre los dos días con agregación condicional, en lugar de unir la tabla consigo misma. Las zonas salen
del maestro, así una zona sin ventas aparece con ceros. `NULLIF` evita la división por cero: Urabá abrió el 27/10 y no
tiene base de comparación, por eso su porcentaje queda vacío en lugar de dar error.

```sql
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
```

| zona | ventas_corte | ventas_dia_anterior | variacion | variacion_pct |
|---|---:|---:|---:|---:|
| Norte | 110.938.100 | 111.000.400 | -62.300 | -0,06 % |
| Oriente | 170.766.700 | 132.190.200 | 38.576.500 | 29,18 % |
| Sin zona | 31.837.400 | 48.742.600 | -16.905.200 | -34,68 % |
| Suroeste | 91.302.300 | 123.124.500 | -31.822.200 | -25,85 % |
| Urabá | 96.810.800 | 0 | 96.810.800 | sin base (N/A) |
| Valle de Aburrá | 414.550.400 | 361.492.600 | 53.057.800 | 14,68 % |

Los valores coinciden con la hoja Comparativo del reporte Excel.

## 4.4 Optimización en SQL Server

**Causa.** Las dos condiciones aplican una función a la columna: `CONVERT(VARCHAR(10), fecha_transaccion, 120)` y
`UPPER(estado)`. El optimizador no puede usar el índice para buscar, porque tendría que calcular la función en cada
fila. Por eso recorre las 80 millones de filas (un Scan, no un Seek), convierte cada fecha a texto y compara, y eso es
lo que lleva el DTU al 100 %. Además, `SELECT *` trae todas las columnas y obliga a leer la tabla completa o a hacer
muchos Key Lookups, y el `ORDER BY` agrega un Sort que un índice bien definido evita.

**Consulta reescrita.** La fecha se compara como rango semiabierto, sin tocar la columna. `UPPER` sobra porque la
intercalación por defecto de SQL Server no distingue mayúsculas. Y se piden solo las columnas que se usan.

```sql
SELECT id_transaccion, fecha_transaccion, cod_punto, producto, valor, canal
FROM dbo.transacciones
WHERE fecha_transaccion >= '2026-10-27'
  AND fecha_transaccion <  '2026-10-28'
  AND estado = 'APROBADA'
ORDER BY fecha_transaccion;
```

**Índice.** Primero la columna con igualdad, después la del rango y el orden, para que el Seek caiga solo sobre las
filas aprobadas del día y ya salgan ordenadas, sin Sort. Las columnas del `SELECT` van en `INCLUDE` para evitar Key
Lookups.

```sql
CREATE NONCLUSTERED INDEX IX_transacciones_estado_fecha
    ON dbo.transacciones (estado, fecha_transaccion)
    INCLUDE (id_transaccion, cod_punto, producto, valor, canal)
    WITH (ONLINE = ON);
```

Con el índice actual solo por `fecha_transaccion`, la reescritura ya haría un Seek sobre el día y filtraría `estado`
fila por fila; el índice nuevo mejora eso. Si la consulta solo se usa para APROBADA, un índice filtrado queda aún más
pequeño.

**Dos advertencias.** Si «2026-10-27» es el día en Colombia y la columna está en UTC como en el core, el rango correcto
es `>= '2026-10-27 05:00'` y `< '2026-10-28 05:00'`. Y el parámetro debe tener el mismo tipo que la columna
(`datetime2`); si no, SQL Server convierte la columna en cada fila y se pierde el Seek otra vez.

**Cómo comprobarlo.** Con `SET STATISTICS IO ON` y el plan real, antes y después: el plan debe pasar de un Scan a un
Index Seek y las lecturas lógicas deben bajar de forma drástica. No ejecuté esto en SQL Server, porque no
tengo acceso a una instancia: es el razonamiento y la forma de verificarlo.
