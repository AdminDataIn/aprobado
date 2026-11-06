# ZapSign Prestadores P0: compatibilidad y activacion independiente

Corte integrador: 2026-11-05, America/Bogota. Base productiva y padre exacto:
2978e54aabc39d80890acada728aa543c8e5abf5 (ID-01D, debe conservarse integro).
Rama candidata: candidate/zapsign-id01d-20261105. Un solo commit integrador;
RELEASE_SHA es el SHA completo comunicado al cerrar este candidato, nunca c9a0587.
El SHA no se incluye dentro de su propio contenido: se pasa explicitamente como
RELEASE_SHA tras contrastarlo con la entrega y Git. Padre y ascendencia se validan abajo.

Procedencia aplicada localmente, sin conflictos, con cherry-pick --no-commit:
f1571e5d5c0e3f7b680104a2995f5d3e1e8ab275,
ac055b430cfcc448b50befcf8eead937990c5f28,
c9a0587468c22c916b57689427afcf3ac1a02a94.
El codigo resultante ZapSign es identico al aprobado en c9a0587. Solo cambia
la documentacion operativa para conservar ID-01D y corregir las precondiciones.
No se hace push, despliegue, activacion, consultas ZapSign ni cambios en documentos.

## Contrato de compatibilidad aprobado

Arquitectura B intacta: dos rutas, un servicio central y propiedad documental
persistida. No se duplican verificacion, scoring, reglas financieras o transiciones.

| Ruta | Configuracion | Resultado de autenticacion |
| --- | --- | --- |
| Libranza /api/webhooks/zapsign/ | Secreto AUSENTE del entorno efectivo (None) | Compatibilidad legacy, aun exige validacion remota y ownership |
| Libranza | Secreto presente vacio, invalido o de tipo incorrecto | 503, sin downgrade |
| Libranza | Secreto valido; header ausente, incorrecto o cruzado | 403 |
| Libranza | Secreto/header correctos | Autenticado |
| Prestadores /api/webhooks/zapsign/prestadores/ | Secreto ausente o invalido | 503, siempre fail-closed |
| Prestadores | Secreto valido; header ausente/incorrecto/cruzado | 403 |
| Prestadores | Secreto exclusivo y header correctos | Autenticado, luego ownership y verificacion remota |

None es el sentinel para variable NO definida. Un ZAPSIGN_WEBHOOK_SECRET=
explicitamente vacio NO equivale a ausencia y debe fallar cerrado. No usar
or None ni normalizar blancos para activar compatibilidad. No hay una bandera
en el request que active la excepcion. Un header no se usa como prueba de ausencia.
La validacion del nombre de header sigue vigente; configuracion invalida, 503.

La auditoria de un callback legacy aceptado sin secreto usa signature_valid=False
y un catalogo autenticacion=legacy_sin_secreto. Un callback autenticado conserva
signature_valid=True. Son evidencias distintas de la validacion remota de firma.
Ninguna auditoria anterior se modifica. Riesgo de entrada legacy no autenticada:
deuda P1, no se declara resuelto por este release. Registro en ROADMAP_APROBADO_2026.md.

## Configuracion exclusiva Prestadores

Ya preparada, no activada: /etc/aprobado-zapsign-production/prestadores.env.pending,
root 0600, dentro de directorio root 0700. Contiene SOLO:

```text
ZAPSIGN_PRESTADORES_WEBHOOK_SECRET=<valor custodiado, no imprimir>
ZAPSIGN_PRESTADORES_WEBHOOK_HEADER=X-ZapSign-Secret
```

El secreto Libranza sigue custodiado e INACTIVO. NO cargar webhook.env.pending
ni webhooks-b.env.pending: contienen ZAPSIGN_WEBHOOK_SECRET y podrian interrumpir
los hooks historicos. No activar un secreto Libranza ni cambiar sus webhooks.
Excepcion puntual autorizada: retirar UNICAMENTE la definicion realmente vacia
de ZAPSIGN_WEBHOOK_SECRET de su origen efectivo durante la ventana productiva,
despues del backup. No ejecutar esa correccion en esta preparacion local.
Si existe un valor no vacio, no eliminarlo ni sobrescribirlo; detenerse y acreditar
su validez y coordinacion. Ausente no equivale a cadena vacia o valor ficticio.

## Secuencia productiva A-H (preparada, NO autorizada ahora)

### A. Compatibilidad y respaldo

- Verificar SHA/branch/remoto y tracked limpio; conservar los tres .env.bak_*
  conocidos y detenerse ante cambios ajenos. Confirmar el nuevo SHA revisado.
- En lectura, comprobar configuracion efectiva de Libranza: variable realmente
  ausente o definicion vacia pendiente de retirada expresamente autorizada.
  Revisar .env y directivas/EnvironmentFiles efectivos de Gunicorn sin imprimir
  valores ni hashes. Un secreto no vacio o una fuente no explicada exige STOP.
- Inventariar SOLO formalizaciones Prestadores en PENDIENTE_FIRMA/ENVIANDO_A_FIRMA,
  documentos emitidos y resultados remotos inciertos. No auditar toda Libranza.
- Respaldo inmediato protegido de codigo, BD y configuracion; restaurabilidad
  en cluster privado como en el procedimiento aprobado. No restore sobre produccion.
  No hay migrate ni rollback de 0051 en este release.

### B. Pausa solo de nuevas formalizaciones Prestadores

Coordinar con los operadores sin alterar estados. No detener Libranza, Celery o
Redis ni bloquear su receptor. Confirmar envios en vuelo finalizados/conciliados.
Los firmantes con enlaces previos pueden actuar: si existe un documento Prestadores
pendiente o un envio incierto, NO continuar C-F sin acreditar recuperacion de
sus eventos durante la ventana. Si no existen documentos/envios Prestadores
pendientes, registrar ese corte; no depender de IDs ni de colas historicas Libranza.

### C. Publicacion autorizada y fast-forward al nuevo candidato

Solo despues de otra autorizacion expresa: desde el worktree limpio de la nueva
rama, publicar el SHA EXACTO, no HEAD de otro worktree. PowerShell:

```powershell
$base = '2978e54aabc39d80890acada728aa543c8e5abf5'
$parent = $base
$release = $env:RELEASE_SHA
if ($release -notmatch '^[0-9a-f]{40}$') { throw 'Falta SHA aprobado' }
if ((git rev-parse HEAD) -ne $release -or (git rev-parse 'HEAD^1') -ne $parent) { throw 'Candidato incorrecto' }
if (git status --porcelain) { throw 'Worktree sucio' }
$id01d = @('static/js/captura_quality.js','templates/gestion_creditos/captura_continuacion.html',
  'gestion_creditos/tests/browser/document_fixture.cjs','gestion_creditos/tests/browser/test_captura_handoff.cjs',
  'gestion_creditos/tests/browser/test_captura_quality.cjs','gestion_creditos/tests/test_captura_documental.py',
  'docs/ID_01D_QUALITY_GATE_2026_11_04.md')
git diff --exit-code $base $release -- $id01d
if ($LASTEXITCODE -ne 0) { throw 'ID-01D alterado' }
$ref = 'refs/heads/release/prestadores-prod-2026-08-28'
$remote = git ls-remote --exit-code origin $ref
if ($LASTEXITCODE -ne 0 -or -not $remote -or ($remote -split '\s+')[0] -ne $base) { throw 'Remoto cambio' }
git merge-base --is-ancestor $base $release
if ($LASTEXITCODE -ne 0) { throw 'Sin fast-forward' }
git push origin "${release}:$ref"
if ($LASTEXITCODE -ne 0) { throw 'Push fallido' }
$remote = git ls-remote --exit-code origin $ref
if ($LASTEXITCODE -ne 0 -or -not $remote -or ($remote -split '\s+')[0] -ne $release) { throw 'SHA remoto no confirmado' }
```

En VPS, con precondiciones A/B acreditadas y RELEASE_SHA completo autorizado:

```bash
set -euo pipefail
cd /root/aprobado_web
: "${RELEASE_SHA:?SHA nuevo aprobado}"
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(git branch --show-current)" = release/prestadores-prod-2026-08-28
test "$(git rev-parse HEAD)" = 2978e54aabc39d80890acada728aa543c8e5abf5
git diff --quiet HEAD --
git fetch origin refs/heads/release/prestadores-prod-2026-08-28
test "$(git rev-parse FETCH_HEAD)" = "$RELEASE_SHA"
test "$(git rev-parse "$RELEASE_SHA^1")" = 2978e54aabc39d80890acada728aa543c8e5abf5
git merge-base --is-ancestor 2978e54aabc39d80890acada728aa543c8e5abf5 "$RELEASE_SHA"
git diff --exit-code 2978e54aabc39d80890acada728aa543c8e5abf5 "$RELEASE_SHA" -- \
  static/js/captura_quality.js templates/gestion_creditos/captura_continuacion.html \
  gestion_creditos/tests/browser/document_fixture.cjs gestion_creditos/tests/browser/test_captura_handoff.cjs \
  gestion_creditos/tests/browser/test_captura_quality.cjs gestion_creditos/tests/test_captura_documental.py \
  docs/ID_01D_QUALITY_GATE_2026_11_04.md
git merge --ff-only "$RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
PYTHONDONTWRITEBYTECODE=1 venv/bin/python manage.py check
```

### D. Activar SOLO Prestadores

Con el backup protegido verificado y antes del reinicio, eliminar solo las
asignaciones vacias acreditadas de ZAPSIGN_WEBHOOK_SECRET. Preservar bytes de
las demas variables, comentarios, propietario y permisos. Si la fuente efectiva
es un Environment= o EnvironmentFile distinto, retirar solo esa asignacion y
conservar todas las demas directivas. Nunca editar /proc ni usar unset solo en
un shell para declarar corregido el servicio. No normalizar valores no vacios.

Nuevo drop-in Gunicorn, sin sobrescribir otros y preservando su EnvironmentFile
actual. Contenido sin secretos; archivo autorizado que ya contiene solo dos claves:

```ini
[Service]
EnvironmentFile=/etc/aprobado-zapsign-production/prestadores.env.pending
```

Validar el archivo root 0600, las DOS claves y ausencia de claves Libranza en ese
archivo; comparar secretos en memoria, nunca stdout. Solo en esta ventana:
systemctl daemon-reload, systemctl restart gunicorn.service y comprobacion de
servicio activo. No reiniciar otros servicios ni cambiar flags de decision.
En .env, el UNICO cambio permitido es retirar la definicion vacia anterior.
El proceso temporal de mantenimiento necesita PATH con venv y /usr/bin:/bin para
Git/SSH; no modificar el PATH configurado del servicio para resolver un fallo
del script de mantenimiento.

Tras el reinicio comprobar en memoria: ZAPSIGN_WEBHOOK_SECRET no es una clave
del entorno real del nuevo PID Gunicorn, settings.ZAPSIGN_WEBHOOK_SECRET is None,
y el secreto Prestadores coincide con su custodia y tiene header X-ZapSign-Secret.
No imprimir valores, hashes, ni systemctl show Environment sin sanitizar.
Si reaparece la clave legacy por otro origen, STOP y conservar evidencia.

### E. Verificar ambos endpoints antes de crear hooks

Con el SHA nuevo realmente cargado y configuracion comprobada:

```bash
set -euo pipefail
target=https://aprobado.com.co/api/webhooks/zapsign/prestadores/
test "$(curl --silent --show-error --max-time 15 --output /dev/null \
  --write-out '%{http_code}' --request POST --header 'Content-Type: application/json' \
  --data '{}' "$target")" = 403
test "$(curl --silent --show-error --max-time 15 --output /dev/null \
  --write-out '%{http_code}' --request POST --header 'Content-Type: application/json' \
  --header 'X-ZapSign-Secret: deliberately-invalid-deployment-probe' --data '{}' "$target")" = 403
# SOLO si A acredito secreto Libranza AUSENTE: JSON sin token se rechaza 400
# antes de crear auditoria o consultar al proveedor; no usar un documento real.
test "$(curl --silent --show-error --max-time 15 --output /dev/null \
  --write-out '%{http_code}' --request POST --header 'Content-Type: application/json' \
  --data '{}' https://aprobado.com.co/api/webhooks/zapsign/)" = 400
test "$(curl --silent --show-error --max-time 15 --output /dev/null \
  --write-out '%{http_code}' https://aprobado.com.co/libranza/)" = 200
```

No seguir redirects. Si Libranza tiene secreto valido preexistente, sustituir
su prueba de continuidad anonima por el rechazo 403; no retirar su secreto.
El JSON sin token no ejecuta una transicion ni crea un documento o auditoria.
Conservar ID-01D: template captura_quality.js?v=id01d-2 y bytes HTTPS del asset
en aprobado.com.co y contratistas.aprobado.com.co iguales a la base 2978e54.
No collectstatic ni reemplazo de assets: este integrador no cambia ningun asset.

### F. Crear SOLO dos hooks Prestadores

Usar la API oficial POST https://api.zapsign.com.br/api/v1/user/company/webhook/
o Postman con credencial productiva protegida. Dos operaciones independientes:

| type | url | header |
| --- | --- | --- |
| doc_signed | https://aprobado.com.co/api/webhooks/zapsign/prestadores/ | X-ZapSign-Secret |
| doc_refused | https://aprobado.com.co/api/webhooks/zapsign/prestadores/ | X-ZapSign-Secret |

El valor del header viene SOLO de la custodia Prestadores. No utilizar token API
como secreto callback ni referencias Sandbox. No imprimir headers/payloads/respuesta
raw. Conservar cada ID real de configuracion, evento y URL. Una peticion por
operacion, sin redirects/retries. Reservar la operacion antes de enviar; si falla,
hay timeout o respuesta sin ID, STOP y conciliar sin repetir POST. No borrar o
recrear hooks. Los dos Libranza y los cuatro educativos NO se modifican.

### G. Evidencia legitima de recepcion

Comprobar configuracion y header con evento legitimo autorizado, documento/firmantes
verificados por API y ownership persistido. No crear obligaciones ni usar documentos
financieros reales como probes tecnicos. No firmar/rechazar por otra persona.
Una prueba negativa HTTP no acredita entrega real. Callbacks ajenos: ignorados,
sin transicion ni GET; propios: flujo existente. Legacy sin secreto sigue no
autenticado (deuda P1), aunque su documento sea verificado remotamente.

### H. Conciliar y reanudar

Conciliar SOLO eventos Prestadores del corte y la ventana con los IDs de entrega
disponibles, IDs nuevos de configuracion, referencias internas y auditorias seguras.
No reprocesar las 83 auditorias legacy historicas ni modificar estados manualmente.
No asumir que 503 recupera eventos indefinidamente ni que crear un hook reenvia
eventos pasados. Si hay un hueco conocido, requerir reenvio oficial acreditado
para esos documentos o detener el cierre de esa evidencia. No convertir esa
verificacion puntual en dependencia de IDs/entregas historicas de Libranza.
Tras comprobar firma/rechazo/idempotencia y estados, reanudar nuevas formalizaciones.

## Contingencia

Antes de crear hooks (A-E), rollback de codigo SOLO con HEAD exacto del candidato
y tracked limpio: revertir el commit integrador, conservando el arbol ID-01D de
2978e54. Retirar solo el drop-in Prestadores creado en esta ejecucion, conservar
el secreto custodiado inactivo y la variable legacy ausente, y renovar unicamente
Gunicorn. Verificar salud y continuidad legacy. No restaurar .env completo sobre
posibles cambios concurrentes ni activar el secreto Libranza como recuperacion.
Despues de crear hooks o recibir eventos propios, priorizar correccion hacia
adelante y conciliacion acreditada; no volver a un receptor inexistente perdiendo
callbacks ni revertir decisiones por restauracion de BD.

Si falla E, NO crear hooks y mantener pausa Prestadores. Conservar servicio legacy
y su configuracion. Ante errores posteriores, bloquear solo la ruta Prestadores
si es necesario y esta autorizado, preservar auditorias y recuperabilidad, y
corregir hacia adelante. No cargar el secreto Libranza preparado como "arreglo"
ni volver al codigo B anterior (romperia legacy sin secreto). No hacer restore
de BD para deshacer firmas acreditadas. No hay migracion inversa.

## Gate del integrador 05/11/2026

- Integracion local sin conflictos. Los once archivos Python de ZapSign son
  identicos a c9a0587; no se cambia comportamiento funcional ya aprobado.
- Siete blobs ID-01D identicos a 2978e54, incluyendo JS, template id01d-2,
  fixtures, pruebas y documento de captura. No se degrada el hotfix desplegado.
- PostgreSQL: 72/72 PASS, cero fallos/omisiones, 326.036 s. Incluye 63 regresiones
  firma/compatibilidad, una continuacion Django compartida y ocho concurrencias
  de captura. Total trece concurrencias reales: cinco de firma y ocho de captura.
- Aislamiento: postgres:16-alpine, 127.0.0.1:58492, tmpfs, rol/credencial
  temporales; NAME=zapsign_rutas_bootstrap y TEST.NAME=test_zapsign_rutas_aisladas.
  HTTP externo, email y publicacion Celery bloqueados, datos sinteticos, sin .env.
  BD de test destruida y contenedor propio retirado al finalizar.
- Browser: 35/35 matriz/contratos y 31/31 captura PASS, cero fallos/omisiones.
  Camara sintetica, nativa/fallback simulados y backend interceptado. No equivale
  a Android fisico ni a una firma productiva real.
- Check con esquema de test completo: sin incidencias. Makemigrations
  --check --dry-run: No changes detected. W001/0018 previo al test corresponde
  solo a bootstrap vacia, no a produccion ni a migraciones nuevas.
- AST Python (11 archivos), sintaxis de un bloque PowerShell/dos Bash y diff
  --check PASS. Sin modelos, migraciones ni dependencias nuevas.
- Remoto release consultado en 2978e54. Un unico commit con padre exacto 2978e54
  permite fast-forward, sujeto a repetir ese gate en la ventana autorizada.
- Sin push, SSH productivo, configuracion, reinicios, consultas ZapSign ni hooks.
  La retirada de la definicion vacia y la activacion Prestadores siguen pendientes
  de ejecucion productiva expresamente autorizada y de las precondiciones A/B.

## Validacion local historica del candidato c9a0587

- PostgreSQL aislado: 63/63 PASS, cero errores/fallos/omisiones, 133.821 segundos.
  Incluye las 50 regresiones B, trece casos adicionales y cinco concurrencias
  reales; la nueva concurrencia admite dos legacy sin header y rechaza efectos
  cruzados sin duplicar transiciones. Verificacion HTTP fuera de atomic.
- Imagen postgres:16-alpine, loopback 127.0.0.1:51464, tmpfs propio.
  NAME=zapsign_rutas_bootstrap, TEST.NAME=test_zapsign_rutas_aisladas, distintos;
  rol y password temporales. No se reutilizan datos, .env o servicios operativos.
- HTTP externo, SMTP y Celery bloqueados; fuentes y datos sinteticos. BD de test
  destruida y contenedor propio retirado al finalizar. Cero operaciones ZapSign.
- Check sin errores y sin incidencias con el esquema de test completo;
  makemigrations --check --dry-run: No changes detected. El aviso W001/0018
  anterior al test corresponde solo a la bootstrap VACIA del contenedor, no a
  una migracion productiva pendiente ni a una autorizacion para migrate.
- No cambios de modelos, migraciones, dependencias, semantica financiera, ownership
  o verificacion remota. La marca legacy de auditoria solo se agrega a eventos
  nuevos no autenticados; historicos intactos. Webhooks educativos sin cambios.

## Inventario integrador exclusivo sobre 2978e54 (15 archivos)

- aprobado_web/settings.py
- aprobado_web/urls_common.py
- contractors/tests/test_aprobacion_interna_prestador.py
- contractors/tests/test_webhook_firma_seguro.py
- gestion_creditos/services/zapsign_client.py
- gestion_creditos/services/zapsign_webhook.py
- gestion_creditos/views/integrations.py
- gestion_creditos/tests/test_core_services.py
- gestion_creditos/tests/test_zapsign_receptores.py
- gestion_creditos/tests/test_zapsign_webhook_seguro.py
- gestion_creditos/tests/test_zapsign_compatibilidad_legacy.py
- docs/ROADMAP_APROBADO_2026.md (solo adenda P1 de este alcance)
- docs/ZAPSIGN_RECEPTORES_SEPARADOS_2026_10_25.md (aviso de precedencia)
- docs/ZAPSIGN_WEBHOOK_HOTFIX_SEGURIDAD.md (historico, no ejecutar sus comandos)
- docs/ZAPSIGN_PRESTADORES_RELEASE_COMPAT_2026_10_26.md (este runbook)

El padre debe ser 2978e54aabc39d80890acada728aa543c8e5abf5. No incluir el runner
temporal, cambios previos del roadmap ni pendientes de otros worktrees.
