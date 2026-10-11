# Prestadores: preparacion de cierre UAT financiera (10/10/2026)

Base: `7cc4ac90a02442b608c0ef8e2b0631cbd6ddf82b`.
Documento operativo complementario al ROADMAP v1.3; no reemplaza ni reescribe su
historial. NO autoriza consumo, activacion de flags, transiciones o despliegue.
No se consultaron el VPS, sus registros ni Experian en esta preparacion.

## A. Diagnostico del normalizador

Call graph real:

`decisor_client.consultar_midecisor_persona_natural`
-> `adapter.consultar_proveedor_datacredito_prestador`
-> `normalizar_midecisor_pn` -> `_estado_midecisor`
-> extraccion (solo si EXITOSA_CON_INFORMACION)
-> DTO allowlist Prestadores -> snapshot -> centrales -> score -> predecision.

La respuesta HC14/TX05 descrita por el operador tiene envelope ACCEPTED /
202 ACCEPTED, validacion y riesgo con informacion y score entero normalizable.
El motivo directo de exclusión es `_estado_midecisor`: luego de validar TX y
envelope, solo admite HC13; HC14 retorna ERROR_TECNICO antes de extraer campos.
El adapter lo convierte en ERROR_PERMANENTE/RESPUESTA_INVALIDA y el servicio
guarda resultado_normalizado vacio. HTTP y HC/TX se conservan. No es un problema
de ubicacion del score, ni se corrige conectando el parser shadow.

No hay un response real completo autorizado disponible para esta revision local.
Se inspeccionaron el contrato local y el fixture sintetico de la estructura
comunicada, no un raw operativo ni documentos de participantes.

Fuentes leidas:

- `DocsIntegracionDatacredito/MiDecisor/Servicio/DecisorServicio.pdf`, pagina 12:
  TX01-08 exito transaccional, TX09-16 fallo, TX23 excepcion interna; TX17 describe
  validacion de empresa, no establece su interpretacion en PN.
- `Swagger MiDecisor.yaml`, `InfoTransaccion.codigosRespuesta`, lineas 719-759:
  confirma TX; describe genericamente HC13 como sin informacion, contradiciendo
  ejemplos/compatibilidad HC13 con informacion. HC14 no tiene definicion PN.
- Schemas `InformacionRiesgoPN` (1992), `Endeudamiento` (1860), `Sugerencias` (1881),
  y `RespuestaPN` (913). No establecen minimo/maximo/modelo/sentinelas del score.
- La semantica HC13/HC14 de Historia de Credito+ NO prueba la de MiDecisor PN.

**Conclusión contractual: PENDIENTE DE ACLARACION EXPERIAN para habilitar HC14.**
Preguntas minimas: significado PN de HC14/TX05 con validacion/riesgo True; si el
modulo riesgo es utilizable pese a HC14; modelo, escala, rango y sentinelas del
score; coherencia esperada entre flags y modulos; resolver descripcion HC13 del
Swagger. Solicitar matriz oficial versionada, no inferirla de un caso DEMO.

### Variables y destino real

Prefijo `R = content.respuesta`. Tipos numericos descritos como string se parsean
en memoria; el rango interno del consumidor no equivale a validez contractual.
La politica activa productiva no se leyo aqui: verificarla con el preflight B.

| Ruta JSON | Tipo / validacion | Uso real y comportamiento ausente/invalido |
|---|---|---|
| status; content.status | string; ACCEPTED / 202 ACCEPTED | Envelope requerido; no demuestra riesgo valido. Contradiccion -> error, sin score. |
| content.infoTransaccion.codigosRespuesta[] | objetos clave/valor string; HC/TX normalizados, sin duplicados contradictorios | TX fallido prevalece; TX23 transitorio sin retry HTTP automatico; TX17 PN/ausente/desconocido indeterminado. HC14 bloqueado. |
| R.validacion.conInformacion | boolean contractual | Ausencia/indeterminado no acredita exito. False con datos contradictorios nunca SIN_INFORMACION. |
| R.informacionRiesgo.conInformacion | boolean | False/null no autoriza datos del modulo. No confundir bandera de validacion con disponibilidad de riesgo. |
| R.informacionRiesgo.score | string numerico entero; finito, sin coma ambigua; consumidor 0..1000 | DTO score_externo -> componente datacredito_score y peso_midecisor. Ausente/-1/sentinela conocido -> None/revision; valor fraccionario o fuera del limite interno -> error, no truncar. 0 no implica aprobacion ni certifica sentinela proveedor. |
| R.informacionRiesgo.viabilidad | string enum ALTA/MEDIA/BAJA | Extraida, pero NO entra al DTO del motor ni sustituye score/bandas. Ausente -> None; no usar viable=True como aprobacion. |
| R.informacionRiesgo.ratingRecaudos | string enum A/B/C/D/N | Extraido para diagnostico; excluido del score actual. N no se traduce a buen/mal cliente. |
| R.informacionRiesgo.montoSugerido | string numerico | Extraido, excluido de oferta/limites internos. No sustituye preparar_oferta(); falta -> None. No ampliar su uso sin politica aprobada. |
| R.informacionRiesgo.alertas[] | array de objetos con texto/fechas | Se reducen a codigos/conteo, nunca texto libre en DTO/log. DTO alertas se conserva; no introduce pesos nuevos. Ausencia no prueba que no existan alertas. Validacion semantica de listas/codigos proveedor pendiente, no inferir veto ni aprobacion. |
| R.endeudamiento.ingreso | string decimal finito; disponibilidad del modulo | DTO ingreso_estimado_midecisor para contraste. NO sustituye ingreso neto manual verificado. Ausente -> None; flag negativo con importe no vacio -> error. |
| R.endeudamiento.porcentajeCuotaVsIngreso | string numerico | Proyeccion diagnostica, no componente de politica. Unidad/escala contractual por confirmar; no convertir 0.8 en 80 ni fijar umbral. |
| R.comportamientoCrediticio.indicadoresValores | objeto con flag; strings saldoActual/saldoMora/valorCuota/porcentajeDeuda y contadores creditosVigentes/Cerrados | DTO saldos/cuotas/contadores; MiDecisor aporta contraste de mora entre fuentes. En dual, capacidad/carga/cuotas completas provienen de HDC; porcentajeDeuda no fija capacidad. Ausentes -> None, no cero. |
| R.comportamientoCrediticio.comportamientoPago.vectorComportamiento | array, null permitido si no hay informacion | Senal de mora en normalizador y contraste; False con vector no vacio es contradictorio. Null no fabrica buen comportamiento. |
| R.sugerencias.vectorSugerencias; evolucionDeuda y otros submodulos | estructuras opcionales del Swagger, flags False/null y vectores null posibles | No consumidos por el scoring actual; se ignoran sin convertir null en ausencia de riesgo. No persistir textos libres ni ampliar DTO sin necesidad. |

HDC se mantiene independiente: ReportHDCplus/productResult.responseCode,
liabilities/estados/vigencia, mora y carga mensual completa. HDC SIN_INFORMACION
no equivale a cero obligaciones ni autoriza una decision positiva. Disponibilidad
de cada fuente se resuelve con la politica existente en centrales_riesgo.

## Correcciones locales sustentadas

Se reprodujeron antes del cambio defectos en HC13/TX02 (no HC14): score
`667.9` se entregaba como 667 utilizable; riesgo con conInformacion=False y score
667 tambien se entregaba utilizable. Ambos infringian el contrato del DTO y
las declaraciones de disponibilidad, independientemente de la semantica HC14.
Tambien se reprodujo la extraccion de score=900 desde sugerencias cuando riesgo
estaba vacio: el fallback recursivo aceptaba una ubicacion no contractual.

- Guard PN en `_modulos_midecisor_validos`: no truncar score fraccionario, no
  eliminar comas ambiguas para producir un entero, ni entregar fuera del limite
  ya utilizado por el consumidor. Ausentes/sentinelas conservan None/revision.
- Flags presentes de riesgo/deuda/comportamiento/indicadores/pagos deben ser
  booleanos o null; False/null no pueden acompañarse de datos crediticios.
  Modulos opcionales ausentes o vacios/null siguen siendo compatibles.
- Score PN exclusivamente desde `content.respuesta.informacionRiesgo.score`;
  no buscar score/puntaje/scoreDecisor en sugerencias u objetos ajenos. Campo
  contractual ausente deja score=None/revision, no un entero hallado al azar.
- No se alteran HDC, helpers numericos compartidos, DTO, shadow, bandas, formulas,
  consentimientos, autorizaciones, fingerprints, modelos ni migraciones.
- VERSION_NORMALIZADOR no cambia: no invalidar globalmente cache ni inducir
  consumos adicionales. Se validan nuevas respuestas; snapshots historicos no se
  corrigen ni reinterpretan. La UAT debe registrar si usa cache o consulta nueva.
  Como no se persiste raw, tampoco puede certificarse retroactivamente la ruta/
  precision original de un score cacheado. Si esa evidencia resulta indispensable,
  solicitar una reconsulta separadamente autorizada, no forzarla automaticamente.
- No se habilita HC14; estos fixes no desbloquean por si solos el caso DEMO.

## B. Readiness pendiente por caso

| Caso | Estado conocido | Pendiente antes de autorizar consumo |
|---|---|---|
| UAT-01 #4 | Datos/consentimiento/simulacion comunicados, pendiente sin fuentes en ultima lectura | Revalidar estado, actor, ingreso neto verificado/horizonte, versiones, fuentes/jobs/leases. Via manual; replay de confirmacion NO crea job retroactivo. |
| UAT-02 | Participante aun debe completar personalmente solicitud y consentimiento | Registrar solo ID interno cuando exista; completar el mismo preflight. Perfil presuntamente favorable no fija resultado. |
| UAT-03 | Participante aun debe completar personalmente solicitud y consentimiento | Mismos controles, sin usar reportes presumidos como decision. Alertas reales se tratan por contrato/politica, sin consultas preliminares adicionales. |

Lectura propuesta en VPS, Python del venv real, desde `/root/aprobado_web`.
Completar ACTOR_ID y agregar IDs nuevos solo cuando existan. No ejecutado aqui.
No llama evaluar/reservar/confirmar, no abre proveedores ni imprime PII/secrets.
La transaccion PostgreSQL READ ONLY impide escrituras accidentales del bloque;
no demuestra por si sola salud del worker ni validez remota de credenciales.

```python
# Ejecutar dentro de: python manage.py shell (sesion nueva, sin overrides de flags)
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from contractors.models import ContractorApplication
from contractors.score.politica import obtener_politica_score_activa
from contractors.services.autorizacion_datacredito import obtener_autorizacion_datacredito_vigente
from contractors.services.capacidad_contractual import obtener_configuracion_simulador_prestador
from contractors.services.datacredito_evaluacion import construir_fingerprint_datacredito, evidencia_uso_snapshot
from contractors.services.horizonte_simulacion import horizonte_para_solicitud
from contractors.services.ingreso_neto import obtener_ingreso_neto_vigente
from contractors.services.solicitud import solicitud_tiene_documentos_obligatorios
from integrations.models import ConsultaDatacreditoSnapshot as Snapshot
from integrations.datacredito.readiness import evaluar_readiness_datacredito
from integrations.datacredito.settings import obtener_configuracion_datacredito, secreto_documental_valido

ACTOR_ID = None  # Completar ID interno autorizado, no nombre/documento.
SOLICITUD_IDS = (4,)  # Anadir IDs UAT-02/03 solo despues de su registro personal.
assert isinstance(ACTOR_ID, int) and ACTOR_ID > 0, 'Falta actor autorizado'
assert connection.vendor == 'postgresql', 'Motor inesperado: detener'

with patch('requests.sessions.Session.request', side_effect=RuntimeError('HTTP bloqueado')), \
     patch('httpx.Client.send', side_effect=RuntimeError('HTTP bloqueado')), \
     patch('httpx.AsyncClient.send', side_effect=RuntimeError('HTTP bloqueado')), \
     transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
    actor = get_user_model().objects.get(pk=ACTOR_ID)
    actor_ok = (actor.is_active and actor.is_staff and not hasattr(actor, 'perfil_pagador')
                and actor.has_perm('contractors.can_evaluate_contractor_application'))
    tecnico_ok = actor_ok and str(actor.pk) == str(settings.PRESTADORES_EVALUACION_ACTOR_ID)
    print('ACTOR', actor.pk, 'MANUAL', bool(actor_ok), 'AUTOMATICO', bool(tecnico_ok))
    print('FLAGS', {n: bool(getattr(settings, n, False)) for n in (
        'DATACREDITO_ENABLED', 'DATACREDITO_REAL_ENABLED', 'PRESTADORES_AUTO_EVALUACION_ENABLED')})
    reporte = evaluar_readiness_datacredito()
    print('CONFIGURACION', reporte['resultado'], reporte['estado'])
    for seccion, requisitos in reporte['secciones'].items():
        print('REQUISITOS', seccion, [(r['nombre'], bool(r['configurado'])) for r in requisitos])
    politica = obtener_politica_score_activa()
    financiera = obtener_configuracion_simulador_prestador()
    cfg = obtener_configuracion_datacredito()
    print('POLITICA', politica.pk if politica else None,
          'FINANCIERA', financiera.pk if financiera else None, 'AMBIENTE', cfg.environment)
    servicios = tuple(s for s, aplica in (
        ('decisor', politica.requiere_midecisor or (politica.peso_midecisor or 0) > 0),
        ('historial', politica.requiere_hdcplus or (politica.peso_hdcplus or 0) > 0),
    ) if aplica) if politica and politica.usa_fuentes_duales else (cfg.default_service,)
    for sid in SOLICITUD_IDS:
        s = ContractorApplication.objects.get(pk=sid)
        auth = obtener_autorizacion_datacredito_vigente(s)
        versiones_ok = bool(politica and financiera
            and politica.configuracion_financiera_id == financiera.pk
            and s.version_configuracion_financiera_simulacion == financiera.version
            and s.version_politica_simulacion == politica.version_politica)
        horizonte = horizonte_para_solicitud(s, financiera) if financiera else None
        ingreso = obtener_ingreso_neto_vigente(s)
        print('SOLICITUD', s.pk, s.estado, 'CONSENTIMIENTO', bool(auth),
              'DOCUMENTOS', solicitud_tiene_documentos_obligatorios(s),
              'SIMULADA', bool(s.simulada_en), 'VERSIONES', versiones_ok,
              'INGRESO_VERIFICADO', bool(ingreso),
              'HORIZONTE', bool(horizonte and horizonte.disponible))
        audits = s.auditorias_predecision.all()
        print('AUDITORIAS', audits.count(), list(audits.values_list(
            'pk', 'estado_ejecucion', 'resultado')))
        print('JOBS', list(s.evaluacion_jobs.values_list('pk', 'estado', 'lease_hasta')))
        for servicio in servicios:
            if not auth or not secreto_documental_valido(cfg.document_hash_secret):
                print('FUENTE', servicio, 'BLOCKED_IDENTIDAD_CONSENTIMIENTO_CONFIG')
                continue
            fp = construir_fingerprint_datacredito(solicitud=s, servicio=servicio,
                autorizacion=auth, configuracion=cfg)
            ids = set(audits.values_list('snapshot_midecisor_id' if servicio == 'decisor'
                                        else 'snapshot_hdcplus_id', flat=True)) - {None}
            fuentes = Snapshot.objects.filter(Q(fingerprint=fp) | Q(pk__in=ids), servicio=servicio)
            print('FUENTE', servicio, 'RESERVAS', fuentes.filter(estado='EN_PROCESO').count())
            for snap in fuentes.order_by('-consultado_en')[:5]:
                uso_valido = bool(evidencia_uso_snapshot(snap, solicitud=s, autorizacion=auth))
                print('SNAPSHOT', str(snap.pk), snap.estado, 'HTTP', snap.codigo_http,
                      'VIGENTE', snap.vigente_hasta > timezone.now(), 'USO_VALIDO', uso_valido)
```

Si el script falla, no imprimir traceback con locales ni excepciones del proveedor;
reportar clase del error y control pendiente. No interpretar ausencia de evidencia
como PASS. Las reservas EN_PROCESO requieren revision: no recuperarlas aqui.
El script no emite APTO automaticamente; se coteja con los diez controles del
checkpoint del roadmap y con el entorno/participantes expresamente autorizados.

Read-only operativo adicional, sin nuevas auditorias de infraestructura:

```bash
systemctl is-active celery.service
systemctl show celery.service -p ActiveState -p SubState -p MainPID
```

No imprimir Environment/ExecStart ni URLs del broker. Adjuntar evidencia existente
de worker cargando el mismo release, tasks registradas, cola `celery`, broker y
dispatcher; si falta, BLOCKED para certificar asincronia. `celery inspect` envia
mensajes de control al broker: no confundirlo con este preflight estrictamente
read-only. El release no instala schedule Beat del dispatcher. No instalarlo aqui.

## C. Primera ejecucion autorizada: procedimiento exacto, NO ejecutar aun

1. Autorizar por escrito solicitud/actor, finalidad y ambiente de Experian,
   servicios requeridos por politica, ventana/coste, y limite de consumo por
   servicio. Una evaluacion dual puede consultar MiDecisor Y HDC: no prometer
   solo MiDecisor. No cambiar politica para aprovechar un caso esperado.
2. Completar preflight B y tomar linea base (IDs/conteos/estados/leases). Validar
   requisitos legales/ingreso/documentos. Si hay errores frescos, cache vigente
   o reserva incierta, decidir su tratamiento antes, sin borrar ni forzar.
3. UAT-01, existente: UNA invocacion manual del motor oficial, por POST staff +
   CSRF `contractors:admin_ejecutar_evaluacion` (ruta
   `/gestion/prestadores/solicitudes/4/evaluar/`), o proceso temporal autorizado.
   El endpoint es sincronico y usa flags efectivos de Gunicorn; no necesita
   worker ni automatizacion. No ejecutar tambien el shell para el mismo caso.
4. Alternativa de proceso temporal (solo tras nueva autorizacion): revalidar
   dentro del proceso, actor explicito, defaults de cache y sin nuevo_intento.
   No envolver todo el motor en atomic: el HTTP esta diseñado fuera de locks.
   La invocacion exacta existente es:

   ```python
   # NO ejecutar en el preflight. Este bloque crea auditoria y puede consumir.
   from django.test import override_settings
   from contractors.services.evaluacion_formal import evaluar_solicitud_prestador
   from contractors.services.datacredito_evaluacion import REUTILIZAR_SI_VIGENTE
   with override_settings(DATACREDITO_ENABLED=True, DATACREDITO_REAL_ENABLED=True,
                          PRESTADORES_AUTO_EVALUACION_ENABLED=False):
       resultado = evaluar_solicitud_prestador(
           solicitud, solicitado_por=actor,
           modo_datacredito=REUTILIZAR_SI_VIGENTE, nuevo_intento=False,
       )
   # No imprimir resultado/snapshot completos ni score/PII.
   ```

   `solicitud` y `actor` deben recargarse por IDs autorizados y superar el
   preflight antes de ese bloque. No ejecutarlo con variables residuales de otra
   UAT. Los overrides se restauran al salir y NO habilitan otros procesos.
5. UAT-02/03 asincronas: participantes completan y consienten personalmente;
   antes de la confirmacion nueva verificar actor tecnico/ingreso/versiones y
   autorizar flags coherentes de web y worker, broker/dispatcher y operacion de
   cierre. UNA confirmacion -> job unico -> publicacion on_commit -> worker ->
   mismo motor. Doble POST/entrega no produce segundo consumo. Si se confirmaron
   con flags apagados, replay no crea job: evaluar por via manual autorizada,
   sin manipular condiciones ni fabricar eventos para aparentar asincronia.
6. Ante error/incierto detener, inspeccionar estados antes de repetir. No
   FORZAR_CONSULTA ni nuevo_intento salvo autorizacion separada, permiso especifico
   y justificacion. Una respuesta negativa valida NO obliga a consultar otra vez.
7. Cerrar ventana: restaurar/desactivar todos los procesos habilitados segun el
   plan aprobado y verificar flags. No desactivar un worker durante un consumo
   en vuelo sin determinar su estado; no reencolar una ejecucion incierta.
8. Verificar diferencias de auditorias/jobs/snapshots, evidencias usos_snapshots,
   HTTP/HC/TX y errores controlados con allowlist, sin raw/PII. Conteo de snapshots
   no demuestra por si solo numero de HTTP: adjuntar telemetria segura disponible
   o marcar ese control pendiente. No usar requests reales adicionales de salud.

## D. Matriz PASS/FAIL de cada UAT

| Control | PASS | FAIL / bloqueo |
|---|---|---|
| Transporte | OAuth/producto concluyen con transporte y metadatos seguros trazables | HTTP/timeout no controlado o perdida de diagnostico. HTTP 200 solo no acredita informacion. |
| Normalizacion | Fuente documentada, estructura/flags coherentes; variables requeridas validas o ausencia explicita | HC14/17 PN sin confirmar, contradiccion o dato invalido no puede habilitar score. Bloqueo seguro es PASS del control negativo, no PASS del camino favorable. |
| Score | Motor existente y politica/versiones vigentes; inputs suficientes, pesos/banda reales | Inventar faltantes, usar shadow o confundir score externo con score final. No asumir aprobado por perfil esperado. |
| Capacidad/oferta | Ingreso verificado, carga HDC y horizonte vigentes; preparar_oferta determina resultado real | SIN_OFERTA/NO_EVALUABLE bloquea gate. No ajustar ingreso, politica o condiciones para forzar continuidad. |
| Predecision | PREAPROBADO_READ_ONLY/BLOQUEADO_READ_ONLY/revision/NO_EVALUABLE segun motor | Cualquier resultado favorable sin fuentes/evidencias suficientes. No inventar rechazo definitivo por fallo tecnico. |
| Fallos proveedor | TX23/error transitorio/indeterminado auditados, normalizado vacio y sin avance indebido | Retry HTTP ciego, perdida de HTTP/HC/TX, o conversion a aprobacion. |
| Idempotencia | Mismo contexto reutiliza evaluacion/captura de fuentes validas sin nueva auditoria/HTTP artificial; origen/destino trazados | Duplicados, cambio de consentimiento de snapshot original, force automatico o lease recuperado a ciegas. |
| Estado publico | Solo pendiente/procesando real/revision/resultado inicial permitido; estado persiste al cerrar pagina | Score/proveedor/error interno/PII visibles o loader fingido con flags apagados. |
| Gate humano | Solo riesgo compatible Y oferta viable vigente permiten proponer continuidad autorizada | Preaprobacion usada como aprobacion definitiva o gate con SIN_OFERTA. No crear gate en esta UAT sin autorizar la transicion. |

## E. Criterio de cierre hasta predecision

Para cada ID UAT registrar: consentimiento compatible (solo referencia tecnica),
versiones, evidencia de ingreso/horizonte, modo/actor, auditoria/snapshots/jobs
origen/destino, conteos antes/despues, HTTP/codigos/clasificacion, resultado real,
estado publico y decision de continuidad/revision. Sin identificaciones/nombres,
score personal, JSON financiero/raw, tokens, hashes completos ni archivos reales.

PASS global de controles no significa tres aprobaciones. Un rechazo/revision
fundamentados pueden cerrar su caso; un error indeterminado solo valida el
control negativo y deja pendiente demostrar una evaluacion con fuentes utilizables.
El conjunto necesita evidencia real de evaluacion -> predecision conforme al
contrato, y evidencia independiente de entrega Celery si se pretende cerrar A/B/C.
Firma, originacion y desembolso quedan fuera de este hito, con sus gates existentes.

## F. Separacion de bloqueos

- Contractuales irresueltos: HC14 PN/HC13 contradictorio, TX17 PN, escala/modelo/
  sentinelas de score y unidades de porcentajes. No se resuelven con programacion.
- Implementacion corregible: truncamiento de score, fallback fuera de su ruta y
  aceptacion de datos en modulos declarados sin informacion; corregidos localmente
  con regresiones.
- Operativos por verificar: permisos, ingreso/documentos/versiones, configuracion
  por servicio, migracion 0021, jobs/leases/cache, worker/broker/dispatcher y
  autorizacion de consumo/activacion. No son defectos presumidos del normalizador.
- La correccion local requiere revision antes de publicarse; el VPS sigue con el
  release indicado. No se reinterpretan snapshots ni se invalida cache historica.

## Validacion inicial SQLite (anterior al precierre)

- Se agregaron nueve pruebas de regresion en test_midecisor_clasificacion.
  Los dos casos iniciales fallaron antes del fix (18 subcasos), reproduciendo
  truncamiento y modulos contradictorios. El fallback de ruta se reprodujo tambien
  antes de corregirlo. El fixture de error NaN ahora usa la ruta contractual;
  conserva todas sus aserciones de metadata, privacidad e idempotencia.
- Pasada final: 141 tests, 141 aprobados, 0 fallos/errores/omisiones, 104.360 s.
  Modulos: integrations.tests.test_midecisor_clasificacion, test_midecisor_shadow,
  test_hdcplus_normalizacion_dual, test_datacredito_snapshot_v2;
  contractors.tests.test_centrales_duales_prestador, test_pipeline_financiero_e2e,
  test_score_prestadores_v2. Incluye 27 escenarios E2E; no se repiten 696 casos.
- SQLite en memoria, dotenv omitido, almacenamiento privado/publico temporales
  separados, cache/correo/broker en memoria y sockets/SMTP bloqueados. Proveedores
  sinteticos/mocks; sin bases operativas, consultas externas ni colas productivas.
- manage.py check exit 0; advertencia W001/0018 exclusivamente sobre la conexion
  temporal vacia del launcher. El runner con esquema de tests aplicado informa
  cero incidencias. No es evidencia de migraciones pendientes en produccion.
- makemigrations --check --dry-run exit 0: No changes detected. Sin migraciones.
  git diff --check sin errores. Bloques Python de la guia comprobados por AST,
  no ejecutados contra el VPS ni usados para consumir proveedores.
- Comparacion AST con HEAD: normalizador HDC, clasificador _estado_midecisor y
  helpers _entero/_decimal/_normalizar_score intactos. Sin cambios en politica,
  oferta, score, consentimiento, shadow o settings. PostgreSQL/concurrencias no
  se repitieron: no se alteraron locks, persistencia ni orquestador.
- ROADMAP v1.3 y pendientes previos de captura/diagnostico conservados sin cambios.
  No se hizo commit, push, deploy, activacion ni consumo real. UAT operativa y
  aclaracion contractual de Experian siguen pendientes; no se declara E2E cerrado.

## Precierre del hotfix: integracion PostgreSQL (10/10/2026)

- Mismos siete modulos: 141 tests aprobados, 0 fallos, 0 errores, 0 omisiones;
  runner: 125.437 s. Incluye las nueve regresiones nuevas, 27 escenarios E2E,
  persistencia de snapshots/diagnosticos, auditorias, reutilizacion/idempotencia,
  scoring existente, HDC y aislamiento de shadow. No se repitio la suite de 696.
- PostgreSQL 16.13, imagen oficial local `postgres:16-alpine`, contenedor propio
  desechable y red dedicada, sin montajes del host ni conexiones operativas.
  Puerto publicado exclusivamente en `127.0.0.1:51905`; usuario temporal
  `normalizador_test`, base `aprobado_normalizador_synthetic` y TEST.NAME distinto
  `test_aprobado_normalizador_synthetic`, creado/destruido por el runner.
  Credenciales aleatorias solo en memoria. No se cargaron archivos .env.
- HTTP/socket/SMTP bloqueados en el proceso; fixtures y proveedores sinteticos,
  cache/correo/broker Celery en memoria, almacenamiento temporal separado.
  Flags base desactivados; overrides de tests no afectan servicios operativos.
- El runner con esquema de tests aplicado informa check sin incidencias.
  `manage.py check` posterior exit 0, con W001/0018 solo en la base temporal
  vacia (no migrada) al terminar el runner; no describe el estado de produccion.
  `makemigrations --check --dry-run`: No changes detected, exit 0.
- Comparacion AST: solo cambian `_extraer_campos_midecisor_pn` y
  `_modulos_midecisor_validos`. Clasificador HC14, HDC, DTO, fingerprints/cache,
  shadow, motor/politica financiera y orquestador conservados. Sin migraciones.
  No se ejecutaron nuevas pruebas de concurrencia: locks y reservas no cambian.
- Un intento preliminar de infraestructura se detuvo antes de Django porque
  Docker no publico el puerto en una red `internal`; se limpiaron sus recursos
  antes de repetir con red dedicada. No fue un fallo de las pruebas.
- Contenedor, volumen anonimo y red propios eliminados al terminar. Pendientes
  de roadmap/captura/diagnostico conservados y excluidos del paquete de cuatro
  archivos autorizado. No hay cambios de configuracion ni datos operativos.
- La publicacion del hotfix no autoriza despliegue, activacion ni UAT real.
  HC14/TX05 sigue fail-closed y pendiente de aclaracion Experian; los historicos
  no se transforman y la cache no se invalida globalmente.
