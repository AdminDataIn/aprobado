# APROBADO-05: Fenalco Meta y ecosistema institucional

P2. Implementado LOCAL el 23/10/2026; no publicado. ID comprobado libre.

El responsable autorizo el uso publico del logo encontrado en Descargas. Su
respuesta no especifica afiliacion ni alianza juridica; no se afirma ninguna.
Titulo neutral: **Conoce nuestro ecosistema**. Se conserva el anchor existente
`quienes-nos-respaldan` para no romper enlaces externos.

Cinco organizaciones previas conservan orden, logos, descripciones y enlaces
(no se crean enlaces nuevos). Fenalco Meta se agrega al final con el PNG original
autorizado, sin redibujarlo y con descripcion literal solicitada:

> Seccional de la Federación Nacional de Comerciantes Empresarios en el departamento del Meta, dedicada a representar, defender y fortalecer los sectores del comercio, los servicios y el turismo en la región.

Grid de tres columnas desktop, dos tablet y una movil; logo cuadrado contenido,
sin deformacion, texto alternativo y comprobacion de desbordamiento. No altera
tasas, simuladores, formularios, permisos ni componentes financieros.

## Candidato visual independiente

```text
usuarios/views.py
templates/libranza/libranza_landing.html
static/images/respaldos/fenalco-meta.png
gestion_creditos/tests/test_libranza_landing_marketing.py
gestion_creditos/tests/browser/test_ecosistema.cjs
docs/APROBADO_05_ECOSISTEMA.md
```

Pruebas Django: contexto, orden, texto alternativo, descripcion y ausencia de
afirmacion de alianza formal. Navegador: template Django real renderizado sin BD,
assets locales, peticiones externas bloqueadas, imagenes cargadas y ausencia de
overflow a 320, 390, 768 y 1440 px en Chromium y WebKit. No es certificacion de
iPhone fisico ni del flujo financiero.

Ejecucion Node: `node --test gestion_creditos/tests/browser/test_ecosistema.cjs`.
Requiere Playwright disponible mediante NODE_PATH; `ECOSYSTEM_WEBKIT=1` selecciona
WebKit y PLAYWRIGHT_BROWSERS_PATH su instalacion local. No se agregan dependencias
al proyecto, no requiere BD ni servidor externo.

Validacion del 23/10: 7/7 Django PostgreSQL PASS (incluidas en la suite focal
74/74), 4/4 Chromium y 4/4 WebKit. Capturas inspeccionadas a 390 y 1440 px,
logos cargados y sin desbordamientos a 320/390/768/1440 px. `git diff --check` OK.
SHA256 del logo original copiado:
`163a64f10045477feadcaeaac74a7a311b2a75359f60a8fa0121eab7da0fbf8c`.

Sin migraciones en este candidato. Revisar/publicar aparte de APROBADO-04 y del
hotfix de firma. La validacion del 23/10 no hizo commit ni despliegue.

## Gate y despliegue preparado el 24/10 (NO ejecutarlo sin autorizacion)

Candidato en `candidate/aprobado-05-20261024`, padre productivo c20962c. Solo los
seis archivos del inventario. No contiene 5054ac0, roadmap ni APROBADO-04, tampoco
por ancestria. Validar/publicar el SHA exacto, NO el HEAD del checkout principal.

En Windows, DESPUES de aprobacion: comprobar con `git ls-remote` que la rama
release continua en c20962c y publicar el SHA de la rama candidata mediante push
normal explicito. Si avanzo el remoto, detenerse; no force/rebase automaticos.

En VPS, con TARGET_SHA igual al commit aprobado y disponible en origin:

```bash
set -euo pipefail
cd /root/aprobado_web
: "${TARGET_SHA:?Asignar el SHA completo autorizado de APROBADO-05}"
PREVIOUS_SHA=c20962cd34cef1e69c0abfffe42a3c4fc253761c
test "$(git rev-parse HEAD)" = "$PREVIOUS_SHA"
test -z "$(git status --porcelain --untracked-files=no)"
# Conservar los respaldos .env.bak_* y cualquier archivo operativo no rastreado.
umask 077
BACKUP_DIR="/root/backups/aprobado-05-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 "$BACKUP_DIR"
git archive --format=tar "$PREVIOUS_SHA" > "$BACKUP_DIR/codigo-previo.tar"
printf '%s\n' "$PREVIOUS_SHA" > "$BACKUP_DIR/previous-sha.txt"
git fetch origin refs/heads/release/prestadores-prod-2026-08-28
test "$(git rev-parse FETCH_HEAD)" = "$TARGET_SHA"
git merge-base --is-ancestor "$PREVIOUS_SHA" "$TARGET_SHA"
test "$(git diff --name-only "$PREVIOUS_SHA" "$TARGET_SHA" | wc -l)" -eq 6
git merge --ff-only "$TARGET_SHA"
venv/bin/python manage.py check
# Necesario: el PNG es NUEVO y Nginx sirve /var/www/aprobado/staticfiles.
venv/bin/python manage.py collectstatic --noinput
test -f /var/www/aprobado/staticfiles/images/respaldos/fenalco-meta.png
systemctl restart gunicorn.service
systemctl is-active --quiet gunicorn.service
curl --fail --silent --show-error https://aprobado.com.co/libranza/ | grep -q 'Conoce nuestro ecosistema'
curl --fail --silent --show-error --output /dev/null https://aprobado.com.co/static/images/respaldos/fenalco-meta.png
test "$(git rev-parse HEAD)" = "$TARGET_SHA"
```

Antes de actualizar, comparar tambien los nombres exactos con el inventario de
seis archivos, no solo el conteo. Reinicio breve de Gunicorn; no migrar ni tocar
Celery, Redis, DataCredito o Nginx. Validacion visual posterior en movil/desktop.
No se necesita escribir BD ni respaldarla por este cambio exclusivamente visual.

Rollback solo si HEAD sigue en TARGET_SHA y no hay cambios rastreados:
`git restore --source="$PREVIOUS_SHA" -- usuarios/views.py templates/libranza/libranza_landing.html`,
seguido de restart de Gunicorn y healthcheck. Restaura SOLO esos dos archivos;
no usar reset/clean ni borrar respaldos. Queda un rollback local rastreado que
se registra como incidente; el PNG nuevo puede permanecer huerfano sin efecto.
No reutilizar ese working tree sucio para otro despliegue hasta reconciliarlo
explicitamente. No se ejecutaron estos comandos en esta tarea.
