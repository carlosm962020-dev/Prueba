# Sección 5. Revisión de código (B4)

Archivo revisado: `codigo/enviar_reporte.py` (44 líneas). La versión corregida es `revision/enviar_reporte_v2.py`,
con 8 pruebas en `tests/test_revision.py`, una por defecto cerrado.

## 5.1 Problemas encontrados

| Sev. | Líneas | Problema | Impacto en producción |
|---|---|---|---|
| Crítica | 7-8 | Usuario `admin_etl` y su contraseña escritos en el código. | Quedan en el repositorio y en su historial, y los ve cualquiera con acceso al código. Es una cuenta de administración usada para solo leer ventas. Hay que rotar la contraseña, no basta con borrar la línea. |
| Crítica | 27 | `print` imprime usuario y contraseña en cada envío. | La clave queda en el log de Jenkins, que muchas más personas pueden ver que el código. |
| Crítica | 38-39 | `except: pass` rodea todo el trabajo de cada zona. | Cualquier error desaparece sin dejar rastro: conexión rechazada, SMTP caído, un valor mal formado. Es la causa del correo que no llega. Además atrapa `KeyboardInterrupt` y `SystemExit`. |
| Crítica | 40 | Imprime «Proceso finalizado OK» pase lo que pase y el script nunca sale con un código distinto de 0. | Por eso Jenkins siempre queda en verde, y nadie se entera de las fallas. |
| Alta | 13 y 20 | `destinatarios=[]` como argumento por defecto: la lista se crea una sola vez y se comparte entre llamadas. | Cada zona agrega otra vez a gerencia a la misma lista: la zona 1 sale con 1 copia, la 5 con 5. Es la causa del correo repetido. |
| Alta | 36 | `if total > 0` sin alternativa: si no hay ventas, no pasa nada. | Un archivo que no llegó o una zona escrita distinta producen un correo que no sale y ningún aviso, justo lo que Laura pidió evitar. |
| Alta | 37 | Sin control de envío previo. | Un reintento de Jenkins o una corrida manual reenvía todo, incluidas las zonas que ya habían salido. |
| Alta | 18 | SQL armado con f-string y `SELECT *`. | Hoy `zona` sale de una lista fija, pero el patrón permite inyección SQL el día que el valor venga de otro lado, y se ejecuta con una cuenta administradora. `SELECT *` trae columnas que no se usan. |
| Media | 31 | `datetime.now()` sin zona horaria. | Depende de la hora del servidor (UTC en Jenkins): entre las 7 p. m. y la medianoche de Colombia, «ayer» es en realidad hoy. |
| Media | 25-26 | SMTP sin TLS, sin autenticación, sin `timeout` y sin cerrar la conexión. `sendmail` recibe el DataFrame como texto, sin encabezados `From`, `To` ni `Subject`. | El correo sale sin asunto y puede caer en spam. Si el servidor no responde, el job se queda colgado. |
| Media | 14-19 | Una conexión nueva por zona, nunca se cierra. | 5 conexiones abiertas por corrida, sin `timeout`. |
| Media | 10 | La lista de zonas está escrita en el código. | Una zona nueva, o escrita con otra tilde, se omite en silencio. |
| Baja | 35 | `float` para sumar dinero y `sum([...])` sobre una lista. | Errores de redondeo de centavos. Debe usarse `Decimal` y sumar en la base. |
| Baja | 27, 40 | `print` en lugar de `logging`, sin niveles ni hora. | Más difícil seguir un incidente. |

## 5.2 Por qué ocurren los tres síntomas

Para no quedarme en la lectura, ejecuté el script original sin modificarlo, con la base de datos y el SMTP
reemplazados por dobles de prueba. Esto es lo que hizo:

| Escenario | Correos enviados | Salida | Código |
|---|---|---|---|
| Todo funciona | 5 (con 1, 2, 3, 4 y 5 copias del destinatario) | «Proceso finalizado OK» | 0 |
| La base rechaza la conexión (firewall) | 0 | «Proceso finalizado OK» | 0 |
| Una zona sin filas | 0 | «Proceso finalizado OK» | 0 |
| Un valor con punto de miles (`1.299.000`) | 0 | «Proceso finalizado OK» | 0 |

**Por qué a veces llega el correo repetido.** Hay tres causas que se suman. Primero, el argumento mutable de las
líneas 13 y 20: como la lista de destinatarios se comparte, el correo de la quinta zona sale con cinco copias de la
misma dirección. Segundo, aun con todo bien, el script manda cinco correos al día, uno por zona. Tercero, no hay forma
de saber qué ya se envió: si el job falla a mitad y Jenkins lo reintenta, reenvía las zonas que ya habían salido.

**Por qué a veces no llega.** El `except: pass` convierte en silencio cualquier falla de una zona. Los casos reales
son una conexión rechazada (por ejemplo, la IP del agente que el firewall de Azure no permite), un SMTP que no
responde, un `float()` que falla con un valor sucio, o una zona sin filas porque el archivo del core no llegó o la
zona viene escrita distinto. En todos, la zona se salta y no queda ni un mensaje en el log. El `total > 0` de la línea
36 hace lo mismo cuando simplemente no hay datos.

**Por qué Jenkins siempre marca éxito.** Jenkins solo mira el código de salida. Como las excepciones nunca llegan
afuera y la línea 40 siempre se ejecuta, el proceso termina con código 0 pase lo que pase. El «OK» que imprime no
significa que se envió algo.

## 5.3 Qué cambia en la versión corregida

* Ninguna credencial en el código: todo entra por variables de entorno, y la contraseña no se imprime. Hay una prueba
  que lo verifica. La contraseña del original hay que darla por comprometida y rotarla.
* Consulta parametrizada y agregada en la base (`GROUP BY zona`), con la conexión cerrada y con `timeout`.
* Los destinatarios se construyen en cada llamada, y se envía un solo correo al día con una línea por zona.
* Las fallas se registran con el traceback y el script termina con código 1. Si no hay ventas en la fecha, termina con
  código 2 y no envía un reporte engañoso. Jenkins pone rojo en ambos casos.
* Marca de envío creada de forma atómica antes de enviar: un reintento no duplica. Si el envío falla, la marca se borra.
* «Ayer» se calcula en hora Colombia. SMTP con TLS, autenticación opcional y `timeout`. Dinero con `Decimal`.
* Una zona inesperada ya no se descarta en silencio: aparece en el reporte y en el log.

## 5.4 Lo que asumí y lo que queda por decidir

* Cambié el correo por zona (con el DataFrame completo volcado como texto) por un solo correo con el resumen por
  zona. Lo hice porque cinco volcados de filas crudas no se leen y son la fuente del duplicado. Si Laura quiere el
  detalle fila por fila, se agrega como adjunto sin tocar el resto.
* No conozco las columnas de la tabla `ventas`, solo las que usa el original (`fecha`, `zona`, `valor`). La consulta
  se limita a esas tres.
* La marca de envío es un archivo: debe vivir en una carpeta que persista entre corridas de Jenkins. Si el agente
  es efímero, la alternativa es una tabla `envios_reporte(fecha)` con llave única en la base.
* El script nunca se corrió contra la base real, porque no tengo acceso: las pruebas usan dobles para la base y el SMTP.
