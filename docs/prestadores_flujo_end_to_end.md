# Flujo end-to-end de Prestadores de Servicios

Estado del documento: **Commit E - gate interno implementado; originacion pendiente**.

Convenciones:

- **IMPLEMENTADO**: existe codigo y pruebas en la rama actual.
- **PARCIAL**: existe una frontera o preparacion, pero no el proceso productivo completo.
- **PENDIENTE**: no debe ejecutarse en la fase actual.

## 1. Objetivo del modulo

El dominio `contractors` recibe y evalua solicitudes de Prestadores de Servicios. Su
responsabilidad termina, por ahora, en una aprobacion interna para originar y en la
construccion determinista de un expediente. No crea obligaciones financieras.

El modulo conserva trazabilidad desde la solicitud publica hasta la decision interna,
sin convertir `PREAPROBADO_READ_ONLY` en una aprobacion crediticia o desembolso.

## 2. Principios arquitectonicos

1. `contractors` controla solicitud, documentos, contrato, simulacion, evaluacion,
   revision y gate interno.
2. `integrations` controla el acceso al proveedor DataCredito y sus DTO normalizados.
3. `gestion_creditos` debe ser la unica frontera futura para crear `Credito` y
   `CreditoLibranza` y para ejecutar su ciclo financiero.
4. Las decisiones se versionan; ningun resultado se aplica si los datos cambiaron.
5. Las respuestas externas se guardan solo como snapshots normalizados y sanitizados.
6. Los permisos de pagador no se reutilizan para decisiones internas de riesgo.
7. Pagar, desembolsar, generar pagare y enviar a ZapSign estan fuera del gate.

## 3. Arquitectura general

```mermaid
flowchart TD
    U[Usuario] --> P[Portal Prestadores]
    P --> S[ContractorApplication]
    S --> D[Documentos]
    D --> AC[Analisis contractual]
    AC --> SIM[Simulacion versionada]
    SIM --> DC[DataCredito seguro]
    DC --> SC[Score parametrizado]
    SC --> EF[Evaluacion formal read-only]
    EF -->|requiere intervencion| RM[Revision manual]
    RM --> SUB[Subsanacion]
    SUB --> EF
    EF -->|PREAPROBADO_READ_ONLY| AI[Aprobacion interna]
    AI -->|APROBADA_PARA_ORIGINAR| EO[Expediente originacion inmutable]
    EO -. PENDIENTE .-> OR[Originacion gestion_creditos]
    OR -. PENDIENTE .-> PG[Pagare]
    PG -. PENDIENTE .-> FI[Firma]
    FI -. PENDIENTE .-> PA[Pagador / validacion operativa]
    PA -. PENDIENTE .-> DE[Desembolso]

    classDef ok fill:#dff7ef,stroke:#0f766e,color:#123;
    classDef partial fill:#fff3cd,stroke:#a16207,color:#123;
    classDef pending fill:#f1f5f9,stroke:#64748b,color:#475569,stroke-dasharray: 5 5;
    class U,P,S,D,AC,SIM,DC,SC,EF,RM,SUB,AI ok;
    class EO partial;
    class OR,PG,FI,PA,DE pending;
```

## 4. Flujo end-to-end

```mermaid
flowchart TD
    A[Registro o login] --> B[Solicitud personal y contractual]
    B --> C[Documentos obligatorios]
    C --> D[Analisis contractual y confirmacion]
    D --> E[Simulacion con configuracion versionada]
    E --> F[EVALUACION_PENDIENTE]
    F --> G[Reserva/reuso snapshot DataCredito]
    G --> H[Score y predecision]
    H -->|NO_EVALUABLE / ERROR / REVISION| I[Revision manual]
    I -->|subsanacion| J[Usuario corrige]
    J --> F
    I -->|validacion empresa| K[Validacion contractual interna]
    K --> F
    H -->|PREAPROBADO_READ_ONLY| L[Gate PENDIENTE]
    L --> M[Revalidacion transaccional corta]
    M -->|cambio o vencimiento| I
    M -->|sin cambios| N[APROBADA_PARA_ORIGINAR]
    N --> O[ExpedienteOriginacionPrestadorDTO]
    O -. Commit F .-> P[Servicio central de originacion]
```

## 5. Maquina de estados ContractorApplication

No se agregan `PENDIENTE_APROBACION_INTERNA` ni `APROBADA_PARA_ORIGINAR` al modelo.
Esos estados se derivan inequívocamente de `AprobacionInternaPrestador`, evitando dos
fuentes de verdad.

```mermaid
stateDiagram-v2
    [*] --> DOCUMENTOS_PENDIENTES: solicitud creada
    DOCUMENTOS_PENDIENTES --> DOCUMENTOS_CARGADOS: documentos completos
    DOCUMENTOS_CARGADOS --> EVALUACION_PENDIENTE: simulacion confirmada
    EVALUACION_PENDIENTE --> EN_EVALUACION: inicia evaluacion formal
    EN_EVALUACION --> EVALUACION_COMPLETADA: resultado final read-only
    EN_EVALUACION --> EN_REVISION: revision requerida/error controlado
    EVALUACION_COMPLETADA --> EVALUACION_PENDIENTE: datos relevantes cambian
    EN_REVISION --> EVALUACION_PENDIENTE: subsanacion y reintento
    EN_REVISION --> EN_REVISION: validacion interna pendiente
```

| Estado | Lo genera | Siguiente accion valida |
|---|---|---|
| `DOCUMENTOS_PENDIENTES` | solicitud/carga parcial | completar documentos |
| `DOCUMENTOS_CARGADOS` | servicio de solicitud | completar simulacion |
| `EVALUACION_PENDIENTE` | simulacion o invalidacion | evaluacion formal |
| `EN_EVALUACION` | `evaluacion_formal._iniciar_evaluacion` | finalizar evaluacion |
| `EVALUACION_COMPLETADA` | predecision final | gate o cierre operativo |
| `EN_REVISION` (semantica manual) | revision/subsanacion | corregir o reintentar |

## 6. Maquina de estados de evaluacion

```mermaid
stateDiagram-v2
    [*] --> EN_PROCESO
    EN_PROCESO --> COMPLETADA: resultado calculado
    EN_PROCESO --> ERROR_CONTROLADO: proveedor/error/datos cambiaron
    COMPLETADA --> [*]
    ERROR_CONTROLADO --> [*]
```

Resultados de `PredecisionPrestadorAudit`: `PREAPROBADO_READ_ONLY`,
`REQUIERE_REVISION_MANUAL`, `BLOQUEADO_READ_ONLY`, `NO_EVALUABLE` y
`ERROR_CONTROLADO`. Solo el primero habilita la creacion del gate.

## 7. Maquina de estados de revision manual

```mermaid
stateDiagram-v2
    [*] --> ABIERTA
    ABIERTA --> ASIGNADA
    ABIERTA --> EN_ANALISIS
    ASIGNADA --> EN_ANALISIS
    EN_ANALISIS --> PENDIENTE_SOLICITANTE
    EN_ANALISIS --> PENDIENTE_VALIDACION_EMPRESA
    PENDIENTE_SOLICITANTE --> EN_ANALISIS: subsanacion atendida
    PENDIENTE_VALIDACION_EMPRESA --> EN_ANALISIS: hecho validado
    EN_ANALISIS --> RESUELTA
    ABIERTA --> CANCELADA
    ASIGNADA --> CANCELADA
```

La revision es una bandeja interna de Aprobado. `PerfilPagador` queda bloqueado en
vistas y servicios incluso si recibe permisos Django por error.

## 8. Maquina de estados de aprobacion interna

```mermaid
stateDiagram-v2
    [*] --> PENDIENTE: PREAPROBADO_READ_ONLY vigente
    PENDIENTE --> EN_ANALISIS
    PENDIENTE --> APROBADA_PARA_ORIGINAR: revalidacion OK
    EN_ANALISIS --> APROBADA_PARA_ORIGINAR: revalidacion OK
    PENDIENTE --> DEVUELTA_A_REVISION
    EN_ANALISIS --> DEVUELTA_A_REVISION
    PENDIENTE --> CERRADA_SIN_ORIGINAR
    EN_ANALISIS --> CERRADA_SIN_ORIGINAR
    APROBADA_PARA_ORIGINAR --> CERRADA_SIN_ORIGINAR
    PENDIENTE --> CANCELADA
    EN_ANALISIS --> CANCELADA
```

`APROBADA_PARA_ORIGINAR` solo autoriza construir el expediente. No implica credito,
pagare, firma, obligacion activa ni desembolso.

## 9. Modelos

| Modelo | Responsabilidad | Datos sensibles | Fuente de verdad | Relaciones |
|---|---|---|---|---|
| `ContractorApplication` | solicitud y estado operativo | identidad/contacto/contrato | si | usuario, empresa |
| `ContractorApplicationDocument` | archivos obligatorios | cedula, contrato, certificado | si para archivo | solicitud, uploader |
| `ConfiguracionSimuladorPrestador` | limites y tasa de simulacion | no | si financiera preliminar | politica score |
| `ConfiguracionScorePrestador` | pesos, umbrales y versiones | no | si de politica | configuracion financiera, bandas |
| `BandaScorePrestador` | banda, topes y resultado | no | si para banda | politica |
| `PredecisionPrestadorAudit` | snapshot inmutable de evaluacion | snapshot sanitizado | si de una evaluacion | solicitud, usuario |
| `TimelinePrestador` | eventos allowlist | metadata sanitizada | auditoria operativa | solicitud, usuario |
| `RevisionManualPrestador` | caso de analisis humano | comentarios internos | si de revision | solicitud, auditoria |
| `RequerimientoSubsanacionPrestador` | correccion solicitada | mensaje controlado | si de subsanacion | solicitud, revision |
| `AprobacionInternaPrestador` | gate humano previo a originar | comentario interno, topes | si del gate | solicitud, auditoria, revision |
| `AutorizacionConsultaDatacreditoPrestador` | consentimiento versionado | hash y referencia | si de autorizacion | solicitud, usuario |
| `ConsultaDatacreditoSnapshot` | resultado externo reutilizable | hash, resultado normalizado | si del snapshot | referencia logica a solicitud/autorizacion |

## 10. Servicios

| Servicio | Entrada | Salida | Efectos laterales | Transaccion |
|---|---|---|---|---|
| `analisis_contractual_seguro` | PDF/autorizacion | metadata firmada | no conserva PDF temporal | corta al persistir |
| `solicitud` | solicitud/documentos | estado operativo | invalida evaluacion si cambia | si |
| `capacidad_contractual` | contrato/solicitud | capacidad preliminar | ninguno | no |
| `validacion_contractual` | solicitud | estado, meses, bloqueos | ninguno | no |
| `autorizacion_datacredito` | solicitud/consentimiento | autorizacion vigente | persiste autorizacion | si |
| `datacredito_evaluacion` | solicitud/modo | DTO normalizado | reserva/finaliza snapshot y timeline | HTTP fuera; escrituras cortas |
| `predecision` | solicitud/politica/DataCredito | resultado read-only | ninguno | no |
| `evaluacion_formal` | solicitud/actor | auditoria | auditoria, estado, timeline, revisiones | fases cortas; HTTP fuera |
| `revision_manual` | revision/actor/accion | revision/requerimiento | revision, subsanacion, timeline | si |
| `subsanacion` | requerimiento/cambios | requerimiento atendido | invalida evaluacion | si |
| `aprobacion_interna` | auditoria o gate/actor | gate | gate, revision, timeline | si, corta |
| `expediente_originacion` | gate aprobado | DTO inmutable | ninguno | no |

## 11. Rutas

El host de Prestadores usa `aprobado_web.urls_contractors`. El login y registro reales
son los de django-allauth bajo `/accounts/`; no existen aliases `/login/` o `/registro/`.

| Metodo | URL | Vista | Autenticacion | Permiso | Responsabilidad |
|---|---|---|---|---|---|
| GET | `/` | `inicio_prestadores_view` | publica | - | redirigir/iniciar portal |
| GET/POST | `/accounts/login/` | allauth `account_login` | publica | - | login |
| GET/POST | `/accounts/signup/` | allauth `account_signup` | publica | - | registro |
| GET/POST | `/solicitar/` | `solicitar_prestador_view` | login | ownership | wizard |
| POST | `/contrato/analizar/` | `analizar_contrato_prestador_view` | login + CSRF | ownership temporal | analisis inicial |
| GET/POST | `/solicitud/<id>/documentos/` | `documentos_prestador_view` | login | ownership | documentos |
| POST | `/solicitud/<id>/contrato/analizar/` | `analizar_contrato_prestador_view` | login + CSRF | ownership | analisis contractual |
| GET | `/solicitud/<id>/documentos/<doc>/descargar/` | `descargar_documento_prestador_view` | login | ownership | descarga protegida |
| GET | `/simular/` | `simular_prestador_view` | login | ownership | simulador |
| POST | `/simular/calcular/` | `calcular_simulacion_prestador_view` | login + CSRF | ownership | registrar simulacion |
| GET | `/mi-credito/` | `mi_credito_prestador_view` | login | ownership | estado publico allowlist |
| GET/POST | `/mi-credito/solicitud/<id>/subsanacion/<req>/` | `atender_subsanacion_prestador_view` | login | ownership | corregir requerimiento |
| GET | `/gestion/prestadores/` | `bandeja_prestadores_view` | staff | `can_view_contractor_review_queue` | bandeja interna |
| GET | `/gestion/prestadores/<id>/` | `detalle_prestador_view` | staff | permiso de bandeja | detalle/revision/gate |
| POST | `/gestion/prestadores/<id>/aprobacion-interna/crear/` | `crear_aprobacion_interna_prestador_view` | staff + CSRF | `can_decide_contractor_internal_approval` | crear gate |
| POST | `/gestion/prestadores/aprobaciones/<gate>/accion/` | `accion_aprobacion_interna_prestador_view` | staff + CSRF | permiso segun accion | decidir gate |
| POST | `/gestion/prestadores/revisiones/<revision>/accion/` | `accion_revision_prestador_view` | staff + CSRF | permiso segun accion | operar revision |
| GET | `/gestion/prestadores/documentos/<doc>/descargar/` | `descargar_documento_prestador_staff_view` | staff | permiso de bandeja | descarga interna |

## 12. Seguridad

- Las vistas publicas filtran por `usuario=request.user` y ocultan existencia ajena.
- Las acciones mutables son POST y usan middleware CSRF de Django.
- Las descargas validan ownership o permiso staff; no se publican URLs directas.
- Los servicios internos exigen `is_staff`, permiso especifico y ausencia de
  `perfil_pagador`.
- El documento se enmascara y se usa HMAC para fingerprint/versionado.
- El versionado de datos personales y actividad usa HMAC; no los copia al snapshot.
- Timeline y auditorias usan metadata allowlist; no guardan PDF, contrato raw, token,
  credencial ni respuesta raw de DataCredito.
- El gate aplica `select_for_update` a solicitud/auditoria/gate y constraints unicas.

## 13. DataCredito

**IMPLEMENTADO** mediante `contractors.services.datacredito_evaluacion` y
`contractors.datacredito.adapter` sobre clientes de `integrations.datacredito`.

La consulta exige autorizacion vigente. Un fingerprint HMAC combina documento,
servicio, ambiente, version de autorizacion y parametros relevantes. Por defecto se
reutiliza un snapshot `EXITOSO` vigente. `FORZAR_CONSULTA` exige staff, permiso y
justificacion; `SOLO_CACHE` nunca consulta proveedor.

La reserva `EN_PROCESO` y la restriccion parcial unica evitan consultas concurrentes
equivalentes. Timeout, proveedor deshabilitado, configuracion incompleta y error del
proveedor producen resultados controlados. No se almacena raw, XML/JSON completo,
token, headers, secreto ni documento plano.

## 14. Score

**IMPLEMENTADO** en `contractors.score`. La politica parametriza componentes, pesos,
bandas, umbrales y topes. Identidad, contrato, documentos, capacidad, autorizacion,
snapshot y configuracion financiera son componentes obligatorios para un resultado
favorable. DataCredito aporta score/señales normalizadas; no se acopla al motor.

Referencias no son obligatorias en V1 (`requiere_referencias=False`) y no reciben
puntaje inventado. La redistribucion solo ocurre si la politica la permite.
Geolocalizacion no puntua ni penaliza sin señal verificable. Antifraude se limita a
coherencia de identidad y datos disponibles.

## 15. Contrato

**IMPLEMENTADO** en `validacion_contractual`.

Estados: `VIGENTE`, `VENCIDO`, `SUSPENDIDO`, `TERMINADO`, `LIQUIDADO` y
`NO_DETERMINABLE`. Solo `VIGENTE` habilita capacidad automatica. La identidad debe
coincidir, la empresa seleccionada debe tener convenio activo y la obligacion a favor
del prestador debe ser positiva y coherente.

Se usa fecha final explicita o fecha inicial mas duracion contractual con meses
calendario. Los meses financiables son meses completos; menos de uno produce cero.
Reemplazar contrato cambia su hash, invalida version/evaluacion/gate y obliga a una
nueva evaluacion.

## 16. Simulacion y politica

**IMPLEMENTADO** con relacion explicita:

```text
ConfiguracionScorePrestador
        |
        +--> ConfiguracionSimuladorPrestador
```

La solicitud conserva versiones de politica/configuracion, tasa, monto maximo, plazo
maximo, monto/plazo simulados y fecha. La evaluacion compara ese snapshot con la
politica activa. Una politica posterior, version ausente o edicion manual genera
revision; nunca se cambian valores silenciosamente.

## 17. Revision manual y subsanacion

**IMPLEMENTADO**. La evaluacion crea/reutiliza revisiones por solicitud y motivo.
El analista puede asignar, iniciar, solicitar correccion allowlist, solicitar
validacion contractual de empresa, resolver, cancelar o reintentar. El solicitante
solo ve mensajes publicos allowlist y atiende requerimientos propios.

La empresa confirma hechos contractuales; no toma la decision crediticia y no se usa
la doble aprobacion de Libranza tradicional.

## 18. Aprobacion interna

**IMPLEMENTADO** en `AprobacionInternaPrestador` y
`contractors.services.aprobacion_interna`.

La creacion exige la ultima auditoria completada con `PREAPROBADO_READ_ONLY`, datos y
documentos vigentes, ausencia de revision/subsanacion, contrato con capacidad,
politica/configuracion/simulacion compatibles, autorizacion y snapshot vigentes. Dos
constraints garantizan un gate por auditoria y un gate activo por solicitud.

Antes de aprobar se repite toda validacion dentro de una transaccion corta. Un cambio
devuelve el gate a revision con motivo allowlist. El analista puede reducir monto o
plazo, nunca aumentarlos. Se conservan topes originales y valores autorizados.

## 19. Frontera de originacion

`contractors` termina en `ExpedienteOriginacionPrestadorDTO`, dataclass inmutable
construida solo desde un gate `APROBADA_PARA_ORIGINAR` y con version intacta. La
funcion no persiste ni ejecuta efectos externos.

`gestion_creditos` debe comenzar en un futuro servicio central que reciba ese DTO,
vuelva a bloquear/revalidar el gate y cree la obligacion idempotentemente. Las vistas
de `contractors` no deben importar vistas de originacion ni escribir modelos del core.

## 20. Auditoria de originacion actual

Hallazgos reales:

1. El `Credito` de Libranza nace en
   `gestion_creditos.views.solicitudes.solicitud_credito_libranza_view` mediante
   `Credito.objects.create`.
2. `CreditoLibranza` nace inmediatamente despues mediante
   `CreditoLibranzaForm.save(commit=False)` y `save()` dentro de la misma transaccion.
3. No existe un unico servicio de dominio reutilizable para crear ambos modelos.
4. La vista mezcla persistencia con parseo de certificado, emails al cliente,
   notificacion interna y notificacion a pagadores.
5. `Credito.save()` calcula `CR-<año>-<secuencia>` consultando el ultimo registro. La
   unicidad protege duplicados, pero el calculo no es una secuencia transaccional y
   puede competir bajo concurrencia.
6. La solicitud tradicional no usa una clave idempotente de origen; repetir un POST
   puede depender de validaciones indirectas (por ejemplo cedula) y no de un contrato
   formal de idempotencia.
7. `credit_services.gestionar_cambio_estado_credito` persiste historial y envia email.
8. `aprobacion_pagador_libranza` llama
   `credit_services.preparar_documento_para_firma`; esto cambia a
   `PENDIENTE_FIRMA`, genera pagare y programa envio ZapSign en `on_commit`.
9. `credit_services.activar_credito` calcula comision, IVA, cuota y amortizacion,
   fija fecha de desembolso y crea cuotas. No corresponde a originacion inicial.
10. Los comandos de creditos especiales tambien crean y activan directamente, por lo
    que no son una frontera apropiada para Prestadores.

Conclusion: originar en Commit E duplicaria reglas y heredaria efectos laterales no
controlados. Se requiere una extraccion en el core antes de Commit F.

## 21. Secuencia futura de originacion

```mermaid
sequenceDiagram
    participant Staff
    participant Contractors as contractors
    participant Core as gestion_creditos.originacion_libranza
    participant DB
    participant Formal as formalizacion futura

    Staff->>Contractors: originar(gate_id, clave_idempotencia)
    Contractors->>Contractors: permiso + PerfilPagador bloqueado
    Contractors->>DB: lock gate y verificar APROBADA_PARA_ORIGINAR
    Contractors->>Contractors: construir DTO y verificar version
    Contractors->>Core: originar_libranza_desde_expediente(DTO, clave)
    Core->>DB: buscar resultado por clave/gate
    alt ya originado
        DB-->>Core: Credito existente
    else nuevo
        Core->>DB: crear Credito EN_REVISION
        Core->>DB: crear CreditoLibranza
        Core->>DB: registrar HistorialEstado y relacion de origen
    end
    Core-->>Contractors: referencia idempotente
    Contractors-->>Staff: originacion registrada
    Note over Formal: pagare, firma, pagador y desembolso siguen separados
```

## 22. Riesgos tecnicos

- Numeracion de credito susceptible a carrera si el futuro servicio no agrega retry o
  secuencia segura.
- Efectos de email/pagare acoplados a transiciones del core.
- Diferencia semantica entre contrato de prestacion y campos laborales obligatorios
  de `CreditoLibranza`.
- Archivos de `ContractorApplicationDocument` no deben copiarse sin estrategia de
  ownership, almacenamiento e idempotencia.
- Un cambio de politica entre gate y originacion debe invalidar, no ajustar valores.
- La doble aprobacion de pagador tradicional no representa la decision interna de
  Prestadores.
- Reintentos HTTP sin clave estable podrian duplicar obligaciones.

## 23. Pendientes

- **PENDIENTE Commit F**: extraer servicio idempotente en `gestion_creditos` para
  crear `Credito` + `CreditoLibranza` sin pagare, pago ni desembolso.
- Definir relacion unica entre gate y credito originado, preferiblemente en un modelo
  de enlace/auditoria del core con constraint unica por gate.
- Definir mapeo contractual a campos actualmente laborales de `CreditoLibranza`.
- Hacer segura la numeracion bajo concurrencia PostgreSQL.
- Separar efectos de notificacion con `transaction.on_commit`/outbox.
- Definir formalizacion, pagare, firma y validacion de hechos por empresa.
- Agregar pruebas PostgreSQL de concurrencia e idempotencia antes de activar origen.

Recomendacion exacta para Commit F: crear
`gestion_creditos/services/originacion_libranza.py` con un DTO de entrada estable,
una clave idempotente igual al ID del gate/version, `transaction.atomic`, locks sobre
el registro de enlace, creacion en estado `EN_REVISION` y cero llamadas a pagare,
ZapSign, pagos o desembolso. Solo despues conectar una accion staff separada.

## 24. Historial de commits/fases

| Fase | Estado | Alcance |
|---|---|---|
| A | IMPLEMENTADO | auditoria y versionado de evaluacion |
| B | IMPLEMENTADO | DataCredito seguro, snapshots e idempotencia |
| C | IMPLEMENTADO | score parametrizado y predecision formal read-only |
| D | IMPLEMENTADO | revision manual, subsanacion y bandeja interna |
| E | IMPLEMENTADO | gate interno y expediente inmutable; sin originacion |
| F | PENDIENTE | servicio central idempotente de originacion |

