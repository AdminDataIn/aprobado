# P0-4D / P0-4E: datos y reconciliacion contractual

## Auditoria del recorrido existente

`SolicitudPrestadorForm` alimenta `ContractorApplication`; los datos se reutilizan
en simulacion, revision y originacion. El formulario de subsanacion comparte los
validadores personales. No se modifican modelos ni el core financiero.

| Campo | Captura / contrato existente | Control incorporado |
|---|---|---|
| Documento CC/CE | TextInput, CharField; frontend 6-12 digitos | Backend ASCII numerico, trim, conserva ceros, no separadores; teclado numerico |
| Celular | TextInput, CharField; Libranza conserva 10 digitos | Misma longitud, normaliza espacios/guiones/parentesis, rechaza letras y prefijo adicional |
| Nombres/apellidos | TextInput, CharField 120 | Unicode, espacios, guion/apostrofe; normaliza NFC y espacios; no numeros ni placeholders exactos |
| Direccion | TextInput, CharField 255 | Contenido significativo, minimo 5 caracteres, admite finca/vereda sin numero vial |
| Correo | EmailInput/EmailField | Validador Django existente |
| Municipio/ciudad | No existe en ContractorApplication | No se agrega catalogo ni campo; geografia actual pertenece a Empresa |
| Empresa/NIT | ModelChoice de convenios activos; NIT registrado en Empresa | NIT primero, formato y DV deterministico; NIT conflictivo no cae a matching por nombre; homonimos requieren seleccion |
| Cargo/tipo/duracion/fechas | Campos/choices/DateField existentes | Se mantienen longitudes, elecciones y consistencia temporal del formulario |
| Importes | MontoContratoField/DecimalField | Se reutiliza parser colombiano; saldo/total/mes validan reglas existentes; JSON IA usa numeros canonicos |
| Forma pago/observaciones/autorizaciones | Choices, texto acotado, BooleanField | Se conservan validaciones y consentimiento existentes |

Errores estructurales bloquean. No hay heuristica de vocales, diccionario de nombres
ni rechazo de apellidos poco frecuentes. Diferencias documentales requieren
confirmacion/revision, no una decision automatica de riesgo.

DV: algoritmo modulo 11, pesos primos de derecha a izquierda, conforme a
[DIAN, Orden Administrativa 4 de 1989](https://normograma.dian.gov.co/dian/compilacion/docs/orden_administrativa_dian_0004_1989.htm).
Si no se suministra DV no se inventa ni se interpreta el ultimo digito como DV.
No se corrigen NIT historicos de Empresa automaticamente.

## Causas de la extraccion parcial

- El fallback solo se ejecutaba cuando fallaba toda la IA, no ante campos ausentes.
- Las etiquetas del parser PDF eran limitadas; no extraia pagos/saldos explicitados.
- El parser de importes eliminaba puntuacion y podia convertir centavos en pesos.
- La precarga reemplazaba valores manuales y seleccionaba empresa sugerida sin
  preservar la seleccion del usuario.

## Pipeline actual

1. Se mantiene autorizacion, PDF privado, limites existentes y hash SHA256.
2. `leer_texto_pdf` lee hasta 25 paginas / 120000 caracteres, reinicia el stream.
   Con texto suficiente, el proveedor recibe texto; sin el, conserva entrada PDF.
3. `extraccion_campos.CAMPOS` establece el esquema por campo: valor normalizado,
   fuente, encontrado y estado `EXTRAIDO_NO_VERIFICADO`/`NO_ENCONTRADO`.
4. IA valida tiene prioridad. Regex del texto completa solo ausentes/invalidos.
   El cero es un dato. Valores no encontrados permanecen vacios; nunca se calcula
   un saldo ausente sin operandos. P0-4E.1 autoriza derivar total menos pagado cuando
   ambos son conocidos, no negativos y pagado <= total. Se marca como derivado,
   no como extraccion literal.
5. Metadata privada conserva procedencia. Documento detectado se enmascara.
   Logs propios solo contienen motor, estado y clase de error, no texto documental.
6. Se conserva invalidacion por archivo/documento y vigencia del analisis. Reanalizar
   nunca cambia campos manuales persistidos ni valores ya diligenciados en pantalla.

El OCR local existente procesa imagenes privadas de cedula, con hashes y ciclo de
vida propio; no es un rasterizador de contratos PDF multipagina. No se duplica.
Se reutilizan resultados OCR vigentes COMPLETADO/CC, capturas activas y no purgados
de la sesion autorizada, para comparar nombres/documento. OCR no verifica identidad.
Un PDF escaneado sin IA utilizable sigue requiriendo captura/revision manual.
PDF cifrado se detecta, pero su desbloqueo pertenece a P0-4F.

## Reconciliacion y UX

`reconciliacion_contractual` compara formulario, contrato, convenio y OCR usable:
COINCIDE, FALTANTE, DIFIERE o NO_COMPARABLE. No modifica ninguna fuente.

Documento contractual diferente mantiene el bloqueo previo. Otras diferencias
y NIT no comparable requieren confirmacion explicita en el formulario: ambos
valores visibles, checkbox y token firmado por servidor ligado al actor, empresa,
hash PDF y valores comparados, con vigencia de una hora. Cambiar evidencia o datos
exige una nueva confirmacion. Reanalizar el mismo contenido no la invalida por
el solo timestamp. La metadata registra actor/fecha/diferencias para revision;
confirmar NO aprueba identidad, riesgo ni credito.

Solo se precargan vacios. Nombre completo no se divide por heuristicas. Empresa
solo se precarga si no fue seleccionada y el matching es exacto; aproximaciones
requieren seleccion manual. La pantalla presenta datos encontrados, faltantes
y diferencias con texto escapado. Se conserva el flujo P0-1/P0-2.

Si el primer POST requiere confirmar discrepancias, no se guarda la solicitud:
se conservan campos/captura, pero el navegador exige reseleccionar los PDF del
intento no persistido. La persistencia temporal privada de PDFs sigue pendiente.

## Limites de este bloque

Sin migraciones, consultas a centrales, score, predecision, aprobacion, ZapSign,
activacion financiera ni configuracion PROD. Los tests mockean el proveedor.
Calidad de extraccion con contratos reales, PostgreSQL y UAT movil requieren
validacion de despliegue; no equivalen a E2E productivo cerrado.

## Validacion local (2026-09-29)

- Django: 184 pruebas, 176 OK, 8 omitidas por depender de PostgreSQL. Modulos:
  `test_calidad_datos`, `test_extraccion_reconciliacion`,
  `test_portal_minimo_prestadores`, `test_validacion_contractual_v2` y
  `gestion_creditos.tests.test_captura_documental`.
- JS/Playwright con Edge: 90 OK (80 handoff, 5 importes, 5 reconciliacion).
  Formulario Django real renderizado en desktop/movil; sin API IA ni Redis real.
- `manage.py check`: sin problemas. `makemigrations --check --dry-run`:
  sin cambios. `git diff --check`: OK.
- Avisos locales no bloqueantes: manifiestos GLib de Windows, staticfiles no
  recolectados y EOF de PDFs sinteticos usados en regresiones anteriores.

## Archivos del bloque

```text
contractors/validators.py
contractors/forms.py
contractors/views.py
contractors/services/analisis_contrato.py
contractors/services/analisis_contrato_ia.py
contractors/services/analisis_contractual_seguro.py
contractors/services/extraccion_campos.py
contractors/services/reconciliacion_contractual.py
templates/contractors/solicitud_prestador.html
static/js/contract_reconciliation.js
contractors/tests/test_calidad_datos.py
contractors/tests/test_extraccion_reconciliacion.py
contractors/tests/test_portal_minimo_prestadores.py
contractors/tests/browser/test_contract_reconciliation.cjs
docs/PRESTADORES_P0_4D_4E.md
docs/ROADMAP_APROBADO_2026.md
```

## Correccion P0-4E.1 (UAT)

Reproduccion real, sin proveedor: `ContratoPruebaCarlos3.pdf` tiene 4020 caracteres
extraibles. Antes del hotfix el fallback reconocia solo documento, NIT y forma de
pago (confianza 0.22). No era ausencia de texto ni se demostro fallo de OpenAI.

Las etiquetas estrictas no cubrian la presentacion narrativa: identificada con
NIT, identificado con cedula, servicios profesionales como, valor en letras
seguido de pesos entre parentesis, pagos mensuales de, Doce (12) meses y fechas
con meses en letras. Se incorporan patrones acotados para esas estructuras.
El fixture genera un PDF textual con las mismas clausulas relevantes, no incluye
el PDF original ni documentos privados como assets.

La UI usaba FALTANTE de la comparacion con un formulario todavia vacio; renderizaba
antes de aplicar sugerencias. No se encontro un alias roto en el mapeo anterior,
pero si mapas duplicados y claves diferentes entre capas. `ESQUEMA` centraliza:

| Clave interna/historica | Canonica (formulario/reconciliacion/UI) |
|---|---|
| cargo_o_servicio | cargo |
| valor_pagado_estimado | valor_pagado_contrato |
| valor_pendiente_estimado | valor_pendiente_cobrar |
| valor_mensual_o_honorarios | valor_mensual_contractual |
| duracion_meses_contrato | duracion_contrato_meses |
| empresa_contratante | empresa |
| nombre_contratista | titular |
| documento_contratista | numero_documento |

IA acepta aliases solo en el adaptador de entrada. La dataclass y datos_sugeridos
historicos siguen compatibles; campos_extraidos v3, precarga, etiquetas y
reconciliacion usan el esquema compartido. No hay mapa de aliases propio en JS.
Detectado no implica comparacion COINCIDE: si falta el valor humano, la comparacion
sigue FALTANTE, pero no se etiqueta como dato documental ausente. Empresa pendiente
de seleccion se muestra aparte, nunca como NIT no detectado.

Precedencia: IA valida > fallback para ausentes > derivacion de saldo si aun falta.
Dato manual no vacio siempre se conserva; las diferencias exigen confirmacion
backend vigente. El saldo guarda DERIVADO_DETERMINISTICAMENTE y sustentado_por
[valor_total_contrato, valor_pagado_contrato]. No se deduce pagado=0 por silencio:
se exige la frase explicita sobre ningun pago efectuado.

Resultado del PDF real y fixture: PRUEBAS DATAIN / NIT 999888777; titular indicado,
Project Manager; 2026-08-01 a 2027-07-31; 12 meses; total 124800000, pagado 0,
mensual 10400000. Solo pendiente 124800000 es derivado. No se divide el nombre
completo por heuristicas. Se conserva la relacion pagado + pendiente <= total.

Un POST invalido conserva analisis y evidencia de captura; ahora vuelve a mostrar
reconciliacion cuando el analisis sigue valido incluso si falla la relacion de
importes. Los PDF no persistidos aun deben reseleccionarse: su borrador privado
temporal sigue fuera de alcance. ID-01F se registra, sin cambiar la camara.

Observacion del archivo UAT: supervisión/firmas tambien mencionan otra razon social
(APROBADO PRUEBAS S.A.S.). El encabezado identifica PRUEBAS DATAIN; una discrepancia
real entre fuentes debe revisarse, no ocultarse ni resolverse sobrescribiendo datos.

Archivos de P0-4E.1 (sin modelos, migraciones ni camara):

Validacion P0-4E.1: 146 pruebas focales Django OK; ampliadas con captura documental,
192 pruebas (184 OK, 8 omitidas por SQLite). JS/Playwright: 13 OK, incluyendo
precarga UAT desktop/mobile y preservacion de correcciones. Check sin problemas,
makemigrations sin cambios y diff --check OK. No llamadas reales al proveedor.

```text
contractors/services/analisis_contrato.py
contractors/services/analisis_contrato_ia.py
contractors/services/analisis_contractual_seguro.py
contractors/services/extraccion_campos.py
contractors/services/reconciliacion_contractual.py
contractors/views.py
templates/contractors/solicitud_prestador.html
static/js/contract_reconciliation.js
contractors/tests/contrato_uat_fixture.py
contractors/tests/test_extraccion_uat.py
contractors/tests/test_extraccion_reconciliacion.py
contractors/tests/test_portal_minimo_prestadores.py
contractors/tests/browser/test_contract_reconciliation.cjs
docs/PRESTADORES_P0_4D_4E.md
docs/ROADMAP_APROBADO_2026.md
```
