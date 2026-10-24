# APROBADO-04: restriccion administrativa de nuevas solicitudes

P1. Implementacion LOCAL preparada el 23/10/2026; no desplegada. El ID no estaba
ocupado en el roadmap/codigo consultado. No cambia politica de riesgo ni marca
a una empresa como morosa mediante inferencia automatica.

## Contrato y controles

- Una regla por empresa y producto: LIBRANZA o PRESTADORES. Otros productos,
  incluidos Adelanto/Marketplace, no se modifican ni se presentan como cubiertos.
- RestriccionEmpresa conserva motivo interno, mensaje publico, inicio, vencimiento,
  referencia parametrizable de mora (90 dias iniciales), actor/fecha de creacion
  y de levantamiento. Estado derivado: PROGRAMADA, VIGENTE, VENCIDA o LEVANTADA.
  La referencia 90 no dispara consultas, tareas ni bloqueos automaticos.
- Solo staff activo con `gestion_creditos.manage_company_restrictions`, sin
  PerfilPagador, puede crear o levantar. No basta ser pagador/colaborador/staff.
  Admin permite alta y levantamiento con motivo, CSRF y permiso especifico;
  no permite editar el registro original ni eliminarlo. El servicio reutiliza
  LogEntry de Django: operacion, IDs, producto, actor y timestamp; sin PII en logs.
- Mensaje publico configurable (maximo 300 caracteres), sin saldos ni detalle de
  cartera. Prototipo: "Por ahora no podemos recibir nuevas solicitudes asociadas
  a esta empresa. Puedes consultar con tu empresa o contactar a Aprobado para
  recibir orientacion." El operador debe revisar su contenido antes del alta.
- Restriccion activa: inicio <= ahora, no levantada y vencimiento ausente o futuro.
  Alta duplicada de regla no levantada/no vencida para el mismo producto se rechaza.
  Levantamiento repetido no duplica auditoria. Cambio de parametros: levantar
  registro anterior y crear otro; no se reescribe el historial.

## Puntos de aplicacion

1. Prestadores: widget de empresa + validacion del formulario; guard de
   ContractorApplication.save para altas/cambio de empresa; revalidacion en
   confirmar_condiciones_simulacion. El formulario maneja tambien la carrera
   entre validacion y guardado sin exponer motivo interno ni devolver error 500.
2. Libranza: API de busqueda entrega solo mensaje publico adicional; JS y
   CreditoLibranzaForm rechazan empresa restringida. CreditoLibranza.save valida
   alta/cambio de empresa, incluido el originador alternativo de casos especiales;
   sus transacciones revierten usuario/credito/detalle ante restriccion.
3. Alta/levantamiento/creacion de solicitudes serializados mediante lock de
   Empresa; no hay lock global. Las pruebas PostgreSQL coordinan transacciones,
   no sustituyen locks reales por mocks.

Una solicitud Prestadores anterior al registro/inicio de restriccion no se bloquea
retroactivamente; cambiarla a otra empresa si exige una nueva validacion. El replay
identico de condiciones ya confirmadas conserva su idempotencia. Originacion de
una solicitud Prestadores anterior no se reinterpreta como solicitud nueva de
Libranza. Creditos existentes conservan pagos, estados, cartera y consultas.

Los guards cubren vias de aplicacion que usan save/create. Como otros servicios
del proyecto, no son una barrera contra SQL directo, QuerySet.update/bulk_create
o cambios de un administrador de BD; no hay endpoints publicos que ofrezcan esas
operaciones de escritura masiva. No utilizar esas vias para crear solicitudes.

## Fuentes para una automatizacion posterior (NO implementada)

- `CuotaAmortizacion`: fecha_vencimiento, pagada, monto_pagado y desglose.
  Requiere considerar pagos parciales, exigibilidad y saldo realmente pendiente.
- `HistorialPago`: EXITOSO, fecha_aplicacion, monto y origen; conciliar con
  detalles contables y la obligacion afectada, no solo con fecha de reporte.
- `PagoBREB` / `PagoBREBDetalle`: PENDIENTE_VERIFICACION no acredita abono.
  `breb_payments.aprobar_pago_breb` verifica y registra HistorialPago EXITOSO;
  `rechazar_pago_breb` no equivale a mora empresarial acreditada.
- Empresa se asocia por detalle Libranza / solicitud Prestadores. Acreditar
  responsabilidad del pagador, no atribuir toda mora personal a una empresa.
- Antes de automatizar: negocio debe aprobar umbral, alcance y levantamiento;
  comprobar fechas/conciliacion de cartera y resolver ambiguedades financieras.

## Candidato independiente

Archivos exclusivos de APROBADO-04 (no agregar otros pendientes del working tree):

```text
contractors/forms.py
contractors/models.py
contractors/services/confirmacion_simulacion.py
contractors/views.py
gestion_creditos/admin.py
gestion_creditos/admin_restricciones.py
gestion_creditos/forms.py
gestion_creditos/models.py
gestion_creditos/migrations/0051_restriccion_empresa.py
gestion_creditos/services/restriccion_empresa.py
gestion_creditos/views/admin.py
gestion_creditos/views/solicitudes.py
gestion_creditos/tests/test_restriccion_empresa.py
gestion_creditos/tests/browser/test_restriccion_empresa.cjs
templates/admin/gestion_creditos/restriccion_empresa_form.html
templates/contractors/solicitud_prestador.html
templates/gestion_creditos/solicitud_libranza.html
docs/APROBADO_04_RESTRICCION_EMPRESARIAL.md
```

0051 depende de 0050_ocr_documental_local y del usuario configurable. Solo crea
tabla/indice/permiso nuevo; no actualiza historicos ni restringe empresas por
defecto. Requiere migracion y despliegue coordinados ANTES de usar el nuevo codigo
en un ambiente servido; no ejecutarlos en produccion en esta tarea.

Validacion reproducible: modulo `gestion_creditos.tests.test_restriccion_empresa`
mas busqueda Libranza, confirmacion Prestadores, integridad originacion/anulacion
y casos especiales. PostgreSQL local desechable, HTTP/Celery bloqueados, datos
sinteticos. Cobertura: permisos, producto, fechas, vencimiento, alta/levantamiento,
auditoria, seleccion/API, cambio de empresa, vias alternativas, solicitudes previas,
concurrencia real y upgrade 0050 -> 0051 sin reescritura de empresas.

Validacion del 23/10: 74/74 pruebas PostgreSQL PASS, sin omisiones, incluidas
las tres concurrencias nuevas, upgrade 0050 -> 0051 y controles Django sobre
esquema migrado. No se repitieron las 74 del hotfix de firma: es otra suite.
Otras 6/6 regresiones del portal real PASS: alta documental, evidencia de
consentimiento, empresa activa, reanalisis, rollback documental y select real.
Total focal de esta entrega: 80 casos Django distintos, 0 fallos y 0 omisiones.
Navegador de seleccion: 4/4 Chromium y 4/4 WebKit, escritorio/movil.
`check` sin advertencias y `makemigrations --check --dry-run` sin cambios;
`git diff --check` OK. Una comprobacion adicional sobre SQLite vacio aviso
0018 ausente; no se interpreto como estado de produccion ni se ejecuto migrate.

La validacion del 23/10 no genero commit ni publico. El hotfix de firma y el
cambio institucional son candidatos separados.

## Gate y operacion preparada el 24/10/2026

SSH read-only confirmo produccion c20962c, PostgreSQL con 0050 aplicada,
historial consistente, ninguna hoja pendiente en el codigo desplegado y 0051
todavia ausente del codigo/BD. Tabla de restricciones ausente, como corresponde.
DataCredito, real y autoevaluacion False. No se aplico migracion alguna.

Gate focal repetido el 24/10 sobre el candidato aislado, SIN el hotfix ZapSign:
27/27 PostgreSQL PASS (incluye upgrade, permisos y tres concurrencias nuevas),
4/4 Chromium y 4/4 WebKit. No se repitio la regresion general 80/80.

Rama `candidate/aprobado-04-20261024`: segundo commit, padre candidato APROBADO-05.
Su diff propio contiene SOLO los 18 archivos del inventario; no arrastra el
hotfix 5054ac0 ni el roadmap. Orden propuesto de publicacion: 05 -> 04. Si se
quiere desplegar solamente 04 desde c20962c, preparar/revisar otra integracion;
no omitir silenciosamente que este candidato tiene 05 como padre.

### Respaldo y despliegue (preparado, NO ejecutado)

1. Autorizar SHA exacto y verificar remoto/servidor en el SHA aprobado de 05,
   tracked limpio; conservar .env.bak_* y archivos operativos. Si difiere, parar.
2. Crear directorio de respaldo exclusivo, modo 0700, `umask 077`. Guardar SHA
   previo y `git archive` del codigo. No copiar .env a Git ni imprimir valores.
3. Ejecutar pg_dump custom en destino protegido usando configuracion Django del
   venv, sin contrasenas en argumentos ni salida. Ejemplo para una ventana
   AUTORIZADA, con BACKUP_DIR absoluto ya creado y exportado:

```bash
cd /root/aprobado_web
venv/bin/python - <<'PY'
import os, subprocess
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aprobado_web.settings')
import django
django.setup()
from django.conf import settings
destino = Path(os.environ['BACKUP_DIR']).resolve()
assert destino.is_dir() and destino.is_relative_to(Path('/root/backups'))
assert destino.stat().st_mode & 0o077 == 0
db = settings.DATABASES['default']
assert db['ENGINE'] == 'django.db.backends.postgresql'
env = dict(os.environ)
for clave, campo in [('PGHOST','HOST'),('PGPORT','PORT'),('PGDATABASE','NAME'),('PGUSER','USER'),('PGPASSWORD','PASSWORD')]:
    env[clave] = str(db.get(campo) or '')
archivo = destino / 'postgres-previo.dump'
assert not archivo.exists()
salida = subprocess.run(['pg_dump','--format=custom','--no-owner','--no-acl','--file',str(archivo)],
                        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
env.pop('PGPASSWORD', None)
if salida.returncode:
    raise SystemExit('BLOCKED: respaldo PostgreSQL fallido; no desplegar')
archivo.chmod(0o600)
print('BACKUP_POSTGRESQL=PASS')
PY
```

4. Validar `pg_restore --list` sin imprimir objetos/datos, hash del dump y
   restauracion de ensayo en BD AISLADA antes de depender del respaldo. El dump
   es sensible: custodia/retenido conforme al mecanismo operativo protegido.
5. Tras publicar el commit aprobado (solo despues de autorizacion), en VPS:

```bash
set -euo pipefail
cd /root/aprobado_web
: "${PREVIOUS_SHA:?SHA completo aprobado de APROBADO-05}"
: "${TARGET_SHA:?SHA completo aprobado de APROBADO-04}"
test "$(git rev-parse HEAD)" = "$PREVIOUS_SHA"
test -z "$(git status --porcelain --untracked-files=no)"
git fetch origin refs/heads/release/prestadores-prod-2026-08-28
test "$(git rev-parse FETCH_HEAD)" = "$TARGET_SHA"
test "$(git rev-parse "$TARGET_SHA^")" = "$PREVIOUS_SHA"
git diff --check "$PREVIOUS_SHA" "$TARGET_SHA"
# Comparar los nombres exactos con los 18 archivos del inventario antes de seguir.
git merge --ff-only "$TARGET_SHA"
venv/bin/python manage.py migrate --plan
# Detenerse si aparecen operaciones distintas a gestion_creditos.0051.
venv/bin/python - <<'PY'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aprobado_web.settings')
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
plan = MigrationExecutor(connection).migration_plan([('gestion_creditos', '0051_restriccion_empresa')])
assert [(m.app_label, m.name, backwards) for m, backwards in plan] == [
    ('gestion_creditos', '0051_restriccion_empresa', False)], 'BLOCKED: plan inesperado'
print('PLAN_0051=PASS')
PY
venv/bin/python manage.py migrate gestion_creditos 0051_restriccion_empresa --noinput
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations --check --dry-run
# Modelo/servicio compartido: renovar web y worker general; no OCR/Beat/Redis.
systemctl restart gunicorn.service celery.service
systemctl is-active --quiet gunicorn.service celery.service
curl --fail --silent --show-error --output /dev/null https://aprobado.com.co/libranza/
test "$(git rev-parse HEAD)" = "$TARGET_SHA"
```

Aplicar esquema aditivo ANTES de renovar procesos. No crear restricciones ni
conceder permisos automaticamente; 0051 solo crea tabla/indice, y post_migrate
crea el permiso. Superusuario o personal con permiso especifico puede gestionar
mas adelante; no dar acceso al pagador. Verificar via consulta read-only:
0051 aplicada, tabla vacia inicialmente, permiso especifico presente y flags
False. Realizar cualquier alta/levantamiento funcional solamente en BD aislada,
salvo autorizacion administrativa independiente. No probar bloqueos con clientes.

### Reversion que conserva auditoria

- Preferir correccion hacia delante si ya existen restricciones. NO ejecutar
  `migrate gestion_creditos 0050`: su reversa borraria tabla y evidencias.
- Mantener 0051 aplicada, tabla, permisos y LogEntry incluso al volver al codigo
  previo. Antes del rollback leer reglas no levantadas/no vencidas (incluidas
  las programadas). Si existen, BLOCKED para volver a un codigo sin enforcement,
  salvo contencion de nuevas solicitudes autorizada expresamente. No levantarlas
  artificialmente para facilitar rollback.
- Si no hay reglas que requieran enforcement y el rollback fue autorizado:
  preservar BACKUP_DIR/SHA, restaurar SOLO los archivos de codigo del inventario
  desde PREVIOUS_SHA con `git restore --source`, conservando el archivo de
  migracion 0051 y la BD. Para archivos nuevos inexistentes en PREVIOUS_SHA,
  retirarlos solo tras verificar que corresponden exactamente al inventario y
  son creaciones de este release; no `git clean`. Registrar el estado local de
  rollback, renovar Gunicorn/worker general y comprobar salud/flags.
- No restaurar el dump sobre produccion con escrituras posteriores sin un plan
  de recuperacion aprobado: se perderian registros. Probar restauracion aislada;
  auditoria/estado administrativo siempre deben conservarse.

Ningun push, respaldo productivo, migracion, restart o despliegue de estos pasos
se ejecuto durante el cierre local.
