# Sección 8. Uso de IA

## 8.1 Herramientas empleadas

Se utilizó Claude (Anthropic) como herramienta de asistencia técnica durante el desarrollo de la prueba, principalmente en las siguientes actividades:

- **Desarrollo:** apoyo en la elaboración y revisión de código Python para el pipeline ETL, consultas SQL y automatización del envío de reportes.
- **Análisis técnico:** revisión de los requerimientos, identificación de posibles defectos y evaluación de alternativas de solución.
- **Documentación:** apoyo en la estructuración del informe, comentarios del código y elaboración del README.
- **Power BI:** asistencia en la elaboración y revisión de medidas DAX.

El desarrollo, integración y validación de los entregables se realizaron mediante revisión directa del código, ejecución de pruebas y contraste de resultados. El tablero de Power BI fue construido directamente en Power BI Desktop. La imagen de fondo del tablero se generó con Gemini (Google).

## 8.2 Instrucciones (prompts) relevantes utilizadas

Se resumen cuatro instrucciones representativas de las consultas realizadas durante el desarrollo de la prueba. Están redactadas de forma técnica y resumida; el texto exacto de cada mensaje está en el historial de la conversación.

**1. Análisis de requerimientos, arquitectura y plan por fases (B1)**

Analiza el requerimiento de automatización del reporte de ventas diario. Identifica los requerimientos funcionales y no funcionales, las posibles ambigüedades y los riesgos técnicos. Propón una arquitectura que contemple el procesamiento de archivos, almacenamiento relacional, ejecución programada, generación de reportes, alertas y prevención de envíos duplicados. Diferencia los requisitos explícitos de los supuestos necesarios para implementar la solución. Incluye un diagrama de la arquitectura y un plan por fases.

**2. Desarrollo y validación del pipeline ETL (B2)**

Revisa la estructura de los archivos CSV suministrados y plantea una implementación en Python utilizando pandas y SQLite para procesar las transacciones. Considera la normalización de campos, validación de registros, aplicación de reglas de negocio, carga incremental mediante el archivo delta y prevención de duplicados. Incluye registro de errores, trazabilidad de registros descartados y pruebas unitarias con pytest. Justifica las decisiones de implementación.

**3. Análisis y corrección del script de envío de reportes (B4)**

Analiza el script enviar_reporte.py e identifica los problemas que podrían explicar los envíos duplicados, la ausencia de correos y los resultados exitosos de Jenkins cuando el envío falla. Clasifica los hallazgos según su severidad, indica las líneas involucradas y explica su impacto operativo. Propón una versión corregida que contemple manejo de excepciones, validación del envío y códigos de salida adecuados.

**4. Comentarios del código y verificación de dependencias**

Comenta el código de Python para poder sustentarlo en la prueba y verifica qué librerías externas utiliza y si están instaladas. El resultado fueron los comentarios de cada módulo y la revisión de pandas, openpyxl y pyarrow.

## 8.3 Casos de corrección o descarte de respuestas de IA

Durante el desarrollo se identificaron respuestas que requerían corrección o validación adicional antes de incorporarse a la solución.

**Caso 1. Formato de los valores en el correo**

- **Resultado inicial:** la propuesta generaba porcentajes con punto decimal y valores de transacciones sin separadores de miles.
- **Decisión técnica:** se ajustó el formato para mantener la convención numérica colombiana utilizada en los reportes. También se incorporó una prueba que verifica el contenido completo del titular del correo.

**Caso 2. Suposiciones sobre la calidad de los datos**

- **Resultado inicial:** durante la documentación del proceso de limpieza se atribuyó la pérdida de ceros en `cod_punto` al formato de Excel y se utilizaron ejemplos de zonas que no correspondían a los archivos suministrados.
- **Decisión técnica:** se descartaron esas explicaciones porque no existía evidencia suficiente para respaldarlas. La documentación se ajustó a las inconsistencias realmente identificadas en los datos, como «VALLE DE ABURRA» y espacios adicionales en «oriente ».

**Caso 3. Interpretación del requisito de filtrado en Power BI**

- **Resultado inicial:** se sugirió que el tablero debía utilizar un segmentador de fecha con selección única.
- **Decisión técnica:** se verificó directamente el apartado B6, numeral 4, del enunciado. Este únicamente exige permitir el filtrado por fecha, zona y canal. Por tanto, se implementó un segmentador de rango de fechas, sin agregar una restricción que no estaba solicitada.

## 8.4 Verificación y validación de resultados

Los resultados obtenidos con apoyo de IA fueron sometidos a verificaciones antes de incorporarse a los entregables:

- **Pruebas automatizadas:** ejecución de 39 pruebas ubicadas en `tests/`, relacionadas con la limpieza, carga de información, procesamiento incremental y funcionamiento del código revisado.
- **Validación de resultados:** cálculo independiente de las cifras de control del 27/10/2026 mediante un script separado del pipeline, contrastando los resultados antes y después de aplicar el delta.
- **Verificación de archivos:** revisión del Excel generado y del correo de salida en formato `.eml`, lo que permitió identificar y corregir inconsistencias en el formato de los valores.
- **Cumplimiento de requerimientos:** contraste de los entregables con el enunciado original para verificar que las funcionalidades implementadas correspondieran a lo solicitado.

Las respuestas generadas no se asumieron como definitivas: se contrastaron con el enunciado, los archivos suministrados, la ejecución del código y los resultados esperados.
