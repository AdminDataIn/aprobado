# ZapSign P0: separacion de receptores

> Precedencia posterior, 26/10/2026: el PM autoriza compatibilidad temporal SOLO
> para Libranza con secreto ausente y activacion exclusiva de Prestadores. Este
> documento conserva el diseno/gate B original, no el orden de activacion vigente.
> NO ejecutar sus bloques que cargan cuatro variables, cierran Libranza o cambian
> hooks historicos. Usar ZAPSIGN_PRESTADORES_RELEASE_COMPAT_2026_10_26.md.

Corte: 2026-10-25, America/Bogota. Cambios exclusivamente locales.
Base productiva: 9916be10f405d7db90f495c966e4c6232b81b579.
Hotfix compartido preservado: f1571e5d5c0e3f7b680104a2995f5d3e1e8ab275.
Rama de trabajo: candidate/zapsign-receptores-20261025, sobre ese hotfix.
El cierre autorizado crea UN commit local B cuyo padre es f1571e5; el SHA se
obtiene de Git despues de crearlo, no se inserta en su propio contenido.
El candidato f1571e5 NO contiene esta separacion y NO debe desplegarse como B.
No hay push, cambios productivos, webhooks creados ni secretos activados en este cierre.

## Comparacion y recomendacion

| Alternativa | Seguridad y aislamiento | Historicos | Coste y plazo relativos |
| --- | --- | --- | --- |
| A. Receptor compartido autenticado | Una ruta/secreto; verificacion remota y locks comunes; una credencial entrante afecta ambos productos | Minima migracion; candidato f1571e5 validado | Menor coste y puesta en produccion mas corta |
| B. Dos receptores en el mismo Django | Rutas y secretos distintos; propiedad persistida controla transiciones; comparte proceso, BD y credencial API | Conserva URL y documentos Libranza/legacy; Prestadores usa otra URL sin backfill | Coste intermedio: configuracion, dos hooks nuevos, regresiones y corte coordinado |
| C. Cuentas/organizaciones separadas | Limita tambien el alcance del API y la distribucion de payloads; no sustituye autenticacion ni validacion remota | Exige conservar cuenta/credencial original para consultar documentos anteriores | Mayor coste, operacion y posible dependencia contractual; no necesario para aislar transiciones |

Se recomienda B para el objetivo solicitado. No equivale a aislamiento fisico ni
a una frontera de confidencialidad de datos en ZapSign: si la cuenta distribuye
todos los eventos a todos sus hooks, ambos receptores y los educativos pueden
recibir payloads ajenos. B los autentica/ignora sin persistir raw ni ejecutar
transiciones ajenas. Si se exige que ni siquiera reciban datos de otro producto,
confirmar filtrado efectivo del proveedor o evaluar C. No prometerlo mediante B.

## Auditoria dirigida y contrato local

- aprobado_web/urls_common.py registra las rutas comunes usadas por los dominios.
- gestion_creditos/views/integrations.py expone dos wrappers POST/CSRF-exempt y
  non_atomic_requests, sin scoring ni logica financiera.
- gestion_creditos/services/zapsign_webhook.py contiene UN procesador: autenticacion,
  ownership, verificacion remota, auditoria, revalidacion, locks e idempotencia.
- Libranza/legacy identifica documentos por Pagare.zapsign_doc_token unico.
- Prestadores identifica documentos por el SHA256 unico de
  FormalizacionCreditoPrestador.proveedor_document_id_hash y sus relaciones a
  Pagare, Credito, CreditoLibranza y OrigenCreditoPrestador. Puede no guardar
  el token raw en Pagare: se conserva esa politica de persistencia.
- El credito originado para Prestadores tiene linea LIBRANZA. No clasificar por
  linea, payload.producto, nombre del PDF, email, external_id entrante o URL recibida.
- Un origen/formalizacion Prestadores impide caer en el camino legacy aunque
  falte su hash. Referencias inconsistentes: fail-closed, sin consulta remota.
  Si se perdieron TODAS las referencias que permiten resolver un token, no se
  reconstruye su propiedad: documento desconocido ignorado y conciliacion operativa.
- Los hashes/tokens de historicos no se cambian ni se copian. Un documento ajeno
  se ignora incluso si el callback afirma pertenecer al producto del receptor.

| Receptor | Ruta | Secreto | Header |
| --- | --- | --- | --- |
| Libranza y legacy | /api/webhooks/zapsign/ | ZAPSIGN_WEBHOOK_SECRET | ZAPSIGN_WEBHOOK_HEADER; default X-ZapSign-Secret |
| Prestadores | /api/webhooks/zapsign/prestadores/ | ZAPSIGN_PRESTADORES_WEBHOOK_SECRET | ZAPSIGN_PRESTADORES_WEBHOOK_HEADER; default X-ZapSign-Secret |

No hay fallback de secreto entre receptores. Configuracion propia ausente/invalida:
503. Header ausente/incorrecto: 403, antes de JSON/auditoria/HTTP. Secretos iguales
en ambos receptores: 503 en ambos; deben ser independientes. Permanece compare_digest.
Prestadores sin configurar NO desactiva Libranza correctamente configurada.

## Propiedad, estados e idempotencia

1. Autenticar con configuracion elegida por la RUTA, nunca por el payload.
2. Validar referencia y evento, guardar solo hash y catalogos seguros. La auditoria
   nueva agrega receptor, producto cuando sea conocido y resultado en su JSON
   existente; no requiere columnas ni transforma auditorias anteriores.
3. Propiedad desconocida: 200 document_not_found_ignored. Producto ajeno:
   200 product_mismatch_ignored. Sin GET remoto, cambios de firma ni financieros.
4. Para el propietario: mantener el cliente existente, TLS, timeout y prohibicion
   de redirects/retries. Validar documento, contexto, ambiente y todos los firmantes.
5. Revalidar relaciones/propiedad despues del GET, bajo los locks del hotfix.
   Cambio de contexto: 409. Mantener orden formalizacion -> credito -> pagare;
   para legacy, credito -> pagare. HTTP fuera de los locks.
6. Terminales no retroceden. Duplicados pueden generar auditorias tecnicas distintas,
   pero no una segunda transicion ni desembolso. No se promete exactly-once del
   transporte ni un solo GET ante dos callbacks simultaneos.
7. Mantener el servicio de transicion actual de cada producto y la copia del PDF
   despues del commit. No alterar politica, scoring, ingresos, firma del titular
   ni los gates de originacion/desembolso. Pruebas de postfirma solo sinteticas.

## Filtros del proveedor y documentos actuales

La [documentacion oficial de webhooks](https://docs.zapsign.com.br/webhooks/criar-webhook)
describe filtro por template desde el panel, doc_token opcional para un documento
y headers por webhook. No acredita un filtro por producto/external_id/metadata
para los PDF que subimos. No inventar parametros template_id en ese POST.

El cliente actual crea por POST /api/v1/docs/ con url_pdf; Prestadores envia su
PDF generado y external_id, no usa creacion desde template ZapSign. Una plantilla
legal local/version_plantilla NO es un template registrado en ZapSign. Por ello,
no se puede declarar que el filtro de template se aplica al flujo actual.
[Upload PDF oficial](https://docs.zapsign.com.br/documentos/criar-documento).

El filtrado, si se acredita posteriormente, optimiza entregas; no reemplaza la
clasificacion local. No introducir doc_token por credito: requeriria un hook
por documento y no constituye la suscripcion de producto solicitada.

## Dos webhooks Prestadores: preparados, NO creados

Crear, SOLO tras autorizacion, dos registros mediante POST oficial
https://api.zapsign.com.br/api/v1/user/company/webhook/:

- URL: https://aprobado.com.co/api/webhooks/zapsign/prestadores/
- type: doc_signed para uno y doc_refused para el otro.
- headers: X-ZapSign-Secret, valor obtenido en memoria desde custodia EXCLUSIVA
  de Prestadores, diferente del secreto Libranza y del token API.
- Sin filtro de template supuesto, sin imprimir body/Authorization/respuesta raw.
- Capturar los IDs de configuracion retornados por cada POST exitoso y registrar
  evento/URL/ID de forma sanitizada. No inventar IDs ni reutilizar los de Sandbox.
- Respuesta incierta/timeout: detener, conciliar con proveedor, NO repetir POST
  automaticamente. No crear duplicados para resolver ausencia de lectura documentada.

Los dos hooks principales existentes conservan su URL y futuros headers propios
de Libranza/legacy. Los cuatro educativos NO se modifican. Los dos hooks nuevos
podrian recibir todos los documentos de la cuenta; su servicio ignorara los ajenos.

## Despliegue coordinado pendiente de autorizacion

1. Aprobar nuevo candidato B con SHA e inventario revisados; no publicar f1571e5
   pretendiendo que incluye esta separacion. Confirmar IDs de configuracion de
   los dos hooks existentes y entregas pendientes/reintentos con el proveedor.
2. Coordinar pausa de nuevos envios en admin, pagadores y formalizacion Prestadores.
   No hay flag global de pausa acreditado. No vaciar API token ni modificar estados.
   Las firmas ya enviadas aun pueden completarse: conciliar y controlar la ventana.
3. Respaldo inmediato protegido de codigo 9916be1, configuracion y BD actual;
   restaurabilidad con procedimiento previo. Conservar 0051 y respaldos anteriores.
4. Tras confirmar reintentos/reenvio, puerta temporal 503 en las DOS rutas exactas
   y hosts pertinentes. Drenar peticiones anteriores. No 200 ficticio, ni modificar
   rutas Sandbox/educativas. Sin mecanismo de recuperacion acreditado: STOP.
5. Provisionar secreto Prestadores independiente; mantener custodia Libranza ya
   preparada inactiva. Configurar SOLO dos headers existentes y crear SOLO dos
   registros nuevos con IDs de respuesta. No tocar cuatro hooks educativos.
6. Actualizacion fast-forward al NUEVO SHA B aprobado, configuracion protegida de
   cuatro variables y reinicio solo Gunicorn. Sin migrate/collectstatic. El motor
   financiero y worker no cambian. Secretos no se transfieren mediante chat/argv/logs.
7. Con puerta 503 aun cerrada, pruebas por loopback de ambas rutas: 403 sin header,
   403 con secreto ajeno/incorrecto; comprobar configuracion y healthchecks.
8. Abrir rutas solo con autenticacion lista. Acreditar header mediante entrega
   productiva legitima/reentrega oficial autorizada, no callback fabricado ni firma
   por terceros. Un 403 o el Sandbox compartido anterior no certifica las nuevas rutas.
9. Conciliar pendientes por propiedad documental; no editar estados financieros.
   La documentacion ofrece reprocess-doc con send_webhook=true/resign_doc=false:
   [reenvio oficial](https://docs.zapsign.com.br/webhooks/reprocessamento-de-documentos-e-webhooks).
   Confirmar con soporte alcance para refusals, headers actuales y destinatarios
   del replay antes de ejecutarlo. No repetir firmas ni regenerar PDFs firmados.
10. Si hubo un callback antes de persistir la referencia de envio, el unknown 200
    no acredita cierre: conciliar con el token/hash YA vinculado y reenvio oficial.
    Al terminar, reanudar envios y comprobar estados, auditorias y ausencia de doble
    efecto. No afirmar que inspeccionar solo la BD elimina reintentos del proveedor.

Contingencia: puerta 503 y pausa, preservar auditorias y secretos, correccion hacia
adelante. No volver a exponer 9916be1 sin autenticar ni restaurar BD para deshacer
firmas validas. Si el proveedor no garantiza recuperar entregas, no abrir la ventana.

## Confirmaciones concretas para soporte

- IDs de CONFIGURACION de los dos hooks principales existentes, no IDs de entrega.
- Para PDF upload sin template: existe filtro oficial por producto/carpeta/metadata?
  Si existe, esquema y alcance documentados; si no, asumir broadcast y propiedad local.
- Reintentos reales y entregas pendientes; reenvio firmado/rechazado, destinatarios
  alcanzados y uso del header actualizado. No asumir que reprocess-doc reenvia refusal.
- Si se exige aislamiento de recepcion de PII, opciones de cuentas/organizaciones
  y acceso a documentos historicos, antes de migrar credenciales o documentos.

Estas respuestas no bloquean el desarrollo local de B. Si se acepta broadcast,
el filtro por producto no es requisito tecnico para impedir transiciones cruzadas.

## Validacion local

- Regresion integrada inicial: 45/45 PostgreSQL PASS, cero fallos/omisiones, 92.392 s.
  Incluye Libranza/legacy, Prestadores, postfirma sintetica y tres concurrencias reales.
- Extension intermedia: 17/17 PostgreSQL PASS, cero fallos/omisiones, 46.118 s.
- Gate FINAL del codigo definitivo: 50/50 PostgreSQL PASS, cero errores/fallos/
  omisiones, 107.941 s. Cuatro concurrencias reales: duplicados Libranza, duplicados
  Prestadores y eventos cruzados concurrentes de ambos productos. Incluye colision
  de referencias y token raw opcional contradictorio. No sumar corridas repetidas
  como pruebas distintas: el gate final contiene cincuenta casos.
- Docker postgres:16-alpine, bind exclusivo 127.0.0.1, puerto dinamico, tmpfs propio;
  NAME zapsign_rutas_bootstrap y TEST.NAME test_zapsign_rutas_aisladas distintos.
  HTTP, SMTP y Celery bloqueados; sin .env, datos personales ni conexiones productivas.
  Puerto de la corrida final: 54254. BD de test destruida y contenedor propio
  eliminado al finalizar, sin conservar volumen ni credenciales temporales.
- Primer harness cargo settings base prematuramente: sus resultados SQLite/fallos
  de fixture NO cuentan como validacion PostgreSQL. Se corrigio solo el harness,
  con verificacion efectiva fail-closed de ENGINE/HOST/PORT/NAME/TEST.NAME antes de tests.
- La extraccion inicial de propiedad exigia un token raw que Prestadores no conserva.
  Se corrigio para usar hash y relaciones existentes; no se agregaron datos historicos.
- check sin errores; makemigrations check/dry-run sin cambios. W001 antes de tests
  corresponde exclusivamente a BD bootstrap VACIA, no a produccion. Check del
  esquema de test completo: sin incidencias. No silenciar controles ni ejecutar migrate productivo.
- Frontera educativa: pruebas de documentos ajenos ignorados en ambas rutas/eventos,
  sin GET ni transiciones; receptor externo y webhooks sin modificaciones. No equivale
  a un nuevo E2E de la aplicacion educativa independiente ni de callbacks reales.
- Sintaxis AST de ocho archivos Python y git diff --check: PASS. Sin nuevos modelos,
  migraciones o dependencias. Candidato compartido original y worktrees previos
  preservados. En el cierre de candidato solo se amplia este runbook: no cambia
  el codigo cubierto por las 50 pruebas aceptadas, ni se repite su suite.

## Inventario incremental respecto a f1571e5

- aprobado_web/settings.py
- aprobado_web/urls_common.py
- gestion_creditos/services/zapsign_webhook.py
- gestion_creditos/views/integrations.py
- gestion_creditos/tests/test_zapsign_webhook_seguro.py
- gestion_creditos/tests/test_zapsign_receptores.py (nuevo)
- contractors/tests/test_webhook_firma_seguro.py
- contractors/tests/test_aprobacion_interna_prestador.py
- docs/ZAPSIGN_RECEPTORES_SEPARADOS_2026_10_25.md (nuevo)

La union con los ocho archivos del hotfix previo da el futuro paquete sobre
9916be1. No mezclar roadmap, captura documental u otros worktrees. Ningun modelo,
migracion, dependencia, servicio financiero ni expediente operativo cambia.

## Comandos de activacion futura: NO ejecutados

La autorizacion actual solo permite cerrar el commit LOCAL. Los bloques siguientes
son para la siguiente ventana expresamente autorizada; no ejecutar en bloque
ignorando las paradas de coordinacion. No se promete perdida cero sin conciliar
las entregas pendientes y su recuperabilidad con ZapSign.

### Publicacion desde el worktree B limpio

PowerShell local. RELEASE_SHA debe ser el SHA completo entregado con este cierre,
no un prefijo, el candidato compartido ni el HEAD de otro worktree.

```powershell
$base = '9916be10f405d7db90f495c966e4c6232b81b579'
$hotfix = 'f1571e5d5c0e3f7b680104a2995f5d3e1e8ab275'
$release = $env:RELEASE_SHA
if ($release -notmatch '^[0-9a-f]{40}$') { throw 'Falta SHA completo aprobado' }
if ((git branch --show-current) -ne 'candidate/zapsign-receptores-20261025') { throw 'Rama incorrecta' }
if ((git rev-parse HEAD) -ne $release -or (git rev-parse 'HEAD^1') -ne $hotfix -or
    (git rev-parse 'HEAD~2') -ne $base) { throw 'Ascendencia inesperada' }
if (git status --porcelain) { throw 'Worktree candidato sucio' }
$ref = 'refs/heads/release/prestadores-prod-2026-08-28'
$remote = git ls-remote --exit-code origin $ref
if ($LASTEXITCODE -ne 0 -or -not $remote -or ($remote -split '\s+')[0] -ne $base) {
    throw 'Remoto distinto de la base; detener sin sobrescribir'
}
git push origin "${release}:$ref"
if ($LASTEXITCODE -ne 0) { throw 'Push fallido; no desplegar' }
$remote = git ls-remote --exit-code origin $ref
if ($LASTEXITCODE -ne 0 -or -not $remote -or ($remote -split '\s+')[0] -ne $release) {
    throw 'Publicacion no confirmada'
}
```

### Custodia inactiva de los dos secretos

En VPS, SOLO con autorizacion de provisionamiento. No cambia .env, unidad,
webhooks ni procesos. Reutiliza el secreto Libranza ya custodiado y genera otro
para Prestadores sin imprimirlo. Archivo existente: validar, nunca sobrescribir.
El archivo compuesto queda INACTIVO hasta la fase de configuracion/reinicio.

```bash
cd /root/aprobado_web
PYTHONDONTWRITEBYTECODE=1 venv/bin/python - <<'PY'
import os
from pathlib import Path
import secrets
import stat
from dotenv import dotenv_values

assert os.geteuid() == 0, 'Requiere root'
root = Path('/etc/aprobado-zapsign-production')
meta = root.lstat()
assert stat.S_ISDIR(meta.st_mode) and not root.is_symlink()
assert meta.st_uid == 0 and stat.S_IMODE(meta.st_mode) == 0o700

def leer(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        meta = os.fstat(stream.fileno())
        assert stat.S_ISREG(meta.st_mode) and meta.st_uid == 0
        assert stat.S_IMODE(meta.st_mode) == 0o600
        return dict(dotenv_values(stream=stream))

def crear(path, values):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(''.join(key + '=' + value + '\n' for key, value in values.items()))
        stream.flush()
        os.fsync(stream.fileno())

legacy = leer(root / 'webhook.env.pending')
assert set(legacy) == {'ZAPSIGN_WEBHOOK_SECRET', 'ZAPSIGN_WEBHOOK_HEADER'}
assert legacy['ZAPSIGN_WEBHOOK_HEADER'] == 'X-ZapSign-Secret'
def valido(value):
    return isinstance(value, str) and 32 <= len(value) <= 512 and all(
        c.isascii() and (c.isalnum() or c in '-_') for c in value)
assert valido(legacy['ZAPSIGN_WEBHOOK_SECRET']), 'Custodia Libranza invalida'
path = root / 'prestadores.env.pending'
if not path.exists() and not path.is_symlink():
    crear(path, {'ZAPSIGN_PRESTADORES_WEBHOOK_SECRET': secrets.token_urlsafe(48),
                 'ZAPSIGN_PRESTADORES_WEBHOOK_HEADER': 'X-ZapSign-Secret'})
prestadores = leer(path)
assert set(prestadores) == {'ZAPSIGN_PRESTADORES_WEBHOOK_SECRET', 'ZAPSIGN_PRESTADORES_WEBHOOK_HEADER'}
assert prestadores['ZAPSIGN_PRESTADORES_WEBHOOK_HEADER'] == 'X-ZapSign-Secret'
assert valido(prestadores['ZAPSIGN_PRESTADORES_WEBHOOK_SECRET'])
assert prestadores['ZAPSIGN_PRESTADORES_WEBHOOK_SECRET'] != legacy['ZAPSIGN_WEBHOOK_SECRET']
values = {**legacy, **prestadores}
combined = root / 'webhooks-b.env.pending'
if not combined.exists() and not combined.is_symlink():
    crear(combined, values)
assert leer(combined) == values, 'Custodia compuesta distinta; detener'
print('CUSTODIA_B=PREPARADA_INACTIVA')
PY
```

Parada obligatoria: coordinar pausa y entregas recuperables. Respaldo inmediato
con el procedimiento protegido/restaurable ya acreditado para los releases 04/05:
codigo 9916be1, dump PostgreSQL actual, configuracion Gunicorn/Nginx y custodia
protegida. Restauracion en cluster privado, nunca sobre produccion. Conservar
evidencia de restauracion y el corte. Un pg_restore --list NO sustituye esa prueba.
No abrir la ventana si falla el respaldo, la pausa o la recuperabilidad.

### Entrada y proveedor durante la ventana

En el vhost principal y cualquier alias que alcance este Django, aplicar bajo
autorizacion estas ubicaciones EXACTAS; no sustituir location / ni tocar vhosts
educativos o el prefijo Sandbox. Registrar el cambio para retirarlo despues.

```nginx
location = /api/webhooks/zapsign/ {
    add_header Retry-After 120 always;
    return 503;
}
location = /api/webhooks/zapsign/prestadores/ {
    add_header Retry-After 120 always;
    return 503;
}
```

Comprobar `nginx -t` y solo entonces `systemctl reload nginx`; drenar solicitudes
anteriores. Los firmantes con enlaces existentes aun pueden completar acciones.
Configurar X-ZapSign-Secret en los DOS IDs Libranza confirmados mediante
POST https://api.zapsign.com.br/api/v1/user/company/webhook/header/, preservando
otros headers necesarios. Crear los dos hooks Prestadores descritos arriba:
POST https://api.zapsign.com.br/api/v1/user/company/webhook/.
Payload y Authorization se construyen en memoria desde custodia; no usar valores
en argumentos curl, historial, chat ni logs. Conservar IDs reales de cada respuesta.
Timeout o resultado incierto: STOP y conciliacion, sin repetir POST de creacion.
Estos pasos del proveedor son separados del deploy; NO estan automatizados por Git.

### Fast-forward y configuracion del receptor

Antes del bloque: proveedor coordinado, pausa, respaldo restaurado y puerta 503
verificados. Establecer RELEASE_SHA al SHA B completo autorizado. Comprobar todos
los archivos sin seguimiento: solo se admiten los tres respaldos .env.bak_*
preexistentes acreditados; ante cualquier otro cambio, detenerse sin eliminarlo.

```bash
set -euo pipefail
umask 077
cd /root/aprobado_web
: "${RELEASE_SHA:?SHA B completo aprobado y publicado}"
[[ "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(git branch --show-current)" = release/prestadores-prod-2026-08-28
test "$(git rev-parse HEAD)" = 9916be10f405d7db90f495c966e4c6232b81b579
git diff --quiet HEAD --
git fetch origin refs/heads/release/prestadores-prod-2026-08-28
test "$(git rev-parse FETCH_HEAD)" = "$RELEASE_SHA"
test "$(git rev-parse "$RELEASE_SHA^1")" = f1571e5d5c0e3f7b680104a2995f5d3e1e8ab275
test "$(git rev-parse "$RELEASE_SHA~2")" = 9916be10f405d7db90f495c966e4c6232b81b579
git merge --ff-only "$RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
PYTHONDONTWRITEBYTECODE=1 venv/bin/python manage.py check
```

Instalar bajo control de cambios un drop-in Gunicorn, sin editar .env ni
sobrescribir otros drop-ins. Su contenido, SIN valores secretos:

```ini
[Service]
EnvironmentFile=/etc/aprobado-zapsign-production/webhooks-b.env.pending
```

Solo despues de verificar custodia/configuracion del proveedor y las cuatro
variables efectivas: `systemctl daemon-reload`, `systemctl restart gunicorn.service`
y `systemctl is-active --quiet gunicorn.service`. Verificar en memoria que los
secretos cargados coinciden con custodia y son distintos; salida exclusivamente
booleana. No reiniciar Celery/Redis ni ejecutar migrate/collectstatic.

### Pruebas negativas y apertura

Por loopback, con la puerta publica 503 aun aplicada, ambas rutas deben devolver
403 sin header y con header incorrecto. No usar tokens o documentos operativos.

```bash
set -euo pipefail
for path in /api/webhooks/zapsign/ /api/webhooks/zapsign/prestadores/; do
    test "$(curl --silent --show-error --max-time 15 --output /dev/null \
      --write-out '%{http_code}' --request POST --header 'Host: aprobado.com.co' \
      --header 'X-Forwarded-Proto: https' --header 'Content-Type: application/json' \
      --data '{}' "http://127.0.0.1:8000$path")" = 403
    test "$(curl --silent --show-error --max-time 15 --output /dev/null \
      --write-out '%{http_code}' --request POST --header 'Host: aprobado.com.co' \
      --header 'X-Forwarded-Proto: https' --header 'Content-Type: application/json' \
      --header 'X-ZapSign-Secret: deliberately-invalid-deployment-probe' \
      --data '{}' "http://127.0.0.1:8000$path")" = 403
done
test "$(curl --silent --show-error --max-time 15 --output /dev/null \
  --write-out '%{http_code}' https://aprobado.com.co/libranza/)" = 200
```

Si falla algo: mantener puerta 503 y pausa; correccion hacia adelante. Si pasa:
retirar exclusivamente las ubicaciones temporales, validar/reload Nginx y repetir
los dos rechazos por HTTPS en las URLs publicas, sin -L. Despues acreditar una
entrega legitima o reentrega oficial autorizada para cada receptor y conciliar
pendientes. El 403 no demuestra que ZapSign envie el header correcto.
Eventos cruzados: 200 ignorado, sin GET ni transicion; duplicados propios: terminal
sin nueva transicion; errores de verificacion: fail-closed y conciliacion, no SQL
manual. Solo tras acreditar los controles reanudar nuevos envios.

## Estado del cierre local

Las cuatro dependencias del proveedor siguen EXTERNAS y no impiden el commit:
IDs Libranza, conteos de entregas recuperables, headers actualizados en reintentos
y alcance/destinatarios del reenvio. Los webhooks Prestadores estan preparados,
no creados. Secreto Libranza preparado inactivo; Prestadores pendiente de ejecutar
el provisionamiento autorizado de la ventana. No se genero ni activo en este cierre.
