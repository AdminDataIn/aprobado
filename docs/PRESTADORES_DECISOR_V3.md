# Prestadores: decisor definitivo V3

Fecha: 2026-10-18. Base de codigo: `5d6bb3f8b5ce3edb503d8e5a8ec1c4a6d5884ec1`.
Implementacion local; este documento NO autoriza publicacion, adopcion,
activacion de flags, consumo ni despliegue productivos.

## Mandato y separacion de responsabilidades

La autorizacion expresa del responsable permite sustituir la decision del
analista SOLO con predecision oficial favorable completa, oferta viable,
ingreso documental validado vigente y sin revisiones/subsanaciones activas.
No autoriza exclusiones crediticias nuevas.

`docs/politica_decision_prestadores_v3.json` contiene el mandato canonico.
Su identidad es `prestadores-score-prod-v3-decision`; su catalogo real de
exclusiones esta VACIO. La infraestructura admite causales controladas, pero
no rechaza en produccion sin nueva autorizacion/version/publicacion explicitas.
Una exclusion sintetica en tests NO constituye politica aprobada de Riesgos.

La publicacion clona la politica legacy activa, sus bandas y configuracion,
sin editar V1/V2. Crea V3 INACTIVA y guarda el mandato y SHA256 en
`CambioPoliticaScorePrestadorAudit.snapshot_nuevo`. La adopcion individual
reutiliza el servicio vigente y rechaza solicitudes con auditorias/jobs/gates.
No adoptar V3 para #4, #9 o #10: tienen historicos y no cumplen ese contrato.
Una modificacion del mandato en codigo deja de coincidir con el publicado y
falla cerrado; no reinterpreta automaticamente una politica anterior.

## Matriz normativa

Los valores son los de la definicion PROD v1 versionada, heredados por V3,
NO nuevos umbrales del decisor. La ejecucion usa la politica efectiva en BD.

| Regla | Fuente | Dato/umbral existente | Resultado V3 | Autorizacion definitiva |
| --- | --- | --- | --- | --- |
| Predecision favorable y completa | Core score/predecision + autorizacion 2026-10-18 | Ambas fuentes oficiales EXITOSO, vigentes y legalmente utilizables; auditoria actual COMPLETADA/PREAPROBADO_READ_ONLY | Candidata a aprobar, no basta por si sola | Si, con TODOS los restantes controles |
| Bandas | parametros_score_prestadores_prod_pendientes.json / bandas BD | PREMIUM 850-1000, ALTA 750-849, MEDIA 680-749, ENTRADA 600-679; REVISION 0-599 | Usa banda y limites del core; REVISION sigue revision | No autoriza rechazo por banda |
| Limites de banda | Misma politica | PREMIUM 10M/8, ALTA 8M/8, MEDIA 5M/8, ENTRADA 3M/6; REVISION 0/0 | Nunca amplian producto, oferta u horizonte | No autoriza rechazo definitivo |
| Carga/ingreso | cuota_ingreso_maxima, accion_exceso_capacidad | 0.30; accion REVISION | Exceso mantiene revision; NO_EVALUABLE/SIN_OFERTA no son rechazo | No autorizada como exclusion |
| Mora | mora_bloqueo_dias y fuentes oficiales | 90 dias existentes; semantica informativa actual | Mantiene tratamiento del core/revision | No autorizada como exclusion |
| Consultas recientes | consultas_recientes_revision | 6 existentes; no crear veto nuevo | Mantiene tratamiento actual | No autorizada como exclusion |
| Ingreso | ingreso_evaluable / ingreso_neto | Documental MANUAL_VERIFICADA, evidencias y vigencia comprobadas | Obligatorio para aprobar; estimado solo oferta preliminar/revision | Si, como requisito, no causal de rechazo |
| Contrato/documentos | validacion_contractual + gate | Contrato vigente, identidad/coherencia, documentos obligatorios y horizonte existentes | Falta/contradiccion: revision | No autorizada como exclusion |
| Oferta | calcular_oferta_evaluada -> preparar_oferta | Sin formulas nuevas; monto/plazo/carga/ingreso/politica actuales | OFERTA_CALCULADA permite gate; SIN_OFERTA, NO_EVALUABLE y ERROR_CONTROLADO bloquean | Si, requisito para aprobar; no rechazo |
| Revisiones/subsanaciones | Servicios existentes | Ninguna activa/pendiente | Exige intervencion humana; no se omite | Si, requisito para aprobar |
| Pagador | aprobacion_pagador | Confirmaciones contractuales/operativas existentes | Mantiene gate humano separado | No se sustituye |
| Identidad/firma/originacion | expediente/originacion/formalizacion/postfirma | Evidencias y estados existentes | Sin firma, origen o desembolso automaticos | No se omiten |
| HDC SIN_INFORMACION | centrales_riesgo/politica | No hay carga monetaria acreditada | Revision/no evaluable; NUNCA deuda cero | No permite aprobar/rechazar por ausencia |
| MiDecisor HC14_TX05 | normalizador oficial | Significado PN de HC14 no confirmado | NO_EVALUABLE; shadow fuera del decisor | No permite aprobar/rechazar |

### Decisiones concretas pendientes de Riesgos

| Codigo implementado, DESHABILITADO | Dato/umbral ya existente | Tratamiento hoy | Decision futura posible |
| --- | --- | --- | --- |
| BANDA_REVISION | Banda efectiva REVISION 0-599 sobre score interno calculado | Revision manual | Mantener revision o autorizar exclusion explicita; requiere nueva version |
| CAPACIDAD_EXCEDIDA | relacion_carga_ingreso > cuota_ingreso_maxima (0.30 actual) | Revision/SIN_OFERTA segun core | Mantener revision o autorizar exclusion explicita; requiere nueva version |
| MORA_SEVERA | Mora severa oficial y dias >= mora_bloqueo_dias (90 actual) | Tratamiento actual de riesgo | Mantener revision o autorizar exclusion explicita; requiere nueva version |

Ni esta tabla ni el codigo habilitan esas exclusiones reales. Tampoco se
proponen nuevos umbrales. Una revision asignada/en analisis o de otra auditoria
nunca se cierra automaticamente por una exclusion. La exclusion sintetica
solo cierra revisiones derivadas de la misma auditoria, abiertas y no asignadas;
la compatibilidad real de politica/simulacion se revalida separadamente.

## Integracion y trazabilidad

`evaluar_solicitud_prestador` conserva su DTO y auditoria de riesgo read-only.
Solo para V3, al completar/reutilizar la evaluacion, invoca
`decidir_solicitud_prestador`. No existe un segundo motor de score/capacidad.

El decisor bloquea solicitud y auditoria; reutiliza fuentes oficiales y el
resolvedor de ingreso y calcula oferta mediante el servicio financiero actual.
Solo si todos los controles pasan llama al gate/aprobacion ya existentes.
Si el actor tecnico carece de facultad de aprobacion/cierre, deriva a revision;
el permiso de evaluar no se convierte en facultad para aprobar.

Los eventos privados `DECISION_FINANCIERA_V3` registran resultado, causales
controladas, actor, timestamp, auditoria, gate, versiones, fuentes, hash de
contexto y vigencia. No contienen score, ingresos, texto del proveedor ni PII.
El contexto incluye evidencia efectiva, revisiones/subsanaciones, configuracion
y mandato. Un cambio/caducidad invalida la reutilizacion. El validador de gates
exige que una aprobacion automatica siga vigente antes de pagador/expediente/
originacion. Las auditorias de riesgo y eventos anteriores no se reescriben.

Un error inesperado revierte la transaccion del decisor. La auditoria de riesgo
ya finalizada permanece y permite un reintento sin nueva consulta externa.
Una lectura repetida no recrea la revision cerrada de un rechazo V3 vigente.

## Experian: unico bloqueo contractual especifico

Fuente: `DocsIntegracionDatacredito/MiDecisor/Servicio/Swagger MiDecisor.yaml`,
InfoTransaccion.codigosRespuesta, lineas 719-759, y DecisorServicio.pdf p.12.
TX01-08 indica exito transaccional, pero NO garantiza modulo HC utilizable.
El Swagger no define HC14 PN y su descripcion generica de HC13 contradice
ejemplos/compatibilidad existentes. No extrapolar HC14 de Historia de Credito+.

Consulta minima a Experian: para MiDecisor PN HC14/TX05, envelope ACCEPTED,
validacion.conInformacion=True e informacionRiesgo.conInformacion=True, confirmar
por escrito si informacionRiesgo es utilizable y que campo/codigo acredita su
validez, incluyendo escala/modelo/sentinelas de score. Hasta entonces HC14
oficial permanece indeterminado; la presencia de score no acredita aprobacion.

## Operacion pendiente de autorizacion

No hacen falta tablas, campos ni migraciones. No se publica/adopta V3 durante
este desarrollo. V1/V2 y solicitudes existentes permanecen sin cambios.

Comando de preparacion (sin escrituras; salida DRY_RUN_NO_VALIDADO):

```bash
venv/bin/python manage.py gestionar_politica_ingreso publicar \
  --origen-id 1 --actor-id 1 --vigente-desde 2026-10-18 \
  --version-destino prestadores-score-prod-v3-decision \
  --motivo 'Preparacion V3 con mandato autorizado y catalogo excluyente vacio' --dry-run
```

Despues de aprobar especificamente el despliegue y operacion, repetir con
`--aplicar` publica INACTIVA. No inventar el ID nuevo. Adoptar solo una solicitud
pendiente elegible y autorizada con el ID devuelto; no forzar adopcion sobre
historicos. Los permisos de configuracion de politica, evaluacion, decision
interna y resolucion son independientes. No habilitar flags para probar gates.

Revision real #3: consulta SSH con transaccion READ ONLY confirmo solicitud #10,
propietario interno 310, auditoria #6 COMPLETADA/NO_EVALUABLE, revision ABIERTA
por DATACREDITO_ERROR, cero gates y flags False/False. Actor #1 tiene permisos
de bandeja, subsanacion y resolucion. No se ejecutaron transiciones ni HTTP.
El operador puede usar bandeja y subsanacion existentes; cerrar una revision
no convierte una fuente indeterminada en preaprobacion ni permite originar.

## Evidencia de pruebas

PostgreSQL Docker desechable, postgres:16-alpine, bind 127.0.0.1:puerto efimero,
datos sinteticos, TEST.NAME separado, tmpfs y credenciales temporales. HTTP,
publicacion Celery y canales reales bloqueados; firma sustituida por cliente
de prueba. Se ejercitan aprobacion, rechazo SOLO sintetico, revision, permisos,
caducidad, cambios de evidencia/mandato, V1/V2 intactas, duplicados y tres
concurrencias reales. Formalizacion incluye pagador humano sintetico, expediente,
origen, pagare, identidad, firma/webhook duplicado y ACTIVO simulado.

Resultados de esta ejecucion:

- 53/53 PostgreSQL: decisor inicial, pipeline financiero V1/V2 y cierre vertical.
- 133/133 PostgreSQL, 0 FAIL/ERROR/SKIP: gate final con el decisor actualizado,
  publicacion historica, idempotencia, snapshots, HDC, MiDecisor y shadow.
  Incluye cinco concurrencias reales: decisor (3) y errores recientes (2).
- 8/8 navegador Chromium/Edge: seguimiento, estados seguros, loader, cierre y
  ajuste responsive a 390 y 1280 px. No certifica iPhone/Android fisicos.
- Django check e imports: PASS. makemigrations --check --dry-run: sin cambios.
- git diff --check: PASS. Sin cambios en modelos, migraciones, score, formulas,
  normalizadores oficiales ni shadow.

El gate final se ejecuto sobre 127.0.0.1:57806 (puerto temporal), NAME
uat03_bootstrap y TEST.NAME test_uat03_aislada, usuario uat03_temporal. Docker y
tmpfs propios eliminados al finalizar. El primer bloque uso otro contenedor
efimero, tambien eliminado. No se reutilizo PostgreSQL operativo ni .env.

Comando Django del gate final (solo con configuracion aislada previamente
verificada y guardas de red/Celery; NO ejecutar sobre settings operativos):

```bash
python manage.py test \
  contractors.tests.test_decision_definitiva \
  contractors.tests.test_politica_ingreso_publicacion \
  contractors.tests.test_evaluacion_errores_idempotencia \
  integrations.tests.test_hdcplus_normalizacion_dual \
  integrations.tests.test_midecisor_clasificacion \
  integrations.tests.test_midecisor_shadow \
  integrations.tests.test_datacredito_snapshot_v2
```

Inventario del candidato: comando gestionar_politica_ingreso; politica_decision;
servicios aprobacion_interna, decision_definitiva, evaluacion_formal,
evaluacion_timeline, politica_ingreso_publicacion y presentacion_solicitud;
test_decision_definitiva; este documento y el JSON canonico V3. Once archivos.
No incluir los pendientes previos de roadmap, captura ni diagnosticos UAT.
El SHA candidato se informa en el cierre. Esta evidencia sintetica NO declara
aprobado el E2E operativo real ni habilita rechazos productivos.
