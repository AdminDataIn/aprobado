# Gate de release: ingreso evaluable Prestadores

**HISTORICO DEL GATE 16/10/2026.** Los bloqueos inferiores se reprodujeron y
motivaron la correccion quirurgica del 17/10. Consultar el cierre y procedimiento
vigentes en `PRESTADORES_INGRESO_RELEASE_2026_10_17.md`. No ejecutar los comandos
historicos inferiores como procedimiento vigente ni interpretar sus estados
BLOCKED como resultado del nuevo candidato.

Corte: 16/10/2026. Base local comprobada:
`4c536c148e946e13aefdc9c0012fdeccdb048123`.
Rama: `release/prestadores-prod-2026-08-28`.
No se modificaron servicios/modelos/migracion durante este gate; se agregaron
pruebas de aceptacion, comparacion legacy y este informe. No commit ni staging.

## Decision

```text
RELEASE_GATE = BLOCKED
MIGRACION_0022 = PASS_ESTRUCTURAL / BLOCKED_CORTE_DE_POLITICA
REGRESION_FINANCIERA = BLOCKED_COMPATIBILIDAD_HISTORICA
UAT03_READY = PASS_ELEGIBILIDAD_CANDIDATA / BLOCKED_CONSUMO
COMMIT_CANDIDATO = NO_CREADO
```

No se considera publicable un paquete con dos pruebas nuevas de gate fallidas.
El usuario confirmo que la regla nueva exige una NUEVA version explicita de
politica antes de aplicarse a solicitudes anteriores. No autorizo su activacion.

## Hallazgos comprobados

### 1. Corte de politica no implementado

`0022_configuracionscoreprestador_politica_ingreso_evaluable.py:9` agrega un
JSONField con default `politica_ingreso_default()`. Ese default contiene 25.00%
y `ingreso-evaluable-v1`, y tambien se carga en las politicas ya existentes,
sin cambiar `version_politica`.

`evaluacion_versionado.py:26-32` incluye incondicionalmente ese JSON en la
entrada de evaluacion. Con una politica historica activa, una solicitud intacta
obtiene otro `version_datos` despues del upgrade. `oferta_financiera.py:55-60`
exige ademas los nuevos campos de ingreso en las variables de una auditoria;
las auditorias anteriores no los tienen y no pueden reutilizarse asi.

No se alteran los bytes de las auditorias/snapshots historicos; el problema es
el cambio de interpretacion/reutilizacion bajo la misma version de politica.
La documentacion anterior ya advertia reevaluacion; la decision de este gate
NO acepta ese corte implicito.

Correccion requerida antes del candidato: conservar una semantica legacy
explicita para politicas anteriores, no inyectarles automaticamente el descuento,
y aplicar la nueva regla exclusivamente mediante una nueva version aprobada.
Versionado, capacidad y oferta deben reconocer ese corte de forma consistente.
No renombrar politicas ni reescribir auditorias existentes para superar el test.

### 2. Preflight acepta un core anterior al resolvedor

`diagnosticos/aprobado_uat03_controlada.py:89` permite por defecto el SHA base
4c536c1. Ese commit no contiene `contractors/services/ingreso_evaluable.py`
(comprobado con `git cat-file -e`). El resolvedor se importa en el diagnostico,
despues de la adquisicion de fuentes, no como requisito previo al consumo.

La prueba que simula ese modulo ausente demuestra que el preflight no rechaza
el core incompatible. Por tanto puede permitir una operacion costosa que luego
no pueda completar el diagnostico candidato, perdiendo nuevamente el DTO en memoria.

Correccion requerida: exigir SHA revisado explicito y comprobar antes de HTTP
resolvedor, esquema 0022 y version de politica compatible. Actualizar el preflight
read-only y los hashes del bundle junto con sus tests. No omitir el control usando
solo una instruccion operativa en documentacion. Los scripts historicos de #4
y perfiles deben conservarse intactos.

## Controles positivos

- Resolver unico, DTO financiero existente y `preparar_oferta()` reutilizados.
- Prioridad documental, proveedor estimado solo con semantica acreditada, contrato
  menos porcentaje configurado, o NO_DETERMINABLE; no se crean ingresos verificados.
- Porcentaje validado/versionado en JSON y politica usada protegida contra cambios
  posteriores. Falta resolver el corte inicial de politicas existentes.
- Gate `_validar_gate()` exige ingreso documental vigente; una oferta estimada
  preliminar no habilita aprobacion interna, confirmacion ni expediente/originacion.
- HC14/TX05 oficial sigue fail-closed. HDC SIN_INFORMACION no equivale a deuda cero.
- No diff en `integrations`, `payroll_loan`, `whatsapp_credit`, normalizadores
  oficiales ni parser shadow productivo. No se cambian fingerprints o historicos.
- El preflight candidato permite ausencia de ingreso manual como
  PENDIENTE_RESOLUCION_AUTOMATICA. Esto no acredita codigo nuevo instalado en VPS.

## Pruebas de este gate

PostgreSQL oficial `postgres:16-alpine`, contenedor propio desechable,
127.0.0.1, usuario temporal, NAME `uat03_bootstrap`, TEST.NAME
`test_uat03_aislada`. Sin .env, datos operativos ni volumen persistente.
HTTP externo y publicaciones Celery bloqueados. Contenedores propios eliminados.

Upgrade real en base aislada desde contractors 0021 con filas sinteticas previas:

- Esquema aditivo, mismas tablas y politicas anteriores legibles/validables: PASS.
- Solicitudes sinteticas IDs 4/9, auditorias y snapshots anteriores intactos: PASS.
- Misma interpretacion/version de contexto sin nueva politica: FAIL reproducible.
- Preflight rechaza core sin resolvedor antes del consumo: FAIL reproducible.
- Corrida final de estos cuatro controles: 2 PASS, 2 FAIL, 0 ERROR, 0 SKIP;
  7.357 segundos. Las dos fallas son criterios de gate, no fallos ocultados.
- `check`: PASS, `makemigrations --check --dry-run`: PASS,
  `migrate --plan`: PASS sin operaciones pendientes tras 0022 en la base aislada.
  Plan de upgrade antes de aplicar: exclusivamente 0022 forward.
- `git diff --check`: PASS.

Regresion focal posterior, con fixtures finales: **138/138 PASS**, 0 FAIL,
0 ERROR, 0 SKIP, 444.671s. Modulos ejecutados:
`test_ingreso_evaluable`, `test_aprobacion_interna_prestador`,
`test_pipeline_financiero_e2e`, `test_diagnostico_midecisor_uat`,
`test_politica_prod_horizonte`. Incluye ingreso documental/estimado, ausencia
de fuentes, oferta insuficiente, HDC sin informacion, HC14, permisos y gates.
Es regresion bajo el contrato nuevo; no sustituye los dos criterios de gate
fallidos ni demuestra compatibilidad historica del corte de politica.
No sumar estas cifras a las corridas previas como si fueran casos unicos.

Las primeras pasadas de desarrollo de los fixtures de upgrade necesitaron corregir
el estado completo de apps y una vigencia obligatoria del snapshot sintetico;
la corrida final anterior ya no tiene errores de fixture.

Comparacion UI legacy con el release base archivado temporalmente, sin checkout
ni cambios del working tree, SQLite memoria y HTTP bloqueado:

| Prueba | Base 4c536c1 | Candidato |
| --- | --- | --- |
| test_error_controlado_publico_no_expone_detalle_tecnico | FAIL | FAIL |
| test_mi_credito_bloqueado_no_expone_motivo_interno | FAIL | FAIL |
| test_mi_credito_preaprobado_no_expone_score_ni_proveedor | FAIL | FAIL |

3 fallos, cero errores/omisiones en cada version (6.932s / 8.365s).
Esos tests y sus templates/views no cambian en el paquete. Se mantienen separados;
el fallo legacy no constituye por si solo evidencia de exposicion crediticia.
Driver: `diagnosticos/verificar_ui_legacy_release_gate.py`; salida sin HTML/PII.

## Inventario propuesto, NO staged

Paquete de implementacion identificado: 27 archivos. No existe aun candidato
aprobado; el inventario debe revalidarse tras las correcciones anteriores.

```text
contractors/admin.py
contractors/datacredito/adapter.py
contractors/datacredito/dto.py
contractors/models.py
contractors/politica_ingresos.py
contractors/migrations/0022_configuracionscoreprestador_politica_ingreso_evaluable.py
contractors/score/componentes.py
contractors/services/aprobacion_interna.py
contractors/services/evaluacion_formal.py
contractors/services/evaluacion_versionado.py
contractors/services/ingreso_evaluable.py
contractors/services/oferta_financiera.py
contractors/services/politica_financiera_prestador.py
contractors/services/politica_score.py
contractors/tests/test_aprobacion_interna_prestador.py
contractors/tests/test_centrales_duales_prestador.py
contractors/tests/test_diagnostico_midecisor_uat.py
contractors/tests/test_evaluacion_errores_idempotencia.py
contractors/tests/test_ingreso_evaluable.py
contractors/tests/test_orquestacion_evaluacion.py
contractors/tests/test_pipeline_financiero_e2e.py
contractors/tests/test_politica_prod_horizonte.py
diagnosticos/aprobado_uat03_controlada.py
diagnosticos/preflight_uat03_actual.ps1
diagnosticos/test_uat03_controlada.py
diagnosticos/uat9_shadow.py
docs/PRESTADORES_INGRESO_EVALUABLE.md
docs/PRESTADORES_UAT03_MEMORIA_Y_PROCEDIMIENTO.md
```

Verificacion agregada en este gate, conservada fuera del staging:

```text
contractors/tests/test_gate_release_ingreso_migracion.py
diagnosticos/verificar_ui_legacy_release_gate.py
docs/PRESTADORES_INGRESO_RELEASE_GATE.md
```

Excluidos y conservados: ROADMAP, test_captura_handoff.cjs, diagnostico documental,
diagnostico anterior #9 y descubrir_uat03.py. Nunca `git add .` ni `git add -A`.
Dependencias operativas fuera de Git: los dos helpers historicos de /tmp, protegidos
por SHA256 en el runner y en PRESTADORES_UAT03_MEMORIA_Y_PROCEDIMIENTO.md.
No requiere nuevas dependencias Python, colas o servicios; si se modifica el runner,
regenerar su hash y verificar bundle completo antes de cualquier operacion.

## Despliegue propuesto, NO ejecutado

No ejecutar esta seccion mientras RELEASE_GATE sea BLOCKED. Requiere candidato
corregido/revisado, respaldo restaurado con exito en base aislada y autorizacion
expresa de despliegue. No se proporciona un SHA ficticio.

Orden: respaldo -> codigo validado -> migracion revisada -> nueva version de
politica por flujo funcional autorizado (sin activacion implicita) -> procesos que
cargan el core -> checks -> preflight read-only. Detener nuevas evaluaciones durante
el intervalo codigo/esquema y drenar jobs en curso; no cambiar flags para ello sin
autorizacion. Gunicorn y worker general cargan este core; no reiniciar OCR, Beat,
Redis o Nginx por defecto. Confirmar nombres reales de unidades antes de operar.

Preparacion de respaldo, solo en ventana autorizada:

```bash
set -euo pipefail
umask 077
cd /root/aprobado_web
export PYTHONDONTWRITEBYTECODE=1
PREVIOUS_SHA=4c536c148e946e13aefdc9c0012fdeccdb048123
test "$(git rev-parse HEAD)" = "$PREVIOUS_SHA"
git diff --quiet HEAD --
test "$(git branch --show-current)" = release/prestadores-prod-2026-08-28
export BACKUP_DIR="/root/aprobado-respaldos/ingreso-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 -p "$BACKUP_DIR"
git bundle create "$BACKUP_DIR/codigo.bundle" HEAD
git rev-parse HEAD > "$BACKUP_DIR/previous_sha.txt"
venv/bin/python - <<'PY'
import os, subprocess
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aprobado_web.settings')
import django
django.setup()
from django.conf import settings
d = settings.DATABASES['default']
assert d['ENGINE'] == 'django.db.backends.postgresql'
env = os.environ.copy()
env.update(PGPASSWORD=str(d.get('PASSWORD') or ''),
           PGHOST=str(d.get('HOST') or ''), PGPORT=str(d.get('PORT') or '5432'),
           PGUSER=str(d.get('USER') or ''), PGDATABASE=str(d['NAME']),
           PGOPTIONS='-c default_transaction_read_only=on')
sslmode = d.get('OPTIONS', {}).get('sslmode')
if sslmode:
    env['PGSSLMODE'] = sslmode
destino = Path(os.environ['BACKUP_DIR']) / 'postgresql.dump'
with destino.open('xb') as salida:
    subprocess.run(['pg_dump', '--format=custom'], env=env, stdout=salida,
                   stderr=subprocess.DEVNULL, check=True)
subprocess.run(['pg_restore', '--list', str(destino)], stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, check=True)
print('RESPALDO_POSTGRESQL_GENERADO_VERIFICAR_RESTAURACION_AISLADA')
PY
```

La lista de pg_restore valida el formato, NO demuestra restauracion; probar restore
en PostgreSQL aislado antes de migrar. El dump contiene datos sensibles: 0600,
fuera de media/Git, acceso restringido y custodia segun politica. No copiar ni
imprimir .env; conservar sus backups preexistentes. No restaurar sobre produccion
para un rollback rutinario de codigo.

Sincronizacion/migracion futura, solo despues de todos los controles anteriores:

```bash
set -euo pipefail
cd /root/aprobado_web
: "${RELEASE_SHA:?SHA candidato aprobado, aun no disponible}"
test "$(git rev-parse HEAD)" = 4c536c148e946e13aefdc9c0012fdeccdb048123
git diff --quiet HEAD --
git fetch origin refs/heads/release/prestadores-prod-2026-08-28
test "$(git rev-parse FETCH_HEAD)" = "$RELEASE_SHA"
git merge --ff-only "$RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
venv/bin/python manage.py migrate contractors 0022 --plan
# CONTINUAR SOLO si plan revisado contiene exclusivamente la migracion autorizada.
venv/bin/python manage.py migrate contractors 0022 --noinput
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/python manage.py migrate --plan
```

No usar `migrate` global sin revisar dependencias/plan. No activar automaticamente
la politica nueva: su version y transicion funcional requieren autorizacion.
Tras migrar y validar, recargar unicamente Gunicorn y worker general en ventana
autorizada, con jobs drenados y nombres de unidades confirmados. No se han ejecutado
estos reinicios ni inspeccionado infraestructura nuevamente en este gate.

Postchecks propuestos:

```bash
venv/bin/python manage.py shell <<'PY'
import json
from django.conf import settings
from contractors.models import ConfiguracionScorePrestador
flags = ('DATACREDITO_ENABLED', 'DATACREDITO_REAL_ENABLED',
         'PRESTADORES_AUTO_EVALUACION_ENABLED')
valores = {n: bool(getattr(settings, n, False)) for n in flags}
assert not any(valores.values()), 'flags_no_desactivados'
print(json.dumps({'flags': valores, 'politicas': list(
    ConfiguracionScorePrestador.objects.values('id', 'version_politica', 'activa',
                                              'politica_ingreso_evaluable'))}))
PY
curl --fail --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  https://contratistas.aprobado.com.co/accounts/login/
```

Verificar TLS sin `-k`, estado publico correcto, unidad Gunicorn/worker saludable
y flags efectivos tambien en sus procesos, sin imprimir entornos ni credenciales.
Despues ejecutar preflight nuevo READ ONLY con SHA y bundle revisados. Un check
desde shell no acredita por si solo los flags efectivos de un servicio ya iniciado.

## Reversion

- Registrar/respaldar codigo y PostgreSQL ANTES del cambio.
- Si falla migracion: no arrancar workers con esquema incompatible; inspeccionar
  estado transaccional/plan, no encadenar migrate inverso o reset automaticamente.
- Si falla solo codigo: conservar preferentemente la columna aditiva 0022. El ORM
  anterior ignora columnas adicionales; no perder configuracion nueva eliminandola.
- Rollback de codigo mediante revert revisado del commit candidato y despliegue
  de ese nuevo commit, o mecanismo de releases ya autorizado; nunca reset --hard
  sobre el worktree con backups. Drenar trabajos antes de cambiar procesos.
- Antes del rollback identificar evaluaciones/politicas nuevas generadas tras el
  corte; conservarlas y suspender su avance hasta revisar compatibilidad. No
  reetiquetar auditorias ni recalcularlas automaticamente con el codigo anterior.
- `migrate contractors 0021` elimina el JSON y sus valores: SOLO con autorizacion
  independiente, respaldo de esa informacion y comprobacion de todos los procesos.
  No es parte del rollback rutinario. Restaurar un dump completo perderia escrituras
  posteriores y requiere plan de recuperacion separado.

## UAT-03 posterior al despliegue

1. Cerrar los dos gates fallidos, versionar la politica y publicar candidato revisado.
2. Confirmar funcionalmente solicitud 10/propietario 310/consentimiento 8 con operador;
   sin imprimir identidad. Repetir readonly: consentimiento/documentos/horizonte/
   simulacion/politica, ausencia de reservas/jobs/auditorias/gates/consultas previas.
3. Cero ingresos verificados NO bloquea elegibilidad de fuentes. No crea evidencia
   documental ni permite originacion. Resolver ingreso contractual aislado antes
   del consumo, usando politica efectiva explicitamente aprobada.
4. Confirmar credenciales UAT sin divulgarlas y SHA/schema/core disponibles ANTES HTTP.
5. Solicitar autorizacion individual: maximo un producto y un OAuth por fuente,
   sin FORCE, redirects o retries, sin repetir #4/#9.
6. HDC/MiDecisor oficiales fuera de atomic global; luego diagnostico candidato
   separado con rollback, sin persistir score personal ni abrir gates.
7. HC14 oficial no cambia, HDC SIN_INFORMACION no se transforma en cero; resultado
   candidato experimental no es aprobacion. Flags solo dentro del proceso temporal.

No ejecutar el runner actual de consumo mientras permanezca este gate BLOCKED.
