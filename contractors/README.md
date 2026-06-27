# Contractors

Contexto acotado para el portal unico de contratistas de Aprobado.

## Definicion Actual De Negocio

`contractors` ya no se modela como multiples portales por empresa ni como una
linea generica separada. El portal publico correcto es:

- `contratistas.aprobado.com.co`
- en local, solo para desarrollo: `contratistas.localhost` cuando `DEBUG=True`

No deben existir experiencias publicas por empresa del tipo:

- `datain.aprobado.com.co`
- `acme.aprobado.com.co`
- `<slug>.aprobado.com.co`

El credito para contratistas debe evolucionar como credito por libranza /
adelanto con reglas propias de contratistas:

- score interno
- DataCredito futuro
- ley de libranza
- capacidad basada en contrato vigente
- valor pendiente por cobrar del contrato
- regla de 40% pagado para recogida de cartera
- pagador como responsable de pago, novedad de nomina u honorarios

El pagador no aprueba el credito. El pagador recibe la novedad y paga segun el
flujo operativo definido.

La empresa contratante/pagador no se captura como texto libre en el portal
publico. El contratista debe seleccionar una `Empresa` existente del core
`gestion_creditos`, con convenio activo y tipo compatible con libranza. El
portal de contratistas no crea empresas, pagadores ni convenios.

## Arquitectura Visual Publica

La landing publica principal vuelve a ser `/libranza/`. Contractors no tiene
landing publica duplicada ni se presenta como producto visual independiente.

El subdominio `contratistas.aprobado.com.co` se mantiene solo como entrada al
flujo de solicitud contratista:

1. `/` redirige a `/solicitar/`.
2. `/solicitar/` exige login/registro antes de mostrar el formulario.
3. El usuario registra datos personales y carga documentos obligatorios desde el
   inicio del formulario.
4. El usuario selecciona empresa existente y confirma informacion contractual.
5. La vista crea la pre-solicitud y redirige a la simulacion asociada.

El formulario, documentos y simulador reutilizan estilos de libranza, pero ya no
existe hero, FAQ, requisitos ni CTA final propios de contractors.

## Diagnostico Del Codigo Existente

Se conserva como valido:

- `ContractorApplication` como pre-solicitud aislada
- `ContractorApplicationDocument`
- servicios de creacion de solicitud
- servicios documentales
- revision documental
- validaciones de archivo
- admin interno
- evaluacion read-only de elegibilidad documental

Queda congelado o marcado como deuda:

- multiples subdominios por organizacion
- multiples landings por empresa
- simulador publico antes de registro
- `ContractorProductConfig` como producto independiente por tenant
- conversion directa a `Credito` sin logica de libranza, adelanto y pagador
- idea de `Credito.LineaCredito.CONTRATISTA`
- pagaré generico distinto sin ley de libranza

## Configuracion Semantica Del Portal

La fuente semantica nueva para el portal unico es:

- `ConfiguracionPortalContratistas`

Este modelo consolida lo que antes estaba repartido en:

- `ContractorOrganization`
- `ContractorBranding`
- `ContractorProductConfig`

Debe existir una sola configuracion activa por `host`. El host productivo
esperado es `contratistas.aprobado.com.co`. En local `contratistas.localhost`
funciona con `DEBUG=True`; si no existe una configuracion local exacta, el
middleware puede usar la configuracion activa con `slug=contratistas`.

`ContractorOrganization`, `ContractorBranding` y `ContractorProductConfig`
quedan como modelos legacy/congelados por compatibilidad. No se borran, no se
renombran y no deben usarse para nuevas vistas publicas.

`ContractorApplication` ahora puede apuntar a `configuracion_portal`. La FK
legacy `organization` queda nullable para permitir pre-solicitudes nuevas sin
depender conceptualmente de organizaciones por subdominio.

Si se necesita conservar compatibilidad con solicitudes antiguas, una
`ContractorOrganization` heredada puede seguir existiendo con:

- `slug = contratistas`
- `subdomain = contratistas`
- `is_active = True`

Renombrar o borrar modelos legacy no se hace todavia para evitar migraciones
ruidosas y riesgo sobre datos existentes.

## Rutas Publicas

Rutas actuales bajo `contratistas.aprobado.com.co`:

- `/`: redirige a `/solicitar/`
- `/login/`: acceso personalizado reutilizando la experiencia de libranza
- `/registro/`: registro personalizado reutilizando la experiencia de libranza
- `/solicitar/`: crea pre-solicitud controlada con documentos iniciales
- `/solicitud/<solicitud_id>/documentos/`: carga versiones adicionales o reemplazos documentales
- `/simular/?solicitud_id=<id>`: simulacion asociada a una pre-solicitud existente
- `/mi-credito/`: redirige al panel existente de libranza/personas

El dominio raiz `aprobado.com.co` no debe exponer rutas de contractors.

`/simular/` ya no debe usarse como entrada publica directa. Si no existe una
pre-solicitud asociada, la vista redirige a `/solicitar/`.

## QA Local

Para probar el flujo local de Prestadores de Servicios sin depender de Google
OAuth ni de configuracion manual en admin, use:

```bash
python manage.py seed_prestadores_qa_local --host contratistas.localhost:8000
```

El comando es idempotente y crea o actualiza:

- `ConfiguracionPortalContratistas` activa para el host indicado.
- organizacion, branding y producto legacy minimo para compatibilidad interna.
- `Empresa Demo Prestadores SAS` con convenio activo.
- usuarios demo:
  - `admin@aprobado.local`
  - `solicitante@aprobado.local`
  - `pagador@aprobado.local`

En `DEBUG=True` el comando imprime la contrasena local demo. No envia correos,
no crea solicitudes, no consulta DataCredito, no crea creditos y no crea pagos.

Para probar como prestador use un usuario normal, por ejemplo
`solicitante@aprobado.local`. No cree manualmente un `Perfil contratista` para
el solicitante final: esos perfiles son roles internos de operacion
(`viewer`, `operator`, `manager`, `owner`) y no representan al usuario publico.

Flujo local recomendado:

1. Abra `http://contratistas.localhost:8000/login/`.
2. Ingrese con correo y contrasena local.
3. Abra `http://contratistas.localhost:8000/solicitar/`.
4. Seleccione `Empresa Demo Prestadores SAS` desde el buscador de empresas.

El login de Prestadores puede probarse con correo y contrasena. Google queda
oculto en este flujo local para evitar errores de OAuth por configuracion de
subdominio, sin afectar el login general de libranza.

## Simulacion Financiera Prestadores V2

La simulacion financiera de prestadores se ejecuta desde:

- `/simular/?solicitud_id=<id>`
- `/simular/calcular/`

La vista exige una pre-solicitud del usuario autenticado, documentos completos y
datos contractuales con empresa seleccionada. El frontend solo envía monto y
plazo; todos los calculos financieros se hacen en backend con
`simular_credito_portal_contratistas`.

La configuracion financiera vive en `ConfiguracionPortalContratistas`:

- monto minimo y maximo
- plazo minimo y maximo
- tasa mensual
- costo de originacion porcentual y fijo
- IVA del costo de originacion
- fondo de garantia FiGarantias
- IVA del fondo de garantia, incluido en el total cuando aplique
- factor de seguro de vida

En la UI publica se usa el termino `Costo de originacion`. Los campos internos
`tasa_comision` y `comision_fija` se conservan por compatibilidad historica, pero
no deben mostrarse como "comision" al solicitante.

Al aceptar la simulacion se crea un snapshot seguro en `SimulacionPrestador` y se
marca como aceptado. Tambien se actualiza la pre-solicitud con monto, plazo,
cuota estimada y `simulation_payload` calculado por backend. Este paso no crea
`Credito`, no crea `CreditoLibranza`, no genera plan de amortizacion productivo,
no toca pagos, no llama ZapSign y no desembolsa.

Caso de referencia V2:

- monto solicitado: `$10.000.000`
- plazo: `8` meses
- tasa mensual: `2,2%`
- costo de originacion: `10%`
- IVA originacion: `19%`
- FiGarantias: `2%` IVA incluido
- seguro de vida: `0,3711%`
- capital total financiado esperado: `$11.427.110`
- cuota mensual aproximada: `$1.573.387,58`

## Flujo Objetivo

Flujo correcto:

1. Login/registro.
2. Registro de pre-solicitud contratista con datos personales.
3. Carga temprana de documentos obligatorios.
4. Seleccion de empresa de convenio existente y datos contractuales.
5. Simulacion asociada a la pre-solicitud.
6. Validacion documental interna.
7. Evaluacion de capacidad contractual.
8. Score interno.
9. DataCredito futuro.
10. Evaluacion de ley de libranza / adelanto.
11. Aprobacion interna.
12. Pagare contratista/libranza.
13. Firma por ZapSign con validacion de identidad obligatoria.
14. Notificacion de novedad al pagador.
15. Pendiente transferencia.
16. Desembolso / activacion.

No esta implementado todavia:

- score completo
- DataCredito
- pagaré contratista/libranza
- ZapSign con validacion de identidad obligatoria
- conversion a `Credito`
- notificacion a pagador
- transferencia
- activacion

## Datos Contractuales

Ya existe soporte interno para registrar datos laborales/contractuales asociados
a una pre-solicitud:

- cargo
- tipo de contrato
- fecha de inicio del contrato
- fecha de fin del contrato
- valor total del contrato
- valor pagado del contrato
- valor pendiente por cobrar
- empresa contratante seleccionada desde `gestion_creditos.Empresa`

Modelo actual:

- `InformacionLaboralSolicitudContratista`

Servicio actual:

- `registrar_datos_contractuales_contratista`
- `calcular_valor_pendiente_contrato`
- `evaluar_capacidad_contractual_contratista`

Selectors actuales:

- `obtener_datos_contractuales_solicitud`
- `solicitud_tiene_datos_contractuales`

Estos datos son base para capacidad, ley de libranza y validacion de recogida
de cartera. Todavia no estan conectados a score, DataCredito, decision
productiva, notificacion a pagador ni conversion a `Credito`.

Los campos legacy `empresa_contratante_nombre`, `empresa_contratante_nit`,
`pagador_nombre`, `pagador_email` y `pagador_telefono` se conservan para
compatibilidad historica, pero no se piden al usuario en la UI publica nueva.
La fuente operativa es la FK `empresa`.

## Capacidad Contractual

Ya existe evaluacion read-only de capacidad contractual:

- servicio `evaluar_capacidad_contractual_contratista`
- DTO `ResultadoCapacidadContractualContratista`
- helper `calcular_meses_restantes_contrato`

Reglas actuales:

- la pre-solicitud debe tener datos contractuales/laborales
- el contrato no debe estar vencido
- `valor_pendiente_cobrar` debe ser mayor a cero
- `requested_amount` no debe superar `valor_pendiente_cobrar`
- `term_months` no debe exceder los meses restantes del contrato
- la capacidad maxima estimada actual es el valor pendiente por cobrar

Esta evaluacion no usa DataCredito, no usa score, no crea credito, no modifica
estados y no notifica al pagador. La integracion con ley de libranza productiva
y capacidad completa queda pendiente para una fase posterior, cuando existan
datos suficientes de ingreso/honorarios, cuota proyectada y reglas finales.

## Predecision Consolidada Read-Only

Existe un servicio de predecision consolidada que combina el estado de una
pre-solicitud antes de cualquier decision productiva:

- servicio `evaluar_predecision_contratista`
- DTO `ResultadoPredecisionPrestador`
- alias legacy `ResultadoPredecisionContratista`

La predecision evalua:

- elegibilidad documental con `evaluar_elegibilidad_conversion_contratista`
- capacidad contractual con `evaluar_capacidad_contractual_contratista`
- riesgo con credito previo localizado por documento del solicitante
- el escenario solicitado en `ContractorApplication.escenario_credito`
- escenarios compartidos desde `libranza.escenarios_credito`
- segundo credito usando `risk.services.second_credit` solo si el escenario es `SEGUNDO_CREDITO`
- recogida de cartera usando `risk.services.portfolio_takeover` solo si el escenario es `RECOGIDA_CARTERA`
- score interno read-only con `contractors.score`
- DataCredito como `PENDIENTE`

La salida consolidada incluye:

- `eligible`
- `decision`
- `razones`
- `bloqueos`
- `advertencias`
- `escenario_credito`
- `documental_status`
- `capacidad_status`
- `riesgo_status`
- `datacredito_status`
- `score_status`
- `score_resultado`
- `datacredito_resultado`
- `capacidad_resultado`
- `segundo_credito_resultado`
- `recogida_cartera_resultado`
- `monto_maximo_sugerido`
- `plazo_maximo_sugerido`
- `requiere_revision_manual`
- `fuente=predecision_prestadores_read_only`

Decisiones posibles:

- `PREAPROBADO_READ_ONLY`: documental, capacidad, riesgo, DataCredito y score no tienen bloqueos.
- `REQUIERE_REVISION_MANUAL`: no hay bloqueo critico, pero falta DataCredito o el score requiere revision.
- `BLOQUEADO_READ_ONLY`: capacidad, riesgo o DataCredito tienen bloqueo critico.
- `INCOMPLETO`: faltan documentos criticos o informacion documental requerida.

Escenarios actuales:

- `NUEVO_CREDITO`: aplica cuando no hay credito previo o no se pretende usar uno.
- `SEGUNDO_CREDITO`: el credito anterior sigue activo y el contratista solicita otro credito adicional.
- `RECOGIDA_CARTERA`: el nuevo credito recoge/paga el saldo anterior y calcula desembolso neto.

Estos valores no son exclusivos de prestadores. Viven en
`libranza.escenarios_credito` porque aplican a toda libranza: libranza
tradicional, prestadores de servicios y futuros canales. `ContractorApplication`
mantiene `EscenarioCredito` como alias compatible para no cambiar valores
guardados ni contrato publico.

Siguen siendo especificos de prestadores y no se mueven a libranza:

- score interno parametrizable
- adapter DataCredito de contractors
- predecision consolidada read-only de prestadores
- capacidad contractual por contrato vigente
- IA contractual
- documentos de prestador

Reglas actuales:

- si falla documental, la predecision no es elegible
- si falla capacidad contractual, la predecision no es elegible
- si falla documental, la decision queda `INCOMPLETO` y no se evalua score
- si falla capacidad contractual, la decision queda `BLOQUEADO_READ_ONLY`
- en `NUEVO_CREDITO`, si no existe credito previo, riesgo no bloquea
- en `NUEVO_CREDITO`, si existe credito previo, bloquea con `credito_previo_existente_requiere_escenario`
- en `SEGUNDO_CREDITO` y `RECOGIDA_CARTERA`, si no existe credito previo, bloquea con `no_existe_credito_previo`
- en `SEGUNDO_CREDITO`, solo se evalua segundo credito: minimo 40% pagado, sin mora y capacidad si la regla aplica
- en `RECOGIDA_CARTERA`, solo se evalua recogida de cartera: minimo 40% pagado, sin mora, saldo pendiente y desembolso neto
- si DataCredito trae mora severa, la decision queda `BLOQUEADO_READ_ONLY`
- si DataCredito no esta disponible, la decision queda `REQUIERE_REVISION_MANUAL`
- si score queda en banda `REVISION`, la decision queda `REQUIERE_REVISION_MANUAL`
- score y DataCredito no aprueban ni rechazan productivamente
- el score solo se evalua si documental, capacidad contractual y riesgo no tienen bloqueos
- si DataCredito esta pendiente, el score puede calcularse parcialmente y exige revision manual

Montos y plazos sugeridos:

- `monto_maximo_sugerido` es el minimo entre monto por banda de score,
  capacidad contractual disponible y configuracion del portal.
- `plazo_maximo_sugerido` es el minimo entre plazo por banda de score, meses
  restantes del contrato y configuracion del portal.

La predecision no cambia estados, no crea `Credito`, no crea pagaré, no crea
historiales, no notifica pagador y no dispara workflows.

## Auditoria De Predecision

Existe auditoria segura para guardar snapshots de evaluaciones de predecision
de prestadores:

- modelo `PredecisionPrestadorAudit`
- servicio `serializar_predecision_prestador`
- servicio `crear_auditoria_predecision_prestador`

La auditoria guarda:

- solicitud
- usuario si aplica
- escenario de credito
- decision
- elegibilidad read-only
- revision manual
- monto y plazo sugeridos
- estado y resultado resumido de score
- estado y metadata segura de DataCredito
- estados de capacidad y riesgo
- bloqueos, advertencias y razones
- snapshot sanitizado
- `request_id`, IP, user agent y fecha

Reglas de seguridad:

- no guarda PDF
- no guarda contrato completo
- no guarda prompt IA
- no guarda base64
- no guarda token
- no guarda credenciales
- no guarda respuesta cruda completa de DataCredito

Puede haber multiples auditorias por solicitud. El admin registra el modelo en
solo lectura y ordena por fecha descendente.

Actualmente la auditoria queda como servicio explicito. No se ejecuta
automaticamente en todas las llamadas a `evaluar_predecision_contratista` para
mantener la predecision como servicio read-only y evitar writes inesperados en
tests, jobs o pantallas internas. Debe llamarse desde el punto operativo que
dispare una evaluacion formal.

## Evaluacion Formal De Predecision

Existen dos niveles de evaluacion:

- `evaluar_predecision_contratista`: calcula la predecision consolidada sin
  writes. No crea auditoria, no cambia estado, no origina y no aprueba.
- `evaluar_formalmente_solicitud_prestador`: ejecuta la misma predecision y
  crea una auditoria segura con `PredecisionPrestadorAudit`.

La evaluacion formal esta disponible como accion de Django Admin sobre
`ContractorApplication`:

- accion: `Evaluar predecision`
- permiso requerido: `contractors.can_evaluate_contractor_predecision`
- genera una auditoria por solicitud seleccionada
- resume evaluadas, preaprobadas read-only, revision manual, bloqueadas,
  incompletas y errores

Esta accion sigue siendo read-only desde el punto de vista financiero: no crea
`Credito`, no crea `CreditoLibranza`, no aprueba productivamente, no genera
pagaré, no notifica pagador y no dispara pagos ni desembolsos.

## Bandeja De Riesgo Prestadores

Existe una bandeja operativa para Riesgo/Operaciones en:

- `/gestion/prestadores/riesgo/`

La fuente principal de la bandeja es exclusivamente:

- `PredecisionPrestadorAudit`

`ContractorApplication` se usa solo como relacion para mostrar datos de la
solicitud asociada: documento, solicitante, empresa y escenario.

La tabla muestra:

- solicitud
- documento
- solicitante
- empresa
- escenario
- decision
- score
- banda
- estado DataCredito
- monto maximo sugerido
- plazo maximo sugerido
- fecha de evaluacion

Filtros disponibles:

- `PREAPROBADO_READ_ONLY`
- `REQUIERE_REVISION_MANUAL`
- `BLOQUEADO_READ_ONLY`
- `INCOMPLETO`

La bandeja ordena por evaluacion mas reciente primero y permite abrir el detalle
de cada auditoria con `Ver evaluacion`.

El detalle muestra el snapshot sanitizado guardado en auditoria:

- documental
- capacidad
- score
- DataCredito
- segundo credito
- recogida de cartera
- bloqueos
- advertencias
- razones

Permiso requerido:

- `contractors.can_view_contractor_risk_queue`

La bandeja no ejecuta predecision, no crea auditorias nuevas, no modifica
estados, no crea `Credito`, no crea `CreditoLibranza`, no origina, no toca
pagos, no toca WhatsApp y no genera pagare.

## Originacion Controlada EN_REVISION

Existe un servicio de originacion controlada para prestadores:

- `originar_credito_prestador_desde_auditoria`

La entrada operativa es una auditoria formal de predecision:

- `PredecisionPrestadorAudit`

Reglas actuales:

- la auditoria debe tener `decision=PREAPROBADO_READ_ONLY`
- la auditoria debe tener `eligible=True`
- la solicitud no debe estar convertida
- la solicitud debe tener usuario autenticado asociado
- deben existir datos contractuales
- debe existir empresa seleccionada desde el core de libranza
- deben existir documentos minimos de la solicitud
- el usuario staff debe tener `contractors.can_originate_contractor_credit`

La originacion crea:

- `Credito` con `linea=LIBRANZA`
- `CreditoLibranza`
- `HistorialEstado` inicial en `EN_REVISION`

La solicitud `ContractorApplication` queda:

- `status=CONVERTIDA`
- vinculada al `Credito` creado mediante `credito`

El monto final originado es el menor entre:

- monto solicitado por el prestador
- monto maximo sugerido por la auditoria

El plazo final originado es el menor entre:

- plazo solicitado por el prestador
- plazo maximo sugerido por la auditoria

Esta originacion no desembolsa, no activa, no genera pagare, no llama ZapSign,
no toca pagos, no crea `HistorialPago`, no crea cuotas de amortizacion
definitivas, no toca WhatsApp y no notifica al pagador.

La accion esta disponible desde el detalle de la bandeja de riesgo solo cuando
aplica:

- auditoria preaprobada read-only
- solicitud no convertida
- usuario con permiso de originacion

La predecision sigue siendo read-only. La originacion es un paso operativo
posterior y controlado que apenas deja el credito en revision para continuar el
flujo interno.

## Novedad Inicial Al Pagador

Para creditos de prestadores originados en `EN_REVISION` existe una novedad
informativa al pagador:

- servicio `notificar_pagador_credito_prestador_en_revision`
- modelo `NovedadPagadorPrestador`
- permiso `contractors.can_notify_contractor_payer`

La novedad comunica:

- `Novedad informativa`
- `Credito de prestador originado en revision`
- `No requiere aprobacion del pagador`

El pagador no aprueba el credito en este flujo. La novedad solo informa que ya
existe un credito de prestador originado en revision y que el equipo interno
continuara el proceso operativo.

Destinatarios:

- todos los `PerfilPagador` activos de la empresa con `es_pagador=True`
- usuarios asociados activos

El modelo actual no diferencia un rol separado de activador; si la empresa
configura varios perfiles pagador/operativos, la novedad se registra para todos
ellos.

La metadata guardada es segura e incluye:

- nombre del prestador
- documento enmascarado
- empresa
- estado `EN_REVISION`
- numero de credito
- monto originado
- plazo
- fecha

La metadata no incluye:

- pagare
- DataCredito
- score
- documentos sensibles
- auditoria completa
- respuestas crudas

La novedad es idempotente por `credito` y `tipo`. Si ya existe una novedad del
tipo `CREDITO_PRESTADOR_EN_REVISION` para el credito, no se crean duplicados ni
nuevas notificaciones.

Si no hay destinatarios pagador activos, la novedad queda registrada con
advertencia `sin_destinatarios_pagador` y no falla la operacion.

Esta fase no envia correos reales. Reutiliza `Notificacion` existente para
dejar la novedad visible a los usuarios pagadores en el sistema. No cambia el
estado financiero del credito, no desembolsa, no activa, no genera pagare, no
toca pagos, no crea cuotas y no llama WhatsApp ni ZapSign.

## Timeline Operativo

El dominio incluye `TimelinePrestador` como bitacora operativa read-only para
seguir el avance de una solicitud o credito de prestador.

No reemplaza:

- estados productivos de `Credito`
- `HistorialEstado`
- auditoria financiera
- auditoria de pagos
- auditoria de DataCredito o score

Eventos actuales:

- `SOLICITUD_CREADA`
- `DOCUMENTOS_CARGADOS`
- `ANALISIS_IA_CONTRATO`
- `PREDECISION_EJECUTADA`
- `AUDITORIA_PREDECISION_CREADA`
- `ORIGINADO_EN_REVISION`
- `NOVEDAD_PAGADOR_REGISTRADA`
- `FIRMA_V2_PREPARADA`
- `DRAFT_FIRMA_V2_CREADO`

Servicios:

- `registrar_evento_timeline_prestador`
- `listar_timeline_por_solicitud`
- `listar_timeline_por_credito`

La metadata se guarda sanitizada. No debe contener documentos completos, contrato
completo, PDF, base64, prompts, respuestas crudas de DataCredito, tokens,
credenciales ni payloads sensibles.

El detalle de la bandeja de riesgo muestra el bloque `Timeline operativo` con
fecha, evento, usuario, descripcion y estado resultante.

## Pagador

El pagador debe recibir notificacion cuando el credito sea aprobado y exista una
novedad operativa. Si una empresa tiene activador y pagador, las notificaciones
y resumenes deben llegar a ambos roles.

Pendiente de implementar:

- servicio de notificacion a pagador
- reglas de destinatarios activador/pagador
- trazabilidad de novedad
- vistas documentales para pagador

## Documentos Visibles Para Pagador

Debe diseñarse un modulo documental en la vista de pagador para consultar
documentos del colaborador o contratista cuando aplique.

El pagador puede ver documentos operativos del colaborador/contratista.

El pagador no debe ver:

- pagaré firmado
- evidencias de transferencia
- informacion financiera interna sensible no necesaria para la novedad

Esta regla aplica tanto a contratistas como a libranza.

## Pagaré

El archivo `Contrato de Mutuo y Autorización Irrevocable.docx` sera base de la
plantilla futura del pagaré contratista/libranza.

La plantilla debe conservar exactamente la informacion legal del documento. Se
pueden adaptar estilos, logos y colores de Aprobado sin alterar obligaciones,
autorizaciones ni contenido legal sustantivo.

La firma debe usar ZapSign con validacion de identidad obligatoria. La
generacion del pagaré no esta implementada en esta fase.

## Score Interno Read-Only

Existe un motor interno read-only para prestadores de servicios en:

- `contractors/score/configuracion.py`
- `contractors/score/dto.py`
- `contractors/score/policies.py`
- `contractors/score/motor.py`

El motor usa configuracion versionada. La version activa por defecto es
`CONFIGURACION_SCORE_PRESTADORES_V2` (`prestadores_score_v2_2026_06`), resuelta
con `obtener_configuracion_score_prestadores()`. `CONFIGURACION_SCORE_PRESTADORES_V1`
se mantiene intacta para compatibilidad e historicos.

- pesos por componente
- bandas de score
- montos y plazos sugeridos
- penalizaciones
- reglas criticas

Componentes actuales:

- DataCredito: se consulta mediante adapter read-only; por defecto queda `PENDIENTE`/`no_configurado`.
- Capacidad: viene de la evaluacion contractual.
- Comportamiento digital: usa valor default configurable.
- Riesgo fraude: usa valor default configurable.
- Referencias: usa valor default configurable.
- Geolocalizacion: no suma; solo penaliza si hay dato y cae bajo umbral.

El resultado incluye:

- version de configuracion
- score final entre 0 y 1000
- banda
- decision preliminar read-only
- monto y plazo sugeridos
- componentes evaluados
- componentes pendientes
- penalizaciones
- razones
- `requiere_revision_manual`
- `datacredito_status`
- pesos usados
- topes aplicados
- tasa mensual y TEA calculada
- regla cuota/ingreso
- capacidad financiera evaluada o advertencia si faltan ingresos formales

La predecision consulta primero el adapter DataCredito read-only. Si el
resultado esta disponible, el score usa `score_normalizado_0_1000` como
componente `datacredito`. Si DataCredito esta pendiente, el score queda parcial
y read-only. Si aparece mora severa, la predecision agrega un bloqueo
read-only y no evalua score.

Politica V2:

- monto maximo producto: `$10.000.000`
- plazo maximo producto: `8` meses
- tasa mensual fija: `2.2000%`
- regla financiera: `cuota_proyectada / ingreso_disponible <= 0.30`
- `ingreso_disponible = ingreso_neto - obligaciones_mensuales`
- geolocalizacion no suma score; solo penaliza `-80` si cae por debajo de `600`

Pendiente:

- administrar parametros desde Django Admin;
- calibrar transformacion de capacidad contractual con datos reales;
- definir trazabilidad de cambios de configuracion.

## Adapter DataCredito Read-Only

Existe una capa desacoplada en `contractors/datacredito/`:

- `dto.py`
- `adapter.py`
- `mock.py`
- `normalizador.py`
- `README.md`

Objetivo:

- trabajar con escenarios controlados ahora;
- conectar proveedor real de forma controlada sin acoplar score ni predecision al proveedor;
- retornar solo resultados sanitizados;
- no guardar respuestas crudas, XML, JSON completo ni documento completo.

Settings:

- `CONTRACTORS_DATACREDITO_ENABLED=False`
- `CONTRACTORS_DATACREDITO_PROVIDER=mock`
- `CONTRACTORS_DATACREDITO_TIMEOUT_SECONDS=10`
- `CONTRACTORS_DATACREDITO_MOCK_SCENARIO=bueno`

Por defecto no se consulta nada y el resultado queda:

- `disponible=False`
- `fuente=no_configurado`
- `score_normalizado_0_1000=None`
- `requiere_revision_manual=True`

Escenarios mock disponibles:

- `bueno`
- `medio`
- `mora_severa`
- `no_disponible`

El proveedor `real` existe, pero no se ejecuta por defecto. Solo puede consumir
si `DATACREDITO_REAL_ENABLED=True`, las credenciales estan completas y el punto
operativo lo solicita con un modo permitido.

## Snapshots DataCredito En Evaluacion Formal

La evaluacion formal de prestadores integra DataCredito por snapshots seguros
sin cambiar la logica de decision ni originar creditos.

Componentes:

- `integrations.models.ConsultaDatacreditoSnapshot`
- `integrations.datacredito.snapshots`
- `contractors.services.datacredito_evaluacion`
- `contractors.services.evaluacion_formal`
- `PredecisionPrestadorAudit`
- bandeja `/gestion/prestadores/riesgo/`

Modos operativos:

- `NO_CONSULTAR`: valor por defecto; no busca snapshots ni consume proveedor.
- `REUTILIZAR_SNAPSHOT`: usa snapshots vigentes de MiDecisor e Historia de
  Credito. Si falta alguno, la predecision queda con revision manual.
- `CONSULTAR_SI_NO_EXISTE`: reutiliza snapshots vigentes y solo intenta consultar
  el servicio faltante si DataCredito real esta habilitado y existe autorizacion
  especifica.
- `FORZAR_CONSULTA`: ignora snapshots vigentes. Requiere permiso separado y
  justificacion.

Permisos:

- `contractors.can_run_contractor_datacredito_evaluation`
- `contractors.can_force_contractor_datacredito_refresh`
- `contractors.can_register_uat_datacredito_authorization`

Autorizacion especifica DataCredito:

- `accepted_terms` no autoriza consulta a centrales de riesgo.
- La autorizacion de DataCredito se guarda en
  `AutorizacionConsultaDatacreditoPrestador`.
- La evidencia queda versionada con:
  - `DATACREDITO_AUTHORIZATION_TEXT_VERSION`
  - hash SHA-256 del texto aprobado en `DATACREDITO_AUTHORIZATION_TEXT`
  - usuario titular de la solicitud
  - fecha/hora, IP, user agent y fuente.
- El formulario publico puede registrar la autorizacion si el usuario la acepta,
  pero no ejecuta consultas DataCredito.
- `CONSULTAR_SI_NO_EXISTE` y `FORZAR_CONSULTA` requieren autorizacion vigente
  antes de llamar proveedor real.
- Los snapshots nuevos guardan referencia segura a la autorizacion usada.
- Snapshots legacy sin autorizacion no se reutilizan por defecto. En UAT puede
  habilitarse excepcion controlada con
  `DATACREDITO_ALLOW_LEGACY_SNAPSHOT_WITHOUT_AUTH_UAT=True`; nunca aplica en
  produccion.
- El registro staff UAT solo esta permitido fuera de produccion, exige permiso,
  justificacion y, si se configura, documento demo autorizado en
  `DATACREDITO_UAT_AUTHORIZED_DEMO_DOCUMENTS`.

Reglas de seguridad:

- no se consulta DataCredito desde el formulario publico ni desde simulacion;
- la evaluacion formal por defecto no consulta proveedor;
- no se usa `accepted_terms` como autorizacion de centrales;
- no se guarda raw del proveedor, XML, JSON completo, token ni credenciales;
- la auditoria guarda referencias a snapshots y resultado sanitizado;
- las consultas externas ocurren antes de abrir la transaccion corta que crea la
  auditoria;
- cada reutilizacion o consulta registra eventos de timeline con metadata
  sanitizada.
- se registra timeline para autorizacion aceptada, revocada, registro UAT y
  consulta bloqueada por falta de autorizacion.

La vista de riesgo permite reevaluar DataCredito desde una auditoria existente.
El resultado crea una nueva `PredecisionPrestadorAudit`; no modifica la solicitud,
no crea `Credito`, no crea `CreditoLibranza` y no aprueba productivamente.

## API Principal Conservada

- `obtener_organizacion_por_subdominio`
- `obtener_configuracion_portal_contratistas_por_host`
- `obtener_configuracion_portal_contratistas_por_slug`
- `obtener_configuracion_producto_activa`
- `obtener_branding_activo_por_organizacion`
- `obtener_contexto_branding_con_defaults`
- `obtener_perfil_contratista_usuario`
- `usuario_pertenece_a_organizacion`
- `obtener_solicitud_contratista`
- `listar_solicitudes_por_organizacion`
- `listar_documentos_solicitud_contratista`
- `solicitud_tiene_documento_tipo`
- `obtener_ultimo_documento_por_tipo`
- `simular_credito_contratista`
- `simular_credito_portal_contratistas`
- `crear_solicitud_contratista`
- `registrar_documento_solicitud_contratista`
- `marcar_solicitud_en_revision`
- `rechazar_solicitud_contratista`
- `aprobar_documento_solicitud`
- `rechazar_documento_solicitud`
- `evaluar_elegibilidad_conversion_contratista`
- `obtener_autorizacion_datacredito_vigente`
- `registrar_autorizacion_datacredito_prestador`
- `revocar_autorizacion_datacredito_prestador`
- `obtener_datos_contractuales_solicitud`
- `solicitud_tiene_datos_contractuales`
- `listar_empresas_libranza_convenio_activas`
- `obtener_credito_previo_por_documento_solicitud`
- `registrar_datos_contractuales_contratista`
- `calcular_valor_pendiente_contrato`
- `evaluar_capacidad_contractual_contratista`
- `calcular_meses_restantes_contrato`
- `evaluar_predecision_contratista`
- `evaluar_score_interno_prestador`
- `consultar_datacredito_prestador`
- `resolver_datacredito_para_solicitud_prestador`
- `evaluar_formalmente_solicitud_prestador`
- `DatosSolicitudContratista`
- `ResultadoSolicitudContratista`
- `DatosDocumentoSolicitudContratista`
- `ResultadoDocumentoSolicitudContratista`
- `DatosContractualesContratista`
- `ResultadoDatosContractualesContratista`
- `ResultadoCapacidadContractualContratista`
- `ResultadoPredecisionContratista`
- `ResultadoElegibilidadConversionContratista`
- `ResultadoSimulacionCreditoContratista`
- `ErrorSimulacionContratista`

Se mantienen aliases temporales en ingles para compatibilidad con imports
existentes durante la transicion.

## Fuera De Alcance En Esta Fase

- No se implementa score productivo ni aprobacion automatica.
- No se implementa DataCredito real/productivo.
- No se implementa pagaré.
- No se implementa ZapSign.
- No se implementa conversion a `Credito`.
- No se toca pagos.
- No se toca WhatsApp.
- No se toca el flujo productivo de libranza.
- No se introduce `Credito.LineaCredito.CONTRATISTA`.
- No se crea motor generico.

## Validacion Operativa Local

Para probar el portal unico en local:

```powershell
$env:DEBUG="True"
$env:CONTRACTORS_PORTAL_HOST="contratistas.localhost"
$env:ALLOWED_HOSTS="localhost,127.0.0.1,.localhost"
venv\Scripts\python.exe manage.py migrate contractors
venv\Scripts\python.exe manage.py contractors_demo_data --host contratistas.localhost
venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
```

El comando `contractors_demo_data` tambien crea o actualiza una empresa de
convenio llamada `Empresa Convenio Contratistas Demo`, necesaria para diligenciar
el formulario publico.

Tambien puede crearse la configuracion demo manualmente:

```python
from decimal import Decimal
from contractors.models import ConfiguracionPortalContratistas

ConfiguracionPortalContratistas.objects.update_or_create(
    host='contratistas.aprobado.com.co',
    defaults={
        'nombre_visible': 'Portal Contratistas',
        'slug': 'contratistas',
        'activo': True,
        'color_primario': '#0d6efd',
        'color_secundario': '#6c757d',
        'texto_landing': 'Credito para contratistas Aprobado.',
        'monto_minimo': Decimal('1000000.00'),
        'monto_maximo': Decimal('10000000.00'),
        'plazo_minimo_meses': 3,
        'plazo_maximo_meses': 8,
        'tasa_mensual': Decimal('2.2000'),
        'tasa_comision': Decimal('5.0000'),
        'comision_fija': Decimal('0.00'),
        'tasa_iva': Decimal('19.0000'),
    },
)
```

URLs locales:

- `http://contratistas.localhost:8000/`
- `http://contratistas.localhost:8000/login/?next=/solicitar/`
- `http://contratistas.localhost:8000/solicitar/`
- `http://contratistas.localhost:8000/solicitud/<id>/documentos/`
- `http://contratistas.localhost:8000/simular/?solicitud_id=<id>`
- `http://contratistas.localhost:8000/terminos-y-condiciones/`
- `http://contratistas.localhost:8000/politica-de-privacidad/`

Flujo manual recomendado:

1. Abrir `http://contratistas.localhost:8000/`; el portal redirige a `/solicitar/`.
2. Si el usuario no esta autenticado, entra primero por login/registro.
3. Diligenciar datos personales.
4. Cargar documentos iniciales:
   - Cedula frontal y reversa: captura obligatoria desde camara.
   - Contrato actual y certificado bancario: PDF obligatorio.
   - Nota visible al usuario: la cedula se captura en vivo desde el dispositivo y el certificado bancario se carga en PDF para validacion.
5. Buscar empresa por autocomplete y seleccionar una empresa existente con convenio activo.
6. Confirmar condiciones solicitadas y datos contractuales.
7. Aceptar terminos y privacidad con los enlaces del portal.
8. La vista registra la pre-solicitud y los cuatro documentos iniciales.
9. El flujo redirige a `/simular/?solicitud_id=<id>`.
10. `/solicitud/<id>/documentos/` queda disponible para versiones adicionales o reemplazos.
11. Revisar internamente desde Django Admin si aplica.

## UX Y Validaciones Publicas

El portal publico reutiliza la experiencia visual de libranza:

- Navbar y footer Aprobado.
- No existe landing publica propia, hero, cards, FAQ ni CTA final de contractors.
- Formulario con `form-container`, sidebar, stepper, `form-input`, `form-select`, errores y botones del flujo de libranza.
- El stepper publico tiene cuatro pasos reales: informacion personal, documentos, empresa contratante e informacion contractual/confirmacion.
- La aceptacion de terminos y privacidad esta al final del ultimo paso real, no en un paso independiente.
- Documentos obligatorios dentro del flujo inicial: cedula frontal, cedula trasera, contrato vigente PDF y certificado bancario PDF.
- Simulador con estructura visual `simulador-*`.
- Footer minimo con logo Aprobado, texto institucional breve, contacto e iconos
  de WhatsApp, Facebook e Instagram. No incluye pagadores, terminos, privacidad
  ni columnas de enlaces.

Reglas implementadas en esta fase:

- `/solicitar/`, `/solicitud/<id>/documentos/` y `/simular/` requieren autenticacion.
- `/mi-credito/` reutiliza el panel existente de libranza/personas mediante redireccion.
- Los CTAs publicos redirigen a `/login/?next=/solicitar/`.
- La empresa contratante se selecciona desde resultados de busqueda; no se digita pagador manual.
- Si el usuario escribe una empresa pero no selecciona resultado, se muestra `Debes elegir una empresa de la lista de resultados.`
- Nombres, apellidos, cedula, celular, correo y direccion tienen validaciones basicas de calidad.
- Tipo de documento es select cerrado: `CC` para cedula de ciudadania y `CE` para cedula de extranjeria.
- Numero de documento acepta solo numeros de 6 a 10 digitos y rechaza secuencias evidentemente invalidas.
- Contrato vigente y certificado bancario solo aceptan PDF en el formulario inicial.
- Cedula frontal y trasera no permiten carga manual desde galeria; deben capturarse en vivo desde camara.
- No se permite reutilizar el mismo archivo para diferentes documentos de la misma solicitud; en backend se compara nombre, tamano, tipo y hash temporal.
- Los documentos no exponen `file.path`.

Pendiente antes de produccion completa:

- Confirmacion automatica de datos extraidos.
- Textos legales definitivos de terminos y politica de privacidad.
- Score interno, DataCredito, pagare, ZapSign, conversion a credito y notificaciones al pagador.

## Analisis Inicial De Contrato Con IA

Existe el servicio `contractors.services.analisis_contrato_ia.analizar_contrato_con_openai`.

La integracion es controlada por variables de entorno:

- `OPENAI_API_KEY`
- `CONTRACTORS_CONTRACT_AI_ENABLED=True/False`
- `CONTRACTORS_CONTRACT_AI_MODEL`

Ejemplo `.env`:

```env
CONTRACTORS_CONTRACT_AI_ENABLED=True
CONTRACTORS_CONTRACT_AI_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...
```

Reglas actuales:

- Se usa OpenAI Responses API.
- Cuando `CONTRACTORS_CONTRACT_AI_ENABLED=True`, se envia el PDF del contrato como archivo de entrada.
- El modelo se configura con `CONTRACTORS_CONTRACT_AI_MODEL`.
- La API key se toma de `OPENAI_API_KEY` y nunca debe imprimirse ni exponerse en UI.
- Se solicita salida estructurada en JSON.
- Si la IA esta deshabilitada o falta `OPENAI_API_KEY`, el formulario no se rompe y la informacion contractual se diligencia manualmente.
- No se ejecuta OCR propio ni parser PDF fragil.
- No se guardan prompts completos ni contenido completo del contrato en logs.
- La IA no decide aprobacion; solo extrae informacion para confirmacion del usuario.
- Si la IA detecta que el PDF no parece ser contrato, el formulario bloquea la solicitud con un mensaje claro.
- La respuesta de IA nunca se toma como verdad absoluta; queda marcada para confirmacion del usuario.
- Se guarda metadata segura dentro de `simulation_payload['analisis_contrato_ia']`: `enabled`, `attempted`, `success`, `modelo`, `es_contrato`, `campos_detectados`, `campos_no_encontrados`, `advertencias`, `confianza_general`, `requiere_confirmacion_usuario`, `error_tipo` y `documento_id` si ya existe.
- La metadata no guarda prompt completo, texto completo del contrato, base64 del PDF ni API key.
- Si se compartio una API key real fuera del entorno privado, debe rotarse antes de produccion.

## Autocompletado Contractual Con IA

El formulario de `/solicitar/` incluye un analisis asistido del contrato. El
usuario carga el contrato PDF, pulsa `Analizar contrato` y el portal llama el
endpoint autenticado:

- `POST /contrato/analizar/`

Reglas del endpoint:

- requiere login y CSRF;
- requiere autorizacion explicita de tratamiento de datos antes de llamar OpenAI;
- recibe un PDF temporal;
- valida extension, `content_type` y tamano maximo;
- aplica limite basico de llamadas repetidas por usuario/IP usando cache;
- llama `analizar_contrato_con_openai`;
- devuelve JSON seguro para autocompletar el formulario;
- no crea `ContractorApplication`;
- no crea `Credito` ni `CreditoLibranza`;
- no guarda archivo permanente;
- no guarda prompt, base64, texto completo del contrato ni API key.

Campos sugeridos por IA:

- empresa contratante y NIT como referencia;
- nombre/documento del contratista como advertencia de consistencia;
- cargo o servicio;
- fecha de inicio y fin del contrato;
- valor total;
- valor mensual u honorarios como referencia;
- valor pagado estimado si aparece de forma explicita;
- valor pendiente estimado;
- duracion en meses si el contrato no informa fecha final directa;
- moneda.

La empresa detectada no crea una `Empresa` nueva ni reemplaza automaticamente
la seleccion operativa. Solo se usa como sugerencia de busqueda; el usuario debe
seleccionar una empresa existente del core de libranza.

Despues de la respuesta de IA se ejecuta una capa deterministica de analisis
contractual seguro:

- si falta fecha final pero existe fecha de inicio y duracion en meses, se
  infiere la fecha final y se exige confirmacion visual del usuario;
- si el contrato esta vencido, el valor pendiente se fuerza a cero, la capacidad
  contractual queda bloqueada y se registra metadata segura;
- si existe total y valor pagado, el valor pendiente se calcula como
  `total - pagado` y requiere confirmacion;
- si solo existe valor mensual y vigencia, el valor pendiente puede estimarse
  por meses restantes, siempre como dato a confirmar;
- si no hay evidencia suficiente para valor pendiente, el flujo queda marcado
  para revision manual;
- la empresa se sugiere por NIT exacto, nombre normalizado exacto o coincidencia
  aproximada; ninguna sugerencia selecciona la empresa automaticamente;
- si NIT y nombre de empresa entran en conflicto, se bloquea el avance normal
  y se exige revision.

La metadata segura queda en `simulation_payload['analisis_contractual_seguro']`
y no contiene PDF, base64, prompt, texto completo del contrato ni respuesta
cruda de OpenAI.

La respuesta de IA nunca aprueba credito ni se toma como verdad absoluta. El
usuario debe confirmar o corregir los campos antes de enviar. Al crear la
pre-solicitud se guardan los datos confirmados por el usuario y solo metadata
segura de IA:

- `attempted`
- `success`
- `modelo`
- `confianza_general`
- `campos_detectados`
- `campos_no_encontrados`
- `advertencias`
- `requiere_confirmacion_usuario`
- `error_tipo`

Si `CONTRACTORS_CONTRACT_AI_ENABLED=False`, falta `OPENAI_API_KEY` o OpenAI
falla, el endpoint responde con `manual_allowed=true` y el usuario puede
continuar diligenciando manualmente. Si la IA confirma que el PDF no parece un
contrato, el avance queda bloqueado hasta cargar un contrato valido.

La capacidad contractual productiva no usa la IA como fuente definitiva. Usa los
datos confirmados por el usuario y la metadata segura solo para marcar bloqueos,
advertencias y `requiere_revision_manual`.

Cada analisis contractual guarda `analysis_input_hash` y `analysis_generated_at`
en la metadata segura. Si despues del analisis el usuario modifica documento,
empresa, fechas, valores contractuales o el PDF del contrato, el backend marca
el analisis anterior como obsoleto, limpia el bloqueo viejo como fuente vigente,
exige reanalisis y registra una huella de comportamiento digital en
`TimelinePrestador` con metadata sanitizada. Esa huella queda disponible para el
componente futuro de comportamiento digital del score, sin recalcular score en
este PR.

Antes de enviar el PDF a OpenAI, el usuario debe aceptar la autorizacion de
tratamiento de datos del analisis contractual. Si no la acepta, el endpoint no
llama OpenAI y responde:

`Debes aceptar la autorizacion de tratamiento de datos antes de analizar el contrato.`

## Login Google en Prestadores

El boton de Google solo se muestra si existe una `SocialApp` de Google asociada
al `Site` activo. Si no existe configuracion, el login por correo/contrasena
sigue disponible y no se lanza `DoesNotExist`.

Callbacks esperados para Google Console:

- local: `http://contratistas.localhost:8000/accounts/google/login/callback/`
- produccion: `https://contratistas.aprobado.com.co/accounts/google/login/callback/`

Para pruebas UAT/local no imprimir ni versionar `client_secret`. Si Google falla
con `SocialApp` existente, revisar asociacion al `Site`, dominio del `Site`,
redirect URI autorizada y preservacion de `next=/solicitar/`.

Usuario admin:

```powershell
venv\Scripts\python.exe manage.py createsuperuser
```

Variables locales minimas:

- `DEBUG=True`
- `CONTRACTORS_PORTAL_HOST=contratistas.localhost`
- `ALLOWED_HOSTS` debe incluir `.localhost`, `contratistas.localhost` o `*`

Evitar comentarios inline en `.env` para valores usados por Django. Por ejemplo,
usar `PRIMARY_DOMAIN_HOST=localhost`, no `PRIMARY_DOMAIN_HOST=localhost # comentario`.
