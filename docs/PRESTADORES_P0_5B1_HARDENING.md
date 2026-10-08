# P0-5B1: hardening DataCredito y preparacion score PROD

P0-5B1: CERRADO y desplegado segun confirmacion operativa del ticket P0-5B2.1.
P0-5B2.1: implementacion local de politica ratificada INACTIVA e ingreso manual
verificado; requiere validacion PostgreSQL antes de promover este cambio.
En P0-5B2.1 no hubo consultas reales, activacion de proveedores/politicas,
cambios a formulas financieras, commit, push ni deploy.

## Implementado y probado localmente

- Homologacion compartida CC=1 y CE=4, rechazando tipos vacios/no soportados.
  Fuente: manual HDC tabla 1 y Swagger MiDecisor ConsultaRequest.
- HDC clasifica estado antes de sumar la cuota mensual; pago total/canceladas
  de tabla 41 no suman cuotas historicas a la carga vigente.
- Ausencia de liabilities no equivale a lista vacia explicita. Campo mensual
  ausente, invalido, negativo o no finito deja carga desconocida, nunca cero.
- DTO allowlist conserva completitud, cantidad de obligaciones incompletas y
  version del normalizador. No se persiste raw, credenciales ni headers.
- Raw de clientes excluido de repr del DTO. No cambia el motor de score.

## Reservas, concurrencia y leases

La auditoria P0-5A encontro carrera entre cache miss y reserva y recuperacion
incompleta de evaluaciones abandonadas. Ahora reserva y cierre adquieren
pg_advisory_xact_lock por fingerprint en una transaccion corta, antes del lock
de fila. El constraint parcial EN_PROCESO se conserva como segunda proteccion.
La reserva vuelve a buscar EXITOSO/SIN_INFORMACION vigente dentro del lock.
No se bloquea DB durante HTTP. El cierre tardio de un lease recuperado se descarta.

Lease snapshot: maximo entre DATACREDITO_IN_PROGRESS_MINUTES (default 5 minutos)
y cuatro timeouts HTTP + 60 segundos. Evaluacion formal: ocho timeouts + 60
segundos, con el mismo minimo, porque puede coordinar ambas fuentes.
Un lease reciente devuelve EN_PROCESO. Vencido se cierra una sola vez como error
controlado; NO reenvia HTTP automaticamente. FORZAR_CONSULTA requiere staff,
permiso can_force_datacredito_refresh y justificacion. El nuevo intento conserva
el anterior; solicitudes manuales solapadas reutilizan la reserva/cierre concurrente.
Todos los errores finales requieren reintento manual, sin loops ni retries generales.
El reuso de error es una respuesta controlada, nunca un resultado financiero valido.

Evaluacion formal bloquea solicitud antes de auditoria, siempre en ese orden.
Recupera EN_PROCESO vencida, no sobrescribe cierres ni intentos anteriores. Reintentos
se identifican por clave_operacion y una clave de intento nueva. Fuentes vencidas
no se reutilizan como vigentes: nueva auditoria NO_EVALUABLE exige revision/reconsulta.

SQLite conserva el comportamiento secuencial, pero NO acredita concurrencia.
Los TransactionTestCase PostgreSQL usan conexiones distintas, sincronizacion
por eventos/barreras y proveedor mockeado, sin Redis ni sleeps.

## Fingerprint y OAuth

Anterior: SHA256 de ambiente, servicio, HMAC documento, autorizacion/version y
parametros HDC. Nuevo: datacredito-fingerprint-v3 incluye normalizador v3,
tipo documental homologado, HMAC del primer apellido (el enviado) y de la cuenta
tecnica del servicio, endpoint,
producto/tipo de consulta y canal/parametros HDC. No incluye nombres/documentos
en claro, UUID de transaccion ni timestamp accidental. Historicos no se reescriben;
la version nueva NO reutiliza snapshots del algoritmo anterior.
La clave de evaluacion formal tambien incorpora ambas versiones tecnicas, para
no reutilizar una predecision calculada con un normalizador anterior. No reescribe
auditorias ni evidencia: casos legacy requieren revision/resimulacion explicita.

OAuth usa namespace v2 por ambiente + servicio + hash de identidad tecnica,
incluyendo endpoint y credenciales para invalidar cache al rotarlas. No usa las
claves genericas antiguas. Verifica flags/credenciales incluso en cache hit.
401 marca SOLO el token rechazado; no descarta otro renovado concurrentemente.
La siguiente operacion manual obtiene autenticacion nueva; NO repite la consulta
cobrable del 401. 429, timeout y 5xx quedan transitorios, sin replay automatico.
El TTL de cache nunca supera expires_in (margen 60s); tokens cortos no se cachean.
Revoke usa Client_id, Client_secret, token y JSON username/password segun
DocsIntegracionDatacredito/MiDecisor/Token/swagger-api-externa.yaml.
Se conserva TLS verificado, timeout y session/proxy existente. No logs de secretos.

## Definicion score ratificada: INACTIVA

`parametros_score_prestadores_prod_pendientes.json` es la unica definicion preparada
de bandas y parametros PROD. P0-5B2.1 ratifica los pesos, flags, TTL y acciones.
Solo fecha_vigencia_desde permanece pendiente: no se inventa fecha de activacion.
No se instala politica PROD en bases operativas durante este ticket.
preparar_politica_score_prod valida parametros explicitos
y puede persistir SOLO durante tests (RUNNING_TESTS), con actor staff autorizado/motivo.
Reutiliza ConfiguracionScorePrestador/BandaScorePrestador, ligado a la configuracion
financiera exacta prestadores-prod-v1; siempre activa=False. Repeticion es idempotente;
rechaza diferencias en versiones existentes. La auditoria existente registra SIN_CAMBIO
con preparacion_inactiva=True, pues NO hay transicion de politica activa.
No copia DEMO ni admite cambiar los parametros ratificados en esta version.

Comando seguro, exclusivamente dry-run, sin opcion --aplicar ni --activar:

```powershell
python manage.py preparar_politica_score_prestadores_prod
```

Sin fecha devuelve pendientes=['fecha_vigencia_desde'] y persistida=false sin DB.
Para una validacion completa, suministrar la fecha autorizada mediante --fecha-vigencia
(YYYY-MM-DD). Debe existir la configuracion financiera exacta prestadores-prod-v1;
el comando informa su ID/estado, pesos, bandas, versiones, TTL y flags. No crea
esa configuracion ni la activa. --parametros admite valores coincidentes con los
ratificados y la fecha explicita; tampoco persiste.

| Parametro | XLSX | DEMO dual | Propuesto PROD | Requiere aprobacion | Efecto |
|---|---|---|---|---|---|
| Pesos | 45/30/8/12/5% | iguales, HDC 0% | ratificados; HDC/legacy 0% | no | score y banda |
| Redistribucion/referencias | componente 5% | opcionales, redistribuye | opcionales, redistribuye | no | pesos efectivos |
| Geografia | -80 bajo 600 | configurada, senal ausente | sin penalizacion, 0/0 | no | sin hard rule |
| Mora | sugiere revision severa | 90d configurados, no hard rule | informativa; metadata historica 90d | no | no bloqueo automatico |
| Consultas | referencia 90d | 6 configuradas; DTO cuenta 180d | informativas; metadata historica 6 | no | no bloqueo automatico |
| TTL fuentes | no fija contrato operativo | 30/30 dias | 30/30 dias | no | vigencia/reuso |
| Fuentes obligatorias | no define disponibilidad | ambas requeridas | ambas requeridas, sin modo parcial | no | disponibilidad |
| Fallos fuentes | no define politica completa | revision/revision/no evaluable | revision/revision/no evaluable | no | resultado seguro |
| Capacidad del score | 30%; regla auxiliar 25% | carga total/contractual 30% | sin recalibrar | no | componente historico |
| Capacidad oferta | disponible x 30% | no equivalente | neto manual menos obligaciones x 30% | no | cuota real ofertable |
| Topes bandas | B24/B27; tabla auxiliar contradictoria | 10/8/5/3M, 8/8/6/4 meses | 10/8/5/3M, 8/8/8/6 meses | no, ticket los aprueba | limites post-score |
| Tolerancia ingreso contractual | sin regla homologada | 15% | 15% | no | revision por discrepancias |

version_politica=prestadores-score-prod-v1; version_score=prestadores-score-dual-v2
identifica la parametrizacion del motor existente, no un segundo motor.
accion_exceso_capacidad=REVISION conserva la regla existente, sin nueva hard rule.
Referencias ausentes redistribuyen el 5% entre componentes puntuables disponibles;
ausencia de HDC/MiDecisor obligatorios nunca se redistribuye.

## Legacy y fuente neta

La ausencia de version_politica_simulacion ya requiere revision en el motor.
Estrategia: incompatibilidad explicita y revision; resimular/reconfirmar bajo politica
ratificada. El motor existente no rellena el snapshot historico. Regresiones comprueban
la ausencia de escrituras sobre version, monto/plazo y fecha de simulacion legacy.
Oferta sigue sin caller productivo.
Ingreso contractual, estimado MiDecisor, neto valido y obligaciones HDC son
conceptos separados. Sin neto valido preparar_oferta retorna NO_EVALUABLE.
No activar PROD ni declarar Prestadores E2E cerrado.

El contrato usa evaluar_score_prestador existente, banda persistida y preparar_oferta;
este ultimo NO recalcula score. OfertaCalculada.como_dict expone score, versiones,
banda/id, topes de banda, capacidad del score separada de cuota_maxima de oferta,
horizonte/fechas, monto/plazo/cuota ofertables y motivo NO_EVALUABLE.
Sin neto valido o sin carga mensual HDC no hay oferta evaluable ni fallback.

## P0-5B2.1: ingreso MANUAL_VERIFICADA

No existia una estructura con toda la procedencia/vigencia requerida. Se agrega
IngresoNetoVerificadoPrestador (contractors.0020, dependiente de 0019 y AUTH_USER_MODEL).
No se modifican modelos financieros ni formulas. Referencia solicitud, monto, corte,
vigente_hasta, verificador/timestamp, observacion, version y evidencias privadas
(ID/tipo/hash SHA256 del documento existente; sin rutas ni copias de documentos).
Invalidar genera una version INVALIDADO sin monto; no recupera una version anterior.

registrar_ingreso_neto exige authenticated + staff +
contractors.can_verify_contractor_net_income y excluye PerfilPagador incluso con permiso.
Admin de riesgo permite registrar/invalidar por POST + CSRF y consultar historial;
no edicion/borrado. Vigencia y evidencia son explicitas, no defaults ni inferencias.
No se permite modificar ingreso despues de aprobacion para originar/firma.

El servicio bloquea la solicitud en atomic y asigna version incremental con UNIQUE
(solicitud, version). Reintento equivalente reutiliza version. Cada cambio conserva
la anterior y registra timeline interno no visible al cliente. Archivo alterado,
vencimiento o invalidacion impiden reuso. save/delete ordinarios estan bloqueados;
QuerySet.update/delete y SQL directo quedan fuera de protecciones de aplicacion.

construir_version_datos incorpora la version/corte/vigencia de ingreso y validez de
su evidencia. Cambios invalidan reuso/cierre/aprobacion por los controles existentes,
sin sobrescribir snapshot de auditoria completada. Solicitudes sin fuente nueva
conservan su fingerprint previo. Nueva evaluacion registra la version exacta.

preparar_oferta recibe IngresoNetoValido y solicitud_id; verifica la version persistida
actual, monto, fechas y hashes de evidencia. OfertaCalculada serializa registro/version/
fuente/vigencia. oferta_con_ingreso_vigente rechaza oferta obsoleta; el caller futuro
debera usar esta validacion y los gates/version_datos existentes al persistir/consumir.
Sin ingreso o vencido: NO_EVALUABLE. Capacidad score contractual no se recalibra;
cuota_maxima oferta = max(0, ingreso neto verificado - carga mensual HDC) * 0.30.
preparar_oferta no recalcula score. Oferta sigue sin caller automatico productivo.

ingreso_estimado_midecisor se conserva solo en DTO/snapshot normalizado interno,
no en resumen publico ni en capacidad/oferta. No se deduce neto desde contrato,
certificado bancario, saldo pendiente ni estimado. Documentos son evidencia que
el analista verifica, no prueba automatica de ingreso. Sin nuevas senales device/IP/OTP.

Pendientes de este bloque: concurrencia PostgreSQL de ingreso, permisos/versionado
sobre clon y UAT del admin. Politica aun INACTIVA; ningun proveedor consultado.
No declarar Prestadores E2E cerrado.

Validacion focal final P0-5B2.1 (SQLite): 51 tests, OK (skipped=2), 21.014 s;
modulos test_ingreso_neto, test_politica_riesgo_prod, test_hardening_score_prod,
test_politica_prod_horizonte. Permisos/admin/CSRF, historial, rollback, evidencia
alterada, expiracion, aislamiento, cierre de evaluacion obsoleta, pesos/flags/TTL,
fallos de centrales, senales informativas y oferta sin recalcular score cubiertos.
Requests y httpx bloqueados para impedir HTTP real durante toda la validacion.
Regresion completa final contractors.tests + integrations.tests: 477 tests,
OK (skipped=10), 602.429 s, cero fallos/errores. Los diez omitidos requieren
PostgreSQL: ocho existentes y dos nuevos de versionado de ingreso.
PostgreSQL pendiente para IngresoNetoConcurrenciaPostgresTest:
test_reintentos_equivalentes_una_version y
test_cambios_simultaneos_versionados_sin_sobrescribir.
manage.py check sin incidencias; makemigrations --check --dry-run sin cambios;
git diff --check sin errores. Migracion nueva solo generada, no aplicada a DB operativa.

## Pruebas anteriores y archivos P0-5B1

Validacion local SQLite (2026-10-07):

- Regresion completa contractors.tests + integrations.tests: 452 tests,
  OK (skipped=8), 562.513 segundos. Se ejecuto antes de los ultimos ajustes
  de fingerprint/normalizador y sus regresiones adicionales.
- Suite focal final, 14 modulos sobre el codigo final: 160 tests,
  OK (skipped=6), 90.387 segundos.
- manage.py check: sin incidencias.
- makemigrations --check --dry-run: No changes detected.
- git diff --check: sin errores.
- Comando de preparacion dry-run: activa=false, persistida=false,
  INCOMPLETA_NO_APLICABLE; 29 campos requeridos sin decision completa.

HTTP real bloqueado con patch de requests.sessions.Session.request durante validacion;
la suite focal final tambien bloquea httpx.Client.send/httpx.AsyncClient.send.
Casos PostgreSQL nuevos (omitidos en SQLite):

- ReservaConcurrenciaPostgresTest.test_consultas_equivalentes_una_reserva_http_fuera_de_atomic
- ReservaConcurrenciaPostgresTest.test_cache_miss_antes_de_finalizacion_no_genera_segunda_consulta
- ReservaConcurrenciaPostgresTest.test_recuperacion_lease_vencido_un_solo_cierre
- ReservaConcurrenciaPostgresTest.test_dos_reintentos_manuales_recuperan_un_lease_una_sola_vez
- EvaluacionLeaseConcurrenciaPostgresTest.test_dos_recuperadores_cierran_una_sola_auditoria

El sexto omitido de la suite focal, ya existente, requiere PostgreSQL:
ActivacionPoliticaScoreConcurrenciaTest.test_dos_activaciones_concurrentes_conservan_una_politica_activa.
Ninguna prueba consulta proveedores reales. Persistencia inactiva/bandas/auditoria
se ejecuta exclusivamente en la base efimera creada por Django test runner.

Archivos del bloque:

```text
contractors/datacredito/adapter.py
contractors/datacredito/dto.py
contractors/services/datacredito_evaluacion.py
contractors/services/evaluacion_formal.py
contractors/services/politica_financiera_prestador.py
contractors/services/preparacion_score_prod.py
contractors/management/commands/preparar_politica_score_prestadores_prod.py
contractors/tests/test_centrales_duales_prestador.py
contractors/tests/test_evaluacion_formal_v2.py
contractors/tests/test_evaluacion_leases.py
contractors/tests/test_hardening_score_prod.py
integrations/datacredito/auth.py
integrations/datacredito/decisor_client.py
integrations/datacredito/dto.py
integrations/datacredito/historial_client.py
integrations/datacredito/identificacion.py
integrations/datacredito/normalizadores.py
integrations/tests/test_datacredito_hardening.py
integrations/tests/test_datacredito_oauth_hardening.py
integrations/tests/test_datacredito_reservas_hardening.py
integrations/tests/test_datacredito_snapshot_v2.py
docs/parametros_score_prestadores_prod_pendientes.json
docs/PRESTADORES_P0_5B1_HARDENING.md
docs/ROADMAP_APROBADO_2026.md
```
