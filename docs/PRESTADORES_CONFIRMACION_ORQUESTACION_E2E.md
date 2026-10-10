# Confirmacion idempotente y orquestacion Prestadores

Base local: `ad43fb6cdcba8cb13e833c3bed9c31c363a223c6`.
Fases A/B/C implementadas localmente. Automatizacion desactivada por defecto;
no hay dispatcher instalado en Beat ni autorizacion de consumo productivo.
La migracion local 0021 crea exclusivamente el job operativo durable.
No se ha accedido a la solicitud operativa 4 ni utilizado sus datos en fixtures.

## A. Confirmacion

`POST /simular/` delega en `confirmar_condiciones_simulacion()`.
Orden de locks: usuario solicitante, luego ContractorApplication. Ownership se
revalida despues del lock. Monto y plazo se validan con los campos Django
existentes; no se comparan strings ni se aceptan valores no finitos.

Ownership, presencia documental y estado de analisis se revalidan sin escribir,
incluso en reenvios; un documento faltante conserva el bloqueo previo de la UI.
Una confirmacion persistida con timestamp/version financiera y monto/plazo
coincidentes (solicitados y simulados) se reconoce SIN save, recalculo, eventos,
versionado, auditorias ni invalidacion. La version de politica puede estar vacia
en simulaciones informativas preexistentes sin politica score; no se inventa una.
El reenvio reconoce evidencia anterior, no certifica su vigencia para un gate.
Si la configuracion cambio o la solicitud ya avanzo, el reenvio sigue siendo
solo lectura. El control P0 de oferta y los validadores de gates siguen intactos.

Para condiciones distintas se admiten estados BORRADOR, DOCUMENTOS_PENDIENTES,
DOCUMENTOS_CARGADOS, EVALUACION_PENDIENTE y EN_REVISION. Se bloquean cambios si
hay un gate PENDIENTE/EN_ANALISIS/APROBADA_PARA_ORIGINAR, una evaluacion en curso
o una etapa posterior/cerrada. No se permite reabrir esas etapas desde simulacion.
Se conservan las validaciones de documentos, analisis, configuracion, rangos y
horizonte. Se reutilizan calculo financiero e invalidacion de evaluacion existentes.

Un solo boton DOM: "Confirmar condiciones y enviar solicitud". En desktop esta
despues del resumen; en mobile el mismo control es fijo inferior. El handler
bloquea doble submit, cancela calculos pendientes y no permite que una respuesta
de calculo tardia habilite de nuevo el envio. Esto NO reemplaza los locks backend.

## B. Contratos existentes

- `evaluacion_formal.evaluar_solicitud_prestador()` valida actor, modo, politica,
  reserva/idempotencia, contexto de consentimiento/fingerprints y fuentes. El HTTP
  ocurre fuera del atomic de reserva; el cierre verifica que los datos/contexto
  no cambiaron. No debe duplicarse en Celery ni en views.
- La auditoria EN_PROCESO representa evaluacion, no publicacion en broker.
  Su clave de operacion/contexto y leases ya protegen ejecuciones equivalentes.
- `datacredito_evaluacion` valida consentimiento y configuracion antes de cache/
  reserva. Ambos flags False impiden consultas y tampoco permiten reutilizacion
  por esa entrada. No iniciar una auditoria artificial por falta de readiness.
- P1-A registra `usos_snapshots` con consentimiento destino y origen inmutable.
- P1-B reutiliza errores recientes del mismo contexto. No usar `nuevo_intento=True`
  ni FORZAR_CONSULTA como mecanismo automatico de entrega/reintento Celery.
- El servicio acepta actor None por compatibilidad interna. El nuevo worker NO
  debe aprovechar ese bypass: siempre debe presentar un actor tecnico autorizado.
- Oferta financiera viable sigue siendo obligatoria en los gates, confirmacion
  pagador y expediente/originacion. Una predecision favorable no la sustituye.

## B. Punto de integracion y autorizacion

`contractors/services/orquestacion_evaluacion.py` es la entrada del automatico;
staff conserva su entrada sincronica existente. Ambos usan el evaluador formal.
Dentro de la transaccion de confirmacion se reserva publicacion
solo cuando los datos/contexto actuales y readiness sean validos. Registrar un
`transaction.on_commit()` que publique el ID de la reserva, nunca PII ni payloads
crediticios. Un reenvio identico no crea otra reserva ni cambia la confirmacion.

Readiness sin HTTP: consentimiento canonico vigente, documentos/contrato,
simulacion versionada, politica score completa/activa/vigente, configuracion
financiera compatible, actor tecnico y configuracion de cada fuente requerida.
Usar los validadores/readiness existentes, sin consultar proveedores para probar
conectividad. Falta de consentimiento/politica/flags mantiene la solicitud
EVALUACION_PENDIENTE sin crear job ni auditoria. No simular resultados.

Actor automatico: ID configurado explicitamente, usuario activo y staff, sin
PerfilPagador, permiso `contractors.can_evaluate_contractor_application`.
No crear usuarios ni conceder permisos automaticamente. El worker revalida su
permiso, consentimiento, politica, contexto y flags propios antes de cualquier
ejecucion. El solicitante no adquiere privilegios de staff por confirmar.

Staff: POST+CSRF, permisos, respuestas y semantica sincronica existentes se
conservan SIN adaptar rutas, Admin ni revision manual a Celery. Automatico y
manual convergen en `evaluar_solicitud_prestador`, la unica entrada al motor.
La orquestacion contiene solo readiness, entrega y claim; no scoring paralelo.
FORZAR_CONSULTA permanece exclusivamente manual con permiso y justificacion.

## B. Persistencia operativa implementada

Un simple on_commit/apply_async NO garantiza entrega: el proceso puede morir
despues del commit o el broker puede rechazarla. Redis/result backend tampoco
puede ser fuente durable del estado operativo. La auditoria de riesgo existente
no tiene lease de publicacion/claim ni estados de encolado y no conviene mezclar
un envio fallido con una evaluacion financiera.

`EvaluacionPrestadorJob` en contractors/models.py, con migracion local 0021
justificada por entrega durable. No modifica tablas financieras ni snapshots.
Campos: solicitud FK, actor FK, clave unica de contexto, version de datos,
versiones de politica/configuracion, hash del contexto de consentimiento/fuentes,
estado, codigo operativo controlado, intentos de publicacion, timestamps de
creacion/publicacion/inicio/cierre, lease/generacion de claim y auditoria
FK nullable. Sin PII, raw ni secretos. El contexto de negocio se calcula con los
constructores existentes, no con un fingerprint paralelo de identidad.

Clave unica: solicitud + version_datos + versiones politica/score/finanzas +
contexto de consentimiento/fuentes + modo existente. Una reserva nunca cambia
de contexto; cambio legitimo crea otra y vuelve obsoleta la anterior pendiente.
El intento manual autorizado usa la semantica existente, no timestamps aleatorios
para forzar nuevas consultas. Agregar constraints/indices para clave y recuperacion.

## B. Maquina de estados y fallos

| Job operativo | Solicitud | Efecto permitido |
| --- | --- | --- |
| Sin job/readiness bloqueada | EVALUACION_PENDIENTE | Sin publicacion ni auditoria |
| PENDIENTE_PUBLICACION | EVALUACION_PENDIENTE | Reserva durable, sin HTTP |
| ENCOLADA | EVALUACION_PENDIENTE | Broker confirmo envio, no significa consulta |
| EN_EJECUCION | EN_EVALUACION al reservar auditoria | Evaluador existente fuera del lock de claim |
| FINALIZADA | Estado fijado por evaluador | Vincular auditoria finalizada |
| REQUIERE_REVISION | Estado fijado por evaluador | Sin preaprobacion ficticia |
| SUSPENDIDA_OPERATIVA | EVALUACION_PENDIENTE | Readiness/flags dejaron de ser validos; sin consumo |
| OBSOLETA | Sin transicion forzada | Datos/consentimiento/politica cambiaron |

- Publicacion fallida: mantener PENDIENTE_PUBLICACION y codigo seguro, no exponer
  excepcion del broker. Un dispatcher de Beat recupera publicaciones pendientes
  con backoff y limite; no modifica estado financiero ni hace HTTP.
- Reusar Beat de `aprobado_web/celery.py`/django_celery_beat; no cron nuevo.
  Dispatcher disponible como task pero NO programado. Una futura activacion
  explicita puede usar Beat cada 60 segundos. No recuperar jobs suspendidos ni
  autorizar reconsultas desde Beat. Nunca recorre solicitudes historicas sin job.
- Worker `contractors.tasks.evaluar_solicitud_prestador_task(job_id)`: lock corto
  del job, claim unico y commit; luego llamar al evaluador con actor autorizado.
  No mantener transaction.atomic durante HTTP. Revalidar version bajo el mismo
  lock de solicitud usado por el evaluador, no solo un check previo vulnerable.
- La publicacion puede confirmarse despues de que empiece el worker: usar updates
  condicionales monotonicamente; nunca retroceder EN_EJECUCION a ENCOLADA.
- Duplicado Celery: mismo job/contexto llama como maximo una evaluacion activa.
  La reserva de evaluacion/fingerprint existente es la segunda barrera de HTTP.
- Worker caido/lease vencido: recuperar con los servicios existentes; inspeccionar
  auditoria y reserva antes de actuar. No reiniciar HTTP a ciegas: si no puede
  demostrarse que no hubo consumo, dejar revision/reintento humano autorizado.
- NO autoretry para timeouts/resultados funcionales, incluido TX23. Reintentar
  publicacion no equivale a autorizar reconsulta a Experian.
- Con flags False en worker, no consumir aunque el job se publicara antes;
  pasar a SUSPENDIDA_OPERATIVA con mensaje publico pendiente. Reactivar flags no
  consume backlog automaticamente: la reanudacion requiere accion staff autorizada
  y nueva revalidacion. No reutilizar el GET de seguimiento para reanudar.
- Cola implementada `celery`, nunca `ocr_documental`. Revisar capacidad/timeouts del
  worker con medicion antes de puesta en marcha. No tocar el OCR dedicado.
- HC14/TX05 sigue fail-closed; shadow nunca es input ni fuente de decision.

## C. Seguimiento publico

Crear `services/estado_evaluacion.py` para proyeccion publica de solicitud/job;
reusar `presentacion_solicitud.construir_estado_publico_solicitud()`, sin exportar
auditorias, scores, proveedor, codigos HC/TX, fingerprints ni razones internas.

GET autenticado `/mi-credito/solicitud/<id>/estado/`: ownership obligatorio,
404 para solicitud ajena, Cache-Control private/no-store. Solo GET de lectura;
no usarlo como disparador ni crear jobs/auditorias al consultar. JSON allowlist:
estado publico, etapa, mensaje seguro, actualizado_en y continuar_seguimiento.

`static/js/seguimiento_evaluacion_prestador.js` consulta con credentials same-origin,
sin almacenar datos crediticios. Intervalo inicial 3s, backoff hasta 15s, maximo
2 minutos por visita; no requests solapadas, pausar al ocultar/cerrar pantalla,
cancelar fetch al salir y detener en estado terminal/401/403/404. Un fallo de red
no implica fallo de evaluacion: ofrecer recargar y consultar despues.

Mi credito conserva estado al volver. ENCOLADA muestra "Solicitud enviada para
evaluacion", no afirma que se estan consultando centrales. Solo EN_EJECUCION/
EN_EVALUACION muestra loader "Estamos validando tu informacion". Pendiente
operativo muestra pendiente/revision sin loader infinito; cierre favorable es
evaluacion inicial, separado de aprobacion interna, pagador, firma y desembolso.

## Archivos implementados para B/C

- `contractors/models.py` y `migrations/0021_evaluacionprestadorjob.py`: job operativo.
- `contractors/services/orquestacion_evaluacion.py`: readiness, reserva, dispatcher.
- `contractors/tasks.py`: adaptador Celery sin formulas ni scoring propios.
- `contractors/services/confirmacion_simulacion.py`: reserva/on_commit postconfirmacion.
- `contractors/services/evaluacion_formal.py`: fencing de contexto esperado del job.
- `views_admin.py`, `admin.py` y `revision_manual.py` NO cambiaron: mantienen
  permisos/respuestas y comparten el evaluador existente con el worker.
- `contractors/services/estado_evaluacion.py`, `contractors/views.py`,
  `contractors/urls.py`: proyeccion/GET con ownership.
- `aprobado_web/settings.py`: routing/actor/flag operativo. `celery.py` NO cambia;
  no se instala programacion periodica ni se activan flags.
- `templates/contractors/mi_credito_prestador.html`,
  `static/js/seguimiento_evaluacion_prestador.js`, CSS administrativo del portal.
- Tests dedicados de orquestacion, permisos, estados publicos y browser/polling.

## Pruebas obligatorias antes de activar B/C

PostgreSQL aislado y sinteticos; HTTP bloqueado y Celery interceptado:
doble confirmacion/publicacion/claim concurrentes; crash entre commit y envio;
envio duplicado despues de ack incierto; broker caido; worker caido; leases;
flags False en web y worker; consentimiento ausente/incompatible/revocado;
politica ausente/invalida y cambio de contexto antes de claim/HTTP/cierre;
actor sin permiso/con PerfilPagador/inactivo; errores y fuentes incompletas;
P1-A evidencia origen/destino; P1-B no duplica auditorias recientes; HC14 cerrado;
P0 SIN_OFERTA no avanza gates; permiso separado para reconsulta forzada;
cerrar navegador no detiene worker; endpoint no consulta proveedores/no escribe;
propietario vs ajeno y privacidad JSON/HTML; polling limitado y sin solapamiento.

No activar B/C antes de revisar resultados, autorizar al actor tecnico y aprobar
la puesta en marcha del dispatcher y los consumos UAT. Un trabajo incierto se
cierra en REQUIERE_REVISION; no se vuelve a ejecutar automaticamente.

## Validacion local de A

- PostgreSQL 16.13, imagen oficial `postgres:16-alpine`, host `127.0.0.1`,
  puerto temporal 59046, base runner `aprobado_confirmacion_base` y base de tests
  `test_aprobado_confirmacion_synthetic`. Credenciales temporales; .env no cargado.
- 174 tests Django aprobados, 0 errores, 0 fallos, 0 omisiones:
  `test_confirmacion_simulacion`, `test_continuidad_solicitud`,
  `test_portal_minimo_prestadores`, `test_semantica_financiera_simulador`,
  `test_pipeline_financiero_e2e` y `test_checkpoint_evaluacion_staff`.
- Concurrencias reales: dos POST iniciales; reenvios de confirmacion existente
  sin cambiar evidencia; dos borradores de un mismo solicitante, uno solo avanza.
- 5 tests JS en `test_simulador_cliente.cjs` y 4 Playwright en
  `browser/test_p04g_simulador.cjs`, desktop 1280 y mobile 390, todos aprobados.
  Capturas revisadas: CTA unica, sin overflow horizontal ni solapamiento de su resumen.
- `check` y `makemigrations --check --dry-run` ejecutados en el proceso aislado
  despues de la suite, sin errores ni migraciones nuevas.
- 0 intentos HTTP externos, 0 publicaciones reales Celery, storage temporal,
  correo en memoria. Contenedor/red eliminados; recursos preexistentes preservados.
- Los fallos intermedios de fixture y las dos regresiones de navegacion/documentos
  se corrigieron antes de la ejecucion final completa de 174 tests.
- Estos resultados corresponden al cierre previo de A. Validacion B/C abajo.

## Activacion futura (NO ejecutada)

1. Revisar/aplicar 0021 solo en un despliegue autorizado; desplegar el nuevo JS
   mediante el procedimiento habitual de assets. No hay backfill ni jobs historicos.
2. `PRESTADORES_AUTO_EVALUACION_ENABLED=False` es el default de codigo.
   `PRESTADORES_EVALUACION_ACTOR_ID` debe identificar un staff activo, sin
   PerfilPagador, con `contractors.can_evaluate_contractor_application`.
   No se crean usuarios ni se otorgan permisos desde la orquestacion.
3. Web y worker deben compartir release, configuracion, contexto de negocio y
   flags autorizados. La disponibilidad de proveedores NO se activa con el flag
   automatico. Se requieren ambos flags DataCredito y readiness local completa
   para las fuentes efectivamente requeridas por la politica vigente.
4. Las dos tasks se enrutan a `celery`, nunca `ocr_documental`:
   `contractors.tasks.evaluar_solicitud_prestador_task` y
   `contractors.tasks.publicar_evaluaciones_pendientes_task`.
   No usar task_always_eager en despliegue ni autoretry para consultas.
5. Para recuperacion durable un operador debe programar explicitamente la segunda
   task en Beat (cada 60 s recomendado). Este bloque NO crea PeriodicTask ni
   modifica beat_schedule. Sin dispatcher, el envio inicial sigue funcionando,
   pero un crash entre commit y on_commit requiere recuperacion operativa.
6. Defaults: lote 25, backoff 60 s exponencial limitado a 3600 s,
   republicacion de ENCOLADA sin claim tras 300 s, lease de ejecucion 900 s.
   Medir latencia/capacidad antes de cambiar estos valores o concurrencia.
7. EN_EJECUCION con lease vencido pasa a REQUIERE_REVISION. No se recupera
   haciendo HTTP: revisar auditoria/reserva con herramientas existentes y usar
   exclusivamente el reintento manual autorizado cuando corresponda.
   Habilitar flags NO reactiva SUSPENDIDA_OPERATIVA.

## Controles de la implementacion B/C

- Reserva solo tras confirmacion nueva, nunca por GET, replay, polling o barrido
  de solicitudes. La solicitud operativa 4 no se toca ni se encola retrospectivamente.
- Locks solicitud -> job. Publicacion tras commit con excepciones controladas;
  caida del broker/DB en callback no revierte ni hace parecer fallida la confirmacion.
- Readiness se verifica al reservar, publicar, reclamar y en el lock del evaluador.
  El ultimo fence valida contexto, actor recargado, lease y generacion del job.
  No hay HTTP durante los locks de claim/reserva. Publicacion/ack no retrocede
  estados si el worker ya comenzo o termino.
- Payload Celery: solo ID del job. Sin documento, datos contractuales, secretos
  ni resultados crediticios. Logs controlados sin strings de excepciones.
- Endpoint de seguimiento solo GET autenticado/owner; 404 ajeno, private/no-store.
  JSON allowlist con estado publico, etiqueta/detalle/mensaje, etapa, timestamp,
  continuar_seguimiento y procesando. Sin resultados internos/score/fingerprints.
- Polling 3-15 s, maximo 2 min, timeout de lectura 10 s, sin solapamientos;
  pausa hidden, aborta al salir, detiene al finalizar/401/403/404. No cancela el job.
  Loader solo con ejecucion real/vigente y flags de proveedor activos.
- El worker no crea gates ni creditos. P0 oferta, P1-A consentimiento origen/destino,
  P1-B errores recientes y HC14/shadow siguen en sus servicios originales.

## Validacion B/C

- Suite integrada PostgreSQL: 373 aprobadas, 0 fallos, 0 errores, 0 omitidas.
  Incluye confirmacion/continuidad, orquestacion, portal, E2E financiero, gates,
  consentimiento/reutilizacion, idempotencia, MiDecisor oficial/shadow y HDC.
- Pasada adicional de orquestacion, evaluacion formal, leases y reservas:
  72 aprobadas, 0 fallos, 0 errores, 0 omitidas. Repite los 39 casos nuevos
  de orquestacion; no representa 72 casos adicionales distintos.
- 16 concurrencias PostgreSQL distintas aprobadas: 10 en la suite integrada
  y 6 adicionales del nucleo. Las 4 concurrencias de orquestacion se repiten
  en la pasada adicional, sin contarlas dos veces.
- JS/browser: 17 aprobadas (8 seguimiento, 4 simulador/layout y 5 cliente).
  Polling sin solapamientos, limites temporales, hidden/pagehide, errores de
  autorizacion y screenshots 390/1280 px. Chromium/Edge, no Safari fisico.
- PostgreSQL 16.13, imagen oficial postgres:16-alpine, host 127.0.0.1;
  base desechable test_aprobado_orquestacion_synthetic, credenciales temporales,
  sin .env, volumen persistente, datos productivos ni conectividad externa.
  HTTP/SMTP bloqueados, Celery interceptado, cache/correo en memoria y storage
  temporal. 0 HTTP externos y 0 publicaciones reales. Contenedor/red eliminados
  en ambas ejecuciones; recursos preexistentes preservados.
- manage.py check: exit 0. La advertencia de 0018 corresponde a la base temporal
  vacia usada por el launcher, no demuestra una migracion pendiente productiva.
  makemigrations --check --dry-run: exit 0, sin cambios adicionales a 0021.
  git diff --check: exit 0; node --check del seguimiento: exit 0.
- Se corrigieron tres regresiones de presentacion detectadas en una pasada
  intermedia: la proyeccion conserva el titulo publico original fuera de la
  evaluacion. Los tests existentes pasan sin debilitar sus expectativas.
- Al cierre previo de B/C, HEAD era ad43fb6cdcba8cb13e833c3bed9c31c363a223c6; sin staging,
  commit, push ni deploy. Captura/diagnostico documental previos conservan sus
  hashes. No se modifican flags operativos, solicitudes reales ni historicos.

Pendiente antes de activar: migracion/despliegue autorizados, actor/permisos y
readiness reales, prueba broker/worker con dispatcher explicitamente programado,
y UAT Safari/iPhone fisico. HC14/TX05 conserva fallo cerrado y shadow separado.
No se declara E2E financiero productivo aprobado.

## Inventario de B/C sobre el working tree de A

- `aprobado_web/settings.py`: flag independiente, actor y routing operativo.
- `contractors/models.py`: EvaluacionPrestadorJob, sin cambios financieros.
- `contractors/migrations/0021_evaluacionprestadorjob.py`: solo nueva tabla operativa.
- `contractors/services/orquestacion_evaluacion.py`: readiness/reserva/publicacion/claim/dispatcher.
- `contractors/tasks.py`: adaptadores Celery sin reglas de negocio nuevas.
- `contractors/services/confirmacion_simulacion.py`: hook solo en confirmacion nueva.
- `contractors/services/evaluacion_formal.py`: fence opcional; callers manuales compatibles.
- `contractors/services/estado_evaluacion.py`: proyeccion publica estrictamente de lectura.
- `contractors/views.py`: seguimiento inicial y GET autenticado.
- `contractors/urls.py`: ruta de estado owner-only.
- `templates/contractors/base.html`: bloque de scripts para el seguimiento.
- `templates/contractors/mi_credito_prestador.html`: estado publico y loader real.
- `static/js/seguimiento_evaluacion_prestador.js`: polling limitado.
- `static/css/contractors-portal.css`: loader y ajuste de texto.
- `contractors/tests/test_orquestacion_evaluacion.py`: servicios/seguridad/concurrencia PostgreSQL.
- `contractors/tests/browser/test_seguimiento_evaluacion.cjs`: estados/polling/browser.
- `contractors/tests/browser/test_p04g_simulador.cjs`: render real de seguimiento en fixture.
- `docs/PRESTADORES_CONFIRMACION_ORQUESTACION_E2E.md`: contrato y cierre local.

La Fase A conserva ademas `test_confirmacion_simulacion.py`,
`test_portal_minimo_prestadores.py`, `test_simulador_cliente.cjs` y
`templates/contractors/simulador_prestador.html` con sus cambios previos.
El test de captura y el diagnostico documental pendientes se preservan sin cambios.

## Precierre del release A/B/C

- Base local/remota verificada: ad43fb6cdcba8cb13e833c3bed9c31c363a223c6.
  Alcance: los 18 archivos B/C anteriores mas los 4 de A, 22 en total.
  El inventario de publicacion excluye los dos archivos de captura/diagnostico.
- Regresion final acotada: 79 tests PostgreSQL aprobados, 0 fallos/errores/omitidos.
  Modulos: test_orquestacion_evaluacion, test_confirmacion_simulacion,
  test_checkpoint_evaluacion_staff y test_evaluacion_formal_v2. Incluye seis
  concurrencias reales y un control temporal adicional de confirmacion completa
  con automatizacion False: sin callbacks/jobs/auditorias/snapshots/consumos.
  No se repitio indiscriminadamente la regresion financiera historica.
- PostgreSQL 16.13, postgres:16-alpine, 127.0.0.1 y base desechable
  test_aprobado_abc_preclose_synthetic, sin .env ni credenciales operativas.
  HTTP/SMTP bloqueados y Celery interceptado. Cero intentos HTTP externos y
  cero publicaciones reales durante los tests; contenedor/red eliminados.
- Upgrade probado desde esquema contractors.0020: el runner crea una BD de
  tests nueva, retira solo 0021 aun vacia y verifica el estado anterior antes
  de reaplicarla. Se comparan columnas y constraints de todas las tablas previas:
  permanecen identicos; solo se agrega contractors_evaluacionprestadorjob.
  No se copian datos productivos ni se transforma evidencia historica.
- sqlmigrate contractors 0021: CREATE TABLE, tres FK y los indices de la tabla
  nueva, incluyendo UNIQUE(clave_contexto) e indice(estado, proxima_publicacion).
  Sin DML ni alteraciones de tablas financieras/snapshots. Dependencia 0020,
  swappable AUTH_USER_MODEL, sin conflictos en el grafo.
  SHA256 del SQL PostgreSQL inspeccionado:
  a844743e1ae7f6a65dd9bb21f63fae5629f4802ca6c011fc92dea3c5928b21c4.
- Descubrimiento Celery forzado en el proceso aislado: ambas tasks registradas,
  routing celery, sin autoretry y sin entradas Beat. No signals/AppConfig/imports
  que creen jobs. Actor None no puede atravesar readiness del automatico.
- Regresion manual: endpoint staff, POST/permisos/PerfilPagador y accion Django
  Admin aprobados; no se cambian sus rutas, respuestas ni semantica sincronica.
- JS/browser repetidos: 17/17 en Chromium/Edge y 17/17 en WebKit 26.0 build 2203
  con touch/mobile emulado. Son los mismos 17 casos, no 34 casos distintos.
  Se usan respuestas interceptadas y datos sinteticos. WebKit se descargo de
  Playwright a cache temporal, sin nuevas dependencias/archivos del proyecto.
  Esto valida el motor WebKit local, NO Safari/iPhone fisico, que sigue pendiente.
- Sin override local del flag automatico en entorno o .env; default False.
  No se cambia ningun flag DataCredito ni se presume el estado vivo del VPS.
  El corte de lectura de settings requiere recargar los procesos web/worker
  general afectados; no requiere tocar OCR, Redis, Nginx ni otros servicios.

## Despliegue propuesto con automatizacion apagada

Procedimiento futuro, NO ejecutado por este precierre:

1. En /root/aprobado_web registrar PREVIOUS_SHA y comprobar rama, diff rastreado
   limpio y respaldos sin seguimiento conservados. Exigir la base ad43fb6.
   Usar el Python/virtualenv real del servicio, no asumir una ruta de venv.
2. Comprobar, sin imprimir secretos, PRESTADORES_AUTO_EVALUACION_ENABLED=False,
   DATACREDITO_ENABLED=False y DATACREDITO_REAL_ENABLED=False en configuracion
   efectiva de web y worker. Verificar que no exista PeriodicTask habilitada
   para publicar_evaluaciones_pendientes_task. Si difiere, detener el despliegue;
   este procedimiento no modifica .env ni instala Beat ni usuarios/permisos.
3. git fetch origin release/prestadores-prod-2026-08-28; exigir que el SHA remoto
   coincida exactamente con RELEASE_SHA publicado; git merge --ff-only RELEASE_SHA.
   Detenerse ante divergencias/modificaciones locales; no reset/clean/force.
4. python manage.py migrate contractors 0021 --plan. Exigir que la unica operacion
   pendiente sea contractors.0021; si hay otras, revisarlas antes de continuar.
   python manage.py migrate contractors 0021 --noinput, solo con autorizacion.
   Es obligatorio aplicar 0021 ANTES de servir las nuevas views: el seguimiento
   consulta la tabla incluso con automatizacion False. Sin backfill historico.
5. python manage.py collectstatic --noinput mediante el procedimiento habitual:
   este release SI incorpora JS nuevo y CSS, a diferencia del hotfix anterior
   que solo versionaba un asset existente. Comprobar permisos/manifest/STATIC_ROOT.
6. Recargar gunicorn.service y celery.service (worker general) para cargar views,
   modelo y registro de tasks del mismo release. No reiniciar celery OCR, Redis,
   Nginx ni Beat; no habilitar dispatcher. Considerar la breve ventana de restart.
7. python manage.py check; verificar SHA, salud HTTPS del dominio contratistas,
   asset seguimiento_evaluacion_prestador.js y GET de seguimiento autenticado.
   Revalidar flags apagados y ausencia de nuevas reservas/consumos automaticos.
   No usar confirmar, reintentar, FORZAR_CONSULTA ni solicitudes operativas como
   smoke test. No declarar UAT crediticia real aprobada con estas comprobaciones.
8. Ante regresion, rollback de codigo al PREVIOUS_SHA mediante procedimiento
   autorizado y limpio, seguido de assets/procesos afectados. Conservar la tabla
   0021; no revertir la migracion ni borrar jobs/auditorias automaticamente.

Antes de activar se requiere autorizacion independiente de consultas reales,
actor tecnico existente/activo/con permiso, readiness y consentimiento vigentes,
configuracion consistente web/worker, broker/worker probados, scheduling explicito
del dispatcher y UAT Safari fisico. HC14/TX05 sigue indeterminado oficialmente;
shadow no alimenta scoring, oferta, gates ni originacion.
