# Prestadores: ingreso evaluable automatico

Decision aprobada el 16/10/2026. Implementacion local; no desplegada ni activada.
No consulta Experian, no cambia pesos/bandas/tasas ni escribe ingresos verificados.

## Corte de politica corregido el 17/10/2026

El campo nuevo usa `default=dict, db_default={}, blank=True`.
`{}` significa LEGACY, no 25%; permite tambien escrituras desde el codigo anterior.
0022 solo agrega ese campo: no publica reglas, no activa politicas, no cambia
solicitudes ni sus hashes. Las instrucciones operativas vigentes se encuentran en
`PRESTADORES_INGRESO_RELEASE_2026_10_17.md`; el gate del 16/10 queda como historia.

`publicar_politica_ingreso()` clona parametros/bandas de la politica activa en
`prestadores-score-prod-v2-ingreso`, conservando el motor/version de score, tasas,
limites y configuracion financiera. Incorpora exclusivamente reglas de ingreso,
identidad nueva y vigencia explicita; queda INACTIVA globalmente y publicada en
`CambioPoliticaScorePrestadorAudit` mediante el patron existente de preparacion.

`adoptar_politica_ingreso()` exige staff con permisos de politica y evaluacion,
motivo, consentimiento, documentos, simulacion compatible, horizonte, ausencia de
auditorias/jobs/gates y antecedentes incompatibles. Solo cambia la version de
politica asociada a esa solicitud y crea un Timeline privado DATOS_MODIFICADOS,
tipo ADOPCION_INGRESO. No cambia simulada_en ni otros registros. Repetir no duplica.
La seleccion efectiva se comparte entre evaluacion, orquestacion, oferta y gates.

Sin adopcion auditada las solicitudes conservan calculo/version_datos LEGACY,
incluso tras publicar la nueva politica. No se recalculan ni invalidan en lote.

## Contrato unico

`contractors.services.ingreso_evaluable.resolver_ingreso_evaluable()` devuelve
el DTO financiero existente `IngresoNetoValido` o `None` (NO_DETERMINABLE).
Su nombre historico no convierte las fuentes estimadas en ingresos netos verificados.

1. Documental: `obtener_ingreso_neto_vigente()`, con evidencia humana vigente y
   hashes actuales. Es la primera fuente y conserva el registro/version originales.
2. MiDecisor: solo snapshot oficial EXITOSO, consentimiento/fingerprint/ambiente/
   titular/vigencia validos por `evidencia_uso_snapshot()`, modulo endeudamiento
   conInformacion=True y sin excepcion, importe finito positivo. La politica debe
   acreditar explicitamente `NETO_MENSUAL_ESTIMADO`; entonces se toma el menor
   entre ingreso estimado y honorarios mensuales del contrato. Se conserva la discrepancia.
3. Contrato: honorarios mensuales explicitos positivos multiplicados por
   `(1 - descuento_contractual_porcentaje / 100)`, truncados conservadoramente
   a centavos. Default 25.00%. Sin dividir saldo/meses para inventar honorarios.
   Exige contrato vigente, identidad/empresa coherentes, analisis COMPLETADO sin
   bloqueos, hash analizado igual al archivo actual, calendario y plazo respaldados.
4. Sin fuente suficiente: None, capacidad no disponible y revision manual, sin cero.

Swagger PN, esquema Endeudamiento: `ingreso` es string e ingreso estimado; NO
acredita por si solo periodicidad mensual ni naturaleza neta. Configuracion inicial
`semantica_midecisor=NO_CONFIRMADA`: fallback contractual, NO reinterpretacion.
La opcion NETO_MENSUAL_ESTIMADO requiere respaldo contractual y revision explicita
antes de configurar una nueva politica; los tests la usan solo como hipotesis sintetica.
No se utiliza porcentajeCuotaVsIngreso para fabricar una cuota monetaria.

HC14/TX05 sigue ERROR_PERMANENTE/no evaluable oficialmente. El resolver nunca
extrae ingreso de ese snapshot, aunque exista candidato shadow en memoria.
HDC SIN_INFORMACION sigue carga desconocida: no habilita capacidad/oferta completa.

## Configuracion e invalidacion

Campo JSON `ConfiguracionScorePrestador.politica_ingreso_evaluable`:

```json
{
  "version": "ingreso-evaluable-v1",
  "descuento_contractual_porcentaje": "25.00",
  "semantica_midecisor": "NO_CONFIRMADA"
}
```

Configuracion completa validada, porcentaje 0 <= valor < 100 y dos decimales.
La politica usada en auditorias no puede cambiar significado; crear nueva version.
La evidencia estimada conserva fuente, importe en DTO interno, honorarios, descuento,
regla/version, contrato SHA256, contexto/version_datos, vigencia y snapshot cuando aplica.
No imprimir esos importes, hashes completos ni respuestas en la operacion UAT.

`construir_version_datos()` incluye configuracion/version de ingreso SOLO tras
adopcion explicita. En solicitudes adoptadas, cambios de contrato, ingreso o
politica invalidan el contexto correspondiente. Las solicitudes anteriores NO
reciben ese cambio implicitamente ni requieren una reevaluacion masiva.

Migracion local 0022: SOLO AddField JSON en ConfiguracionScorePrestador.
No tablas nuevas, backfill de ingresos, activaciones o transformacion de snapshots.
El default de esquema/modelo es {}, NO la definicion nueva del 25%.
No ejecutada sobre VPS. Es necesaria para persistir el parametro dentro de la
politica, no como setting global ni constante financiera paralela.

## Score, oferta y gates

La capacidad PROD/dual y `calcular_oferta_evaluada()` usan el mismo resolvedor.
Se reutilizan `calcular_carga_financiera_prestador()` y `preparar_oferta()` sin
duplicar formulas ni cambiar sus limites, amortizacion, pesos o bandas.
El calculo contractual bruto historico se conserva como informacion, no como
segunda fuente para capacidad PROD. El componente legacy DEMO v1 conserva su
contrato historico; el producto PROD, incluso por entrada single-source, usa el resolvedor.

Una oferta estimada puede ser OFERTA_CALCULADA preliminar;
`habilitada_para_originacion=False`. No es una aprobacion.
El gate exige ingreso DOCUMENTAL vigente mediante `_validar_gate()` antes de
crearse/avanzar. Confirmacion de pagador, expediente y originacion reutilizan ese
control obligatorio. Cambiar/verificar ingreso requiere reevaluacion del contexto,
no promover una evaluacion estimada previa. No hay aprobacion automatica nueva.

## UAT-03 #10

VPS inspeccionado exclusivamente READ ONLY el 16/10/2026, release 4c536c1.
Solicitud 10, propietario 310, consentimiento 8, actor 1. Propietario/consentimiento
coincidentes, cuatro documentos, analisis COMPLETADO, simulacion, politica y
horizonte vigentes; sin ingresos documentales, snapshots del fingerprint, reservas,
auditorias, jobs ni gates. Confirmar atribucion operacional a la participante UAT-03;
el codigo no acredita por si solo la identidad fisica de quien opera el dispositivo.

Se ejecuto la funcion candidata de preflight EN MEMORIA sobre PostgreSQL READ ONLY,
sin transferir archivos ni instalar codigo. Resultado:
PASS_ELEGIBILIDAD_FUENTES_CANDIDATA, ingreso PENDIENTE_RESOLUCION_AUTOMATICA.
Cero OAuth/producto por fuente y cero escrituras. Flags globales siguen False.
Este PASS NO acredita despliegue de la nueva politica ni autoriza consumo.

Repetir exclusivamente esta lectura desde el checkout local:

```powershell
$script = [scriptblock]::Create((Get-Content -Raw .\diagnosticos\preflight_uat03_actual.ps1))
& $script
```

El runner `aprobado_uat03_controlada.py` separa fuentes HTTP (autocommit, maximo
un OAuth/producto por servicio, sin FORCE/redirect/retry) y diagnostico posterior
(READ ONLY, red bloqueada, rollback, sin auditorias/gates). Los scripts historicos
de 4/perfiles permanecen intactos. El helper comun usa ahora el resolvedor.

Tras aprobar/desplegar el cambio y su migracion, con bundle verificado en /tmp y
APROBADO_UAT_RELEASE_SHA igual al SHA autorizado, ejecutar primero SIN CONSUMO:

```bash
cd /root/aprobado_web
PYTHONDONTWRITEBYTECODE=1 APROBADO_UAT_HELPERS_DIR=/tmp \
APROBADO_UAT_MODO=diagnostico_ingreso APROBADO_UAT_SOLICITUD_ID=10 \
APROBADO_UAT_PROPIETARIO_ID=310 APROBADO_UAT_ACTOR_ID=1 \
APROBADO_UAT_RELEASE_SHA="$RELEASE_SHA_AUTORIZADO" \
/root/aprobado_web/venv/bin/python manage.py shell -c \
  'import runpy; runpy.run_path("/tmp/aprobado_uat03_controlada.py", run_name="__main__")'
```

La autorizacion individual de consumo sigue pendiente y separada. No consumir
con el runner anterior: exige ingreso manual y pierde el DTO candidato al cerrar.
No repetir #4/#9. No aplicar migraciones ni cargar bundle sin revision/autorizacion.

## Inventario historico del cambio (16/10)

```text
contractors/politica_ingresos.py
contractors/services/ingreso_evaluable.py
contractors/models.py
contractors/migrations/0022_configuracionscoreprestador_politica_ingreso_evaluable.py
contractors/admin.py
contractors/datacredito/adapter.py
contractors/datacredito/dto.py
contractors/score/componentes.py
contractors/services/evaluacion_formal.py
contractors/services/evaluacion_versionado.py
contractors/services/oferta_financiera.py
contractors/services/politica_financiera_prestador.py
contractors/services/politica_score.py
contractors/tests/test_ingreso_evaluable.py
contractors/tests/test_diagnostico_midecisor_uat.py
contractors/tests/test_aprobacion_interna_prestador.py
contractors/tests/test_centrales_duales_prestador.py
contractors/tests/test_evaluacion_errores_idempotencia.py
contractors/tests/test_orquestacion_evaluacion.py
contractors/tests/test_pipeline_financiero_e2e.py
contractors/tests/test_politica_prod_horizonte.py
diagnosticos/aprobado_uat03_controlada.py
diagnosticos/uat9_shadow.py
diagnosticos/test_uat03_controlada.py
diagnosticos/preflight_uat03_actual.ps1
docs/PRESTADORES_INGRESO_EVALUABLE.md
docs/PRESTADORES_UAT03_MEMORIA_Y_PROCEDIMIENTO.md
```

Los cambios anteriores de ROADMAP, captura documental, diagnostico #9 y demas
archivos locales ajenos permanecen conservados. No staged, commit, push o deploy.

## Validacion historica (16/10)

- PostgreSQL 16 oficial aislado, binding 127.0.0.1, usuario temporal,
  NAME uat03_bootstrap y TEST.NAME test_uat03_aislada, distintos de bases operativas.
  Sin .env, datos reales ni volumen persistente. HTTP/Celery interceptados.
- Matriz ampliada: 300/300 PASS en 253.138s, cero errores/fallos/omisiones;
  incluye concurrencia real de orquestacion/leases, P0/P1, HDC/MiDecisor,
  parser shadow, oferta, horizonte y gates directos. No son consumos reales.
- Tras los ultimos guards de configuracion/subcentavos: 32/32 PostgreSQL PASS
  en 56.997s (14 ingresos y 18 runner), con escenarios superpuestos a la matriz.
- SQLite memoria: 14/14 ingresos PASS en 31.408s.
- Check con esquema de tests: cero incidencias. Makemigrations check/dry-run:
  No changes detected. La migracion 0022 ya esta incluida, no se ejecuta en VPS.
- Revalidacion del contenido final: 90/90 PostgreSQL PASS en 127.266s, cero
  errores/fallos/omisiones (14 ingresos, 18 runner, 16 diagnostico anterior,
  15 score y 27 pipeline). Casos superpuestos a las corridas anteriores, no sumar
  esas cifras como pruebas unicas. Todos los contenedores propios eliminados.
- `git diff --check`: OK; imports/rutas de servicios validados por las suites,
  sintaxis Python y PowerShell revisada. HEAD conserva 4c536c1, sin staging.

Los fallos de las pasadas de desarrollo se corrigieron sin cambiar umbrales:
fixtures antiguos asumian bruto para score y neto para oferta, o ausencia manual
equivalente a ausencia total. Ahora se esperan revision por ingreso insuficiente,
oferta estimada no originable o NO_DETERMINABLE, segun las fuentes reales del fixture.
El fixture integrado de score/oferta usa evidencia sintetica de LA MISMA solicitud,
no ingreso de otra cuenta. La regresion previa de diagnostico sustituye ahora el
resolvedor completo cuando quiere representar ausencia de todas las fuentes.
