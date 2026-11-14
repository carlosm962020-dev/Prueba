# Sección 3. Pipeline (B2)

## 3.1 Reglas de limpieza y por qué

Los archivos llegaron con problemas que el diccionario de datos no menciona. Cada fila se descarta por el
primer motivo que le aplique, así lo leído siempre cuadra con lo cargado más lo descartado
(el código lo verifica y falla si no cuadra).

| Regla | Justificación | Filas en `transacciones.csv` |
|---|---|---|
| Leer todo como texto; el maestro en Windows-1252 y las transacciones en UTF-8 con BOM | El maestro trae tildes en otra codificación. Leer números directamente borra los ceros de `00101`. | todas |
| `estado`: quitar espacios y pasar a mayúsculas | Llegan `" aprobada "` y `Aprobada`. Sin esto no se reconocen como ventas. | 349 corregidas |
| `cod_punto`: completar a 5 dígitos | Llegan `101` en vez de `00101`. Sin esto no cruzan con el maestro. | 846 corregidas |
| `valor`: `1.299.000` y `522.500` se leen como miles; `10000.00` como decimal | Hay tres formatos. Es un supuesto: un punto seguido de exactamente tres dígitos es separador de miles, porque en pesos no hay milésimas. | 429 corregidas |
| `fecha_*`: aceptar ISO, `dd/mm/aaaa hh:mm` e ISO con `T` y `Z` | Hay tres formatos. El de `dd/mm/aaaa` no trae segundos. | 835 corregidas |
| Día de negocio = fecha en hora Colombia (UTC-5) | El core opera en UTC. Una venta de las 8 p. m. del 27/10 llega fechada 28/10 01:00. Con UTC, todo lo vendido entre las 7 p. m. y la medianoche caería en el día siguiente. | todas |
| Misma transacción repetida: quedarse con la de mayor `fecha_actualizacion` | El core manda el historial de cambios de estado. | 143 versiones anteriores y 138 duplicados exactos |
| Descartar el punto de pruebas (90001) | Lo pidió Laura. | 24 |
| Descartar `cod_punto` que no existe en el maestro (00199) | Sin punto no hay zona, y no se debe inventar. | 12 |
| Descartar `cod_punto` vacío | Igual que el anterior. | 4 |
| Descartar `valor` vacío | Son ventas aprobadas sin monto; no se inventa. Quedan en la hoja Calidad para pedírselas al proveedor. | 5 |

Maestro de puntos (42 filas leídas, 40 válidas):

* El código `00117` aparece dos veces, una con Rionegro Llanogrande (Oriente, inactivo) y otra con Envigado
  La Magnolia (Valle de Aburrá, activo). Se conserva el activo, porque el punto tiene transacciones recientes.
* Dos zonas venían con mayúsculas o espacios distintos (`VALLE DE ABURRA`, `oriente `) y se normalizaron.
* El `00133` no tiene zona. Se deja como «Sin zona» en vez de asignarle una al azar.
* El `90001` (pruebas) se excluye.

## 3.2 Modelo de datos

```mermaid
erDiagram
    dim_fecha     ||--o{ fact_transacciones : fecha_key
    dim_punto     ||--o{ fact_transacciones : punto_key
    dim_producto  ||--o{ fact_transacciones : producto_key
    dim_canal     ||--o{ fact_transacciones : canal_key
    dim_estado    ||--o{ fact_transacciones : estado_key

    fact_transacciones {
        text id_transaccion PK
        int fecha_key FK
        int punto_key FK
        int producto_key FK
        int canal_key FK
        int estado_key FK
        numeric valor
        text fecha_hora_utc
        text fecha_hora_col
        text fecha_actualizacion_utc
    }
    dim_punto {
        int punto_key PK
        text cod_punto
        text nombre
        text zona
        int activo
        text vigente_desde
        text vigente_hasta
        int es_actual
    }
    dim_estado {
        int estado_key PK
        text estado
        int cuenta_como_venta
    }
    dim_fecha {
        int fecha_key PK
        text fecha
        int anio
        int mes
        text nombre_mes
    }
    dim_producto { int producto_key PK }
    dim_canal { int canal_key PK }
```

Decisiones:

* La tabla de hechos guarda todos los estados, no solo las ventas. Si solo se cargara lo aprobado, una
  anulación posterior nunca podría reflejarse. La regla «venta = APROBADA o REVERSADA» vive en
  `dim_estado.cuenta_como_venta` y en la vista `vw_ventas`.
* La llave es `id_transaccion`. Repetir la carga no duplica, y una fila solo se reemplaza si llega con una
  `fecha_actualizacion` más reciente. Por eso da lo mismo cargar el principal antes o después del delta.
* `dim_punto` guarda la historia de la zona. Si un punto cambia de zona se cierra su versión y se abre otra, y los
  hechos ya cargados conservan la zona de su época. Es la respuesta al caso A9: el pasado no se mueve.
* Todo el cargue (maestro, hechos y calidad) corre en una sola transacción. Si algo falla, no queda nada a medias.
* Las tablas `dq_resumen`, `dq_descartados` y `etl_cargas` guardan la calidad y la bitácora de cada carga.
