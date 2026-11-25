# APROBADO-DATACREDITO-REPORTE-01 | Reporte mensual de cartera SME

**Corte:** 31/10/2026, `America/Bogota`. **Base verificada en VPS:**
`7327bae01e5b5382bc2c79cfaea483a91255d8b8`. Este frente no consulta HDC+,
MiDecisor ni scoring. La salida de este candidato es **interna, no remitir**.

## Planilla y formato

`901949137.287922.PLANILLA__SME_RESUMIDA.xls`, 3.748.864 bytes, SHA-256
`0a80b2ee09d4de0bef0c4a806c303546dc263029a82812d018a0921568b8600d`.
Se comprobó contenedor OLE, BIFF8, registro `FILEPASS` (tipo y versión
`010004000200`) y componentes VBA. Se preservan el archivo y las macros;
no se ejecutaron ni se intentó ninguna clave.

`diagnosticos/inspeccionar_plantilla_sme.py` inspecciona el contenedor sin
Excel. Con `--estructura <ruta-local-nueva.json>` y una consola local,
solicita la contraseña sin eco, descifra en memoria y enumera metadatos de
las hojas; no guarda el libro sin cifrar. Requiere lectores locales
`olefile==0.47`, `msoffcrypto-tool==5.4.2` y `xlrd==2.0.2`.
El inventario estructural no demuestra reglas ni ejecución de fórmulas o
VBA. Con la plantilla abierta hay que cotejar la hoja de instrucciones,
rangos, encabezados, obligatoriedad, tipos, longitudes, formatos, catálogos,
validaciones, fórmulas y salida de remisión. No se presume que se envíe el
XLS mismo ni que un CSV o XLSX lo reemplace.

## Matriz previa: sin nombres, códigos ni orden oficiales supuestos

La primera columna queda sin acreditar para todas las filas hasta leer la
planilla. Las fuentes abajo son para la conciliación interna, no para
autorizar su copia en el archivo de transmisión.

| Columna oficial | Concepto | Fuente Django | Transformación / validación | Cobertura interna |
| --- | --- | --- | --- | --- |
| Pendiente | Titular y tipo/documento | Detalle de la línea: `CreditoLibranza`, `CreditoEmprendimiento`, vínculo de `CreditoAdelantoNomina` | Verificar titular y tipo por expediente; no usar OCR o usuario como evidencia única | No se lee ni exporta PII en el cierre interno |
| Pendiente | Identificador de la obligación | `Credito.numero_credito` único | Longitud, uso de prefijo y continuidad dependen de catálogo SME; las descargas privadas conservan el número interno y PK | Una fila por crédito, no por persona |
| Pendiente | Apertura / fecha original | `Credito.fecha_desembolso` + `HistorialEstado` con evidencia de activación | Bogotá; no inferir apertura contractual a partir de solicitud o firma | Se exige la secuencia comprobada del Core |
| Pendiente | Valor original, capital financiado y cuota | `Credito.monto_aprobado`, condiciones del Core y `CuotaAmortizacion` | Decimales COP, sin confundir neto recibido con capital o cuotas | Se preservan divergencias contra el plan en campos distintos |
| Pendiente | Saldo de capital y cuota vencida | `DetalleContablePago` + plan persistido verificado | Restar sólo movimientos aplicados; no leer el saldo vigente como saldo histórico | Imputación por cuota y compartimentos de capital/interés |
| Pendiente | Cuotas pactadas, pagadas, pendientes y vencidas | `CuotaAmortizacion` + aplicaciones exitosas correlacionadas | Pagada con saldo de cuota cero y apoyo contable; vencida anterior al corte | Parciales, múltiples cuotas y días desde el vencimiento pendiente más antiguo |
| Pendiente | Último pago / vencimiento final | `HistorialPago.fecha_aplicacion`, último vencimiento del plan verificado | Frontera: antes de 00:00 del 01/11 en Bogotá. Fecha de registro no reemplaza la aplicación | No incluye pagos posteriores en saldos o última aplicación |
| Pendiente | Estado financiero, mora y calificación | Derivación de cuotas e historial; calificación sin fuente oficial | No convertir vencida en al día por impedimento; no usar scoring V3 para inventar código | Estado interno no equivale a códigos SME |
| Pendiente | Pagada, modificaciones, reversos o novedad posterior | Historial de estados, `ReestructuracionCredito`, trazabilidad de pagos | No reconstruir con el plan actual cuando cambia; revisar efecto a la fecha de remitir | Cierres conciliados visibles internamente; regla SME de reporte no acreditada |
| Pendiente | Autorización, reclamo, rectificación y comunicaciones | Evidencia individual de la obligación y registro operativo acreditado | No adoptar consentimiento de consulta ni avisos de mora como prueba de reporte | Bloqueo explícito por falta de conciliación de evidencias |

## Corte reproducible y límites

La cartera interna se obtiene en PostgreSQL `REPEATABLE READ READ ONLY`.
El universo de revisión utiliza desembolsos registrados y deja como
incidencias las situaciones operativas sin fecha o sin comprobante
correlacionado; no usa las solicitudes sin desembolso como cartera.
Libranza y Prestadores se separan por `es_prestador_servicios`. Adelantos y
Emprendimiento se evalúan por línea si existen obligaciones. Se excluyen
la empresa de pruebas documentada y los IDs de prueba acreditados en la
configuración protegida `SME_CREDITOS_DE_PRUEBA_ACREDITADOS`. Ausencia de
exclusiones detectadas no equivale a certificar todos los marcadores de
pruebas históricos.

Se verifica el plan y para Libranza se coteja el ancla con el Core. Se
agrupan aplicaciones exitosas desde su fecha efectiva; movimientos
pendientes, sin imputación, no correlacionados o sin evidencia causal
no reducen el saldo. No se crean intereses de mora, no se hacen ajustes
contables y no se reconstruyen reversos por inferencia. La primera
incidencia bloqueante se registra por obligación; no es inventario
exhaustivo de indicios para esa obligación. Nómina descontada sin giro
o pago aún no conciliado requiere evidencia por separado.

La activación legacy redondea el total original y cada cuota en momentos
distintos. Para una Libranza homogénea con comisión/IVA acreditados se
verifica el margen matemático de esa persistencia y la consistencia local
de cada fila; se guardan las diferencias del plan contra el total y el
capital financiado. No se redondean aplicaciones para forzar un cierre.
Capital pendiente interno se toma del capital amortizable del plan menos lo
realmente imputado; la diferencia frente al capital financiado se conserva.
Los agregados monetarios cubren exclusivamente las filas conciliadas.

## Cierre interno de octubre

La lectura de producción del 24/11/2026 confirmó el SHA base y forzó
PostgreSQL a solo lectura. Se inventariaron 122 obligaciones de Libranza:
110 con desembolso acreditado y 85 conciliadas (55 al día, 29 vencidas,
una pagada). Al subconjunto conciliado corresponden COP 391.189.173,50
de capital pendiente y COP 21.367.322,65 de saldo vencido. Tres registros
con desembolso posterior al corte quedaron fuera del inventario. No se
identificaron obligaciones de Prestadores, Adelantos o Emprendimiento
dentro del universo del corte.

Las 37 incidencias financieras principales se distribuyen en 12 sin
traza de desembolso al corte, 9 con movimiento de cuota sin soporte
contable, 8 con desglose o reverso no conciliado, 7 con monto aplicado
distinto del pago y una reestructuración sin plan histórico acreditado.
Las 122 filas tienen al menos un impedimento de remisión; cero quedaron
habilitadas en esta revisión. Una lectura previa encontró 110 pagarés
marcados firmados (versión 1.0) y 60 sellos operativos de aviso de mora.
No se acreditó a partir de ellos ni la cláusula de reporte ni el
cumplimiento de comunicación previa.

Consolidado, conciliación y excepciones se custodian localmente en
`%LOCALAPPDATA%\Aprobado\SME-2026-10-31-r3` con ACL restringida. Pueden
consultarse por número interno sin exponer identificaciones personales.
No se guardan en Git ni constituyen un archivo de transmisión.

## Habilitación legal y operativa

- Acreditar soporte específico de la obligación, identidad, veracidad y
  autorización aplicable al reporte (no solo a consultar), incluida cualquier
  excepción jurídicamente aplicable. Un sello de pagaré firmado no sustituye
  cotejar la versión y el tenor autorizado. Fuente: [SIC, soporte de la obligación y autorización](https://sedeelectronica.sic.gov.co/publicaciones/boletin-juridico/concepto/el-reporte-negativo-de-informacion-financiera-debe-ser-sustentando-con-la-obligacion-respectiva).
- Antes de un dato negativo, acreditar destinatario, contenido y fechas de
  comunicación previa, y al menos 20 días calendario desde la última
  comunicación aplicable antes de reportar. Para obligaciones de hasta
  15 % del SMLMV verificar dos comunicaciones en días distintos; no tomar
  el saldo residual como monto completo de la obligación. No equiparar
  `fecha_ultimo_aviso_usuario_mora` a esos soportes.
  Referencias: [Ley 1266, art. 12, y Ley 2157](https://www1.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=34488),
  [concepto SIC sobre pequeña cuantía](https://sedeelectronica.sic.gov.co/sites/default/files/boletin-juridico/conceptos/22-228073.pdf).
- Conciliar reclamos y rectificaciones, marcas de discusión y oportunidad
  de notificación al operador, cancelaciones, pagos posteriores y demás
  novedades antes de preparar la remisión. Validar plazo de primer reporte
  negativo y permanencia/retirada aplicables; no codificar un indicador
  de reclamo o calificación sin manual. Referencias: [Ley 2157, arts. 3, 6 y 7](https://www1.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=173246)
  y [Circular Única SIC, Título V](https://sedeelectronica.sic.gov.co/sites/default/files/normativa/T%C3%ADtulo%20V%20Versi%C3%B3n%2029-09-2022.pdf).

## Panel y siguiente gate

Dashboard → Reportes → Centrales de riesgo → DataCrédito. El candidato local
permite corte y producto, indicadores, incidencias, historial y descargas
internas JSON. Se utiliza un solo snapshot para pantalla y salidas. Permiso
`gestion_creditos.operar_cierre_cartera_sme`, staff activo sin perfil
pagador. La migración local `0052` define una tabla de snapshots privados:
operador, fecha, corte, versión interna, huellas, totales y filas mínimas.
El historial no se sobreescribe en regeneración. Descarga por vista
autorizada, sin URL pública, sin caché y con verificación de integridad.
No se agrega un endpoint de remisión o descarga de plantilla oficial.

Pruebas del candidato: 20/20 sobre PostgreSQL aislado y 2/2 en Chromium
(1280 px y 390 px, datos sintéticos). Incluyen corte Bogotá, pago parcial
y posterior, cancelación posterior, reverso sin soporte, exclusión de
pruebas, separación por línea, redondeo legacy, permisos, huella, historial
y la prohibición de modificar movimientos en la transacción de cierre.
No se ha probado fidelidad de la salida oficial: sigue bloqueada.

Para cerrar el ticket hacen falta: (1) abrir la planilla con la credencial
legítima o disponer de su instructivo/otra copia oficial autorizada, (2)
acreditar formato y semántica de cartera cerrada, reclamos y novedades si
el archivo no los define, (3) conciliar las incidencias de las obligaciones
seleccionadas y acreditar evidencia de reporte antes de cualquier remisión.
No despliegue, migración en producción, push ni envío sin revisión.
