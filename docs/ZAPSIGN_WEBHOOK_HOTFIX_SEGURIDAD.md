# ZapSign: receptor autenticado y confirmacion remota de firma

> Historico del receptor compartido, NO es el procedimiento de despliegue actual.
> Integrador 05/11/2026: base/padre 2978e54 (ID-01D); arquitectura B y compatibilidad
> temporal Libranza. Usar exclusivamente ZAPSIGN_PRESTADORES_RELEASE_COMPAT_2026_10_26.md.
> No ejecutar los bloques historicos que cargan secretos Libranza/compartidos.

Base original: `c20962cd34cef1e69c0abfffe42a3c4fc253761c`.
Base del candidato integrado: `9916be10f405d7db90f495c966e4c6232b81b579`.
Alcance exclusivo: seguridad de `/api/webhooks/zapsign/`, compartido por
Libranza, Prestadores y pagares legacy. Wompi no cambia.

## Cierre real Sandbox y candidato sobre APROBADO-04/05

Hotfix origen: `5054ac003f907a7f33c76b700f1bd5336b73d5b8`. Se integro por
cherry-pick en worktree separado, conservando APROBADO-04/05 y los cambios
pendientes del checkout principal. Los siete archivos Python mantienen el
parche original; esta adenda incorpora evidencia, no cambios financieros.
Sin modelos/migraciones/dependencias nuevas, push ni despliegue productivo.

### Evidencia autentica, sin identidades ni enlaces

Sandbox 4815, receptor HTTPS exclusivo bajo `/sandbox-prestadores/`, codigo
5054ac0, PostgreSQL propio y referencias sinteticas de importe cero. Los dos
documentos se conservaron durante las sustituciones autorizadas; se comprobo
retirada de firmantes expuestos por GET 404 y nuevas referencias bajo custodia.
Solo el participante realizo las acciones personales con los ultimos enlaces.

| Documento Sandbox | API oficial | Auditoria autentica | Resultado local | Transiciones |
| --- | --- | --- | --- | --- |
| 17 / SIGNED | signed; unico firmante autorizado signed | 53 / doc_signed / signed | Formalizacion FIRMADO; pagare SIGNED | 1 |
| 18 / REFUSED | recusado; unico firmante autorizado y contacto coincidentes | 54 / doc_refused / recusado | ERROR_CONTROLADO; pagare REFUSED | 0 |

Ambas auditorias: signature_valid true, processed true, sin error y posteriores
a la habilitacion de acciones personales tras la ultima sustitucion. Coinciden
documento, external_id, ambiente Sandbox, token del ultimo firmante y correo
custodiado. Para firma se valido a TODOS los firmantes y sus fechas mediante API.
Firma del 17: 2026-10-24T23:40:54.528580-05:00, America/Bogota. Para el 18 el API
no entrega refused_at/rejected_at interpretable: recepcion local del rechazo
2026-10-24T23:43:00.517739-05:00, que NO se presenta como fecha remota de rechazo.
El campo signed_at tambien aparece en el documento recusado; no prueba firma
ni prevalece sobre recusado. Su estado individual es una cadena no catalogada;
no se invento un enum ni se extendio el contrato del core para aceptarlo como firma.

Los IDs 72333/72332 acreditan configuracion de los webhooks y sus headers. El
receptor acredita evento/documento/autenticacion/GET remoto; no persiste un ID
de registro o entrega del proveedor, por lo que no se afirma leerlo del callback.

Eventos de mantenimiento registrados al cerrar las sustituciones: cero. La
auditoria 55 es un doc_signed adicional del documento rechazado, recibido cuando
el estado ya era terminal: ignorado, payload unverified y sin nueva transicion.
No se usa como firma personal ni se atribuye sin evidencia a una sustitucion.
Se conservan auditorias y trazabilidad; no se corrigieron registros historicos.

### Idempotencia posterior a ambos callbacks PASS

Cuatro replays sinteticos controlados sobre las referencias Sandbox YA finalizadas:
duplicado y evento cruzado para cada documento. Auditorias 70/71 y 72/73 etiquetadas
separadamente, sin incluirlas como entregas autenticas. Cero HTTP al proveedor.
Despues del replay y nueva lectura oficial: ambos callbacks originales siguen
PASS, un evento de negocio por documento, transiciones 1/0, cuotas 0 y desembolsos 0.
Gate sintetico PENDIENTE y origen EN_PROCESO en ambos; no se origino un credito real.

El verificador temporal tenia un falso BLOCKED: filtraba error_message='', pero
las auditorias exitosas guardan NULL. Se corrigio SOLO ese runner externo al
producto para admitir NULL o vacio, conservando exclusion de errores, mantenimiento,
replays y ventana temporal. 36/36 pruebas focales PASS con rollback en PostgreSQL
Sandbox. Ningun cambio del runner ni dato de custodia forma parte de este commit.

### Regresion del candidato integrado

- 32/32 Django PostgreSQL PASS, 0 fallos/errores/omisiones, 66.971 segundos.
  Incluye Libranza/legacy, Prestadores y dos concurrencias reales de callbacks.
  Solo los modulos de firma, clase ZapSignWebhookViewTest y los dos casos existentes
  de webhook en AprobacionInternaPrestadorTest; no se repitio la suite financiera.
- Contenedor oficial postgres:16-alpine, 127.0.0.1:25483, datos tmpfs, usuario y
  credencial temporales. NAME=zapsign_candidate_20261024;
  TEST.NAME=test_zapsign_candidate_20261024, distinta y destruida al finalizar.
  Sin .env, HTTP/Celery bloqueados, correo locmem, almacenamiento privado separado.
  Contenedor eliminado, sin recursos temporales activos de esta validacion.
- Check sin errores; makemigrations --check --dry-run: No changes detected.
  Aviso W001/0018 emitido contra la BD bootstrap VACIA del contenedor despues de
  destruir TEST.NAME: no demuestra migracion pendiente productiva ni autoriza migrate.
  La preparacion inicial fallo por MEDIA_ROOT=PRIVATE_DOCUMENTS_ROOT; se corrigio
  solo el settings temporal, sin silenciar el control ni modificar codigo productivo.
- Financiacion educativa es una aplicacion/receptor independiente, no un modulo
  de este repositorio. Ocho regresiones unitarias sobre sus funciones reales,
  extraidas en memoria con AST y colaboradores simulados, PASS: autenticacion,
  payload invalido, documento desconocido e idempotencia. Sin cargar su Django,
  acceder a su BD o HTTP, ni modificar sus webhooks. No equivale a ejecutar su suite
  integral ni a certificar un E2E educativo. Se preserva su frontera UNKNOWN_DOCUMENT.

### Gate y siguiente operacion

Sandbox firma/rechazo autenticos e idempotencia: PASS. Candidato tecnico: PASS.
Produccion sigue en 9916be1: no se hizo push, restart, migrate ni despliegue.
Antes de publicar/desplegar: autorizacion explicita y coordinacion del secreto
PRODUCTIVO independiente y su header con ZapSign. Nunca reutilizar el secreto
Sandbox o el API token como secreto del callback. El procedimiento de ventana,
activacion y rollback de abajo sigue siendo obligatorio; no aceptar callbacks
sin autenticar como rollback para recuperar disponibilidad.

## Contrato oficial consultado

- [Configurar webhooks y headers](https://docs.zapsign.com.br/webhooks/criar-webhook):
  panel o API; headers personalizados por webhook. El endpoint documentado para
  actualizarlos es POST /api/v1/user/company/webhook/header/, con ID del webhook
  y lista name/value. No usar el API token del proveedor como secreto entrante.
- [doc_signed](https://docs.zapsign.com.br/webhooks/eventos/document/documento-assinado):
  se emite cuando firma un signatario, no necesariamente todos.
- [Detalle de documento](https://docs.zapsign.com.br/documentos/detalhar-documento):
  GET /api/v1/docs/{doc_token}/ mediante credencial API. Estado documental signed
  significa todos firmados; pending no permite cerrar.
- [Documento recusado](https://docs.zapsign.com.br/webhooks/eventos/document/documento-recusado):
  el ejemplo usa estado documental recusado. No confundir rechazo de firma con
  rechazo crediticio definitivo.

## Comportamiento del hotfix

1. Secreto y nombre de header obligatorios. Configuracion vacia/invalida: 503.
   Header ausente/incorrecto: 403. Comparacion UTF-8 con hmac.compare_digest.
   Se autentica ANTES de parsear el cuerpo o crear auditoria de negocio.
2. Se conserva X-ZapSign-Secret por defecto y compatibilidad con Authorization
   Bearer si se configura explicitamente ese nombre. No hay bypass sin secreto.
3. Un token desconocido no cambia estados ni provoca GET al proveedor. Se ignoran
   eventos fuera de doc_signed/doc_refused y estados locales ya terminales.
4. Para documento pendiente conocido se usa el cliente existente, un GET con
   timeout 10 s, TLS normal y allow_redirects=False; no reintento automatico.
   No HTTP mientras se mantienen los locks del procesamiento del callback.
5. La respuesta remota debe coincidir en token, titular esperado, ambiente si lo
   informa y external_id en Prestadores. La lista de firmantes debe ser no vacia,
   con tokens validos/unicos. Para signed: todos signed, fechas conscientes de
   zona horaria, no futuras ni anteriores al envio conocido. Documento eliminado,
   evidencia incompleta o contradiccion: sin transicion, 503 seguro.
6. Se revalida contexto despues del HTTP y bajo locks. Prestadores conserva el
   orden formalizacion -> credito -> pagare; legacy credito -> pagare.
   Cambio de contexto: 409. Errores de procesamiento: rollback, 500 controlado.
7. Duplicados y eventos atrasados no revierten SIGNED/REFUSED/CANCELLED. La
   anulacion sigue protegida. No se borra ni reconstruye evidencia historica.
8. Auditoria nueva: hash del token, evento/estado de catalogo, headers vacios,
   codigos de error controlados; nunca raw, secreto, identidad o URLs de firma.
   signature_valid en el modelo existente indica header autenticado, no una
   firma criptografica del payload ni confirmacion documental por si sola.
9. Libranza preserva FIRMADO -> PENDIENTE_TRANSFERENCIA; NO desembolsa ni activa.
   Su copia local del PDF se intenta despues del commit, mediante API oficial, nunca
   desde una URL suministrada en el callback. Prestadores usa su servicio actual,
   conserva identidad validada y termina FIRMADO sin ejecutar postfirma.

La verificacion asume vigente el documento remoto observado durante el GET.
No existe una transaccion distribuida con ZapSign. Los estados terminales locales,
locks y revalidacion impiden dobles efectos y regresiones; una revocacion remota
posterior a la firma requiere el procedimiento operativo correspondiente.

## Configuracion, coordinacion y rotacion

No se modificaron .env, secretos, cuenta ZapSign ni servicios del VPS en esta tarea.
El diagnostico anterior encontro token API productivo pero secreto de webhook
ausente. El hotfix NO debe instalarse suponiendo que el proveedor ya envia header.

Procedimiento que requiere autorizacion operativa posterior:

1. Abrir ventana coordinada y suspender nuevos envios a firma. Inventariar en el
   panel los webhooks que apuntan al receptor compartido, por ambiente y eventos.
   No mostrar sus payloads, tokens ni URLs privadas en tickets o consola.
2. Generar secreto aleatorio independiente (por ejemplo secrets.token_urlsafe(48))
   exclusivamente en el gestor de secretos/canal protegido. No imprimirlo, pasarlo
   como argumento CLI, guardarlo en Git ni reutilizar el API token ZapSign.
3. Registrar el mismo header X-ZapSign-Secret en los webhooks pertinentes del
   proveedor y ZAPSIGN_WEBHOOK_SECRET en el entorno protegido de Gunicorn. El
   nombre ZAPSIGN_WEBHOOK_HEADER debe coincidir exactamente. No publicar valores.
4. Confirmar coordinadamente la carga del entorno y la recepcion del header.
   Antes del hotfix, el receptor antiguo puede aceptar eventos sin autenticar:
   no prolongar esa ventana ni habilitar firmas mientras se configura.
5. Validar primero Sandbox con credenciales propias y receptor HTTPS aislado,
   PostgreSQL sintetico y canales de envio desactivados. Nunca apuntar Sandbox
   a obligaciones reales ni firmar en nombre de terceros. Acreditar header,
   consulta remota, documento pendiente, firma personal autorizada y duplicado.
6. Desplegar solo tras aprobacion del hotfix y la coordinacion del header. Si
   falta el secreto, 503 es intencional: no desactivar autenticacion para evitarlo.
7. Rotacion: ventana sin nuevas firmas, cambiar secreto en proveedor y servidor
   por canal seguro y recargar Gunicorn autorizadamente. El receptor admite SOLO
   un secreto, sin periodo de doble aceptacion ni fallback al secreto anterior.
   Puede haber 403 durante el cambio; configurar entregas/reintentos del proveedor
   y conciliar pendientes mediante sus eventos y el GET oficial, sin nuevos
   documentos ni cambios manuales de estado. Retirar el anterior del gestor.

Estado Sandbox en este cierre: PENDIENTE de acceso autorizado a cuenta Sandbox
y receptor HTTPS aislado. No se utilizaron las credenciales productivas para
probarlo ni se hicieron llamadas reales de firma. La consulta de documentacion
publica NO cuenta como integracion Sandbox aprobada.

## Despliegue preparado, no ejecutado

El commit sera exclusivamente los ocho archivos listados al final. No hay
migraciones, cambios de score, ingresos, politica V3, HDC/MiDecisor o dependencias.
No se toca Celery, Redis, OCR ni Beat. El receptor y cliente aqui usados se cargan
en Gunicorn. No se ejecutan collectstatic ni migrate.

Tras aprobacion, publicar el SHA exacto a la rama release con push normal y
comprobar su referencia remota. En VPS, con ventana y secreto/header coordinados:

```bash
set -euo pipefail
umask 077
cd /root/aprobado_web
: "${HOTFIX_SHA:?SHA completo aprobado y publicado}"
test "$(git rev-parse HEAD)" = c20962cd34cef1e69c0abfffe42a3c4fc253761c
test "$(git branch --show-current)" = release/prestadores-prod-2026-08-28
git diff --quiet HEAD --

# Respaldar codigo y BD con el procedimiento protegido/restaurable ya aprobado.
# Conservar respaldos .env existentes; no abrir ni imprimir su contenido.
git fetch origin refs/heads/release/prestadores-prod-2026-08-28
test "$(git rev-parse FETCH_HEAD)" = "$HOTFIX_SHA"
test "$(git rev-parse "$HOTFIX_SHA^1")" = c20962cd34cef1e69c0abfffe42a3c4fc253761c
git merge --ff-only "$HOTFIX_SHA"
PYTHONDONTWRITEBYTECODE=1 venv/bin/python manage.py check
PYTHONDONTWRITEBYTECODE=1 venv/bin/python manage.py makemigrations --check --dry-run

# Solo con configuracion ya coordinada y autorizacion de reinicio:
systemctl restart gunicorn.service
systemctl is-active --quiet gunicorn.service
test "$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  https://contratistas.aprobado.com.co/accounts/login/)" = 200

# No hay header ni token: debe rechazar ANTES de BD/HTTP de proveedor.
test "$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --request POST --header 'Content-Type: application/json' --data '{}' \
  https://contratistas.aprobado.com.co/api/webhooks/zapsign/)" = 403
```

No ejecutar pruebas autenticadas contra un documento operativo para healthcheck.
Verificar en solo lectura flags DataCredito/real/autoevaluacion False, catalogo
V3 vacio, ninguna adopcion ni nuevas decisiones y ningun cambio financiero.
Una respuesta 403 sin header NO demuestra que el header del proveedor funciona:
eso requiere la evidencia coordinada Sandbox y posterior entrega autorizada.

### Reversion segura

No hacer rollback automatico al receptor vulnerable ni vaciar el secreto.
Ante regresion: mantener detenidos nuevos envios, preservar auditorias y
reintentos, bloquear temporalmente este receptor en la capa de entrada bajo
autorizacion especifica y preparar correccion hacia adelante. Solo si se autoriza
volver al codigo anterior, mantener el receptor bloqueado hasta reponer la
verificacion obligatoria. No revertir BD ni firmas ya acreditadas; no requiere
migracion inversa. Cualquier restriccion Nginx o reinicio se coordina, no se
ejecuta por este runbook de forma implicita.

## Continuidad V1 sin activar decisiones productivas

- V3 instalado, V1 activa, exclusiones reales vacias: se conservan. No se publica
  ni adopta una politica y no se activan centrales ni autoevaluacion.
- Consulta Experian lista para envio, no enviada: texto seguro al final de esta
  seccion. No se repiten consultas a #4, #9 o #10.
- Revision manual sigue siendo salida legitima. No se exige ingreso manual
  previo para consultar fuentes bajo politica de ingreso publicada/adoptada.
- Para aprobacion real: soporte autentico de neto, verificador autorizado,
  corte/vigencia, documentos propios y hashes mediante registrar_ingreso_neto().
  Reutilizar evidencia vigente; honorarios brutos menos 25 % siguen estimados.
- El expediente actual solo admite contrato, certificado bancario e identidad.
  No falsificar tipos para adjuntar extractos/retenciones: si Riesgos requiere
  otro soporte, aprobar su mecanismo de custodia vinculado antes de aceptarlo.
- No hay nuevo caso elegible acreditado. Se requiere participante genuino,
  consentimiento, contrato/calendario/simulacion, fuentes oficiales utilizables,
  ingreso documental y oferta viable. Luego pagador, expediente y autorizaciones
  separadas de originacion, firma del titular y cierre financiero.

Comunicacion lista para aprobacion/envio por el canal tecnico contratado:

> Asunto: aclaracion MiDecisor PN HC14/TX05 y HDC codigo 14.
>
> En tres UAT consentidas observamos HTTP 200, ACCEPTED/202 ACCEPTED y HC14/TX05;
> HDC retorno codigo 14. Las inspecciones disponibles mostraron modulos de riesgo
> y score, pero el clasificador oficial conserva el resultado no evaluable.
> Solicitamos respuesta contractual versionada sobre:
>
> 1. Significado de HC14 especificamente en MiDecisor Persona Natural.
> 2. Alcance de TX05 y ACCEPTED: exito transaccional frente a disponibilidad real.
> 3. Condiciones y precedencia de flags/codigos para utilizar los modulos de riesgo.
> 4. Modelo, escala, sentinelas, fechas y vigencia de informacionRiesgo.score.
> 5. Significado operativo de HDC14 en nuestro producto/canal UAT.
> 6. Fuente alternativa documentada de obligaciones y cuota monetaria mensual;
>    periodicidad de endeudamiento.ingreso y escala de porcentajeCuotaVsIngreso.
> 7. Ejemplo PN anonimizado utilizable e identidad UAT expresamente autorizada,
>    con esquema/version, modulos parciales y resultado funcional esperado.
>
> No trasladamos la semantica HC de HDC a MiDecisor PN, no interpretamos ausencia
> de informacion como deuda cero ni adjuntamos identidades, scores personales,
> tokens o payloads. Acordaremos cualquier correlacion tecnica por canal seguro.

## Inventario del hotfix

```text
gestion_creditos/views/integrations.py
gestion_creditos/services/zapsign_webhook.py
gestion_creditos/services/zapsign_client.py
gestion_creditos/tests/test_zapsign_webhook_seguro.py
gestion_creditos/tests/test_core_services.py
contractors/tests/test_webhook_firma_seguro.py
contractors/tests/test_aprobacion_interna_prestador.py
docs/ZAPSIGN_WEBHOOK_HOTFIX_SEGURIDAD.md
```

Las modificaciones previas del roadmap, captura documental y diagnosticos no
forman parte del commit. Ningun secreto real aparece en este documento; los
valores del codigo de tests son fixtures sinteticos no utilizables.

## Validacion ejecutada

- PostgreSQL oficial `postgres:16-alpine`, contenedor efimero propio con tmpfs,
  bind 127.0.0.1:59912. NAME=uat03_bootstrap;
  TEST.NAME=test_uat03_aislada, distinto y desechable; usuario temporal.
  Sin .env, HTTP externo y Celery interceptados, correo locmem, datos sinteticos.
  BD de test destruida y contenedor/datos temporales eliminados al terminar.
- Gate ampliado: **74/74 PASS, 0 errores/fallos/omisiones**, 161.928 segundos.
  Incluye dos nuevas concurrencias reales de callbacks (legacy y Prestadores),
  regresion concurrente de anulacion/desembolso y recorrido desde aprobacion V3
  sintetica, pagador y expediente hasta webhook verificado, postfirma y ACTIVO
  exclusivamente en la BD de test.
- Modulos: test_zapsign_webhook_seguro; test_core_services completo;
  test_webhook_firma_seguro; test_integridad_originacion_anulacion;
  test_anulacion_credito; los dos tests de webhook Prestadores existentes en
  AprobacionInternaPrestadorTest.
- Primera corrida focal: 49 tests, seis fallos del nuevo fixture por host
  contratistas.localhost no permitido. Se corrigio SOLO ALLOWED_HOSTS del fixture,
  no settings productivos. Repeticion focal: 49/49 PASS antes del gate ampliado.
- manage.py check: OK. makemigrations --check --dry-run: No changes detected.
  Sintaxis de los siete archivos Python y git diff --check: OK.
- Sandbox real: BLOCKED, no acceso/receptor aislado acreditados en esta ejecucion.
  Pruebas con GET simulado no se presentan como certificacion del proveedor.
- No se conecto al VPS ni se cambiaron datos/configuracion/flags productivos.
  No push ni despliegue. Los pendientes previos se conservan fuera del commit.
