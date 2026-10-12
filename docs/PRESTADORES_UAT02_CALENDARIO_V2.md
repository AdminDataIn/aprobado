# UAT-02: calendario contractual del PDF v2

Base revisada: `87f3713975cf43556c94948d927659809a2f8f3c`.
Fecha de diagnostico local: 12/10/2026, America/Bogota.

## Causa comprobada

El PDF local coincide con SHA256
`00b532ba22b2f00237be4ac3b363e6a0dfdc7142595ba44e1054a37baa187dfa`.
La base extrae inicio, fin y total, pero no el honorario mensual ni la regla
"dia cinco (5) de cada mes siguiente". No es falta de clausula documental.

- `analisis_contrato.py`: faltaban las formas "honorarios ... son de $... mensuales"
  y "honorarios mensuales de SIETE MILLONES DE PESOS ($... COP)".
- `horizonte_contractual.py`: solo reconocia dias habiles del mes siguiente y fin
  de mes. La evidencia del dia fijo se descartaba durante el analisis seguro.
- `_aplicar_evidencia_analisis()` copia la evidencia documental de metadata al
  modelo. La confirmacion de valores manuales no cambia ese contrato.
- `horizonte_para_solicitud()` lee esa evidencia persistida; no lee el PDF.

La correccion admite el dia calendario explicito del mes siguiente, sin moverlo
por festivos/fines de semana ni recortarlo a fin de mes. Reglas contradictorias,
incompletas o fechas inexistentes siguen bloqueadas. No cambian topes, tasas,
primera cuota del credito, verificacion de ingreso, scoring ni originacion.

El PDF real se inspecciona localmente; no se incorpora al repositorio. El fixture
versionado conserva las clausulas relevantes con una identidad sintetica.
Los vencimientos calculados son 05/08/2026 a 05/07/2027, 12 periodos. Esto NO
demuestra que ningun pago haya sido recibido.

## Diagnostico VPS read-only, pendiente de ejecucion por operador

No conocemos el ID de UAT-02 ni hemos consultado produccion. Usar su ID real;
no asumir que es 4. El documento vigente es el unico CONTRATO por solicitud
(constraint `unique_documento_por_solicitud_prestador`); no existe flag activo.
El siguiente bloque funciona tanto en la base como en el hotfix y no ejecuta
extraccion, reanalisis, escrituras ni consultas a proveedores. Lee el archivo privado sin
imprimir texto, identificacion, ruta, nombre fisico ni hashes de identidad.

Definir `APROBADO_PYTHON` como Python del venv real y `SOLICITUD_ID` como ID UAT-02.

```bash
cd /root/aprobado_web
: "${APROBADO_PYTHON:?Definir Python del virtualenv real}"
: "${SOLICITUD_ID:?Definir ID interno de UAT-02}"
export SOLICITUD_ID
"$APROBADO_PYTHON" manage.py shell <<'PY'
import hashlib, json, os
from django.db import connection, transaction
from contractors.models import ContractorApplication, ContractorApplicationDocument
from contractors.services.capacidad_contractual import obtener_configuracion_simulador_prestador
from contractors.services.horizonte_simulacion import horizonte_para_solicitud
from gestion_creditos.services.horizonte_contractual import extraer_regla_pago

assert connection.vendor == 'postgresql', 'Se requiere PostgreSQL read-only'
sid = os.environ['SOLICITUD_ID']
assert sid.isascii() and sid.isdigit() and int(sid) > 0, 'ID interno invalido'
expected = '00b532ba22b2f00237be4ac3b363e6a0dfdc7142595ba44e1054a37baa187dfa'
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
    s = ContractorApplication.objects.filter(pk=int(sid)).first()
    if s is None:
        print(json.dumps({'solicitud_id': int(sid), 'resultado': 'NO_ENCONTRADA'}))
    else:
        docs = list(s.documentos.filter(tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO))
        meta = s.metadata_analisis_contractual or {}
        evidencia = (meta.get('forma_pago_contractual') or {}).get('evidencia', '')
        out = {'solicitud_id': s.pk, 'estado': s.estado, 'contratos_actuales': len(docs),
               'estado_analisis': s.estado_analisis_contractual,
               'fecha_analisis': s.fecha_analisis_contractual.isoformat() if s.fecha_analisis_contractual else None,
               'metadata_corresponde_v2': meta.get('archivo_hash_sha256') == expected,
               'evidencia_forma_pago_presente': bool(s.evidencia_forma_pago),
               'regla_metadata': extraer_regla_pago(evidencia),
               'regla_persistida': extraer_regla_pago(s.evidencia_forma_pago),
               'evidencia_modelo_igual_metadata': s.evidencia_forma_pago == evidencia,
               'reconciliacion_confirmada': bool((meta.get('reconciliacion') or {}).get('confirmada'))}
        if len(docs) == 1:
            d = docs[0]
            out.update(documento_id=d.pk, documento_actualizado=d.updated_at.isoformat(),
                       analisis_posterior_carga=bool(s.fecha_analisis_contractual and s.fecha_analisis_contractual >= d.updated_at))
            try:
                digest = hashlib.sha256()
                with d.archivo.open('rb') as archivo:
                    for chunk in archivo.chunks():
                        digest.update(chunk)
                out.update(bytes_corresponden_v2=digest.hexdigest() == expected,
                           bytes_iguales_hash_analisis=digest.hexdigest() == meta.get('archivo_hash_sha256'))
            except Exception as exc:
                out['lectura_archivo_error_tipo'] = type(exc).__name__
        h = horizonte_para_solicitud(s, obtener_configuracion_simulador_prestador())
        out.update(horizonte_disponible=h.disponible if h is not None else None,
                   horizonte_motivo=h.motivo if h is not None else 'CONFIGURACION_NO_COMPATIBLE')
        print(json.dumps(out, ensure_ascii=True))
PY
```

Interpretacion: hash de bytes distinto del esperado implica documento distinto;
bytes distintos del hash del analisis implican evidencia desactualizada. Si bytes
y metadata coinciden con v2 pero la regla es null en la base, la evidencia aun
no acredita el calendario. La fecha de analisis, por si sola, no acredita
hash vigente: carga y analisis pueden ocurrir en distinto orden dentro del POST.
No sobrescribir valores manuales confirmados. No se actualiza ninguna solicitud
con este bloque. En el release anterior al hotfix la regla de dia fijo seguira
siendo null aunque el texto persistido exista: interpretar junto con el SHA desplegado.

## Decision funcional separada, NO implementada

Mostrar especificamente "dia/regla de pago no interpretado" y enlazar la revision
del contrato existente, sin pedir otro documento si la clausula ya esta presente.
Una futura confirmacion humana deberia vincular regla, pagina/clausula, hash,
version del documento, actor y timestamp, distinguiendo declarado de verificado.
Una simulacion indicativa sin verificacion requeriria un estado y etiqueta propios
y NO habilitaria gates, aprobacion, firma u originacion. Requiere decision funcional
expresa; este hotfix no la habilita ni marca ingreso como verificado.

## Alcance de release para revision

Solo parser contractual, parser/calendario central y regresiones directamente
relacionadas (Django y WebKit), mas este diagnostico. Excluir ROADMAP, diagnostico
documental general y cambios de captura pendientes. Sin migraciones ni flags.
Commit y push aislados autorizados para el cierre; despliegue pendiente de
autorizacion independiente. Antes del E2E real, el operador debera
comprobar el documento persistido con el bloque read-only y, tras aprobar/desplegar
el hotfix, usar el flujo habitual de analisis y confirmacion del MISMO PDF v2.

Inventario exclusivo:

- `contractors/services/analisis_contrato.py`
- `gestion_creditos/services/horizonte_contractual.py`
- `contractors/tests/contrato_uat_fixture.py`
- `contractors/tests/test_extraccion_uat.py`
- `contractors/tests/test_politica_prod_horizonte.py`
- `contractors/tests/test_portal_minimo_prestadores.py`
- `contractors/tests/browser/calendario_django_bridge.py`
- `contractors/tests/browser/test_contractual_calendar.cjs`
- `docs/PRESTADORES_UAT02_CALENDARIO_V2.md`

## Validacion local

- 41 pruebas focales SQLite: OK (subconjunto de las siguientes).
- Revalidacion de cierre: 56 pruebas SQLite de extraccion UAT, reconciliacion y
  politica/horizonte OK; check y makemigrations comprobados sobre base aislada
  en memoria migrada. Solo se ajusta esta guia respecto del codigo validado en PostgreSQL.
- PostgreSQL 16 oficial desechable, solo 127.0.0.1:55439: 188 pruebas OK,
  0 fallos/errores/omisiones. Incluye 177 de extraccion/reconciliacion/horizonte/
  portal, 10 de reglas Libranza y 1 comprobacion local del PDF original por hash.
  Esta ultima se ejecuta fuera de Git; el PDF original no se versiona.
- WebKit movil: 2 recorridos OK, dias habiles y dia fijo, con Django real por
  TestClient y SQLite en memoria. No sustituye UAT Safari fisico en produccion.
- HTTP externo y publicaciones Celery bloqueados; .env no cargado en tests.
- Django check: OK. makemigrations --check --dry-run: No changes detected.
- Bloque read-only: sintaxis Python validada, no ejecutado en VPS.
- Pendiente operativo: confirmar bytes/metadata de UAT-02 y repetir el flujo
  habitual con el mismo PDF despues de aprobar el hotfix. No requiere otro contrato.

## Despliegue propuesto, NO ejecutado

Solo tras autorizacion. Definir `HOTFIX_SHA` con el SHA publicado del cierre y
`APROBADO_PYTHON` con el Python del virtualenv real. Conservar los archivos no
rastreados del servidor; detenerse ante diferencias rastreadas o SHA inesperado.

```bash
set -euo pipefail
cd /root/aprobado_web
: "${APROBADO_PYTHON:?Definir Python del virtualenv real}"
: "${HOTFIX_SHA:?Definir SHA completo autorizado del hotfix}"
PREVIOUS_SHA=87f3713975cf43556c94948d927659809a2f8f3c
test "$(git branch --show-current)" = release/prestadores-prod-2026-08-28
test "$(git rev-parse HEAD)" = "$PREVIOUS_SHA"
test -z "$(git status --porcelain --untracked-files=no)"
git fetch origin release/prestadores-prod-2026-08-28
test "$(git rev-parse FETCH_HEAD)" = "$HOTFIX_SHA"
test "$(git rev-parse "$HOTFIX_SHA^1")" = "$PREVIOUS_SHA"
git merge --ff-only "$HOTFIX_SHA"
"$APROBADO_PYTHON" manage.py check
sudo systemctl restart gunicorn.service
sudo systemctl is-active --quiet gunicorn.service
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
  --output /dev/null --write-out '%{http_code}\n' https://contratistas.aprobado.com.co/
test "$(git rev-parse HEAD)" = "$HOTFIX_SHA"
test -z "$(git status --porcelain --untracked-files=no)"
```

No migrate, collectstatic, cambios de flags ni reinicio de Celery/Redis: los
parsers cambiados se ejecutan en el analisis contractual y simulacion de Gunicorn.
El reinicio de Gunicorn puede causar una indisponibilidad breve. Un HTTPS 200
no sustituye la comprobacion funcional autenticada de UAT-02.

Rollback manual ante regresion, en la misma sesion con las variables anteriores:

```bash
test "$(git rev-parse HEAD)" = "$HOTFIX_SHA"
test -z "$(git status --porcelain --untracked-files=no)"
git switch --detach "$PREVIOUS_SHA"
"$APROBADO_PYTHON" manage.py check
sudo systemctl restart gunicorn.service
sudo systemctl is-active --quiet gunicorn.service
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
  --output /dev/null --write-out '%{http_code}\n' https://contratistas.aprobado.com.co/
```

El rollback deja HEAD separado y conserva los commits; no restaurar la rama
automaticamente ni sobrescribir trabajo local. No hay rollback de datos porque
este despliegue no transforma registros ni aplica migraciones.

## Comprobacion funcional posterior

1. Ejecutar el diagnostico read-only con el ID del borrador UAT-02 y verificar
   bytes v2, metadata y fecha de analisis. No asumir el ID de otra solicitud.
2. Si la evidencia corresponde al parser anterior, reanalizar el MISMO PDF v2
   mediante la UI habitual y confirmar los datos, conservando correcciones humanas.
3. Repetir diagnostico: evidencia persistida y metadata deben reconocer
   `DIA_MES_SIGUIENTE`, dia 5. No inferir pagos efectivamente recibidos.
4. Validar horizonte a la fecha real de simulacion y plazo solicitado; el maximo
   viable puede cambiar con el tiempo. No reutilizar el corte fijo del fixture.
5. En Safari fisico, calcular con un plazo viable y confirmar una sola vez;
   registrar el ID interno obtenido. Mantener centrales/autoevaluacion desactivadas.
6. Conservar como bloqueo valido fechas invertidas, calendario sin respaldo,
   fechas inexistentes o plazo superior al horizonte. No cambiar estados a mano.
