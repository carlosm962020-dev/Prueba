# Sección 2. Análisis del requerimiento (B1)

## 2.1 Requerimientos

**Funcionales.**

- **RF1** Correo diario a Laura a las 6:00 a. m. con las ventas del día anterior (00:00 a 24:00, hora Colombia), por zona y por producto.
- **RF2** Venta = APROBADA o REVERSADA; la ANULADA no cuenta; el punto de pruebas queda fuera.
- **RF3** Comparativo contra el día anterior.
- **RF4** Aviso si el archivo del core no llega.
- **RF5** Un solo correo por día, aunque el proceso se reintente.
- **RF6** Historia guardada en un modelo dimensional que Power BI pueda leer.
- **RF7** (implícito) Aplicar los cambios de estado que llegan después y reportar qué se descartó o corrigió.

**No funcionales.** Puntualidad (arranca a las 4:30 para tener margen) · idempotencia (correrlo N veces deja lo mismo) · exactitud (Decimal, reglas auditables, el resultado debe igualar el Excel de Paola en días pasados) · fallas ruidosas (código de salida distinto de 0 y alerta, nada silencioso) · seguridad (sin credenciales en el código, SMTP con TLS, usuario de solo lectura para BI) · trazabilidad (log con conteos por motivo y una fila de control por carga).

## 2.2 Ambigüedades, riesgos y preguntas para Laura

| # | Qué no está claro | Riesgo, con los datos del kit | Pregunta |
|-|----------|--------------------------|--------------------------|
| 1 | «A las 6 a. m. con las ventas de ayer» | El archivo completo sale a la 1:00 a. m. y las anulaciones llegan en el incremental de las 6:00 p. m. Con el archivo de la madrugada el 27/10 suma $954,4 M; con el incremental, $916,2 M (−4,0 %). | ¿Sirve una cifra preliminar a las 6 a. m., corregida en el tablero por la noche? ¿Quiere un segundo correo de corrección, marcado como tal, o solo el dato final? |
| 2 | «De 12 de la noche a 12 de la noche» | El core guarda UTC. Agrupar por fecha UTC deja el 27/10 en $883,7 M en vez de $954,4 M (−7,4 %). | ¿Confirma que el día es hora de Colombia? |
| 3 | «Lo reversado sí cuenta» | ¿Valor completo o solo la comisión? Las RECHAZADAS son cerca del 8 % de los registros y el correo no las menciona. | ¿La reversada entra por su valor completo? ¿La rechazada queda fuera? |
| 4 | «El punto de pruebas» | Otro punto de pruebas inflaría ventas. Hay también un punto sin zona (00133) y puntos nuevos de Urabá, abiertos el 27/10. | ¿El de pruebas es solo el 90001? ¿Cómo quiere ver los puntos sin zona? |
| 5 | «Si el archivo no llega, avísame» | Un archivo vacío, viejo o incompleto tampoco «llegó». En el incidente de B5 se cargó solo una parte y nadie se enteró. | ¿A quién se avisa, por qué canal y a qué hora se considera tarde? ¿Prefiere no recibir reporte o uno con advertencia? |
| 6 | «Contra el día anterior» | Un lunes contra un domingo no es comparable. | ¿Contra el día anterior o contra el mismo día de la semana pasada? ¿En valor, porcentaje o ambos? |
| 7 | «Que no me llegue dos veces» | No sé quién más lo recibe ni en qué formato. | ¿Quiénes reciben el correo? ¿Excel adjunto o resumen en el cuerpo? |
| 8 | «Que quede guardada» para Power BI | Sin historia, detalle ni permisos definidos. | ¿Cuánto histórico, a nivel de transacción o agregado, y quién debe ver solo su zona? |

## 2.3 Supuestos para avanzar sin sus respuestas

1. El día va de 00:00 a 23:59:59 hora de Colombia (UTC-5); el pipeline convierte desde UTC.
2. Venta = APROBADA + REVERSADA por su valor completo. ANULADA y RECHAZADA no cuentan.
3. Punto de pruebas = 90001, excluido antes de cargar. El punto sin zona se muestra como «Sin zona», no se descarta.
4. El comparativo es contra el día calendario anterior, en valor y en porcentaje.
5. El correo de las 6 a. m. es preliminar (usa el archivo de la madrugada). El incremental corrige la base y el tablero, y no genera otro correo.
6. Un correo al día a Laura con el Excel de tres hojas. Una zona sin ventas sale con cero y marcada.
7. Si el archivo no llega, el equipo recibe la alerta de inmediato y Laura un aviso corto a las 5:45. No se envía un reporte vacío ni con el archivo de ayer.
8. Si el core corrige un registro, gana el de `fecha_actualizacion` más reciente.
9. Sin servidor SMTP en la prueba, el pipeline deja el correo armado (.eml).
10. Los datos del kit no traen información personal de clientes (id, punto, producto, valor, estado, canal).
