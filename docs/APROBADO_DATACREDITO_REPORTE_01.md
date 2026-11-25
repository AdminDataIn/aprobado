# APROBADO-DATACREDITO-REPORTE-01 | Reporte mensual de cartera SME

**Corte:** 31/10/2026, `America/Bogota`. **Base verificada en VPS:**
`7327bae01e5b5382bc2c79cfaea483a91255d8b8`. Este frente no consulta HDC+,
MiDecisor ni scoring. La salida de este candidato es **interna, no remitir**.

## Cierre administrativo con los documentos de capacitación

El [manual operativo local](OPERAR_FACILIDAT_SME.md) identifica las páginas
de los tres documentos de Experian y distingue Empresas de SME Resumida.
Se versionó el perfil propuesto sin atribuir LBD al suscriptor ni asumir
historial de reportes. El diccionario y la matriz nuevos conservan los
36 encabezados y referencian el documento, página y alcance por campo.

El panel descarga cartera completa, preparación Facilidat de conciliadas,
simulaciones positiva y mixta, y conciliación y excepciones. Todo sale del
mismo cierre guardado y queda marcado NO REMITIR, incluso los intermedios
con datos reales. Los intermedios requieren fin de mes; el reporte interno
no. El endpoint de remisión permanece bloqueado para todos los usuarios.

En la copia de trabajo de octubre se verificaron por solo lectura 47 fechas
de cuota del mes de 57 casos al día; no se cambiaron cifras ni registros
productivos. La simulación positiva incluye los 57 candidatos financieros;
la mixta conserva esos 57 y separa 32 vencidas para revisión, con un tope
teórico de 28 y cero negativas seleccionadas. Quedan 90 conciliadas de
122, 32 excepciones y ninguna obligación habilitada. El XLSX compatible de
preparación está disponible; no equivale a una planilla diligenciada lista
para validar o remitir. No se ejecutó ningún componente de Facilidat.

## Excel intermedio y panel administrativo (entrega anterior)

El panel genera archivos .xlsx con openpyxl en Linux. No usa Excel COM,
no toca el .xls Facilidat y no genera TXT, ZIP de transmisión ni envía
información. La descarga real está bloqueada en el servidor; no se abre
con un cambio de los indicadores del cierre. El XLSX de carga solo contiene
los tres casos sintéticos ya ensayados; no lee datos financieros ni de
identificación. Las 36 columnas exactas A:AJ, con encabezado en fila 1,
se fijaron a partir del diccionario; las celdas de entrada son cadenas
con formato de Excel Texto (@). Así se conservan ceros iniciales,
identificadores extendidos, fechas AAAAMMDD y códigos de ejemplo.

El operador encuentra en el detalle de un cierre **Carga Facilidat
sintética** y **Excepciones Excel**, además de conciliación, excepciones
JSON e historial. Las descargas requieren el permiso de SME, verifican
la huella del cierre y no se almacenan en caché. El informe Excel se
arma con las mismas filas del cierre que muestra el panel. En los
cierres sin trazas monetarias enlazadas, deja el importe vacío; no
consulta una tabla vigente ni presenta el monto aprobado como diferencia.

**Pegado sintético previsto.** En el .xlsx, copiar Carga!A2:AJ4, sin
encabezados. Usar Pegado especial → Valores en Hoja1!A5 de una copia
de trabajo autorizada de Facilidat. No editar encabezados, configuración
ni fórmulas. No guardar si Excel advierte pérdida de ActiveX; no
desbloquear globalmente contenido. El paso de pegado no autoriza usar
los botones del proveedor. No confundir la plantilla original, esta
carga intermedia y el archivo oficial que generaría el operador.

**Excepciones al 31/10/2026.** Se reutilizaron los JSON privados del
cierre focal, pendientes y trazas, verificando sus huellas y el
cotejo por obligación. La hoja Excepciones tiene filtros, producto,
incidencia, importe sustentado en COP si existe, alcance del importe,
fuente, evidencia y acción. Se conservan 12 casos sin comprobante de
giro, 10 diferencias cuota/asientos, 5 cierres con residuo, 4 remanentes
sin imputación y 1 reestructuración. Los residuos y diferencias son
indicadores de su evidencia, no saldos ni ajustes. No se suman entre
causas; los importes desconocidos no se presentan como cero.
El informe local tiene 32 filas: 9 con importe sustentado y 23 sin
importe acreditado.

Verificación: 35/35 pruebas focales, incluidas las dos descargas en
Chromium de escritorio y móvil. Excel nativo en Vista protegida leyó
36/36 encabezados y 108/108 valores como texto, iguales a la muestra
original. No se probó todavía el pegado manual ni los botones del
proveedor. No se requiere una migración adicional a la del panel.

### Documentación oficial requerida para la remisión

El operador indica en su [comunicación a suscriptores](https://lmsdatacredito.com/imagescortes/2022/Julio/12/Carta_Comunicacion.pdf)
que los manuales se descargan en **Empresas → Acceso a Suscriptores →
Soporte → Manuales**. Se acreditó la ruta, no el contenido de la edición
contratada. La [guía pública de Experian](https://www.datacredito.com.co/blogs/datablog/por-que-es-importante-reportar-a-un-cliente-en-datacredito-experian/)
confirma el canal y el RAI, pero no el archivo exacto de este Facilidat.

| Documento o anexo vigente que se necesita | Validación por cerrar |
| --- | --- |
| Manual de Entrega de Información de Cartera, Tarjeta de Crédito y CRT: diccionario y anexos de cartera aplicables a SME | Combinaciones I/J/K/M/O, significado de M=17, límites de bandas de mora J, fecha AC y AD cuando no hay pago o cuando está vencido. |
| Catálogo de calificación para este reporte y política institucional aplicable | Q: AA/BB/CC y asignación autorizada de calificación. No usar scoring como reemplazo. |
| Especificación de registro y obligatoriedad del layout SME resumido | Unidad/escala/redondeo de importes S:W; largo y caracteres de identidad/obligación/nombre; tratamiento de cupo no aplicable y valores sin dato; correspondencia, titular y roles acreditados. |
| Procedimiento del operador para dato en discusión, novedades y rectificaciones de este canal | Registro del reclamo fuera de A:AJ, corrección de estados y confirmación de su procesamiento. No usar un código de mora/adjetivo por descarte. |
| Instructivo vigente de Facilidat SME Resumida, Actualización Mensual, para la edición de la plantilla inspeccionada | Pegado soportado, control de seguridad local autorizado que conserve ActiveX, Validar, Generar Archivo para Enviar, formato/nombre/compresión admitidos y conciliación del RAI. |

Los ejemplos públicos sobre TXT/ZIP no acreditan esta versión. No se
ejecutó ningún botón ni se transmitió el ensayo. Obtener ese instructivo
y sus anexos, verificar procedencia y hacer primero una validación
sintética aislada. Si hace falta intervención del PM, limitarla a esa
prueba local autorizada; no cambiar las políticas globales de Office.
En paralelo, para casos reales deben acreditarse los soportes y las
reglas de reporte positivo/negativo. No se cambió la habilitación:
90 de 122 obligaciones están conciliadas, 32 tienen incidencias y
0 pueden remitirse.

## Facilidat: evidencia del 25/11/2026

Excel 365 para Windows abrió una copia byte a byte en Vista protegida,
sin solicitar contraseña. No se descifró el libro ni se cambiaron políticas
de Office. La lectura se ejecutó en una instancia COM exclusiva, sin
eventos, actualización de vínculos, ejecución de VBA, activación de
ActiveX ni autorización de XLM. El modelo de objetos expuso cero hojas XLM,
cero conexiones y cero vínculos Excel/OLE. En Vista protegida Excel no
expone el proyecto VBA: su presencia se verifica por el contenedor
original, no por esa propiedad COM. SHA-256 de la copia inspeccionada:
`dd57903ab11cfedd628b7d188d2610b5e7632b70f5cdca4149421a285b761e09`.

- Hoja1: encabezados en fila 4, primera fila de entrada 5. Se verificaron
  los **36 campos A:AJ**, todos con nota oficial y formato Excel de texto
  en fila 5. Se leyeron las áreas de validación y búsqueda de fórmulas hasta
  la fila 65.536. No se detectaron fórmulas en el área de datos A5:AJ65536.
- Hoja2: configuración, referencias y anotaciones de homologación. Se
  leyeron 54 nombres definidos y 33 notas de Hoja2. El formato de fecha
  configurado es AAAAMMDD. La configuración de la copia todavía muestra
  fechas de 2019: no se cambió ni representa el corte de octubre de 2026.
- Nueve catálogos leídos: A, E, H, I, J, K, M, O y Q. Acreditado que en
  J/M al día es 01/1 y pago total 05/3; M=2 es en mora y J utiliza
  distintas bandas. No se acreditaron las fronteras exactas de las bandas.
  M permite 17 sin descripción; Q permite AA, BB y CC sin descripción
  y la nota no establece una política que se pueda aplicar a Libranza.
  Algunas listas están interrumpidas y muchas tienen la alerta de validación
  desactivada. El permiso de editar una celda o dejarla vacía no prueba
  que el validador de remisión la acepte.
- No hay una columna propia de reclamos en A:AJ. No se codifican como
  mora ni como adjetivo por analogía. S pide valor desembolsado, no capital
  financiado; T pide saldo total, y W pide capital e intereses vencidos.
  Se dejan pendientes formato y unidad monetaria de transmisión,
  obligatoriedad completa y los campos no sustentados por Aprobado.

### Prueba de captura de 3 filas

Excel COM escribió y leyó nuevamente 108 celdas ficticias de A5:AJ7
en memoria; 27 celdas con lista se cotejaron con el catálogo. Los casos
fueron al día, mora 30 y pago total. No prueban identidad ni validez ante
el operador. Al intentar el guardado BIFF8 en una copia, el comprobador
de compatibilidad advirtió que los controles ActiveX no inicializados
de Hoja1 se perderían. **Se canceló.** No se desactivó el comprobador,
no se habilitaron los controles, no se guardó un XLS degradado y no se
convirtió a XLSX. La copia cancelada difiere como contenedor, pero sus
34 flujos internos (libro, VBA y demás componentes) son idénticos a la
referencia; no contiene las filas escritas en memoria.

Se custodian fuera de Git el diccionario nativo, la matriz de los 36
campos y el JSON sintético que conserva orden, encabezados y cadenas.
No son el archivo oficial de octubre. El inspector Excel y el ensayo
solo en memoria son herramientas locales Windows; no son dependencias
del VPS.

### Gates que quedan

1. Obtener el procedimiento vigente de la edición contratada en el portal
   de suscriptores, **Soporte → Manuales**, y acreditar el modo soportado
   de validar/generar el artefacto sin degradar la plantilla. Los botones
   no se ejecutaron. [Experian: canales, cargue y RAI](https://www.datacredito.com.co/blogs/datablog/por-que-es-importante-reportar-a-un-cliente-en-datacredito-experian/)
   confirma Facilidat y entrega por archivo plano como opciones; no
   identifica el resultado de los botones de este libro. No se presume que
   el XLS editado sea el archivo final ni que sea TXT, plano comprimido u
   otro formato. Primera validación: exclusivamente sintética y sin envío.
2. Conciliar las 32 excepciones y acreditar elegibilidad de cada obligación
   seleccionada. Siguen 90 conciliadas de 122, 110 desembolsos acreditados
   y cero habilitadas. Esta intervención no aportó nuevos soportes
   financieros ni cambió el cierre.
3. Acreditado el formato y la elegibilidad, emitir en Linux datos ordenados
   desde el mismo cierre SME para la etapa local de Facilidat. No usar un
   conversor que pierda VBA, validaciones, catálogo o ActiveX. El JSON de
   esta prueba demuestra el intercambio intermedio, no la remisión. No se
   habilitó una exportación real ni cambió el Dashboard interno.

## Revisión focal posterior al cierre inicial

Se conservó el cierre inicial como evidencia. En PostgreSQL forzado a solo
lectura se revisaron sus 37 incidencias, se recalcularon únicamente esas
filas y se mantuvieron intactas las 85 previamente conciliadas.

| Corte 31/10/2026 | Resultado |
| --- | ---: |
| Inventariadas, Libranza | 122 |
| Desembolsos acreditados | 110 |
| Conciliadas | 90 |
| Al día / vencidas / pagada entre conciliadas | 57 / 32 / 1 |
| Capital pendiente entre conciliadas (COP) | 396.273.939,89 |
| Saldo vencido entre conciliadas (COP) | 23.989.743,20 |
| Habilitadas para remisión | 0 |

Se resolvieron 4 casos de un centavo debido al redondeo del plan y al tope
de capital e interés del Core, y un caso con diferencia de imputación
exclusivamente posterior al corte. El centavo solo se acepta como tal si
coincide con la diferencia de componentes del plan Libranza y la cuota
está completamente cubierta. Se usa el efectivo aplicado para saldo de
cuota y la imputación registrada para capital; no se añade capital ni
interés. Un excedente no aplicado posterior no reduce el saldo de octubre.
No se cambia ningún asiento ni se utiliza un acta de cierre como recibo.

**Pendientes reales (32):** 12 activaciones anteriores al corte sin
comprobante de desembolso correlacionado; 10 diferencias entre cuota y
asientos o cierres sin acta verificable; 5 cierres operativos auditados
con residuo financiero; 4 pagos anteriores con remanente sin imputación
acreditada; un abono a capital con plan reestructurado no reconstruible.
La revisión inicial detectó 8 diferencias de desglose de un centavo, no
reversos demostrados. Tres desembolsos posteriores ya estaban fuera del
inventario; no se deben volver a excluir del denominador de 122. Los 12
sin comprobante no prueban ausencia de giro.

**Pagador.** Entre las 32 vencidas al corte, 12 tienen pago parcial, 8
tienen aplicaciones posteriores y 3 tienen pagos ligados al pagador, sin
referencia de comprobante de lote para esos pagos. No se encontró un pago
pendiente en el historial ni un Wompi aprobado o un BRE-B reportado sin
aplicar en el grupo. No se acreditó un descuento de nómina retenido sin
giro ni un incumplimiento imputable al titular. Para atribuir la causa,
cotejar nómina, giro y recepción bancaria por obligación. Al 23/11/2026,
31 de esas mismas 32 obligaciones seguían vencidas; una quedó sin
conciliar por un pago que no coincide con sus asientos. El estado nominal
del crédito no reemplaza la clasificación derivada de cuotas. Referencia:
[Ley 1527, art. 6](https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=47213).

**Soportes de reporte.** El cierre identifica 57 candidatas financieras
al día y una pagada, no registros positivos habilitados. La comunicación
previa no bloquea por sí sola un reporte positivo. Para ambos tipos falta
confirmar reglas de la planilla y reclamos/rectificaciones, el soporte de
la obligación y la base aplicable, incluida autorización cuando proceda.
Hay 110 pagarés v1.0 marcados firmados y ninguno de esos registros tiene
un archivo firmado referenciado en el campo correspondiente. La plantilla
v1.0 revisada no acredita una cláusula expresa de reporte, y el texto
general de privacidad no demuestra una aceptación individual versionada.
Para las 32 vencidas, 11 tenían una marca operativa de aviso al corte;
ninguna marca sola acredita destinatario, contenido, envío ni plazo legal.
No se identificó pequeña cuantía entre ellas en el filtro preliminar por
monto original; no usar el saldo pendiente para esa decisión. No se
generaron comunicaciones ni se declaró inexistencia de reclamos. Fuentes:
[Ley 1266, arts. 6, 8, 12 y 16](https://www1.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=34488)
y [orientación SIC sobre soporte y comunicación](https://sedeelectronica.sic.gov.co/publicaciones/boletin-juridico/concepto/el-reporte-negativo-de-informacion-financiera-debe-ser-sustentando-con-la-obligacion-respectiva).

**Custodia.** El cierre focal, los soportes de vencidas y el informe
`Pendientes_operativos_2026-10-31_NO_REMITIR.json` están en
`%LOCALAPPDATA%\Aprobado\SME-2026-10-31-r3`. No contienen nombres,
identificaciones ni archivos de titulares. No se publicaron ni enviaron.

**Gates independientes.** El panel interno mantiene previsualización,
historial y descargas privadas; muestra conciliación y remisión por
separado. La descarga oficial y el gate de remisión están bloqueados.
En esa revisión el usuario ejecutó el inspector anterior pero no se completó
la lectura; ese hallazgo se conserva solo como antecedente.
La copia actualmente bajo el mismo nombre sigue cifrada y su SHA-256 es
`cdebe9515200a963b509099d83dc2c2e5a49357b269870c5e01b906fbaae4336`, distinto
del observado al primer cierre. La prueba del 25/11 superó ese bloqueo
con Excel nativo; en adelante no se pide clave ni se usa la vía de
descifrado. No se generó XLS sintético ni real con encabezados supuestos.

## Inspección del contenedor (antecedente)

`901949137.287922.PLANILLA__SME_RESUMIDA.xls`, 3.748.864 bytes, SHA-256
`0a80b2ee09d4de0bef0c4a806c303546dc263029a82812d018a0921568b8600d`.
Se comprobó contenedor OLE, BIFF8, registro `FILEPASS` (tipo y versión
`010004000200`) y componentes VBA. Se preservan el archivo y las macros;
no se ejecutaron ni se intentó ninguna clave.

El inspector previo confirma el contenedor sin Excel. FILEPASS no implica
que Excel solicite una clave al abrir el libro ni impide la lectura con COM.
Para esta edición usar `diagnosticos/inspeccionar_facilidat_excel.ps1` sobre
la copia custodiada; no usar la ruta anterior de descifrado.

## Matriz preliminar histórica: antes de la lectura de Excel

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

## Cierre interno inicial de octubre (evidencia conservada)

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

Los gates actuales son los de la evidencia Facilidat del 25/11/2026, al
inicio de este documento. No despliegue, migración en producción, push ni
envío sin revisión.
