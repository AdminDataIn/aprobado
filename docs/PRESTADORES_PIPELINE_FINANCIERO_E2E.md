# Prestadores: diagnostico E2E financiero local

Fecha: 2026-10-09. Rama: `release/prestadores-prod-2026-08-28`.
HEAD: `4885d8e03a8f2da029bebfc0dbfb7233f58ad4af`, mas cambios shadow locales preexistentes.

## Alcance y aislamiento

- Suite nueva: `contractors.tests.test_pipeline_financiero_e2e`, 23 pruebas tras la correccion P0.
- SQLite en memoria, base creada/destruida por DiscoverRunner; archivos privados en TemporaryDirectory.
- Se omite la carga de dotenv en el proceso de validacion. No se utiliza una base existente.
- Los flags/credenciales sinteticos y la activacion de la definicion versionada solo existen en tests.
- PDF generado localmente y capturas PNG sinteticas; ninguna identidad corresponde al caso UAT real.
- Unicamente se sustituyen las respuestas de los clientes externos en el adapter; formulario,
  extraccion contractual local, consentimiento, captura, simulacion, normalizadores, snapshots,
  capacidad, score, predecision y gates ejecutan codigo real.
- Red bloqueada mediante requests, httpx y socket; shadow protegido contra invocacion.
- Se termina en el expediente de originacion. No se ejecuta la originacion favorable, firma ni desembolso.
- Signup/login tienen regresion separada; el fixture E2E crea una cuenta y usa force_login.

## Recorrido real comprobado

| Paso | Ruta o funcion | Resultado |
| --- | --- | --- |
| Autenticacion | `/accounts/signup/`, `/accounts/login/`; `test_auth_prestadores` | Regresion aprobada, sin OAuth externo |
| Contrato | POST `/contrato/analizar/`; `analizar_contrato_seguro` | Extraccion local del PDF sintetico, sin IA |
| Identidad | `sesion_finalizada`; servicios reales de captura documental | Ambas caras, FINALIZADA y posterior consumo UTILIZADA |
| Solicitud/consentimiento | POST `/solicitar/`; `registrar_autorizacion_datacredito_desde_solicitud` | Cuatro documentos privados y autorizacion vigente |
| Simulacion | POST `/simular/`; `simular_credito_prestador_informativo` | Configuracion versionada, limites y horizonte; EVALUACION_PENDIENTE |
| Evaluacion | `evaluar_solicitud_prestador` -> `obtener_evaluacion_centrales_prestador` | Un intento por proveedor, auditoria y snapshots vinculados |
| Normalizacion | adapter -> `normalizar_midecisor_pn` / `normalizar_historial_credito` | Clasificacion oficial; shadow no participa |
| Score/decision | `_evaluar_predecision_dual` -> `evaluar_score_prestador` | Pesos y bandas persistidos, sin reglas nuevas |
| Aprobacion | `crear_o_reutilizar_aprobacion_interna` -> `aprobar_para_originar` | Revalidacion y gate interno, sin credito |
| Empresa | `decidir_aprobacion_pagador_prestador` | Confirmaciones y permiso del pagador, separado del staff |
| Preoriginacion | `construir_expediente_originacion_prestador` | Solo despues de ambas aprobaciones |
| Vista usuario | GET `/mi-credito/` | EVALUACION_APROBADA; diccionario publico sin score ni proveedor |

La simulacion delega los calculos en `gestion_creditos.services.condiciones_financieras`.
El originador existente delega la creacion financiera en
`gestion_creditos.services.originacion_libranza.originar_libranza_desde_expediente`;
en el diagnostico inicial esa llamada no se ejecutaba. La regresion P0 la invoca solo
para comprobar que un expediente vencido es rechazado, sin crear origen ni credito.

## Matriz inicial, antes de la correccion P0

Se usa la definicion aprobada `prestadores-score-prod-v1` y financiera `prestadores-prod-v1`,
sin alterar tasas, pesos, bandas ni limites. Scores internos son resultados observados al corte,
no umbrales inventados ni valores forzados.

| Escenario | Estado esperado segun reglas | Estado obtenido | Resultado | Bloqueo |
| --- | --- | --- | --- | --- |
| A: HDC completo, HC13/TX02, score externo 900, capacidad suficiente | PREAPROBADO_READ_ONLY | PREAPROBADO_READ_ONLY, score interno 826.39 | OK hasta expediente | Requiere staff y pagador; no originado |
| B: HDC SIN_INFORMACION y MiDecisor exitoso | REQUIERE_REVISION_MANUAL | REQUIERE_REVISION_MANUAL, sin score | OK | Fuente HDC requerida |
| C: HC14/TX05, informacion y score 667 presentes | NO_EVALUABLE | NO_EVALUABLE; ERROR_PERMANENTE, HTTP 200, HC14_TX05, normalizado vacio | OK, falla cerrado | Normalizacion oficial pendiente de Experian |
| D: timeout MiDecisor | REQUIERE_REVISION_MANUAL | REQUIERE_REVISION_MANUAL; ERROR_TRANSITORIO | OK | Dependencia externa |
| D: TX20 | NO_EVALUABLE | NO_EVALUABLE; normalizado vacio | OK | Error funcional |
| D: envelope incompleto | NO_EVALUABLE | NO_EVALUABLE; RESPUESTA_INVALIDA | OK | Respuesta no interpretable |
| E: score externo bajo (0) | REVISION, no rechazo automatico | REQUIERE_REVISION_MANUAL, score interno 400.08 | OK | Banda REVISION |
| E: score externo medio (667) | Decision ponderada del motor | PREAPROBADO_READ_ONLY, score interno 716.02 | OK | Gates posteriores |
| E: obligaciones mensuales HDC 9 millones | REQUIERE_REVISION_MANUAL | REQUIERE_REVISION_MANUAL, relacion > 0.30 | OK | Capacidad insuficiente |
| E: mora HDC de 90 dias | Informativa segun definicion actual | PREAPROBADO_READ_ONLY, sin rechazo automatico | Caracterizacion | No existe hard rule activa de mora |
| E: monto < minimo, > maximo o plazo 9 | Formulario invalido | No guarda nueva simulacion ni consulta centrales | OK | Limites de producto |
| Consentimiento retirado / documento faltante | No avanzar | NO_EVALUABLE / formulario bloqueado | OK | Autorizacion / flujo documental |
| Staff sin permiso / pagador con permisos amplios | Denegar evaluacion | PermissionDenied, cero consultas | OK | Segregacion de roles |
| Misma evaluacion exitosa repetida | Reutilizar | Una auditoria y dos snapshots | OK | Ninguno |
| Nueva solicitud, misma identidad y nueva autorizacion | Cache reutilizable, pero gate exige referencia exacta | PREAPROBADO_READ_ONLY; gate rechaza snapshots | DEFECTO | Integracion cache/consentimiento |
| Repeticion de evaluacion con snapshot fresco en error | Sin nuevo consumo | Otra auditoria fuentes_evaluacion_vencidas, sin HTTP adicional | DEFECTO de estado | Error cerrado confundido con fuente vencida |
| Ingreso neto verificado 100 mil, contractual 10 millones | Oferta existente debe aplicar ingreso neto | Pipeline preaprueba y crea gate; preparar_oferta devuelve SIN_OFERTA, cuota maxima 0 | DEFECTO | Politica de oferta no conectada |
| Cierre operativo manual del staff | CERRADA_SIN_ORIGINAR | CERRADA_SIN_ORIGINAR; originacion denegada | OK | Decision humana, no rechazo automatico por score |

## Hallazgos iniciales y pendientes

1. **P0, integracion financiera:** `preparar_oferta` (`politica_financiera_prestador.py:80`)
   no tiene callers productivos. `predecision.py:232` y `score/componentes.py:259` calculan
   capacidad con ingreso contractual; `aprobacion_interna.py:275` no exige una oferta vigente
   basada en ingreso neto verificado. El mismo caso obtiene gate favorable pero SIN_OFERTA
   en la politica ya implementada. Propuesta: conectar la politica existente despues del score
   y exigir su evidencia/version en el gate, sin crear otro motor ni alterar formulas.
   El calendario si se valida en simulacion, pero no se revalida mediante esa oferta en el gate.
2. **P1, integracion/persistencia:** `datacredito_evaluacion.py:234` comparte fingerprint por
   identidad/servicio/consentimiento versionado, no por solicitud ni referencia de autorizacion.
   `aprobacion_interna.py:520` exige que autorizacion_referencia coincida exactamente.
   Propuesta: alinear elegibilidad de cache con esa vinculacion antes de evaluar, o definir un
   vinculo auditado de reutilizacion. No mutar snapshots ni eliminar el control de ownership.
   Aislar cache por autorizacion es la alternativa conservadora, con costo adicional de consultas.
3. **P1, persistencia/estado:** `evaluacion_formal.py:155`, calculo de `vencida`, cuenta solo
   snapshots EXITOSO/SIN_INFORMACION; un error recien cerrado se interpreta como vencido.
   Propuesta: distinguir fuente exitosa expirada de error cerrado; reutilizar el diagnostico
   hasta un reintento manual autorizado, sin consumo automatico ni cambios historicos.
4. **Dependencia externa/normalizacion:** HC14/TX05 sigue cerrado correctamente. Confirmar
   semantica contractual con Experian antes de habilitar su uso oficial. No conectar shadow.

Mora/consultas recientes son informativas expresamente en la definicion versionada. No se
reporta esto como bug ni se inventa un rechazo. Cualquier cambio exige decision de negocio.

## Validacion inicial y limites

- Suite E2E final: **18 aprobadas, 0 fallidas, 0 errores, 0 omitidas**.
- Suite ampliada de 14 modulos: **239 ejecutadas, 238 aprobadas, 0 failures, 1 error, 0 omitidas**.
- Unico error ampliado: `RegresionActivacionLibranzaTradicionalTest.test_activar_sin_componentes_conserva_formula_tradicional`.
  El fixture crea una Libranza nueva sin snapshot de originacion; `credit_services.py:924` lo
  rechaza. Test y servicio identicos a HEAD; no corregidos ni proteccion relajada en este bloque.
- La suite ampliada cubre HDC, MiDecisor/shadow, snapshots, score, evaluacion, aprobacion interna,
  autenticacion, politica PROD, horizonte y reglas Libranza. No equivale a toda la suite del repo.
- `manage.py check`: sin incidencias. `makemigrations --check --dry-run`: No changes detected.
- `git diff --check`: sin errores de whitespace; comprobado tambien el archivo nuevo sin stage.
- Sin consultas reales, nuevos modelos/migraciones, cambios financieros, commits, push ni deploy.
- PostgreSQL/concurrencia no ejecutados en este bloque. No se valida hardware movil ni proveedores reales.
- Los tests de caracterizacion de defectos deben cambiar a la regla corregida cuando se autorice su solucion.

**Conclusion:** el recorrido sintetico favorable llega al expediente y vista final previa a originacion.
El E2E productivo NO esta cerrado: falta integrar la oferta con ingreso neto, resolver el contrato
cache/gate y aclarar HC14. Firma, originacion y desembolso no se ejecutaron en esta validacion.

## Correccion local P0: oferta financiera obligatoria

El riesgo y la viabilidad financiera se mantienen separados. PREAPROBADO_READ_ONLY acredita
una evaluacion de riesgo favorable, no una oferta ni una aprobacion efectiva. No se cambian
el score, sus bandas, las formulas financieras ni el tratamiento oficial de HC14.

La primera transicion que exige oferta es `_validar_gate` en `aprobacion_interna.py`.
`oferta_financiera.calcular_oferta_evaluada` proyecta los campos del score ya persistido y llama
exclusivamente a `politica_financiera_prestador.preparar_oferta`. Usa ingreso neto verificado
vigente de la misma solicitud, carga mensual HDC evaluada y calendario contractual al corte.
No consulta proveedores, no recalcula score ni infiere ingresos del contrato.

- SIN_OFERTA: no hay combinacion viable conforme a la politica existente; no implica rechazo definitivo.
- NO_EVALUABLE: faltan evidencias/configuracion validas; requiere revision manual.
- ERROR_CONTROLADO: fallo tecnico; no expone la excepcion ni habilita avance.
- OFERTA_CALCULADA: permite crear el gate humano, limitado al monto/plazo de esa oferta.

La reutilizacion de gates activos, inicio de analisis, aprobacion interna, creacion/confirmacion
del pagador y construccion del expediente revalidan la oferta. Monto y plazo autorizados se
validan como pareja: un plazo menor no garantiza capacidad. La solicitud se bloquea en la
transaccion para serializar cambios de evidencia; no hay locks globales ni nuevas tablas.

El originador del core revalida el expediente actual antes de crear efectos financieros,
incluidos callers directos con un DTO antiguo. Una originacion ya completada conserva su
reutilizacion idempotente y snapshot historico. Los calculos y la creacion siguen en el core.

| Caso | Antes | Despues |
| --- | --- | --- |
| Ingreso neto 100.000, riesgo alto | PREAPROBADO y gate pese a SIN_OFERTA | Riesgo favorable conservado; SIN_OFERTA impide gate, pagador y originacion |
| Ingreso verificado suficiente | Gate humano | Oferta real y gates humanos, sin aprobacion automatica |
| Riesgo bajo, ingreso suficiente | Revision manual | Misma decision de riesgo; no se habilita gate |
| Sin ingreso/fuentes evaluables | Sin control obligatorio de oferta | NO_EVALUABLE, sin gate |
| Oferta antes valida, evidencia vencida/modificada | Control parcial de version | Revalidacion obligatoria; no avance financiero |
| Plazo reducido que supera capacidad | Solo maximos independientes | Pareja monto/plazo debe producir oferta exacta |
| DTO de originacion desactualizado | Core confiaba en DTO preparado | Core revalida antes de crear credito; rollback integral |

### P1 registrados al cerrar P0

1. Cache compartida por identidad frente a referencia exacta de consentimiento exigida por gate.
2. Error fresco de proveedor interpretado como fuente vencida al repetir evaluacion formal.

En ese cierre ambos conservaban pruebas de caracterizacion para tickets separados. HC14 sigue pendiente
de aclaracion de Experian; shadow no se conecta. No se activa ninguna politica/flag productivo.

### Validacion final de la correccion P0

- Focal: 101/101 aprobadas (23 E2E, 55 gates y 23 politica/horizonte), sin omisiones.
- Repeticion ampliada final: 348 casos, 341 aprobados, 0 failures, 1 error preexistente y 6 omitidos.
- Las 6 omisiones requieren PostgreSQL: ingreso neto (2), activacion de politica (1),
  snapshot de originacion Libranza (1), carrera anulacion/desembolso (2). No ejecutadas aqui.
- HDC, clasificacion oficial MiDecisor, shadow, score, capacidad, consentimiento, permisos,
  simulacion, revision y Libranza incluidos en los 21 modulos de regresion.
- `check`: sin incidencias. `makemigrations --check --dry-run`: No changes detected.
- `git diff --check`: sin errores; los archivos nuevos se revisan tambien con --no-index.
- Base aislada SQLite en memoria, dotenv omitido y red bloqueada. Sin consumo real,
  cambios de flags/politicas productivas, modelos, migraciones, commit, push ni despliegue.

El error de Libranza se reprodujo por separado ejecutando EN MEMORIA el test de `4885d8e`.
Se comprobo que el test, `gestion_creditos/credit_services.py` y los modelos son identicos a
esa base. Falla en `activar_credito`, linea 924: la Libranza nueva carece del snapshot de
originacion obligatorio. No lo introduce este P0 y no se modifica ese fixture ni la proteccion.
Correccion separada necesaria: si el caso prueba una Libranza nueva, construir su snapshot con
el servicio real y comprobar sus condiciones; si prueba fallback historico, identificarla
explicitamente como anterior a la frontera temporal, igual que la regresion legacy existente.
Nunca omitir el snapshot obligatorio para las nuevas originaciones.

Riesgos pendientes: validar estos controles transaccionales con PostgreSQL; medir costo de
revalidacion de oferta bajo carga. Auditorias legacy sin score/carga HDC/ingreso verificable o
calendario suficiente no habilitan oferta y requieren revision/reevaluacion, sin backfill.
El P0 queda implementado localmente; Prestadores E2E productivo no se declara cerrado.

## P1-A: consentimiento y reutilizacion de snapshots

### Regla encontrada y alcance

`construir_fingerprint_datacredito` permite resultados compartidos por identidad, version/hash
del consentimiento y contexto tecnico, no por solicitud ni PK de autorizacion. El TTL procede
de la politica de cada fuente o DATACREDITO_REUSE_DAYS (30 por defecto). El consentimiento
canonico autoriza el uso para evaluacion de identidad, comportamiento, endeudamiento y riesgo;
no convierte dos evidencias individuales en una sola ni autoriza compartir entre titulares.
La documentacion tecnica P0-5B1 describe el fingerprint y vigencia, pero no existia evidencia
separada de que la solicitud nueva respaldara el uso del snapshot. El gate exigia la referencia
de origen exacta, contradiciendo la cache.

La reutilizacion queda acotada al mismo usuario titular, misma identidad consultada, finalidad
de evaluacion Prestadores, version/cuerpo legal canonicos, ambiente/cuenta/producto/parametros
y fingerprint vigente. Se exige consentimiento propio vigente en la solicitud nueva y evidencia
de origen autorizada, del mismo titular, cuyo consentimiento no este retirado en su solicitud.
No se amplian clausulas juridicas, vigencia ni finalidades; no se intercambian autorizaciones
entre usuarios aunque declaren el mismo documento. La regla tecnica no sustituye una revision
juridica ni confirma licencias de reutilizacion de Experian para otros productos/finalidades.

### Implementacion

- `datacredito_evaluacion.evidencia_uso_snapshot` centraliza la elegibilidad y la evidencia.
  Tanto el hit inicial como el hit tras tomar la reserva pasan por esa validacion.
- Una cache incompatible devuelve NO_EVALUABLE / reutilizacion_no_autorizada, sin resultados
  ni consulta automatica adicional. Una nueva consulta exige la via FORZAR_CONSULTA existente,
  con staff/permiso/justificacion y consentimiento vigente del destino.
- La nueva auditoria inmutable guarda `snapshot_salida.usos_snapshots`, por servicio:
  solicitud, autorizacion vigente, autorizacion de origen, snapshot, fingerprint, finalidad,
  version de regla y version/hash legal. Actor y timestamp son los de la auditoria; el resumen
  de centrales conserva si fue cache. No se agrega una tabla ni se modifica la procedencia.
- La huella de entrada y las claves de idempotencia existentes no se cambian: ya incluyen
  aceptacion/version/hash legal. El ID de autorizacion propio queda en la evidencia nueva;
  el gate exige que siga siendo vigente. No se invalidan gates anteriores por cambiar el
  formato de la huella ni se modifican fingerprints, autorizaciones o auditorias anteriores.
- El gate compara la evidencia auditada contra la misma regla y estado actual. Auditorias
  anteriores sin `usos_snapshots` mantienen referencia exacta: no habilitan uso cruzado sin
  reevaluacion trazada; no se hace backfill. No se rellenan evidencias a partir de una aprobacion.
- El control P0 de oferta se conserva despues de validar fuentes. PREAPROBADO de riesgo con
  SIN_OFERTA sigue sin gate ni avance financiero.

### Matriz antes/despues

| Caso | Antes | Despues |
| --- | --- | --- |
| Misma solicitud y consentimiento vigente | Cache y gate admitidos | Admitidos, evidencia propia en nueva evaluacion |
| Otra solicitud, mismo titular y contexto, consentimiento vigente | Cache admitida, gate rechazado por PK original | Cache y gate coherentes, evidencia nueva y origen intacto |
| Sin consentimiento / retirado / version no vigente | Bloqueo por autorizacion | Bloqueo conservado, sin HTTP |
| Otro usuario con igual documento / origen retirado | Cache tecnica podia devolver resultados | Bloqueo de reutilizacion, sin resultados ni reconsulta automatica |
| Snapshot vencido / fingerprint o parametros distintos | No reutilizable | No reutilizable; politica temporal sin cambios |
| Reutilizacion valida, ingreso neto insuficiente | P0 impedia gate | P0 sigue impidiendo gate por SIN_OFERTA |
| Auditoria antigua sin evidencia cruzada | Gate rechazado | Requiere reevaluacion, historico inmutable |
| Dos evaluaciones simultaneas de solicitud nueva | Sin prueba especifica | Una auditoria con dos vinculos, cero HTTP adicional |

### Validacion P1-A

- PostgreSQL 16 (`postgres:16-alpine`), contenedor temporal exclusivo en 127.0.0.1,
  base del runner `test_aprobado_p1a_desechable`, distinta de `aprobado_p1a_base`.
  7/7 concurrencias aprobadas, 0 errores, 0 omisiones: nueva doble evaluacion con cache (1),
  reservas/fingerprint/leases (4) y leases de evaluacion (2). Sin acceso a bases operativas.
- Proveedores sustituidos por fixtures sinteticos, HTTP externo bloqueado y dotenv omitido.
  La prueba nueva comprueba snapshots identicos antes/despues, una sola auditoria nueva,
  dos vinculos de consentimiento y ausencia de originacion/correos. Contenedor eliminado.
- La primera ejecucion detecto solo una comparacion UUID vs str en el test nuevo; se corrigio
  la asercion y se repitieron las siete pruebas sobre otro contenedor limpio.
- Suite ampliada SQLite: 448 casos, 433 aprobados, 0 failures, 1 error preexistente de
  activacion Libranza sin snapshot y 14 omisiones PostgreSQL. Incluye los 27 E2E, todos los
  modulos de integrations (HDC, MiDecisor, shadow, cache, OAuth, readiness), consentimiento,
  gates, score/capacidad, simulacion y las regresiones anteriores de Libranza.
  Siete de esas omisiones se ejecutaron y aprobaron en PostgreSQL aislado. Las otras siete
  son ingreso neto (2), activacion de politica (1), snapshot de originacion (1),
  anulacion/desembolso (2) y aceptacion concurrente de consentimiento (1), no ejecutadas
  en PostgreSQL en este ticket. No se presenta SQLite como validacion de locks.
- `manage.py check`: 0 incidencias. `makemigrations --check --dry-run`: No changes detected.
  Sin modificaciones de modelos/migraciones/settings ni datos operativos.

Al cierre de P1-A, las auditorias repetidas con fuentes en error reciente seguian pendientes.
HC14 y shadow no se conectan al scoring. El error independiente del fixture de Libranza
conserva su requisito de snapshot de originacion. Sin commit, push ni despliegue.

## P1-B: idempotencia de evaluaciones con fuentes en error

### Causa y correccion

La reserva formal contaba solamente EXITOSO/SIN_INFORMACION con vigencia futura. Un error
terminado tiene vigente_hasta igual a su fecha de cierre, por lo que una lectura repetida
lo interpretaba como fuente vencida y generaba otra auditoria fuentes_evaluacion_vencidas.
El servicio de snapshots, en cambio, conserva ese error diagnostico hasta un reintento manual:
no lo considera informacion crediticia utilizable ni vuelve a consultar automaticamente.

- La reserva formal distingue errores terminales de fuentes crediticias realmente vencidas.
  La repeticion con contexto identico devuelve la misma auditoria, sin nuevos snapshots ni HTTP.
  No se extiende TTL ni se habilita el error para score. No se introduce una caducidad nueva
  para errores: su recuperacion sigue requiriendo las vias manuales autorizadas existentes.
- Cada nueva auditoria registra contexto_consulta: PK de consentimiento aplicable y fingerprints
  actuales por servicio. Se usan el constructor y las reglas existentes, sin modificar huellas.
  Antes de reutilizar se compara ese contexto; al cerrar se revalida para detectar cambios
  durante la ejecucion. No se cambia la version de datos ni el formato de las claves existentes.
- Para auditorias anteriores sin contexto_consulta se contrastan referencias/fingerprints de
  los snapshots y usos_snapshots existentes, sin reescribir ni completar el historico.
- Datos/evidencia/politica/configuracion financiera siguen versionados por los mecanismos
  existentes. Un cambio relevante no devuelve la auditoria de otro contexto. Una fuente
  EXITOSO/SIN_INFORMACION vencida mantiene reconfirmacion manual y bloqueo, incluso junto a
  otro snapshot en error. El lease activo sigue serializando la evaluacion por solicitud.
- Nuevo intento operativo: reintentar_evaluacion conserva una auditoria nueva y puede reutilizar
  fuentes sin HTTP. Reconsulta tecnica: FORZAR_CONSULTA por servicio exige permiso, justificacion
  y consentimiento; conserva el snapshot anterior. Una posterior evaluacion explicita vincula
  la fuente nueva. Una simple lectura no se convierte en reconsulta ni en nuevo intento.
  El modo FORZAR_CONSULTA de evaluacion formal conserva tambien el reintento tras un error,
  sin reinterpretarlo como lectura por haber dejado de clasificar el error como fuente vencida.
- usos_snapshots y sus validaciones P1-A no cambian. P0 sigue exigiendo oferta viable antes
  de gates/aprobacion/pagador/originacion. HC14 permanece indeterminado y shadow queda aislado.

### Matriz antes/despues

| Caso | Antes | Despues |
| --- | --- | --- |
| Exito repetido con fuentes vigentes | Misma auditoria | Misma auditoria y evidencia |
| Timeout reciente repetido | Auditoria artificial de fuentes vencidas | Misma REQUIERE_REVISION_MANUAL; ERROR_TRANSITORIO intacto |
| Error funcional permanente repetido | Auditoria artificial de fuentes vencidas | Mismo NO_EVALUABLE; HTTP/HC/TX y ERROR_PERMANENTE intactos |
| HC14/TX05 con score presente | NO_EVALUABLE y duplicado al repetir | NO_EVALUABLE sin duplicado ni score shadow |
| HDC SIN_INFORMACION vigente | Revision segun politica | Misma revision; sin nueva consulta |
| Consentimiento/fingerprint/contexto relevante distinto | Huella tecnica no siempre revisada | No reutiliza auditoria del contexto anterior |
| Fuente exitosa realmente vencida junto a error | Reconfirmacion | Reconfirmacion conservada, sin HTTP automatico |
| Reintento operativo autorizado | Nuevo intento | Nuevo intento auditado, sin pago externo automatico |
| Reconsulta tecnica autorizada | Nuevo snapshot | Nuevo snapshot historico y evaluacion explicita posterior |
| Dos evaluaciones iniciales concurrentes con timeout | Sin regresion especifica | Una auditoria y una llamada sintetica por fuente |
| Dos lecturas concurrentes de error | Riesgo de auditoria artificial | Misma auditoria; cero HTTP adicional |

### Validacion P1-B

- Suite ampliada SQLite: 469 casos, 452 aprobados, 0 failures, 1 error preexistente de fixture
  de Libranza y 16 omisiones PostgreSQL. Los 27 E2E y las 19 regresiones no concurrentes nuevas
  pasan. Incluye HDC, MiDecisor, shadow, score/capacidad, consentimiento, cache y gates.
  Las regresiones nuevas incluyen auditoria legacy sin reescritura, cambio de fingerprint
  durante el cierre y reintento formal FORZAR_CONSULTA tras error.
- PostgreSQL 16, imagen oficial postgres:16-alpine, contenedor exclusivo aprobado-p1b-pgtest
  publicado solamente en 127.0.0.1 (puerto efimero; ultima ejecucion 57506).
  Base de conexion aprobado_p1b_base y base desechable
  del runner test_aprobado_p1b_desechable; credenciales aleatorias temporales y datos en tmpfs.
  9/9 aprobadas, 0 fallos y 0 omisiones: P1-B (2), P1-A (1), reservas (4), leases formales (2).
  Repetidas sobre otro contenedor limpio con la version final del servicio: nuevamente 9/9.
  La base del runner y el contenedor fueron eliminados; no se reutilizaron recursos operativos.
- Nueve omisiones SQLite quedan cubiertas por esa ejecucion real. Las otras siete (ingreso
  neto, activacion de politica, originacion, anulacion/desembolso y consentimiento concurrente)
  no se ejecutaron en PostgreSQL en este ticket y no se presentan como validadas aqui.
- El error de Libranza sigue en test_activar_sin_componentes_conserva_formula_tradicional:
  su credito nuevo carece del snapshot obligatorio. Fixture y proteccion financiera intactos.
- check: 0 incidencias. makemigrations --check --dry-run: No changes detected.
  Proveedores sinteticos y bloqueo de red, dotenv omitido, almacenamiento temporal.
  Sin HTTP real, cambios productivos, modelos/migraciones/settings, commit, push ni deploy.

El cierre es tecnico local, no una declaracion de Prestadores E2E productivo completo.

## Precierre integrado del release (2026-10-10)

### Resultado definitivo local

Base y rama: `4885d8e03a8f2da029bebfc0dbfb7233f58ad4af`,
`release/prestadores-prod-2026-08-28`. No se crearon commits ni se publicaron cambios.

| Validacion | Casos | Aprobados | Fallos / errores | Omitidos |
| --- | ---: | ---: | ---: | ---: |
| Fixture historico y SnapshotYActivacionLibranzaTests | 5 | 5 | 0 / 0 | 0 |
| Regresion integrada SQLite | 696 | 676 | 0 / 0 | 20 |
| Primera ejecucion PostgreSQL: nueve anteriores + siete pendientes | 16 | 16 | 0 / 0 | 0 |
| Segunda ejecucion PostgreSQL: todas las omisiones de la integrada | 20 | 20 | 0 / 0 | 0 |

Las 20 omisiones SQLite son exclusivamente concurrencias; todas se ejecutaron realmente
en PostgreSQL. Son 696 casos distintos cubiertos en el motor correspondiente, no una suma
de las ejecuciones repetidas. Los 27 E2E estan incluidos entre los 676 aprobados SQLite.

Suites integradas: `contractors.tests`, `integrations.tests`,
`gestion_creditos.tests.test_condiciones_financieras_prestador`,
`gestion_creditos.tests.test_libranza_rules`,
`gestion_creditos.tests.test_originacion_libranza` y
`gestion_creditos.tests.test_integridad_originacion_anulacion`.
Incluyen portal, extraccion, simulacion, score/capacidad, consentimiento, gates, expediente,
originacion, formalizacion/postfirma, HDC, MiDecisor, shadow y regresiones Libranza.

Se utilizo DiscoverRunner con configuracion exclusiva del proceso: dotenv omitido, SQLite
en memoria o PostgreSQL local desechable, caches en memoria, storage temporal, contrasenas
de test con MD5 y hosts sinteticos permitidos. Red socket y requests bloqueados; fixtures
sustituyen los proveedores. Los correos del runner son locmem, no SMTP.
Las activaciones y desembolsos sinteticos de los tests solo afectan la base desechable.

`manage.py check`: 0 incidencias. `makemigrations --check --dry-run`:
`No changes detected`, ejecutados antes de destruir las bases de test.
`git diff --check` correcto, incluyendo comprobacion separada de archivos sin seguimiento.
Avisos GLib/UWP de Windows, staticfiles ausente y PDFs deliberadamente invalidos de tests
no produjeron fallos. No se usaron bases operativas ni HTTP real.

### PostgreSQL y limpieza

Imagen oficial `postgres:16-alpine`; contenedor exclusivo `aprobado-release-pgtest`.
HOST `127.0.0.1`; puertos efimeros 51721 (16 casos) y 52470 (20 casos).
NAME `aprobado_release_base`, USER temporal `aprobado_release_test`,
TEST.NAME `test_aprobado_release_desechable`, distinta de NAME.
Password aleatoria solo en el proceso; datos en tmpfs, sin volumen productivo.
Se aplicaron las migraciones existentes solamente en la base de tests.
El runner destruyo TEST.NAME y cada contenedor de esta ejecucion fue eliminado.

| Modulo / clase PostgreSQL | Casos | Garantia comprobada |
| --- | ---: | --- |
| test_evaluacion_errores_idempotencia.EvaluacionErrorConcurrenciaPostgresTest | 2 | Una auditoria ante error inicial; lecturas sin duplicados |
| test_reutilizacion_snapshots.ReutilizacionConcurrenciaPostgresTest | 1 | Consentimiento propio, un vinculo auditado, cero HTTP extra |
| test_datacredito_reservas_hardening.ReservaConcurrenciaPostgresTest | 4 | Reserva unica, cache miss serializado y recuperacion de leases |
| test_evaluacion_leases.EvaluacionLeaseConcurrenciaPostgresTest | 2 | Un intento y un cierre ante recuperadores concurrentes |
| test_ingreso_neto.IngresoNetoConcurrenciaPostgresTest | 2 | Versiones de ingreso sin sobrescritura ni duplicacion |
| test_activacion_politica_score.ActivacionPoliticaScoreConcurrenciaTest | 1 | Permanece una sola politica activa; admite colision controlada |
| test_originacion_libranza.SnapshotOriginacionLibranzaConcurrenciaPostgresTests | 1 | Un snapshot consolidado |
| test_integridad_originacion_anulacion.AnulacionDesembolsoConcurrenciaTests | 2 | Gana anulacion o desembolso, nunca ambos |
| test_consentimiento_centrales.ConsentimientoCentralesConcurrenciaPostgresTest | 1 | Una evidencia ante doble aceptacion |
| test_cierre_financiero_prestador.CierreFinancieroPrestadorConcurrenciaPostgresTest | 1 | Postfirma/desembolso sintetico idempotentes |
| test_continuidad_solicitud.ContinuidadSolicitudConcurrenciaTest | 1 | Solo un borrador entra a evaluacion |
| test_preparacion_score_prod.PreparacionScoreProdConcurrenciaPostgresTest | 2 | Preparacion inactiva unica; parametros incompatibles fallan cerrado |

### Fixture Libranza resuelto

`test_activar_sin_componentes_conserva_formula_tradicional` comprueba la formula historica:
10% de comision + 19% de IVA sobre comision, capital financiado 1.119.000 sobre 1.000.000.
Su fecha automatica lo convertia accidentalmente en una originacion nueva posterior
a la frontera `FECHA_INICIO_V2=2026-09-01`, que exige snapshot.

Solo se fecha el fixture al dia anterior a esa frontera y se afirma expresamente que no
requiere snapshot. Conserva todas las aserciones de formula/cuota/amortizacion.
`test_libranza_nueva_sin_snapshot_no_activa` sigue pasando con rechazo.
No se modifica credit_services.py ni se agrega ninguna excepcion productiva.

### Evidencia de P0, P1-A, P1-B y shadow

| Control | Regresion que lo acredita |
| --- | --- |
| SIN_OFERTA sin gate, pagador ni originacion | test_ingreso_neto_insuficiente_bloquea_gate_aunque_score_alto |
| Oferta valida continua por gates humanos | test_a_fuentes_completas_hasta_expediente_sin_originar |
| Gate/oferta dejan de ser vigentes | test_gate_vigente_deja_de_ser_viable_al_cambiar_ingreso |
| Accesos directos pagador/expediente/core no eluden oferta | test_pagador_y_expediente_directos_no_omiten_oferta_vencida |
| Reutilizacion conserva autorizacion de origen y vincula la propia | test_cache_entre_solicitudes_traza_consentimiento_propio_y_conserva_historicos |
| Reutilizacion no evade P0 | test_reutilizacion_autorizada_no_omite_oferta_sin_capacidad |
| Consentimiento ausente, retirado, ajeno o no vigente bloquea | ReutilizacionSnapshotTest y test_gate_revalida_origen_y_consentimiento_destino_despues_de_reutilizar |
| Lecturas de error no crean auditorias artificiales | test_error_transitorio_repetido_no_crea_auditoria_artificial y test_error_permanente_preserva_http_tx_y_fallo_cerrado |
| Reintento explicito conserva historial | test_reintento_operativo_autorizado_registra_intento_sin_repetir_http y test_reconsulta_tecnica_autorizada_preserva_origen_y_historico |
| HC14/TX05 no usa score shadow | test_c_hc14_tx05_no_usa_shadow y test_hc14_tx05_no_conecta_shadow_ni_cambia_clasificacion |
| Core exige expediente/snapshot y mantiene idempotencia | SnapshotYActivacionLibranzaTests, test_originacion_es_idempotente_y_crea_un_solo_par y test_credito_prestador_sin_snapshot_no_se_activa |

Ademas se comparo en memoria el normalizador oficial actual contra el codigo de 4885d8e:
1.008 combinaciones sinteticas MiDecisor y seis HDC, con DTOs identicos en todos los casos.
Comparacion AST: clasificador MiDecisor, HDC y helpers existentes sin cambios; solo se
extrae la proyeccion PN a un helper compartido. VERSION_NORMALIZADOR permanece igual.
Fingerprint, HMAC, modos y permisos de consulta mantienen AST identico a la base.
El parser shadow exige opt-in, conserva estado oficial de error/disponible=False,
requiere revision y no tiene callers productivos. Su salida no habilita decisiones.
No se modificaron snapshots, autorizaciones ni auditorias historicas; no hay backfill.

### Inventario final del paquete

Los 17 archivos siguientes pertenecen al release; diez rastreados modificados y siete nuevos.
No hay otros archivos pendientes ni cambios staged.

| Archivo | Alcance |
| --- | --- |
| contractors/services/aprobacion_interna.py | Control de oferta y evidencia de reutilizacion en gates |
| contractors/services/aprobacion_pagador.py | Revalidacion antes de confirmacion y reintento |
| contractors/services/datacredito_evaluacion.py | Elegibilidad compartida de reutilizacion y evidencia de origen/destino |
| contractors/services/evaluacion_formal.py | Contexto de consulta, cierre y reutilizacion idempotente de errores |
| contractors/services/expediente_originacion.py | Oferta vigente obligatoria |
| contractors/services/oferta_financiera.py | Adaptador al preparar_oferta existente, sin formulas duplicadas |
| contractors/tests/test_aprobacion_interna_prestador.py | Fixtures completos con ingreso verificado y oferta real |
| contractors/tests/test_evaluacion_errores_idempotencia.py | P1-B y concurrencia |
| contractors/tests/test_pipeline_financiero_e2e.py | 27 escenarios integrados con proveedores sinteticos |
| contractors/tests/test_reutilizacion_snapshots.py | P1-A y concurrencia |
| gestion_creditos/services/originacion_libranza.py | Revalidar expediente previo a nueva originacion Prestadores |
| gestion_creditos/tests/test_condiciones_financieras_prestador.py | Fecha historica legitima del fixture Libranza |
| integrations/datacredito/normalizadores.py | Extraer proyeccion compartida, sin cambiar clasificacion oficial |
| integrations/datacredito/midecisor_shadow.py | Diagnostico opt-in aislado y no persistente |
| integrations/tests/test_midecisor_clasificacion.py | Regresiones HC14 negativas conservadas |
| integrations/tests/test_midecisor_shadow.py | Matriz shadow, privacidad y aislamiento |
| docs/PRESTADORES_PIPELINE_FINANCIERO_E2E.md | Diagnosticos previos e informe de precierre |

Revision de contenido y sintaxis de los 16 Python correcta; sin raw crediticio real,
PII real, secretos, tokens reales, logs, bases ni artefactos temporales en el paquete.
Los valores de credenciales/identidad de fixtures son sinteticos.
No cambian settings, .env, modelos, migraciones, dependencias, umbrales, bandas, formulas,
consentimiento canonico, flags ni configuracion de Celery. No se habilita DataCredito.
El diff rastreado es 373 inserciones/43 eliminaciones; no incluye los siete archivos nuevos.

### Dependencias, riesgos y despliegue propuesto (NO ejecutado)

- Requiere la base de codigo 4885d8e y su esquema ya existente; cero migraciones nuevas,
  cero transformaciones de datos. Las nuevas claves JSON son aditivas.
- El estado actual del VPS no se consulto en esta fase. La compatibilidad se contrasta
  contra la base local, no se afirma que el servidor conserve ese SHA o este limpio.
- Ingreso neto verificado vigente, horizonte respaldado, configuracion financiera y politica
  compatibles siguen siendo prerrequisitos. Gates antiguos sin evidencia completa pueden
  bloquearse intencionalmente; resolver mediante revision/reevaluacion autorizada, no backfill.
- Riesgo read-only favorable no equivale a oferta ni aprobacion. HC14/TX05 sigue bloqueado
  para decisiones oficiales hasta aclaracion contractual Experian; este release no lo habilita.
- La ruta evaluacion/gates/originacion revisada se ejecuta sin Celery desde views/admin.
  El despliegue informado usa Gunicorn/systemd: verificar unidad y virtualenv reales.
  No se identifica una tarea Celery llamando estos servicios; no hace falta reiniciar
  Redis, Beat ni OCR para este paquete. Reiniciar otro proceso solo si se demuestra
  que carga alguno de los modulos modificados.
- No hay cambios static/templates ni dependencias: no requiere collectstatic o pip install.
  El reinicio de Gunicorn puede ocasionar una interrupcion breve; coordinar ventana y
  evitar operaciones financieras en curso durante la sustitucion del proceso.

Procedimiento para una autorizacion posterior:

1. Confirmar rama/base/status e inventario anterior; agregar explicitamente solo esos
   17 paths con `git add -- <paths>`, nunca `git add .`. Revisar
   `git diff --cached --check`, `git diff --cached --stat` y diff completo.
2. Commit propuesto: `fix(prestadores): exigir oferta y alinear consentimiento e idempotencia`.
   Registrar RELEASE_SHA, verificar que el commit sea descendiente de la base y contenga
   exactamente el inventario. No se ha creado ese commit.
3. Antes del push, comprobar remoto/upstream y SHA remoto, divergencia y automatizaciones.
   Si hay cambios externos, detenerse. Con autorizacion:
   `git push origin "${RELEASE_SHA}:refs/heads/release/prestadores-prod-2026-08-28"`.
4. En /root/aprobado_web registrar PREVIOUS_SHA; comprobar rama, tracked limpios,
   untracked relevantes y base esperada. Conservar respaldos .env, no sobrescribirlos.
   Confirmar flags efectivos False sin imprimir secretos y migraciones actuales en lectura.
   Si el servidor difiere de la base esperada o tiene conflictos, detenerse.
5. Solo con autorizacion de deploy: `git fetch origin release/prestadores-prod-2026-08-28`;
   exigir FETCH_HEAD == RELEASE_SHA y ancestro PREVIOUS_SHA;
   `git merge --ff-only "$RELEASE_SHA"`. No reset, force ni resolucion automatica.
6. Con el Python del virtualenv confirmado, ejecutar `manage.py check` y
   `manage.py showmigrations --plan` en lectura. No ejecutar migrate.
   Mantener ambos flags DataCredito False y no activar politica SCORE.
7. Reiniciar exclusivamente la unidad Gunicorn confirmada:
   `sudo systemctl restart "$GUNICORN_UNIT"`; verificar is-active,
   healthcheck HTTP local/publico y login sin evaluar solicitudes ni llamar proveedores.
   Verificar SHA/status y flags efectivos del proceso tras el reinicio, no solo del shell.
   No enviar firmas, correos, desembolsos ni una consulta UAT como healthcheck.

Rollback si aparece regresion: suspender originacion/evaluaciones operativamente;
comprobar tracked limpios y volver al PREVIOUS_SHA registrado con
`git switch --detach "$PREVIOUS_SHA"` bajo autorizacion. Reiniciar la misma unidad
Gunicorn, comprobar salud y flags False. No reset --hard, rollback de BD, borrado de
snapshots ni migraciones inversas. Conservar todas las auditorias creadas; el codigo
anterior puede ignorar las claves JSON nuevas y bloquear reutilizacion cruzada.
Retroceder tambien retira el control P0: mantener originacion suspendida hasta corregir
el incidente o reponer un paquete validado. No reabrir operaciones financieras solo porque
el healthcheck responda. Volver a la rama posteriormente exige estado limpio y revision.

**Resultado: LISTO PARA REVISION Y PREPARACION DEL RELEASE.**
No equivale a Prestadores E2E productivo cerrado ni autoriza consumos, activacion de politica,
commit, push, migraciones, reinicios o despliegue.
