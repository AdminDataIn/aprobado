# UAT-03: fuentes oficiales y diagnostico candidato en memoria

**Actualizacion 17/10/2026:** el procedimiento vigente esta en
`PRESTADORES_INGRESO_RELEASE_2026_10_17.md`. Las evidencias y comandos inferiores
se conservan como historial del 16/10, NO como autorizacion para ejecutarlos.
El launcher nuevo separa PREPARACION (nunca acredita despliegue) y OPERATIVO
(solo importa el runner instalado). El runner exige SHA completo explicito,
coincidencia HEAD/tracking/remoto, core integrado, 0022 y nueva politica publicada
y adoptada antes de HTTP. El release base 4c536c1 siempre resulta BLOCKED.
Tras modificar el runner, su SHA anterior inferior queda obsoleto; regenerar
SHA256 desde el commit candidato revisado. Los hashes de los helpers historicos
y del helper shadow se conservan intactos.

Base: `4c536c148e946e13aefdc9c0012fdeccdb048123`.
Actualizacion funcional 16/10/2026: ver `PRESTADORES_INGRESO_EVALUABLE.md`.
El preflight candidato de #10/310 ahora PASS para fuentes, con ingreso
PENDIENTE_RESOLUCION_AUTOMATICA. La evidencia BLOCKED inferior es historica,
anterior a esa decision. El nuevo core/migracion aun NO estan desplegados.
Validacion nueva: 300/300 PostgreSQL PASS; revalidacion final 90/90 PASS, con
casos superpuestos. El inventario y resultados actuales estan en ese documento.
Descubrimiento read-only del 16/10/2026: candidata **solicitud 10, propietario 310**.
Es la unica solicitud reciente distinta de las identidades de 4/9 con simulacion
registrada. Falta confirmacion operativa de que corresponde a la participante UAT-03.
SSH no interactivo mediante `aprobado-vps`: PASS (`CODEX_SSH_OK`). PostgreSQL
operativo se inspecciono exclusivamente en READ ONLY, con rollback y HTTP bloqueado.
No se consulto Experian ni se modificaron registros, flags o servicios.

## Descubrimiento read-only

`diagnosticos/descubrir_uat03.py` lista hasta diez solicitudes de los ultimos siete
dias, excluye 3/4/9 y muestra solo IDs, estados, disponibilidad y conteos/evidencia
tecnica. Compara identidad con UAT previas en memoria, sin imprimirla. No acredita
por si solo que una fila sea la participante UAT-03: requiere confirmacion operativa.
PostgreSQL READ ONLY + rollback, red/Celery/email bloqueados, logs deshabilitados.

Desde PowerShell, con la clave autorizada, el siguiente comando
transmite el script por stdin; no crea archivos en VPS ni hace despliegue:

```powershell
$codigo = (Get-Content -Raw -Encoding UTF8 .\diagnosticos\descubrir_uat03.py).Replace("if __name__ == '__main__':", "if True:")
$codigo | ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 aprobado-vps "PYTHONDONTWRITEBYTECODE=1 /root/aprobado_web/venv/bin/python /root/aprobado_web/manage.py shell"
```

El ID/propietario deben contrastarse con el registro de la participante por un
operador autorizado sin transcribir su identificacion. Despues fijar los tres
IDs internos para el preflight: solicitud, propietario y actor staff.

### Evidencia operativa anterior a la decision de ingreso automatico

| Control read-only | Resultado |
| --- | --- |
| Propietario 310 activo, sin PerfilPagador, identidad distinta de 4/9 | PASS |
| EVALUACION_PENDIENTE, contrato COMPLETADO, cuatro documentos, simulacion | PASS |
| Consentimiento 8 vigente, del propietario, prestadores-centrales-v1 | PASS |
| Actor 1 staff activo, sin PerfilPagador, permisos evaluar/verificar ingreso/refrescar | PASS |
| Politica 1 dual, configuracion 2, versiones de simulacion compatibles | PASS |
| Horizonte y plazo mediante validador existente | PASS |
| Readiness critico GLOBAL/CONSENTIMIENTO/HDC/MIDECISOR y URLs UAT | PASS de configuracion |
| Credenciales canonicas; procedencia UAT de la cuenta | Confirmacion humana pendiente |
| Snapshots del fingerprint: HDC 0, MiDecisor 0; reservas/auditorias/jobs/gates 0 | PASS |
| DataCredito/real/autoevaluacion | False / False / False |
| Ingreso verificado | BLOCKED: cero registros, no es vencimiento ni invalidacion |

El preflight historico verificado por SHA se ejecuto con IDs 10/310/1 y bloqueo
en `ingreso_uat_no_vigente`. Los controles restantes se comprobaron separadamente
sin omitir ese bloqueo. OAuth/producto HDC y MiDecisor: cero llamadas; escrituras: cero.

Con la decision posterior, NO es obligatorio registrar ingreso manual para consultar
fuentes. Confirmar atribucion de 10/310, revisar la implementacion de ingreso evaluable
y su migracion, repetir preflight, confirmar procedencia UAT de credenciales y obtener
autorizacion expresa de consumo. La aprobacion/originacion siguen exigiendo evidencia
documental validada. No se ha registrado ingreso desde Codex.

Repeticion del preflight historico YA disponible en VPS, solo tras confirmar
10/310. No transfiere scripts ni consulta proveedores; mientras falte ingreso
seguira devolviendo `BLOCKED: ingreso_uat_no_vigente`:

```bash
cd /root/aprobado_web
PYTHONDONTWRITEBYTECODE=1 APROBADO_UAT_MODO=preflight \
APROBADO_UAT_SOLICITUD_ID=10 APROBADO_UAT_PROPIETARIO_ID=310 APROBADO_UAT_ACTOR_ID=1 \
/root/aprobado_web/venv/bin/python manage.py shell -c \
  'import runpy; runpy.run_path("/tmp/aprobado_uat_perfiles.py", run_name="__main__")'
```

## Variante preparada

Nuevo runner: `diagnosticos/aprobado_uat03_controlada.py`.
Helper comun: `diagnosticos/uat9_shadow.py`. La extraccion del helper financiero
se reutiliza tanto en los fixtures sinteticos anteriores como en la operacion
controlada; no se crea otro normalizador, score ni formula de capacidad/oferta.
No se importa desde rutas, views o tareas productivas.

Secuencia:

1. Preflight read-only, flags globales apagados, release exacto y codigo core
   rastreado limpio. Actor staff activo con permiso de evaluacion y sin PerfilPagador;
   propietario/consentimiento, documentos, politica/configuracion,
   simulacion y plazo/horizonte vigentes mediante validadores existentes.
2. Contexto completamente nuevo: ningun snapshot del fingerprint, ninguna reserva,
   auditoria, job o gate. No FORCE, no solicitudes 3/4/9, no reutilizacion ajena.
3. Autorizacion operativa INDIVIDUAL requerida por variable de proceso, mas
   confirmacion humana de procedencia UAT de credenciales. Endpoints UAT exactos
   y credenciales canonicas separadas; un endpoint UAT no prueba por si solo que
   la cuenta/credenciales sean UAT. Nunca imprimirlas.
4. Lock advisory de sesion por solicitud, sin transaction.atomic global durante
   HTTP. El servicio existente reserva/finaliza cada snapshot con sus propias
   transacciones; el guard comprueba `not connection.in_atomic_block` al enviar.
5. Maximo un OAuth y un producto por fuente; HDC primero, MiDecisor ultimo.
   Session sin proxy/env, TLS verificado, retries=0 y redirects deshabilitados.
   Timeout/redirect/error de transporte o fuente no interpretable detiene sin retry.
6. Observer envuelve SOLO el cliente MiDecisor: devuelve su respuesta ORIGINAL al
   adapter oficial y conserva solo el DTO allowlist candidato, no raw. Shadow
   valida HC14/TX05, envelope, flags, score entero/rango/ruta y modulos coherentes.
7. HC14/TX05 coherente puede habilitar SOLO el diagnostico experimental: snapshot
   ERROR_PERMANENTE, respuesta_funcional_indeterminada/RESPUESTA_INVALIDA,
   HTTP 200/202 y resultado_normalizado vacio permanecen intactos. No se habilita
   otra llamada despues de ese resultado; es la ultima fuente.
8. Cerrada la fase HTTP: restaurar flags y cache, revalidar contexto; entrar en
   PostgreSQL READ ONLY con bloqueo de red/publicaciones/SQL mutante y rollback.
   Consolidacion, score, predecision y preparar_oferta son los servicios reales.
   No se llama al evaluador formal persistente ni se crea auditoria candidata.
9. HDC SIN_INFORMACION conserva carga desconocida, nunca cero ni sustitucion
   automatica por cuota MiDecisor. Bajo PROD v1: revision manual, score completo
   no calculable y oferta NO_EVALUABLE aunque el componente MiDecisor exista.
10. Resultado sanitario: estados, disponibilidad, UUIDs y contadores, nunca scores
    numericos, raw, identificaciones, montos personales, tokens o mensajes libres.
    Elegibilidad crediticia final siempre False. No gates/firma/originacion/desembolso.
11. Limpiar referencias a DTOs/resultados, cache OAuth LOCMEM exclusivo del proceso
    y restaurar logging/flags. No afirmar borrado criptografico de memoria.
    Ante error diagnostico se conservan fuentes oficiales y se prohibe repetirlas.

## Bundle y SHA256

Transferencia futura exclusivamente a `/tmp`, tras revision; no ejecutada.
Los dos scripts historicos existentes no se modifican. El runner valida sus
dependencias por hash antes de operar.

```text
17f17178b10dd3db4f6896c3070b623e691e1aedd6b82520f9b7b09ca0c33cb7  aprobado_uat03_controlada.py
d5a980b0dad60cb801d54a44770b91765da0dd31866926500c58473429b306d3  uat9_shadow.py
ef318be20a514b9524a9ed2351b32a6518744ce057e78fff6d3ab780eeab1173  aprobado_uat_perfiles.py
b27f49170c0379c7ec62ebc21795b88e5c0cbe0e34993806c516e436a5a47627  aprobado_uat4_controlada.py
3b3443a26dadf7beb75ae317ed77d81ae72085ba96d7be7cc823760626cbc974  descubrir_uat03.py
```

## Preflight individual sin consumo

Solo cuando el descubrimiento identifique la solicitud y propietario. Definir
IDs confirmados, sin asumir actor apto ni inventar datos. El actor se revalida.
El runner/helper nuevos deben estar disponibles en /tmp con los hashes anteriores.

```bash
set -euo pipefail
cd /root/aprobado_web
: "${APROBADO_UAT_SOLICITUD_ID:?ID confirmado de UAT-03}"
: "${APROBADO_UAT_PROPIETARIO_ID:?ID confirmado del propietario}"
: "${APROBADO_UAT_ACTOR_ID:?ID del actor a verificar}"
export APROBADO_UAT_SOLICITUD_ID APROBADO_UAT_PROPIETARIO_ID APROBADO_UAT_ACTOR_ID
export APROBADO_UAT_HELPERS_DIR=/tmp
unset APROBADO_UAT_AUTORIZACION
(
  cd /tmp
  sha256sum --check <<'SHA'
17f17178b10dd3db4f6896c3070b623e691e1aedd6b82520f9b7b09ca0c33cb7  aprobado_uat03_controlada.py
d5a980b0dad60cb801d54a44770b91765da0dd31866926500c58473429b306d3  uat9_shadow.py
ef318be20a514b9524a9ed2351b32a6518744ce057e78fff6d3ab780eeab1173  aprobado_uat_perfiles.py
b27f49170c0379c7ec62ebc21795b88e5c0cbe0e34993806c516e436a5a47627  aprobado_uat4_controlada.py
SHA
)
APROBADO_UAT_MODO=preflight /root/aprobado_web/venv/bin/python manage.py shell -c \
  'import runpy; runpy.run_path("/tmp/aprobado_uat03_controlada.py", run_name="__main__")'
```

## Consumo futuro, NO AUTORIZADO TODAVIA

Solicitar autorizacion expresa citando ID confirmado, propietario, consentimiento,
ingreso/version, actor, preflight PASS, SHA verificado y maximos: un producto HDC,
un producto MiDecisor y hasta un OAuth por fuente. Revisar procedencia UAT de
credenciales antes de colocar la marca siguiente; no inferirla de los endpoints.

Solo DESPUES de esa autorizacion, en un proceso nuevo con los mismos IDs:

```bash
set -euo pipefail
cd /root/aprobado_web
: "${APROBADO_UAT_SOLICITUD_ID:?Falta ID autorizado}"
: "${APROBADO_UAT_PROPIETARIO_ID:?Falta propietario confirmado}"
: "${APROBADO_UAT_ACTOR_ID:?Falta actor verificado}"
export APROBADO_UAT_SOLICITUD_ID APROBADO_UAT_PROPIETARIO_ID APROBADO_UAT_ACTOR_ID
(
  cd /tmp
  sha256sum --check <<'SHA'
17f17178b10dd3db4f6896c3070b623e691e1aedd6b82520f9b7b09ca0c33cb7  aprobado_uat03_controlada.py
d5a980b0dad60cb801d54a44770b91765da0dd31866926500c58473429b306d3  uat9_shadow.py
ef318be20a514b9524a9ed2351b32a6518744ce057e78fff6d3ab780eeab1173  aprobado_uat_perfiles.py
b27f49170c0379c7ec62ebc21795b88e5c0cbe0e34993806c516e436a5a47627  aprobado_uat4_controlada.py
SHA
)
APROBADO_UAT_HELPERS_DIR=/tmp \
APROBADO_UAT_MODO=fuentes_y_diagnostico \
APROBADO_UAT_CREDENCIALES=CONFIRMADAS_POR_OPERADOR_COMO_UAT \
APROBADO_UAT_AUTORIZACION="AUTORIZO_UAT03_FUENTES_Y_DIAGNOSTICO_SOLICITUD_${APROBADO_UAT_SOLICITUD_ID}" \
/root/aprobado_web/venv/bin/python manage.py shell -c \
  'import runpy; runpy.run_path("/tmp/aprobado_uat03_controlada.py", run_name="__main__")'
```

No repetir el comando tras error o cierre de terminal incierto: inspeccionar
reservas/snapshots read-only. No borrar leases ni errores, no FORCE, no reiniciar
el pipeline dual. Ningun resultado candidato sustituye el resultado oficial.

## Pruebas y ROADMAP

Offline y PostgreSQL aislado interceptan HTTP en HTTPAdapter.send: el OAuth y
producto sinteticamente contabilizados no salen a Internet. En PostgreSQL se
prueban autocommit real, READ ONLY, rollback, fuentes oficiales/candidatas
segregadas, HC14, HDC sin informacion/completo, ingreso insuficiente, ausencia de
autorizacion, redirect/timeout, reenvio, privacidad, limpieza y descubrimiento.

Los gates humanos se verifican aparte con fixtures contractuales completos y
servicios existentes, no con candidato HC14 ni sobre la participante real.
El test E2E incluye revision, aprobacion interna, confirmacion del pagador y
expediente, sin originar ni enviar firmas. No se declara E2E operativo real cerrado.

Se detectaron tres aserciones publicas preexistentes en
`test_revision_manual_prestador`: error controlado, bloqueado y preaprobado.
Esperan textos antiguos que no aparecen en la UI actual; se reprodujeron aisladas
sin importar el runner nuevo. No se modifican vistas/templates/tests ajenos.
No declarar esa suite completamente verde ni deducir una fuga de score por esas
aserciones de texto. Pendiente separado de UX/regresion de presentacion.

Validacion:

- Offline: 43 pruebas Django de salvaguardas, diagnostico anterior y shadow OK,
  SQLite en memoria sin .env; adicionalmente 10 pruebas del runner historico de
  perfiles y 14 salvaguardas de transporte/SQL OK.
- PostgreSQL 16 oficial, efimero, solo 127.0.0.1, sin volumen productivo:
  59/59 pruebas OK en 190.783s, cero fallos/errores/omisiones. Incluye 16 del
  bundle UAT-03, 16 del diagnostico previo y 27 del pipeline E2E/gates.
- La primera pasada exploratoria de 75 pruebas tuvo fallos de configuracion de
  fixtures del bundle y las tres aserciones UI indicadas. Los fixtures se
  corrigieron sin tocar codigo productivo; las tres aserciones UI se reprodujeron
  por separado (3/3 fallan tambien sin importar este bundle) y quedan pendientes.
- Revalidacion focal del reporte de snapshots en errores: 16/16 PostgreSQL OK
  en 55.334s, cero fallos/errores/omisiones; incluye casos ya cubiertos en las 59.
- Motor de pruebas: django.db.backends.postgresql, NAME uat03_bootstrap,
  TEST.NAME test_uat03_aislada, USER uat03_temporal; puerto local efimero por corrida.
  Usuario/contrasena exclusivos del contenedor, .env no cargado; red externa y
  publicaciones bloqueadas, HTTP de contrato interceptado por adapter sintetico.
- Test runner crea/destruye solo TEST.NAME. Contenedores propios con --rm y
  datos tmpfs se detienen/eliminan al finalizar. No se usan recursos preexistentes.
- Django check: cero incidencias. `makemigrations --check --dry-run`: No changes
  detected. Sin migraciones nuevas. `git diff --check`: OK.
- Acceso SSH/read-only: PASS. Candidata 10/310 y consentimiento 8; actor 1 apto.
  Preflight operativo BLOCKED por ingreso ausente, con los demas controles indicados.
- No se autoriza consumo por tener fixtures verdes: falta confirmar el caso,
  verificar ingreso, obtener preflight PASS, acreditar credenciales UAT y autorizacion
  explicita. Los scripts nuevos permanecen locales; no se transfirieron al VPS.
