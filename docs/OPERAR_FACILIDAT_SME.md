# Facilidat SME | Preparación administrativa

**Corte de referencia:** 31/10/2026, hora de Bogotá.
**Fin del procedimiento:** preparar y verificar localmente. **No remitir**,
no entrar al portal para cargar archivos y no aceptar términos en nombre de
Aprobado. El permiso interno de SME no autoriza generar un archivo oficial
ni transmitir datos.

## Evidencia y separación de plantillas

| Fuente entregada | Ubicación verificable | Aplicación |
| --- | --- | --- |
| Presentación Primera Entrega de Información | pp. 5-8 | Autorización, comunicación previa, criterio operativo de edad de mora, certificación y alternativas iniciales. |
| Misma presentación, **Planilla Empresas** | pp. 18-21 | Proceso mensual, entrega total, fin de mes, formato AAAAMMDD, pesos, término definido cuando aplica y moneda legal. Es referencia, no prueba de claves asignadas en SME. |
| Misma presentación, **Planilla Empresas** | pp. 22-25, 27-29 | Campos esenciales, financieros y demográficos; novedades y autorrelleno general. Q depende del tipo de obligación y de la novedad; el requisito para entidades supervisadas no acredita que Aprobado lo sea. |
| Misma presentación, **Planilla Empresas** | pp. 30-32 | Validar después de corregir y volver a autorrellenar; producir un plano. La demostración guarda un archivo con extensión .T y lo comprime en .ZIP. No hay evidencia de que la edición SME inspeccionada produzca exactamente ese nombre, extensión o estructura. |
| Ejemplos Diligenciamiento Planilla | p. 1, bloque Créditos | Semántica financiera, ejemplos al día y vencido, fechas y ausencia de pago. No se trasladan posiciones ni se confunde con facturas o cartera rotativa. |
| Descripción Tipologías de Cuenta o Cartera v2.0 | pp. 14-15 | LBZ Libranza; LBD Libranza Digital; LBE empleados; LBP pensionados; LBR rotativa; LBC compra de cartera. LBD es una propuesta para Libranza Digital de Aprobado, no una asignación ni clasificación de Prestadores. |
| SME Resumida, inspección de 25/11 | Hoja1 A4:AJ4, notas y listas; Hoja2, nombres | Orden real de 36 columnas, comentarios, catálogos y formato de fecha. Los campos automáticos de Empresas se cruzan **por nombre**. Su rango X:AH no se usa en SME. Abrir las macros no fue necesario para leer la estructura. |

El diccionario y la matriz nuevos incluyen huellas de los tres documentos,
referencias de página y letra de SME por campo. No acreditan la
configuración asignada. El 287922 viene del nombre del XLS original;
la configuración leída en Hoja2 tiene valores de ejemplo (fechas de
2019, un código histórico distinto y abreviatura de cartera vacía).
No copiar esos valores a la configuración de Aprobado. Las vistas vacías
del portal mencionadas en el expediente no prueban primera entrega.

## Configuración y campos

- **Perfil de referencia versionado:** facilidat-sme-preparacion-2026-11-25-v1.
  Actualización Mensual, Actualización Total, AAAAMMDD, Pesos, Legal,
  término Definido si hay vencimiento pactado. Fin propuesto: 20261031,
  último día calendario del mes. No cambiar la plantilla ni llenar nombre
  abreviado, tipo de obligación, garantía o cartera sin cotejo institucional.
  La periodicidad debe concordar con el contrato. Registrar la captura
  institucional autorizada y el historial/acuse antes de considerar un envío.
- **Suministro Aprobado:** A/B/C/D/F/G/H, J/K, S:Z, AC/AD y
  correspondencia AE:AJ según soporte. La preparación existente omite
  identidad/contacto y demás campos no verificados en el snapshot.
- **Autorrelleno Empresas, por nombre en SME:** E, R, I, L, M, N, O, P, AA,
  AB y Q cuando aplica. Se dejan sin prefijar en octubre. No reemplazar
  cifras calculadas con ceros por comodidad ni asumir que SME automatiza el
  mismo conjunto. No inventar M=17 ni Q=AA/BB/CC, ni equiparar Q a scoring.
- **Condicionados:** correspondencia acreditada, causas de cierre, tipo de
  titular, autorrelleno/calificación aplicable y novedad de una cuenta ya
  remitida. No hay columna explícita de reclamos en SME. No codificar un
  reclamo con un adjetivo ni un estado financiero sin respaldo.

## Reglas financieras y simulaciones de octubre

El Excel interno distingue monto aprobado, capital financiado, capital
pendiente del plan, saldo pendiente con intereses y saldo vencido. En
**Carga**, S requiere el capital original desembolsado sin intereses y T
el remanente de ese capital. Libranza puede financiar comisión e IVA; no
se sustituye el giro por el aprobado ni su principal por el capital del
plan que incluye cargos. Falta evidencia segregada S/T para las filas de
octubre. U=0 en las obligaciones no rotativas de Libranza acreditadas;
V, W, X, Y y Z reutilizan el cierre. W incorpora el vencido ya respaldado
sin crear cargos. AC es la cuota del mes para al día y la más antigua
impagada en mora. Se corroboraron 47 de las 57 fechas del mes por lectura
del plan; en las otras 10 no se inventa fecha. AD usa el último pago/abono
al corte; cero solo donde se acredita ausencia de pagos.

- **Positiva:** 57 activas al día y con saldo pendiente. No incluye la
  obligación pagada. Es un escenario interno, no 57 registros autorizados.
- **Mixta:** 57 en base positiva y 32 vencidas en revisión. Capacidad
  teórica de 28 negativas con esa base (2:1), no una selección. Los
  32 casos cumplen la ventana operativa de edad al corte, pero **0
  negativas fueron seleccionadas**; faltan evidencias y bandas.
- **Actualización posterior:** 05 para cierre total conciliado donde se
  acredita que la obligación ya se reportaba. No borrar obligaciones
  reportadas ni emitir 05 en primera entrega. 06, 07, 08 y 09 se leen como
  etiquetas de mora; no están cerrados sus límites. 12/13 no se originan
  por el mero paso del tiempo. 01 nunca sustituye una mora.

Para un negativo separar: (1) mora financiera y atribución; (2) autorización
y soporte; (3) comunicación verificable y espera de 20 días calendario
desde la última aplicable; (4) dos comunicaciones en días distintos
cuando el monto completo no supera 15 % de un SMLMV, no según saldo;
(5) ventana operativa de Experian de 30 y menos de 540 días para el
primer negativo, distinta del máximo legal de 18 meses; (6) reclamos,
novedades posteriores y certificación semestral. Un reporte previo en
otra central o un acuerdo firmado exige prueba individual; la
presentación no autoriza una excepción automática. Reevaluar a la fecha
real de remisión si alguna vez el PM la autoriza, no respecto al corte
únicamente. No se generan comunicaciones. Véanse [Ley 2157 de 2021](https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=173246)
y [SIC, concepto 22-228073](https://sedeelectronica.sic.gov.co/sites/default/files/boletin-juridico/conceptos/22-228073.pdf).

## Operación local, sin envío

1. En **Dashboard → Reportes → Centrales de riesgo → DataCrédito**,
   seleccionar corte y producto y abrir su cierre. Descargar Cartera Excel,
   Preparación Facilidat, las simulaciones, Conciliación y Excepciones.
   Confirmar la huella del cierre y los conteos. Los intermedios de
   octubre contienen 90 conciliadas de 122; los 32 casos pendientes son
   parte de la cartera interna, no de la base preparada.
2. Para una prueba, usar **Carga Facilidat sintética**. Abrir una copia
   autorizada del Facilidat original, conservar el original y cotejar la
   configuración. No pegar un borrador real incompleto.
3. Configurar en esa copia el fin de reporte del ensayo. Copiar en el
   XLSX sintético la hoja Carga A2:AJ4 y pegar **solo valores** desde
   Hoja1 A5 en celdas autorizadas. No copiar encabezados, formatos, listas
   ni macros y no sobrescribir campos que el flujo oficial deba calcular.
4. Probar el autorrelleno y **Validar** solo en entorno aislado, después de
   confirmar procedencia e integridad de los componentes y cómo se
   conservan sin cambiar la seguridad global. El guardado nativo BIFF8
   se detuvo al advertirse pérdida de ActiveX. No aceptar esa pérdida
   ni usar una conversión.
5. Solo si una futura prueba sintética autorizada supera lo anterior:
   generar localmente el plano con la herramienta del proveedor,
   identificar la extensión producida y comprimir su salida en ZIP si
   corresponde. Conservar huellas y resultados del ensayo. **Detenerse
   aquí, no cargar, no aceptar términos y no transmitir.** No se
   ejecutaron esos pasos en este candidato.

## Soportes concretos de Aprobado

| Casos | Evidencia faltante |
| ---: | --- |
| 12 | Comprobante de giro con fecha efectiva vinculado a la activación. |
| 10 | Asiento, recibo o acta correlacionada que explique cuota frente a asientos. |
| 5 | Tratamiento contable del residuo: el acta de cierre no acredita efectivo. |
| 4 | Imputación acreditada del remanente contra el pago antes del corte. |
| 1 | Plan y saldo históricos a la fecha efectiva del abono a capital. |

El Excel de excepciones ya existente identifica cada obligación y aporta
9 cuantías respaldadas. Además: (a) ficha y evidencia del suscriptor que
confirmen tipología, parámetros e historial de entregas, (b) evidencia
segregada para S y T y fechas de cuota para los 10 casos pendientes de AC,
(c) autorización, reclamos, certificación y comunicaciones cuando
correspondan, y (d) constancia de prueba sintética de autorrelleno,
validación y plano de **SME** sin pérdida de componentes. Ninguno de estos
pendientes bloquea las descargas administrativas.
