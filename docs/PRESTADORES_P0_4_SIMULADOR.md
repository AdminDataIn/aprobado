# P0-4: simulador y captura mobile

> Vigente desde P0-4C.3 (2026-09-30): usar la politica cerrada del apartado final
> y `parametros_prestadores_prod_aprobados.json`. Los ejemplos DEMO y la plantilla
> pendiente P0-4C.2 se conservan como antecedentes, NO como instrucciones PROD.

## Disponibilidad

`obtener_configuracion_simulador_prestador()` selecciona la configuracion financiera
de la politica score activa y vigente, si existe. Esa configuracion debe estar
activa y versionada. Sin politica vigente selecciona la unica configuracion
financiera activa con version no vacia. No modifica ni activa score.

Una configuracion faltante, inactiva, sin version, o una politica vigente con enlace
invalido explica el estado no disponible. No puede determinarse cual ocurre en
produccion sin consultar su configuracion. No se consulto ni modifico produccion.

El Admin existente permite configurarla en
`/admin/contractors/configuracionsimuladorprestador/`, con sus permisos habituales.
No basta crear una fila con los defaults: requiere version y parametros aprobados.
Si existe politica vigente, revisar su enlace sin sustituirla automaticamente.

## Parametros documentados y habilitacion

La seccion 27 de `prestadores_flujo_end_to_end.md` documenta los siguientes valores
**DEMO**, expresamente no aprobados como politica productiva. El default historico
del modelo (1.9%) no equivale a la tasa documentada (2.2%). No se activan al desplegar.

Ejemplo JSON para validacion/UAT con esos parametros:

```json
{
  "version": "prestadores-simulador-uat-v1",
  "monto_minimo": "1000000",
  "monto_maximo": "10000000",
  "plazo_minimo_meses": 3,
  "plazo_maximo_meses": 8,
  "tasa_mensual": "2.2000",
  "porcentaje_originacion": "10.0000",
  "porcentaje_iva_originacion": "19.0000",
  "porcentaje_fondo_garantia": "2.0000",
  "porcentaje_seguro_vida_primera_cuota": "0.3711"
}
```

El operador suministra el JSON autorizado. Primero confirmar entorno/BD y validar:

```sh
python manage.py configurar_simulador_prestadores --archivo parametros.json
```

La validacion usa una transaccion revertida, sin conservar cambios. Para persistir,
solo tras autorizacion de los parametros y del entorno:

```sh
python manage.py configurar_simulador_prestadores --archivo parametros.json --aplicar
```

El comando no reemplaza otra configuracion activa ni reescribe una version existente.
Repetir con iguales valores es idempotente. Rechaza el enlace incompatible de una
politica vigente y revierte la operacion. No crea bandas, score, creditos ni cuotas.
No ejecutar el bootstrap de politica DEMO para habilitar solamente este simulador.

## Verificacion

Solicitud propia con documentos y analisis vigentes -> GET simulacion -> calculo
backend con `calcular_componentes_financieros` -> POST monto/plazo -> snapshot
persistido y Mi credito. Sin configuracion: mensaje seguro, calculo HTTP 503 y sin
fallback financiero. No se declara cerrado el E2E de riesgo/originacion.

El mojibake estaba en literales del template de ausencia de configuracion; se
corrigieron en UTF-8 y se prueban charset HTTP, meta charset y ambas ramas del HTML.

La camara conserva object-fit contain y resolucion intrinseca; el layout inmersivo
usa dvh/safe-area, guia ID-1 y controles superpuestos. El cambio de camara detiene
el stream anterior y reutiliza la sesion; no altera canje, TTL, polling ni retorno.
Playwright no sustituye la validacion fisica de enfoque/permisos en iPhone/Safari.

## P0-4C.2: semantica aprobada y plantilla PROD pendiente

Definiciones recibidas el 2026-09-30: monto minimo $500.000, maximo $3.000.000,
tasa mensual 1,0000%, originacion 10%, IVA 19% exclusivamente sobre originacion,
fondo Figarantias 2%. Seguro SURA financiado, porcentaje aun NO aprobado.
El plazo del ejemplo (3) no define limites productivos.

Plantilla: `docs/parametros_prestadores_prod_pendientes.json`. Version, seguro,
plazo minimo y plazo maximo permanecen `null`. El comando rechaza cualquier
parametro requerido nulo antes de consultar/escribir configuracion. No se
aplico esta plantilla, no se activaron defaults ni DEMO como politica productiva.
La seleccion de configuraciones existentes se conserva; desplegar este codigo
NO convierte una configuracion DEMO existente en configuracion PROD aprobada.
Antes de habilitar produccion debe verificarse explicitamente la version activa.

Unica formula: `gestion_creditos.services.condiciones_financieras.calcular_componentes_financieros`.
El simulador y la capacidad preliminar usan esa funcion a traves de
`simular_credito_prestador_informativo`; ya no se calcula cuota sobre monto base
sin cargos. Monto/plazo fuera de configuracion no producen una cuota de capacidad.
No se ha implementado ni ejecutado score, centrales o predecision en este bloque.

Desembolso neto = monto solicitado (redondeado a centavos). Originacion, su IVA,
fondo y seguro se financian dentro del capital y generan intereses; ninguno se
descuenta del desembolso ni se cobra separado o exclusivamente en la primera cuota.
Se conserva `porcentaje_seguro_vida_primera_cuota` como nombre historico compatible
con BD, JSON y snapshots. No describe una prima exclusiva de la primera cuota.
No se renombra ni modifica su default historico mediante migracion; este default
NO es aprobacion del porcentaje SURA productivo. `comision` sigue como nombre
interno del core compartido; la UX Prestadores usa originacion.

Calculo en Decimal con ROUND_HALF_UP a centavos. Ejemplo de test, NO configuracion
productiva: monto 1.000.000, seguro 3.711, plazo 3, tasa mensual 1%. Capital
1.142.711, cuota 388.547,01, total 1.165.641,03, intereses 22.930,03. El centavo de
diferencia frente a la referencia aproximada se debe a total = cuota redondeada
por numero de cuotas. No se altera la formula ni los snapshots historicos.

El navegador no replica la formula con floats: solicita el resultado al endpoint
existente y solo formatea los importes. Al cambiar sliders invalida resultados
anteriores y descarta respuestas atrasadas; sin respuesta no inventa una cuota.
La UX muestra monto recibido, cargos financiados, tasa, capital, cuota, numero de
cuotas y total con centavos.

P0-4E.2 sigue obligatorio antes de P0-5: saldo declarado no prueba pagos recibidos;
separar causado, exigible, recibido/verificado y flujo futuro. Esta alineacion de
cuota no certifica capacidad final. La brecha de auditoria actor/motivo del comando
identificada en P0-4C.1 no se corrige ni se da por cerrada en este ticket.

## P0-4C.3 + P0-4E.2: PROD v1 y horizonte contractual

Politica cerrada por negocio: `prestadores-prod-v1`, monto 1M-10M, plazo 1-8,
tasa mensual 2,2000%, originacion 10%, IVA 19% solo sobre originacion,
Figarantias 2% y SURA 0,3711%. Todos los cargos se financian. Desembolso neto
igual al monto solicitado; formula Decimal central sin cambios. Esta aprobacion
sustituye los valores provisionales 500.000-3M y 1% de P0-4C.2. No se aplica
configuracion al desplegar codigo ni se convierte DEMO en PROD.

JSON completo: `docs/parametros_prestadores_prod_aprobados.json`.

```sh
python manage.py configurar_simulador_prestadores --archivo docs/parametros_prestadores_prod_aprobados.json
```

Sin `--aplicar`: rollback de la validacion, ninguna activacion persistida. En
PostgreSQL puede consumir secuencias. Si existe otra configuracion activa, rechaza
el cambio sin reemplazarla. Validado en tests con BD aislada vacia y con DEMO.
El dry-run sobre la BD local existente fue bloqueado por otra configuracion activa;
no se desactivo ni modifico. No ejecutar en produccion en este ticket.
El comando verifica que la version PROD v1 coincida con todos los valores aprobados.
Auditoria actor/motivo del comando sigue como deuda separada; no se declara resuelta.

### Calendario y semantica temporal

`gestion_creditos.services.horizonte_contractual.calcular_horizonte_contractual`
es puro: corte explicito, sin ORM, sin IA, sin ingresos presumidos. Construye meses
calendario completos con importe periodico y fecha de pago respaldada. Reconoce
fin de mes o primeros N dias habiles del mes siguiente. Para dias habiles usa lunes
a viernes excluyendo festivos nacionales colombianos con `holidays==0.83`, sin
llamadas externas en runtime (fuente tecnica: https://holidays.readthedocs.io/en/latest/auto_gen_docs/colombia/).
El ultimo dia de la ventana de pago define exigibilidad; hasta ese dia no se presume
vencimiento anticipado. Si el contrato define sabados laborables, excepciones locales,
periodos parciales, otra periodicidad o solo dice 'primeros dias' sin N, requiere
calendario confirmado: se bloquea, no se inventa fecha. No inferir N=5 por defecto.

Devuelve periodos totales, causados (fin de periodo <= corte), exigibles (fecha de
pago <= corte), futuros (fecha de pago > corte), fechas e importes de flujos y
horizonte disponible. Estas cantidades no son categorias disjuntas: un periodo
causado puede tener pago futuro. Valor programado al corte es el valor de periodos
completamente causados; no se prorratea un periodo en curso. EXIGIBLE != PAGADO.
Los valores pagado al corte de fuente, actual declarado y actual verificado se
conservan separados; ninguno se deduce de fechas. El adaptador no promueve el
campo declarado a evidencia verificada. No hay campo de corte documental fiable
en los datos historicos: no se inventa ni se usa fecha de analisis como fecha de pago.

Ejemplo: 01/08/2026-31/07/2027, 12 pagos de 10.400.000, primeros cinco dias habiles
del mes siguiente. Al 29/09/2026: 1 periodo causado/exigible y 11 flujos futuros;
agosto exigible 07/09/2026, septiembre 07/10/2026. El ultimo flujo es en agosto
2027. Un cero extraido al firmar no es cero actual permanente.

El parser conserva la clausula expresa del PDF en `evidencia_forma_pago`; contratos
ya analizados cuya evidencia no incluye calendario requieren reanalisis/confirmacion,
no backfill automatico. Sin nuevos campos/modelos/migraciones.

### Integracion y oferta preparada para P0-5

Para version PROD v1: formulario GET, endpoint de calculo y POST final limitan a
MIN(8, flujos futuros), restringido ademas por las fechas reales del cronograma.
Revalidacion bajo lock de solicitud antes del snapshot.
Con cero flujos o calendario incompleto se informa el bloqueo; no se dibuja un
slider con minimo mayor al maximo. Se conservan configuraciones y snapshots
historicos: esta regla nueva no se aplica retroactivamente a otras versiones.
Fin formal vencido no elimina un ultimo flujo posterior respaldado por contrato;
si tambien paso el ultimo flujo, no se permite simular.

`preparar_oferta` en `contractors.services.politica_financiera_prestador` NO esta
conectado funcionalmente a endpoints ni a la predecision productiva. Su contrato
recibe `ResultadoScorePrestador` producido por `evaluar_score_prestador`, la misma
`ConfiguracionScorePrestador` y la configuracion financiera vinculada. Valida las
versiones y resuelve la banda mediante `buscar_banda` / `BandaScorePrestador`.
No existe un catalogo paralelo de bandas en el servicio de oferta; no calcula
componentes, ponderaciones ni un segundo score. El motor existente no fue reemplazado.
Recibe ingreso neto valido con fuente/fecha; no lo obtiene ni presume del contrato.
Ingreso mensual contractual, ingreso verificado y neto valido para riesgo no son
equivalentes. La verificacion de procedencia y vigencia del ingreso queda en P0-5.

Capacidad mensual = MAX(0, ingreso neto valido - obligaciones) * 30%.
Busca el monto maximo en centavos con la MISMA cuota financiera real del simulador,
incluyendo cargos; no usa PV sobre monto base. Limita monto por solicitud, producto,
banda y capacidad. Si capacidad/monto resultante <1M, devuelve sin oferta, no eleva.
Plazo ofertable = MIN(solicitado, producto, politica, banda, horizonte), ajustado
para que ultima cuota <= ultimo flujo contractual respaldante. Es calculo, no aprobacion.

### Consolidacion post-score y pendientes P0-5

El unico motor 0-1000 continua siendo `contractors.score.motor.evaluar_score_prestador`.
Se conservan componentes, pesos, snapshots, auditorias y predecision formal.
Los tests integrados usan entradas sinteticas sin proveedores y bandas persistidas;
cambiar un tope en una politica de test cambia la oferta sin modificar codigo.

La configuracion PROD debera tener las siguientes filas de `BandaScorePrestador`
asociadas a su `ConfiguracionScorePrestador`, mediante el admin/mecanismo existente:

| Banda | Score | Monto maximo | Plazo maximo |
|---|---|---|---|
| PREMIUM | 850-1000 | 10.000.000 | 8 |
| ALTA | 750-849 | 8.000.000 | 8 |
| MEDIA | 680-749 | 5.000.000 | 8 |
| ENTRADA | 600-679 | 3.000.000 | 6 |
| REVISION | 0-599 | 0 | 0 |

Esto es especificacion de configuracion, no un catalogo ejecutable adicional.
No se crean ni activan estas filas en la BD operativa en este ticket. El comando
del simulador sigue sin crear/activar score. Las bandas DEMO historicas no se alteran.

- `capacidad_componente_score`: conserva el calculo dual historico
  MAX(0, ingreso contractual * limite - obligaciones); sigue siendo variable del
  motor y parte de su calibracion. Sus meses hasta fin contractual no se sustituyen.
- `capacidad_crediticia_oferta` (`cuota_maxima` en el resultado):
  MAX(0, ingreso neto valido - obligaciones) * 30%; limita la cuota real financiada.
  NO cambia los puntos del componente capacidad del score.
- Sin ingreso neto valido: estado `NO_EVALUABLE`, monto/plazo cero; nunca fallback
  a `valor_mensual_contractual`. Procedencia definitiva, verificacion y vigencia
  del neto siguen pendientes de P0-5. Contrato mensual != ingreso neto verificado.
- Sin capacidad minima, score bloqueado/revision o respaldo temporal: `SIN_OFERTA`.
  Una oferta calculada no implica aprobacion ni originacion.

`OfertaCalculada.como_dict()` conserva banda/PK, versiones, capacidad del score,
limite de oferta y fechas, serializable para la estructura de snapshot existente
de `PredecisionPrestadorAudit`. No se crea una segunda predecision/auditoria.
La conexion funcional al pipeline formal queda deliberadamente pendiente hasta
definir la fuente de ingreso neto en P0-5; los topes historicos del motor siguen
operando en el flujo existente y no se deben encadenar como otra politica PROD.

La proyeccion de vencimientos reutiliza `calcular_primera_fecha_pago_libranza`
y `sumar_meses_con_dia_ancla`, usados por el core para Prestadores. Corte explicito:
al dia 14 la primera cuota es el dia 1 del mes siguiente; despues del 14, del
segundo mes siguiente. Ejemplo: corte 29/08/2026, cuatro flujos hasta 07/12/2026:
cuota 4 venceria 01/01/2027 y se reduce el plazo a 3 (ultima 01/12/2026).
La igualdad con el ultimo flujo se admite; una fecha posterior no. Esta proyeccion
no promete fecha de desembolso: al conectar originacion/activacion debera revalidarse
con la fecha efectiva y cualquier primera cuota forzada; esa conexion no se implementa aqui.
El horizonte nuevo limita simulacion/oferta, NO recalibra el score existente.
La alineacion eventual de capacidad e ingreso del score requiere contraste P0-5.

### XLSX: referencia computacional e inconsistencias

Leido `Score_Ajustado_Politica_Aprobado.xlsx`, hoja `Simulador Fintech`:
- B24: topes 10M / 8M / 5M / 3M para >=850 / >=750 / >=680 / >=600.
- B27: plazo 8 / 8 / 8 / 6; B6 limita producto a 8.
- B10: 0,30; B22 resta obligaciones; B26 toma el menor monto.
- J18/J20 conservan 7,5M/2,5M; K19/K20 conservan 6/4: NO se usan.
- B25/B30 usan PV/PMT sobre monto: se respeta el core aprobado que incluye cargos,
  no se traslada literalmente esa omision del XLSX.
Bandas <600: REVISION, sin oferta automatica. No se modifica el XLSX ni se ejecuta
score real, predecision productiva, centrales o aprobacion. Pendiente UAT/PostgreSQL, integracion
de ingreso neto P0-5 y calendarios contractuales no cubiertos. No cerrar E2E.
