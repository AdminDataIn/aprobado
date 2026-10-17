# Release quirurgico de ingreso evaluable - 17/10/2026

Base: `4c536c148e946e13aefdc9c0012fdeccdb048123`.
Rama: `release/prestadores-prod-2026-08-28`.
Preparacion LOCAL. Ningun comando de este documento se ejecuto en produccion.
El SHA candidato se entrega en el cierre Git; usarlo explicitamente en RELEASE_SHA.

```text
RELEASE_GATE = PASS
MIGRACION_0022 = PASS
HISTORICOS_INMUTABLES = PASS
PREFLIGHT_RELEASE = PASS
REGRESION_FINANCIERA = PASS
UAT03_POST_DEPLOY_READY = PASS
```

PASS tecnico local, NO preflight operativo del VPS ni autorizacion de consumo.
Pendientes operativos: autorizar publicacion/despliegue, publicar/adoptar la nueva
version para 10/310/8, repetir preflight instalado y autorizar individualmente UAT.
No se afirma E2E real aprobado ni semantica contractual HC14 confirmada.

## Alcance y contrato

- 0022: AddField JSON opcional/default Python y SQL {}. Sin reglas retroactivas, nuevas tablas,
  transformaciones de auditorias/snapshots o cambios en solicitudes/hashes.
- Nueva politica `prestadores-score-prod-v2-ingreso`, publicada INACTIVA, clonada
  de la vigente. Mismos pesos, bandas, tasas, cargos, limites y version_score.
- Solo cambia identidad/vigencia explicitas y politica_ingreso_evaluable:
  ingreso-evaluable-v1, descuento 25.00%, semantica MiDecisor NO_CONFIRMADA.
- Adopcion individual auditada mediante version_politica_simulacion y Timeline
  existente. No requiere ingreso manual para fuentes. No recalcula historicos.
- Estimaciones no habilitan gates de aprobacion, firma u originacion sin evidencia
  documental vigente y reevaluacion del contexto. El P0 de oferta sigue obligatorio.
- HC14/TX05 oficial fail-closed, shadow solo diagnostico en memoria. HDC sin
  informacion no representa deuda cero. Fingerprints no cambian.
- Preflight exige release local/tracking/remoto exactos, descendencia de base,
  core e integraciones, esquema/migracion, publicacion/adopcion y elegibilidad.
  No existe default de SHA, bypass de core o PASS por prefijo/variable de bandera.

## Pruebas

PostgreSQL 16 oficial (`postgres:16-alpine`), contenedor propio desechable,
binding 127.0.0.1, tmpfs sin volumen persistente, usuario temporal exclusivo.
NAME `uat03_bootstrap`; TEST.NAME `test_uat03_aislada`. Sin .env, datos operativos
ni red de proveedores: HTTP y Celery interceptados, email locmem.

- Integrada final: **364/364 PASS**, 0 FAIL, 0 ERROR, 0 SKIP, 777.961s.
  Ingreso, publicacion/adopcion, oferta, gates, E2E, scoring, HDC/MiDecisor,
  shadow, consentimiento/reutilizacion, idempotencia y concurrencia real.
- Revalidacion del contenido final: **18/18 PASS**, 0 FAIL/ERROR/SKIP, 34.830s:
  9 publicacion/adopcion, 5 upgrade/historicos/configuracion y 4 reservas reales
  concurrentes (`test_datacredito_reservas_hardening.ReservaConcurrenciaPostgresTest`).
  Incluye INSERT desde el modelo anterior con default SQL {}, y serializacion
  de publicacion con locks politica -> financiera -> bandas. Casos parcialmente
  superpuestos a los 364 anteriores; no sumar como pruebas unicas.
- Upgrade real 0021 -> 0022 con politicas/solicitudes/auditorias/snapshots previos
  sinteticos: PASS. Hashes/contextos legacy intactos; acceso y validacion posibles.
- Los dos casos del gate inicialmente fallidos ahora PASS: historicos sin corte
  implicito y preflight bloqueando core anterior. Nueva version/adopcion necesaria.
- `check`: PASS; `makemigrations --check --dry-run`: sin cambios adicionales;
  `migrate --plan`: solo 0022 antes del upgrade, sin pendientes tras aplicarla.
- `git diff --check`: PASS. Ninguna consulta real a Experian o PostgreSQL operativo.
  Todos los contenedores propios eliminados tras cada corrida.

La primera ampliada tuvo 336/339 PASS y tres fixtures incompatibles con el nuevo
corte explicito: dos desactivaban una politica v2 ya inactiva en vez de probar
el control legacy; otro adoptaba v2 pese a fuentes en error incompatible.
Se mantuvieron los controles, se corrigieron los fixtures y se repitio la matriz
completa (364 casos incluyendo publicacion y diagnostico). No se debilito politica.

Los tres fallos UI legacy ya comprobados en base/candidato siguen independientes;
no se modificaron sus vistas/templates/tests en este release.

Inventario exacto del candidato: **40 archivos**, en
`PRESTADORES_INGRESO_RELEASE_MANIFEST.json`. Incluye la conservacion de la politica
adoptada al cambiar legitimamente condiciones de simulacion; el replay identico
no cambia fechas, version o eventos. `descubrir_uat03.py` se incluye como dependencia
directa de las pruebas read-only, no como un nuevo mecanismo de evaluacion.
ROADMAP, cambios de captura y sus borradores ajenos quedan fuera del staging.

## Despliegue controlado: NO ejecutar sin autorizacion

Requisitos: gate verde, commit revisado y luego publicacion autorizada; respaldo
de codigo/PostgreSQL generado y restauracion comprobada en PostgreSQL aislado.
Preservar archivos locales del VPS y backups .env existentes. No imprimir entornos.
El procedimiento de respaldo protegido de codigo y pg_dump esta documentado en
PRESTADORES_INGRESO_RELEASE_GATE.md (seccion respaldo); permanece valido para
respaldo, NO sus instrucciones antiguas de politica/preflight.

Durante el intervalo codigo/esquema impedir nuevas evaluaciones y drenar jobs en
curso segun ventana autorizada. No habilitar DataCredito/autoevaluacion. Requiere
recargar Gunicorn y worker general que importan el core; no Redis, OCR, Nginx ni
Beat por defecto. Confirmar nombres reales de unidades antes de autorizar reinicios.

```bash
set -euo pipefail
cd /root/aprobado_web
export PYTHONDONTWRITEBYTECODE=1
: "${RELEASE_SHA:?SHA completo del candidato aprobado}"
: "${RESPALDO_VALIDADO:?Confirmar respaldo y restauracion aislada}"
test "$(git branch --show-current)" = release/prestadores-prod-2026-08-28
test "$(git rev-parse HEAD)" = 4c536c148e946e13aefdc9c0012fdeccdb048123
git diff --quiet HEAD --
git fetch origin release/prestadores-prod-2026-08-28
test "$(git rev-parse refs/remotes/origin/release/prestadores-prod-2026-08-28)" = "$RELEASE_SHA"
git merge-base --is-ancestor HEAD "$RELEASE_SHA"
systemctl show --property=Id --property=LoadState --property=ActiveState gunicorn.service celery.service
# Confirmar ambas unidades y ventana autorizada antes de interrumpir servicio.
# Detener nuevas evaluaciones, drenar trabajos; indisponibilidad breve de Django.
systemctl stop celery.service gunicorn.service
git merge --ff-only "$RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
venv/bin/python manage.py migrate contractors 0022 --plan
# Detenerse si el plan incluye algo distinto del AddField autorizado.
venv/bin/python manage.py migrate contractors 0022 --noinput
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/python manage.py migrate --plan
systemctl start gunicorn.service celery.service
systemctl is-active --quiet gunicorn.service celery.service
```

Publicacion y adopcion son operaciones funcionales adicionales, NO parte de la
migracion. Requieren actor autorizado, vigencia y motivo aprobados; no consumo.
El dry-run no escribe y se identifica como NO_VALIDADO, no simula un PASS.
Primero comprobar en lectura la politica de origen y la pertenencia de 10/310/8.

```bash
: "${ORIGEN_ID:?ID politica legacy vigente comprobado}"
: "${ACTOR_ID:?ID actor con permisos efectivos comprobados}"
: "${VIGENTE_DESDE:?Fecha de inicio aprobada YYYY-MM-DD}"
venv/bin/python manage.py gestionar_politica_ingreso publicar \
  --origen-id "$ORIGEN_ID" --actor-id "$ACTOR_ID" \
  --vigente-desde "$VIGENTE_DESDE" --descuento 25.00 \
  --motivo 'Publicacion autorizada ingreso evaluable Prestadores v2' --dry-run
# SOLO con autorizacion de publicacion:
venv/bin/python manage.py gestionar_politica_ingreso publicar \
  --origen-id "$ORIGEN_ID" --actor-id "$ACTOR_ID" \
  --vigente-desde "$VIGENTE_DESDE" --descuento 25.00 \
  --motivo 'Publicacion autorizada ingreso evaluable Prestadores v2' --aplicar
# Tomar el politica_id de PUBLICADA_INACTIVA; no adivinarlo:
: "${POLITICA_ID:?ID de la nueva version publicada}"
venv/bin/python manage.py gestionar_politica_ingreso adoptar \
  --solicitud-id 10 --politica-id "$POLITICA_ID" --actor-id "$ACTOR_ID" \
  --motivo 'Adopcion autorizada UAT-03 sin evaluaciones previas' --dry-run
# SOLO tras confirmar 10/310/8 y autorizar adopcion individual:
venv/bin/python manage.py gestionar_politica_ingreso adoptar \
  --solicitud-id 10 --politica-id "$POLITICA_ID" --actor-id "$ACTOR_ID" \
  --motivo 'Adopcion autorizada UAT-03 sin evaluaciones previas' --aplicar
```

No llamar activar_politica_score_prestador para esta version: se rechaza activacion
global deliberadamente. La politica anterior queda intacta/activa; la nueva esta
habilitada exclusivamente para solicitudes con adopcion auditada vigente.

Postchecks read-only (no datos personales):

```bash
venv/bin/python manage.py shell <<'PY'
import json
from django.conf import settings
from contractors.models import ContractorApplication, ConfiguracionScorePrestador
flags = {n: bool(getattr(settings, n, False)) for n in (
    'DATACREDITO_ENABLED', 'DATACREDITO_REAL_ENABLED', 'PRESTADORES_AUTO_EVALUACION_ENABLED')}
assert not any(flags.values()), 'flags_no_desactivados'
s = ContractorApplication.objects.get(pk=10)
assert s.usuario_id == 310
assert not s.auditorias_predecision.exists() and not s.evaluacion_jobs.exists()
assert not s.aprobaciones_internas.exists() and not s.aprobaciones_pagador.exists()
print(json.dumps({'flags': flags, 'solicitud_id': s.pk,
    'version_politica': s.version_politica_simulacion,
    'politicas': list(ConfiguracionScorePrestador.objects.values(
        'id', 'version_politica', 'activa', 'politica_ingreso_evaluable'))}))
PY
curl --fail --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  https://contratistas.aprobado.com.co/accounts/login/
```

Iniciar solo unidades aprobadas despues de esquema/validacion; verificar tambien
flags efectivos de los procesos, no solo del shell. HTTPS/TLS sin -k, login 200 y
procesos sanos. No collectstatic: este paquete no cambia assets/templates.

## Preflight operativo instalado, sin consumo

Verificar hashes de los helpers historicos /tmp segun la guia UAT-03, sin editarlos.
El runner nuevo se ejecuta DESDE el checkout, no desde una copia /tmp desactualizada.

```bash
(cd /tmp && sha256sum --check <<'SHA'
ef318be20a514b9524a9ed2351b32a6518744ce057e78fff6d3ab780eeab1173  aprobado_uat_perfiles.py
b27f49170c0379c7ec62ebc21795b88e5c0cbe0e34993806c516e436a5a47627  aprobado_uat4_controlada.py
SHA
)
APROBADO_UAT_RELEASE_SHA="$RELEASE_SHA" \
APROBADO_UAT_HELPERS_DIR=/tmp APROBADO_UAT_MODO=preflight \
APROBADO_UAT_SOLICITUD_ID=10 APROBADO_UAT_PROPIETARIO_ID=310 \
APROBADO_UAT_ACTOR_ID="$ACTOR_ID" \
venv/bin/python manage.py shell -c \
 'import runpy; runpy.run_module("diagnosticos.aprobado_uat03_controlada", run_name="__main__")'
```

Desde Windows el launcher `preflight_uat03_actual.ps1 -Modo OPERATIVO -ReleaseSha
<SHA> -SolicitudId 10 -PropietarioId 310 -ActorId <ID>` solo importa el modulo
instalado. PREPARACION nunca declara despliegue ni consentimiento acreditados.

PASS significa elegibilidad de fuentes, NO autorizacion de consumo. Para UAT-03
se requiere autorizacion individual posterior: maximo un OAuth/producto por fuente,
sin FORCE/retries/redirects, HTTP fuera de atomic, shadow en memoria y diagnostico
con rollback/red bloqueada. No repetir #4/#9. No aprobar ni originar automaticamente.

## Reversion compatible

Conservar columna 0022 y todos los registros. El codigo anterior ignora el campo
aditivo. Su default SQL {} permite tambien INSERTs desde el modelo anterior sin
esa columna; no es un default financiero. NO ejecutar migrate contractors 0021
rutinariamente: elimina configuracion.
Revertir el commit candidato mediante commit de reversion revisado y despliegue
autorizado, nunca reset --hard ni sobreescribir worktree sucio/backups.
Antes, drenar jobs y suspender avance de solicitudes que adoptaron v2: el codigo
anterior no conoce esa politica. Conservar su adopcion/auditoria sin volverlas a
v1 silenciosamente. Las solicitudes legacy no se modificaron y conservan contexto.
Si se necesita revertir una adopcion, requiere operacion funcional independiente
auditada; no hay reversion masiva ni borrado de eventos.
Restaurar dump completo solo en recuperacion de desastre autorizada, considerando
escrituras posteriores. Rollback de codigo NO equivale a rollback de datos.
