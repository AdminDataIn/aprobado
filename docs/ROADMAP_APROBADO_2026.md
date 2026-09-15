# Aprobado — Roadmap maestro de implementación y cierre operativo

**Versión:** 1.1

**Fecha de corte:** 2026-09-12

**Estado:** Documento rector de planificación

**Proyecto:** Aprobado / Project_aprobado / WhatsApp Platform

---

## 1. Propósito

Este documento consolida el estado real del proyecto Aprobado, define el objetivo operativo inmediato y organiza el trabajo pendiente en un backlog único, priorizado y verificable.

Su finalidad es evitar que el equipo continúe abriendo frentes sin cerrar los existentes, reducir contradicciones entre documentos históricos y código actual, y establecer una ruta clara desde el estado actual hasta una operación estable y escalable.

Este documento **no reemplaza contratos técnicos específicos** de APIs, modelos, migraciones o integraciones. Para esos temas siguen aplicando las fuentes técnicas especializadas del proyecto. Para efectos de planeación y priorización, este documento se considera la referencia principal.

---

## 2. Objetivo general

Llevar Aprobado desde su estado actual —con varios componentes productivos y otros en fase avanzada de cierre— a una plataforma financiera operativamente consolidada, con:

- flujos de libranza estables;
- Prestadores de Servicios cerrado end-to-end;
- BRE-B operativo y validado financieramente;
- reportería consistente con la realidad contable;
- UX productiva afinada;
- WhatsApp integrado sin duplicar lógica financiera;
- trazabilidad, pruebas, permisos y observabilidad suficientes para operar con seguridad;
- backlog explícito para las siguientes fases.

---

## 3. Principios de arquitectura que no se deben romper

### 3.1 Fuente de verdad

`Project_aprobado` es la fuente de verdad financiera y de negocio.

El bot de WhatsApp, el portal y otros canales deben orquestar conversación, captura y experiencia de usuario, pero no replicar cálculos financieros ni decisiones de negocio que pertenezcan al core.

### 3.2 Separación de productos

Mantener separados:

- `payroll_loan`
- `whatsapp_credit`

No reutilizar estados, reglas o pipelines entre productos si el dominio no lo justifica.

### 3.3 Pagos

Los movimientos que impactan cartera deben pasar por servicios financieros centrales existentes. Una interfaz o canal de recaudo no debe modificar saldos directamente.

### 3.4 Seguridad

- documentos privados fuera de exposición pública;
- permisos explícitos para decisiones financieras;
- trazabilidad de acciones administrativas;
- idempotencia donde exista riesgo de doble aplicación;
- no exponer datos sensibles innecesariamente;
- no conectar canales públicos directamente a fuentes críticas si existe una API intermedia segura.

> Toda transición financiera o de identidad sensible debe validar en backend actor, rol, estado, ownership y segregación de funciones. La UI nunca constituye el control de seguridad principal.

### 3.5 Evolución

Priorizar:

1. cierre de frentes abiertos;
2. regresiones;
3. validación funcional real;
4. afinación;
5. nuevas funcionalidades.

---

## 4. Estado consolidado actual

### 4.1 Libranza tradicional — ESTABLE / PRODUCTIVO

Capacidades relevantes ya implementadas:

- originación de libranza;
- doble aprobación de pagador;
- aprobación por niveles;
- formalización;
- pagaré/firma;
- abonos a capital;
- reducción de plazo;
- reestructuración;
- anulaciones controladas;
- notificaciones;
- reportería y administración asociada.

**Estado:** Maduro.

**Acción:** Mantener regresiones y atender ajustes incrementales.

---

### 4.2 BRE-B para pagadores — PRODUCTIVO / CIERRE OPERATIVO

Arquitectura implementada:

```text
Pagador / Empresa
    ↓
Selecciona una o varias obligaciones
    ↓
Realiza una sola transferencia BRE-B a Aprobado
    ↓
Carga referencia y comprobante
    ↓
PENDIENTE_VERIFICACION
    ↓
Staff interno Aprobado
    ├── RECHAZA → no modifica cartera
    └── APRUEBA → aplica cada detalle mediante el servicio financiero central
```

Implementado:

- configuración BRE-B;
- QR privado;
- pagos agrupados;
- detalle por obligación;
- fingerprint de reporte;
- idempotencia;
- comprobante privado;
- bandeja interna;
- aprobación/rechazo;
- bloqueo de aprobación para pagadores;
- concurrencia PostgreSQL;
- aplicación atómica;
- regresiones automatizadas;
- UI de pagador y administración;
- Wompi oculto mediante feature flag mientras no esté operativo.

Despliegue actual relevante:

- migración `0044_configuracion_pago_breb_y_pagobreb` aplicada;
- migración `0045_pago_breb_agrupado_pagadores` aplicada;
- rama de release con `fede4c6` como cierre de validación de tests streaming;
- configuración BRE-B activa con receptor Aprobado y QR validado por hash.

**Pendiente para marcar DONE V1:**

- UAT financiero controlado en producción;
- validar rechazo sin impacto de cartera;
- volver a reportar después de rechazo;
- validar aprobación real;
- comprobar `HistorialPago`;
- comprobar `DetalleContablePago`;
- comprobar cuota, saldo e idempotencia después de aplicar.

---

### 4.3 APROBADO-01 — Geografía/mapa — DONE

La representación geográfica y normalización DANE/DIVIPOLA ya están implementadas.

Bloque cerrado, incluidos APROBADO-01.1 y ajustes de legibilidad:

- marcadores reducidos y etiquetas activas elevadas;
- interacción hover/focus/touch y tooltip ajustados;
- listado completo de municipios activos, sin límite visual de ocho;
- conteos, coordenadas y normalización DANE/DIVIPOLA preservados.

**Alcance del ajuste:** exclusivamente presentación.

No modificar:

- geocodificación;
- agrupación municipio/departamento;
- fuentes DANE;
- lógica de conteo.

---

### 4.4 APROBADO-02 — Reportería administrativa / cartera — DONE

Incluye APROBADO-02, 02.1, 02.2, 02.3, 02.4 y 02.4.1:

- obligación por vencimiento separada del recaudo contable por período;
- recaudo exclusivamente por `DetalleContablePago.fecha_aplicacion` e importes persistidos;
- hoja `Cuotas` conserva cronograma, estado actual y pagado acumulado, con recaudo/aplicaciones del período explícitos;
- pagos anticipados de otro mes excluidos del recaudo actual; última cuota aplicada en el mes incluida aunque el crédito esté `PAGADO`;
- dashboard gerencial y vista por empresa, sin inferir pendiente como obligación menos recaudo;
- reconciliación individual explícita de redondeos, con permiso, auditoría y sin ingresos ficticios;
- IDs técnicos de reconciliación sin localización; PK 999, 1000 y 1234 cubiertos.

Mantener estas regresiones. Históricos sin detalle contable no generan recaudo inferido.

---

### 4.5 Prestadores de Servicios — AVANZADO / NO CERRADO

Prestadores ya no debe tratarse como un módulo embrionario. Existe un pipeline considerablemente avanzado.

Capacidades implementadas o integradas en etapas anteriores:

- dominio `contractors`;
- subdominio dedicado;
- registro/autenticación base;
- solicitud;
- documentos protegidos;
- análisis contractual;
- simulación;
- autorización para centrales;
- Historial de Crédito;
- MiDecisor;
- score interno parametrizado;
- predecisión;
- revisión manual;
- subsanación;
- aprobación interna;
- originación centralizada;
- formalización;
- pagaré/firma;
- postfirma;
- cierre financiero;
- políticas de monto/plazo;
- pruebas y regresiones relevantes.

### Pendiente real

#### A. Auditoría end-to-end del código actual

No desarrollar primero. Auditar desde registro hasta cierre financiero y clasificar cada etapa como:

- IMPLEMENTADO;
- PARCIAL;
- ROTO;
- NO CONECTADO;
- PENDIENTE.

#### B. UAT funcional real

Recorrer como usuario y como operador:

```text
Registro/Login
→ Solicitud
→ Empresa/Contrato
→ Documentos
→ Simulación
→ Autorización centrales
→ HDC / MiDecisor
→ Score
→ Predecisión
→ Revisión / Subsanación
→ Aprobación interna
→ Originación
→ Formalización
→ Firma
→ Postfirma
→ Cierre financiero
```

#### C. Autenticación y UX pública

Validar y afinar:

- login;
- registro;
- Google OAuth;
- callback;
- recuperación de contraseña;
- asociación usuario existente/Google;
- logout;
- redirecciones por subdominio;
- mensajes de error;
- responsive;
- consistencia visual.

#### D. Matriz de roles y permisos

Definir explícitamente:

- solicitante;
- empresa/pagador;
- analista;
- aprobador;
- operador de centrales;
- formalizador;
- administrador.

#### E. Calibración financiera y de riesgo

Contrastar el motor actual contra:

- `Score_Ajustado_Politica_Aprobado.xlsx`;
- casos reales o históricos;
- salidas HDC;
- MiDecisor;
- decisión esperada;
- monto sugerido;
- plazo sugerido.

Los parámetros de riesgo requieren validación de negocio/riesgo; no deben definirse por inferencia técnica.

---

### 4.6 WhatsApp Platform — BASE IMPLEMENTADA / SIGUIENTE GRAN FASE

Arquitectura vigente:

```text
WhatsApp Cloud API
        ↓
Bot Aprobado
conversación / sesión / UX / Flows
        ↓
Internal API
        ↓
Project_aprobado
fuente de verdad financiera
```

Ya existe una base para:

- menú conversacional;
- selección de producto;
- simulaciones contra Core;
- separación `payroll_loan` / `whatsapp_credit`;
- identidad básica;
- OTP;
- consulta de estado;
- consulta de créditos;
- metadata de documentos;
- API interna autenticada;
- observabilidad;
- rate limit;
- trazabilidad con request/correlation IDs;
- WhatsApp Flows iniciales.

Pendientes principales:

- publicación/estabilización de Flows en Meta;
- contrato seguro para media;
- descarga segura de archivos;
- ownership de media;
- validación MIME/tamaño;
- antivirus;
- almacenamiento privado;
- consolidación de identidad fuerte antes de exponer información sensible;
- conectar originación conversacional sin duplicar lógica del Core;
- observabilidad operacional completa;
- IA solo después de estabilizar el flujo determinista.

---

### 4.7 APROBADO-SEC-01 — Integridad de originación y segregación de roles — DONE / MONITOREO

**Clasificación:** P0 mitigado; mantener monitoreo.

Incidente informado por operación: TANBAR TRANSPORTE BARRANCA S.A.S. Una cuenta con `PerfilPagador` originó y aprobó sus propias solicitudes; seis créditos alcanzaron `PENDIENTE_TRANSFERENCIA`, con pagarés `SIGNED`, sin desembolso ni movimiento financiero.

Controles implementados en el código actual:

- bloqueo backend de originación por `PerfilPagador`, sin bloquear staff legítimo sin ese perfil;
- prohibición de autoaprobación en el servicio central, independiente del nivel/configuración;
- anulación segura `PENDIENTE_TRANSFERENCIA -> ANULADO` antes del desembolso;
- `atomic` y `select_for_update` sobre el crédito tanto en anulación como en confirmación de desembolso;
- actor, permiso, motivo y ausencia de movimientos revalidados bajo lock;
- pagaré firmado, archivos, fechas, tokens y eventos ZapSign preservados;
- estados sensibles protegidos en Admin; registro en `HistorialEstado`.

**Cierre operativo comunicado por el responsable en esta actualización:** seis créditos TANBAR anulados conservando histórico: 156/CR-2026-00124, 163/CR-2026-00131, 164/CR-2026-00132, 166/CR-2026-00134, 167/CR-2026-00135 y 168/CR-2026-00136. El crédito 165 no pertenece a este conjunto.

**Alcance de la evidencia local:** commit `b9365b0`; 109 pruebas, 106 aprobadas y tres omitidas por requerir PostgreSQL. Esta sesión no consultó ni anuló créditos productivos, no verificó independientemente el cierre comunicado y no hizo push/deploy. Adjuntar a seguimiento la evidencia operativa y el resultado PostgreSQL de ambas carreras antes de considerar verificada la concurrencia en ese entorno. `DONE / MONITOREO` registra el cierre reportado, no certifica un despliegue ejecutado por esta sesión.

### 4.8 APROBADO-ID-01 — Captura documental móvil controlada

**Prioridad:** P0/P1. **Estado:** PENDIENTE de implementación; auditoría y diseño registrados en sección 14.

Desktop: no ofrecer captura directa; mostrar "Continúa desde tu celular" mediante enlace/QR de continuación autenticada. Móvil: frontal, revisión, repetir/aceptar, posterior, revisión, repetir/aceptar. Continuar solo con ambas caras aceptadas por backend y asociadas al mismo borrador/solicitud.

La interfaz web actual con recuadro no demuestra identidad, legibilidad ni captura física. Presencia de archivos nunca equivale a identidad validada.

---

## 5. Priorización maestra y riesgos prioritarios

### P0 — Mitigados, bajo monitoreo

1. Originación por `PerfilPagador`.
2. Autoaprobación del solicitante.
3. Carrera anulación/desembolso; prueba PostgreSQL pendiente de evidencia en esta sesión.

### P0 — Controles exigidos en APROBADO-ID-01

1. No continuar sin frontal y posterior aceptados técnicamente en backend.
2. Ownership de cada captura y segregación de roles en toda entrada, incluidos reemplazos y casos especiales.
3. Desktop sin captura directa; handoff autenticado obligatorio, sin usar User-Agent como autorización.
4. Separar documento recibido, calidad técnica e identidad validada. Ningún archivo arbitrario acredita identidad.
5. Verificar exposición del storage de cédulas: los modelos actuales usan storage predeterminado con URL media. No se inspeccionó Nginx productivo; si hay acceso público efectivo, tratarlo como incidente P0 y corregir antes de abrir captura externa.

### P1 — Abiertos

1. Archivo de cédula presente no garantiza documento válido, identidad, legibilidad o correspondencia con el solicitante.
2. Frontal/trasera pueden almacenarse sin validación de calidad; captura desde computador facilita imágenes inadecuadas.
3. MIME real, dimensiones mínimas, límites de tamaño/píxeles, orientación y rechazo de imágenes negras/blancas o extremadamente borrosas: definir umbrales con muestras y UAT, no inventarlos.
4. Repetición de captura, timestamp del servidor, privacidad y almacenamiento privado.
5. Continuar endureciendo permisos administrativos sensibles.
6. Movimientos bancarios externos no son completamente observables desde Django; mantener confirmación operativa.
7. Tras ID-01: cerrar E2E/UAT de Prestadores y comprobar cierre UAT BRE-B, sin adelantar esos frentes.

### P2 — Identidad avanzada / apertura posterior

1. OCR de cédula y contraste de número/nombre con solicitud.
2. Detección de documento colombiano, antifraude y biometría/liveness solo con aprobación posterior.
3. Regresiones de autenticación/OAuth, recuperación, subdominio y redirects de Prestadores.
4. Correos/estados visibles y calibración score con negocio.

No implementar P2 durante la captura V1. Ni OCR ni validaciones de imagen sustituyen por sí solos verificación de identidad.

### P3 — Evolución de canal

1. Retomar WhatsApp Platform.
2. Flows productivos.
3. Media segura.
4. Originación conversacional.
5. Observabilidad operacional.

### P4 — Futuro

1. Wompi cuando la integración sea estable.
2. IA conversacional avanzada.
3. Automatización adicional basada en evidencia operativa.

---

## 6. Orden de ejecución recomendado

### Fase 1 — Correcciones de producción cerradas

#### Ticket APROBADO-01 — Mapa

**Estado:** DONE.

**Objetivo:** mejorar legibilidad sin tocar lógica geográfica.

Criterios de aceptación:

- marcadores reducidos;
- Medellín y ciudades densas siguen visibles;
- etiquetas no quedan ocultas permanentemente;
- hover y mobile funcionan;
- conteos no cambian;
- DANE/DIVIPOLA no cambia;
- pruebas existentes continúan pasando.

#### Ticket APROBADO-02 — Reporte cuotas pagadas

**Estado:** DONE, incluidos dashboard gerencial y reconciliación individual.

**Objetivo:** que el reporte represente recaudo ocurrido en el período solicitado.

Fuente consolidada: `DetalleContablePago.fecha_aplicacion`; cronograma y recaudo separados.

Criterios de aceptación:

- pago anticipado de julio no aparece como recaudo de septiembre;
- pago real de última cuota en septiembre sí aparece aunque el crédito quede `PAGADO`;
- tests automatizados para ambos casos;
- totales reconciliables con movimientos financieros.

---

### Fase 2 — APROBADO-ID-01, siguiente frente

1. Actualizar roadmap y completar auditoría/diseño (sección 14).
2. Implementar captura móvil vinculada a solicitud/borrador.
3. UAT iPhone Safari y Android Chrome, resoluciones y tamaños distintos.
4. Confirmar handoff desktop/móvil, expiración, ownership y reintentos.

No desarrollar la cámara en esta actualización documental. No volver a Prestadores antes de cerrar este bloque salvo hallazgo P0.

---

### Fase 3 — Auditoría Prestadores

#### Ticket APROBADO-03 — Auditoría E2E

**Objetivo:** determinar el estado real del pipeline actual sin desarrollar funcionalidad nueva.

Salida esperada:

| Etapa | Ruta/UI | Servicio | Modelo/estado | Pruebas | Estado | Hallazgo | Acción |
|---|---|---|---|---|---|---|---|

Clasificación:

- IMPLEMENTADO
- PARCIAL
- ROTO
- NO CONECTADO
- PENDIENTE

La auditoría debe inspeccionar código actual y no asumir que documentos históricos describen el estado vigente.

---

### Fase 4 — Cierre Prestadores

Convertir la auditoría en tickets P0/P1/P2.

Validar en tres recorridos: A. ingreso -> decisión; B. originación -> firma; C. postfirma -> cierre financiero.

Orden sugerido:

1. bloqueantes funcionales;
2. transiciones/estados;
3. permisos;
4. autenticación;
5. documentos;
6. notificaciones;
7. UX;
8. calibración de política;
9. UAT externo.

---

### Fase 5 — Cierre BRE-B V1 si sigue pendiente

Después de Prestadores, confirmar evidencia UAT: reporte, rechazo sin impacto, nuevo intento, aprobación, `HistorialPago`, `DetalleContablePago`, cuota/saldo e idempotencia. No marcar DONE únicamente por tests automatizados.

### Fase 6 — WhatsApp

Retomar solo cuando Prestadores y BRE-B estén estabilizados.

Orden:

1. identidad;
2. seguridad;
3. Flows;
4. media;
5. originación;
6. observabilidad;
7. IA.

---

## 7. Forma de trabajo con Codex

Para cada ticket:

### Etapa A — Auditoría

Codex debe identificar:

- archivos implicados;
- flujo actual;
- modelos/estados;
- servicios reutilizados;
- riesgos de regresión;
- pruebas existentes;
- comportamiento incorrecto reproducible.

No modificar código durante esta etapa salvo instrucción expresa.

### Etapa B — Plan

Definir:

- cambio mínimo;
- archivos permitidos;
- migración sí/no;
- pruebas nuevas;
- regresiones obligatorias;
- criterios de aceptación.

### Etapa C — Implementación local

- cambio acotado;
- no duplicar lógica financiera;
- no mezclar productos;
- mantener compatibilidad histórica cuando aplique.

### Etapa D — Validación

Como mínimo:

```text
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
```

Además, suite focal y regresiones relacionadas.

Cuando exista concurrencia/locking/constraints PostgreSQL, validar en clon PostgreSQL y no depender solo de SQLite.

### Etapa E — Commit

Un commit coherente por bloque funcional.

No usar `git add .`.

### Etapa F — Producción

1. confirmar branch y HEAD;
2. confirmar DB real;
3. revisar `migrate --plan`;
4. aplicar migraciones;
5. `collectstatic` si aplica;
6. reiniciar servicios necesarios;
7. health/check;
8. UAT controlado;
9. registrar resultado.

---

## 8. Regresiones que deben preservarse

No romper:

- doble aprobación pagador;
- `CAPITAL_REDUCIR_PLAZO`;
- anulaciones y protecciones de firma;
- reestructuración;
- selectores administrativos;
- formalización;
- documentos privados;
- lógica financiera central;
- separación entre pagador, colaborador y staff interno;
- separación `payroll_loan` / `whatsapp_credit`;
- comportamiento histórico compatible donde ya existan créditos previos.

---

## 9. Decisiones vigentes

### BRE-B

BRE-B V1 es canal de recaudo para empresa/pagador de libranza.

No es, en esta fase, el canal ordinario de pago individual del colaborador.

Un eventual BRE-B del colaborador para abonos extraordinarios será una funcionalidad separada.

### Wompi

La integración permanece en código pero deshabilitada mediante feature flag hasta que el canal sea operacionalmente confiable.

### Prestadores

No iniciar nuevas capacidades hasta auditar y cerrar el pipeline existente.

### WhatsApp

El bot no será fuente de verdad financiera.

### Score

Codex puede implementar reglas aprobadas, pero no inventar parámetros de riesgo ni sustituir la validación de negocio.

---

## 10. Cronograma operativo inmediato

### Bloque A — Ahora

- [x] APROBADO-01: geografía/mapa.
- [x] APROBADO-02: reportería/cartera, reconciliación y fix de IDs.
- [x] APROBADO-SEC-01: controles TANBAR implementados; cierre operativo reportado, mantener monitoreo y adjuntar evidencia PostgreSQL.
- [x] Actualizar roadmap y auditar/diseñar APROBADO-ID-01.
- [ ] APROBADO-ID-01A/B: implementar base segura y captura móvil.
- [ ] APROBADO-ID-01C: UAT móvil y confirmación desktop handoff.

### Bloque B — Inmediatamente después

- [ ] APROBADO-03: auditoría end-to-end Prestadores.
- [ ] convertir hallazgos en backlog P0/P1/P2.
- [ ] ejecutar correcciones bloqueantes.

### Bloque C — Cierre Prestadores

- [ ] UAT interno completo.
- [ ] autenticación/OAuth.
- [ ] permisos.
- [ ] notificaciones.
- [ ] UX/responsive.
- [ ] calibración de riesgo.
- [ ] UAT externo controlado.

### Bloque D — WhatsApp

- [ ] Antes: confirmar UAT BRE-B pendiente después del cierre Prestadores.
- [ ] identidad fuerte.
- [ ] Flows productivos.
- [ ] media segura.
- [ ] originación conversacional.
- [ ] observabilidad.

---

## 11. Métrica de avance

Un bloque se considera:

### DONE

Solo cuando cumple:

- código integrado;
- migraciones aplicadas si existen;
- pruebas focales;
- regresiones;
- validación PostgreSQL cuando corresponda;
- UAT funcional;
- permisos configurados;
- documentación actualizada.

### EN CIERRE

Código productivo existe, pero falta UAT, permisos, calibración o afinación operativa.

### EN DESARROLLO

Hay funcionalidad pendiente o parcialmente conectada.

### FUTURO

No debe consumir capacidad hasta cerrar prioridades superiores.

---

## 12. Próxima acción concreta

La secuencia inmediata acordada es:

```text
1. Actualizar roadmap
2. APROBADO-ID-01 — Auditoría + diseño
3. Implementar captura móvil
4. UAT iPhone Safari / Android Chrome / resoluciones distintas
5. Confirmar desktop handoff
6. Retomar APROBADO-03 — Auditoría E2E Prestadores
7. Prestadores A: ingreso -> decisión; B: originación -> firma; C: postfirma -> cierre financiero
8. UAT BRE-B si aún falta cierre
9. WhatsApp posteriormente
```

Este orden debe mantenerse salvo aparición de un incidente productivo P0.

---

## 13. Fuentes técnicas relacionadas

Este roadmap debe leerse junto con las fuentes técnicas del proyecto, entre ellas:

- `whatsapp_internal_api.md`
- `whatsapp_flows.md`
- `initial_whatsapp_flow.md`
- `core_api_integration.md`
- `conversations.md`
- documentación de Prestadores y score vigente en el repositorio;
- migraciones y tests del código actual;
- `Score_Ajustado_Politica_Aprobado.xlsx`;
- documentación de HDC / MiDecisor.

Cuando exista contradicción entre un documento histórico y el código productivo actual, primero se debe auditar el código y actualizar la documentación antes de desarrollar nuevas capacidades.

---

## 14. APROBADO-ID-01: auditoría actual y diseño propuesto

Auditoría estática del código local del 2026-09-12. No se modificó código de captura ni se ejecutó UAT de cámara/dispositivos. Las observaciones de producción de TANBAR provienen del responsable; no se accedió a bases ni archivos productivos.

### 14.1 Arquitectura actual comprobada

| Parte | Ubicación / llamada real | Estado | Hallazgo |
|---|---|---|---|
| Entrada Libranza | `usuarios/urls_libranza.py:73`, `/libranza/solicitar/` -> `gestion_creditos/views/solicitudes.py:solicitud_credito_libranza_view`; alias en `gestion_creditos/urls/solicitudes.py` | IMPLEMENTADO | Login, restricción de producto y bloqueo PerfilPagador. POST usa `CreditoLibranzaForm`, crea `Credito(usuario=request.user)` en `EN_REVISION`, snapshot y detalle dentro de `atomic`. |
| Cámara Libranza | `templates/gestion_creditos/solicitud_libranza.html:abrirCamaraParaInput`, `asignarArchivoAInput` | IMPLEMENTADO | JS inline: `getUserMedia`, cámara trasera preferida, vídeo/canvas/JPEG, `File` y `DataTransfer`. Permite repetir/aceptar en modal; detiene tracks al cerrar. No hay endpoint separado de captura. |
| Fallback Libranza | Mismo template, `hiddenCaptureInput` | PARCIAL | `accept=image/*`, `capture=environment` ante falta/error de cámara web. Asigna el archivo al formulario sin la misma revisión explícita del modal. No excluye webcam desktop ni POST manual; ocultar el input no es seguridad. |
| Validación Libranza | `gestion_creditos/forms.py:CreditoLibranzaForm`, `clean_cedula_frontal/trasera`, `_validar_documento_imagen`, `clean` | PARCIAL | Exige ambas caras y certificado bancario. Extensiones JPG/JPEG/PNG/WEBP y MIME declarado opcional; SHA-256 detecta archivos idénticos entre campos. No decodifica imagen ni limita específicamente bytes/dimensiones de cédula. No verifica MIME real, calidad, orientación o identidad. |
| Modelo/almacenamiento Libranza | `gestion_creditos/models.py:CreditoLibranza.cedula_frontal/cedula_trasera` | PARCIAL | FileField obligatorio en formulario, `upload_to=credito_libranza/cedulas/`, storage predeterminado. Normalización del nombre conserva fragmento del original y podría conservar PII. Sin metadata de captura de cédula, borrador documental ni marcador de identidad verificada. `save()` por sí solo no sustituye validaciones del formulario. |
| Originación especial | `libranza/services/special_case_originator.py:originate_special_case_libranza`, `_get_or_create_user` | PARCIAL | Persiste frontal/trasera aportados en `files` en el mismo detalle. PerfilPagador como solicitante queda bloqueado por hotfix, pero no hay prueba de origen móvil o calidad de imagen. Integrar la misma política documental futura, sin bloquear al operador staff legítimo. |
| Entrada Prestadores | `contractors/urls.py`, `/solicitar/` en host contratistas -> `solicitar_prestador_view` | IMPLEMENTADO | Autenticación, ownership por `_obtener_solicitud_del_usuario`; nueva solicitud asigna `usuario=request.user`. Cámara equivalente en `templates/contractors/solicitud_prestador.html`; no requiere crear crédito financiero. |
| Validación Prestadores | `contractors/forms.py:SolicitudPrestadorForm.clean`, `contractors/models.py:ContractorApplicationDocument.clean` | PARCIAL | JPG/JPEG/PNG, máximo 8 MiB por documento y PDF para contrato/certificado. Se ejecuta `full_clean()` al guardar. No inspecciona MIME real ni decodifica/califica la imagen. |
| Origen de captura Prestadores | `contractors/views.py:_validar_origenes_cedula`, `_metadata_documentos_desde_request` | PARCIAL | Acepta valores POST `camera`, `capture`, o `upload_fallback` según flag. Son declaraciones manipulables, no evidencia del dispositivo. `captured_at` se calcula al recibir el POST: es recepción del servidor, no fecha verificable de toma. |
| Ruta alternativa de carga | `/solicitud/<id>/documentos/` -> `documentos_prestador_view`, `DocumentoPrestadorForm`, `templates/contractors/documentos_prestador.html` | PARCIAL | Upload/reemplazo convencional, incluido documento de identidad, sin `_validar_origenes_cedula`. Galería/desktop permitidos. Esta entrada debe adoptar el mismo servicio de captura para no dejar un bypass. |
| Persistencia/descarga Prestadores | `ContractorApplicationDocument`, `contractors/services/solicitud.py:guardar_documento_prestador`, vistas de descarga de usuario/staff | PARCIAL | FK solicitud, actor, tipo único, metadata y timestamps. Descarga de usuario comprueba ownership; el servicio no comprueba ownership por sí mismo. Storage predeterminado en `prestadores/solicitudes/<id>/<tipo>`. Al reemplazar, elimina archivo anterior mediante `on_commit`: no es archivo histórico inmutable. |
| Completitud/transiciones | `contractors/services/solicitud.py:solicitud_tiene_documentos_obligatorios`, `actualizar_estado_documental`; `contractors/services/predecision.py` | PARCIAL | Completo significa que existen los cuatro tipos documentales en BD. Pasa de `DOCUMENTOS_PENDIENTES` a `DOCUMENTOS_CARGADOS` fuera de estados de evaluación; habilita continuación a simulación. Predecisión exige los tipos, no identidad visual validada. Reemplazos pueden invalidar evaluación; no crear una transición paralela. |
| Continuación desktop/móvil | Entradas y modelos anteriores | PENDIENTE | No se encontró sesión/token documental de handoff, endpoint de canje ni polling de captura. |
| Verificación de identidad de la imagen | Validadores anteriores | PENDIENTE | Presencia de archivo, hashes y validación de datos escritos no prueban que la cédula fotografiada pertenezca al solicitante. |

**Privacidad por verificar antes de implementación:** ambos modelos de cédula usan storage predeterminado; `aprobado_web/settings.py` define `MEDIA_URL=/media/` y los URLconf sirven media en DEBUG. Los enlaces protegidos de Prestadores no protegen por sí solos el archivo ante acceso directo al storage. `gestion_creditos/views/pagador.py` construye URLs de documentos en su exportación. No se ha comprobado la configuración efectiva de Nginx ni la exposición pública productiva; no afirmar privacidad completa sin esa verificación. Reutilizable: `gestion_creditos/storage.py:PrivateDocumentStorage`, sin URL pública, utilizado ya por otros documentos del sistema. Una transición de storage deberá preservar archivos/referencias existentes y no borrar evidencias.

**Completitud no es identidad:** Libranza envía a `EN_REVISION` después de validar el formulario; no tiene un estado documental previo dedicado. Prestadores comprueba tipos de documentos y datos contractuales. Ninguno de esos hechos certifica legibilidad o identidad del documento recibido. No confundir comprobaciones contractuales del pipeline con OCR/validación de cédula.

### 14.2 Alternativas de cámara y decisión recomendada

| Alternativa | Ventaja | Limitación / decisión |
|---|---|---|
| `<input type="file" accept="image/*" capture="environment">` | Puede invocar captura nativa móvil, experiencia familiar y menor mantenimiento. | El navegador decide el comportamiento; no garantiza impedir galería, cámara frontal o selector de archivos. Revisión explícita de ambas caras sigue siendo responsabilidad de Aprobado. |
| `navigator.mediaDevices.getUserMedia()` | Permite secuencia propia, encuadre, preview, captura y futuras verificaciones de calidad. | Cámara dentro de la web, HTTPS y permiso; orientación/zoom/resolución varían. Un stream no certifica hardware móvil, identidad ni ausencia de cámaras virtuales. |
| App nativa / PWA avanzada | App nativa permite integraciones específicas de plataforma si se justifican posteriormente. | PWA sigue sujeta a APIs/permisos del navegador; no equivale a control total nativo ni a biometría/liveness. Una app nativa tampoco acredita identidad solo por usar cámara. |

**Recomendación: híbrida mobile-first**, invirtiendo la prioridad actual: `capture=environment` como entrada principal móvil, con revisión/repetición uniforme de frontal y posterior; `getUserMedia` como alternativa explícita si el navegador lo soporta y la UX lo requiere. No fallback automático a carga libre en desktop. Si falla la cámara/permisos, conservar borrador y ofrecer reintento/ayuda, sin etiquetar un upload como prueba de cámara.

El requerimiento "desktop no captura" es una restricción del flujo soportado: eliminar su upload directo y exigir sesión de captura canjeada/autorizada en backend. **No puede garantizarse desde una web convencional que un cliente manipulado sea físicamente un móvil**: UA, ancho, touch y `facingMode` son señales UX, no acreditación. Un QR puede abrirse en otro navegador desktop. Si negocio exige prohibición física inviolable de desktop/galería, esta propuesta web no la satisface; requeriría evaluar mecanismos de atestación/SDK y una política específica, fuera de V1. No certificar cumplimiento estricto con un booleano enviado por JS.

Fuentes técnicas consultadas: [W3C HTML Media Capture](https://www.w3.org/TR/html-media-capture/) y [W3C Media Capture and Streams](https://www.w3.org/TR/mediacapture-streams/). Respaldan la dependencia del navegador/permiso y la distinción entre selección/captura y un stream controlado por la página. La elección híbrida es una recomendación de diseño para Aprobado, no una garantía de esas especificaciones.

### 14.3 Desktop handoff y backend propuesto (no implementado)

1. Usuario autenticado inicia solicitud. En Libranza crear un **borrador documental**, no un `Credito`: hoy el crédito solo existe tras el POST completo. Persistir campos permitidos del formulario en backend, con propietario y producto, sin datos sensibles en URL/QR. Prestadores puede vincular la sesión a su solicitud existente; para una nueva también se requiere contexto previo a completar el formulario.
2. Backend emite token opaco aleatorio con `secrets.token_urlsafe(32)`, guarda solo hash, propósito de captura, borrador/solicitud, usuario, expiración (propuesta inicial: 10 minutos, configurable) y estado. Nunca enviar PII, cookie de sesión, cédula ni contraseña en QR. Enlace HTTPS de host permitido, sin dependencia de un generador QR externo.
3. GET del enlace solo abre continuación, no consume el token: previews/escáneres no deben gastarlo. Usuario se autentica en móvil con la **misma cuenta**; el token no autentica ni autoriza por sí solo. Validar actor/rol, ownership, producto, vigencia, estado del borrador y propósito antes de un POST+CSRF de canje.
4. Canje único bajo `transaction.atomic` y lock del registro. Rotar el secreto y ligar la sesión de captura a la sesión autenticada móvil. Revocar cualquier canje anterior al regenerar el QR. No exigir la misma cookie que desktop: son dos sesiones del mismo usuario. Móvil iniciado directamente obtiene su sesión documental sin QR después de las mismas comprobaciones.
5. Frontal y posterior se cargan por separado mediante el servicio común. Validar contenido real, bytes/píxeles/dimensiones y estado; aplicar límites configurables, nombres aleatorios y storage privado. No confiar en `source`, MIME declarado, filenames, EXIF ni timestamps enviados como evidencia. Normalizar orientación y minimizar metadatos; conservar evidencia necesaria según política aprobada.
6. Cada aceptación queda persistida con lado, hash, recepción del servidor, validación técnica, actor y sesión. Una repetición cambia solo el lado seleccionado y no completa el otro. Registro de eventos de emisión/canje/rechazo/aceptación/finalización sin imágenes ni tokens crudos en logs. Calidad básica no marca identidad como validada.
7. Finalización idempotente y bloqueada exige ambas caras válidas de la misma sesión vigente. Asociar documentos al borrador/solicitud correcto. Al enviar Libranza, reutilizar la creación financiera existente dentro de su transacción, consumir una sola vez el borrador y vincular el crédito resultante. Desactivar el antiguo POST de archivos como bypass; repetir envíos no debe crear otro crédito.
8. Desktop consulta únicamente estado/progreso mediante GET autenticado y propietario, con polling acotado/backoff. No devuelve archivos ni credenciales. Después de finalización continúa el formulario existente; no hay aprobación crediticia automática por completar captura.
9. Caducidad/abandono revocan tokens; definir limpieza de archivos temporales no vinculados y retención de auditoría. No borrar evidencia ya asociada a créditos o formalización. Evitar tokens en logs/referers/historial: propuesta enlace con fragmento intercambiado por POST, `history.replaceState`, `Referrer-Policy: no-referrer` y `Cache-Control: no-store` en las pantallas sensibles, sin recursos de terceros.

**Infraestructura:** Django ORM, sesiones, CSRF, `secrets`/hashlib, almacenamiento privado y Pillow ya usado en el proyecto. Polling basta inicialmente, sin WebSockets ni nuevos servicios. Los tokens existentes de activación de pagador/ejecutivo/inversionista sirven como referencia de expiración/auditoría, **no** deben reutilizarse como credenciales de captura ni compartir propósito. Un token firmado sin estado persistente no garantiza un solo uso ni revocación entre workers. Para QR, no se identificó una dependencia declarada específica; entregar enlace seguro funcional y evaluar una biblioteca local mínima y mantenida para codificarlo, nunca construir el algoritmo ni enviar el token a un servicio externo.

**Persistencia/migraciones previstas:** sí, para una sesión/borrador documental durable con ownership, token hash único, vencimiento, canje/consumo, asociación final y metadata de auditoría. Definir el esquema mínimo en ID-01A; no crear créditos temporales ni una segunda lógica financiera. Cambiar FileField a storage privado puede requerir `AlterField` y un procedimiento independiente de traslado/compatibilidad de históricos. No asignar número de migración ni ejecutar movimiento de archivos en esta fase. No basta una sesión Django/local-memory cache como registro compartido entre dispositivos y procesos.

### 14.4 Archivos candidatos para implementación posterior

- `gestion_creditos/views/solicitudes.py`, `gestion_creditos/forms.py`, `usuarios/urls_libranza.py` y alias `gestion_creditos/urls/solicitudes.py`: conectar borrador/captura con POST final y cerrar entradas antiguas.
- `templates/gestion_creditos/solicitud_libranza.html`: mensaje desktop, QR/enlace, estado y revisión móvil; extraer JS de captura a asset compartido si reduce la duplicación existente.
- Nuevos servicio/vistas/templates/tests de captura bajo `gestion_creditos`, con modelo/migración mínimos en su dominio: sesión, validación, asociación y auditoría. No duplicar originación.
- `gestion_creditos/storage.py` como utilidad existente a reutilizar; `gestion_creditos/models.py` y settings solo donde lo exijan esquema/storage/TTL. Auditar descargas y consumidores `.url` de Libranza antes de cambiar almacenamiento.
- `libranza/services/special_case_originator.py` y sus formularios/entradas: exigir contexto documental válido del solicitante, preservando operación legítima del asesor.
- `contractors/views.py`, `contractors/forms.py`, `contractors/services/solicitud.py`, `contractors/models.py`, `contractors/urls.py`, `templates/contractors/solicitud_prestador.html` y `documentos_prestador.html`: adaptadores sobre el servicio común, cerrar upload alternativo. Revisar subsanación y vínculo de archivos al expediente para no invalidar/cambiar datos financieros por el transporte documental.

No modificar estos archivos todavía. Integrar por producto con pruebas; la auditoría completa APROBADO-03 sigue después del cierre documental.

### 14.5 Cobertura existente y pruebas necesarias

**Existentes inspeccionadas, no ejecutadas en esta tarea documental:**

- `gestion_creditos/tests/test_libranza_form_js.py`: referencias JS, rutas y selección de empresa; no prueba cámara real.
- `gestion_creditos/tests/test_integridad_originacion_anulacion.py`: PerfilPagador bloqueado y POST válido normal/staff. Sus bytes simulados con MIME imagen pasan el formulario actual: no demuestra imagen decodificable.
- `contractors/tests/test_portal_minimo_prestadores.py`: PDF rechazado como cédula, fallback manual bloqueado en formulario principal, asociación/ownership, reemplazo, tipos obligatorios, descargas y presencia de controles cámara. No acredita dispositivo real, MIME real ni que toda ruta respete captura móvil.

**Nuevas obligatorias:**

1. Ambos lados requeridos en backend; falta/vacío/archivo corrupto/extensión engañosa/MIME falso/tamaño y megapíxeles excesivos rechazados. Dimensiones, EXIF/orientación e imágenes negras/blancas/borrosas con fixtures controlados y umbrales aprobados; no rechazar documentos válidos sin UAT.
2. Ownership cruzado, PerfilPagador, usuario anónimo, staff legítimo, propósito/host/producto equivocados, token expirado/revocado/reutilizado, CSRF y reautenticación. QR no autoriza otra solicitud y no revela PII.
3. Doble canje simultáneo y doble finalización en PostgreSQL: un ganador, una vinculación, sin crédito duplicado. Fallos de upload/DB, expiración durante carga, retry y rollback sin mezclar archivos ni dejar evidencia consumida a medias.
4. Desktop no muestra captura y el endpoint antiguo no acepta archivos sin sesión documental válida. Documentar que emular un navegador móvil no demuestra hardware: no usar un test de UA como prueba de seguridad absoluta.
5. Captura frontal/posterior, revisión, repetir, cancelar, permisos denegados, cambio de orientación, navegación atrás, recarga, sesión expirada, cambio de dispositivo, red interrumpida y reconexión.
6. Descarga privada protegida, URL media directa inaccesible, ausencia de PII/tokens en logs y polling, retención segura y ningún borrado de documentos históricos. Validar Nginx/storage con prueba read-only autorizada antes de afirmar privacidad productiva.
7. Regresión de Libranza, casos especiales, Prestadores/documentos/subsanación y estados existentes: una captura completa no crea crédito ni aprueba identidad por sí sola.

**UAT real iOS/Android:** iPhone Safari y Android Chrome; vertical/horizontal, diferentes cámaras/resoluciones, tamaños de pantalla, permiso denegado/revocación, volver desde cámara nativa, imágenes grandes y formatos HEIC/HEIF si el dispositivo los entrega. Decidir normalización soportada o error recuperable, sin fingir compatibilidad de un decodificador inexistente. Revisar `DataTransfer`, autoplay/`playsinline`, interrupciones de stream y consumo de memoria. Probar navegadores embebidos y orientar a Safari/Chrome cuando no sean compatibles. Simulación Playwright no sustituye cámara en dispositivo físico.

### 14.6 Tickets pequeños y puertas de salida

| Ticket | Prioridad / estado | Alcance | Salida verificable |
|---|---|---|---|
| APROBADO-ID-01A: contrato seguro y handoff | P0, PENDIENTE de implementación; diseño documentado | Borrador/sesión persistente, propietario, canje único, TTL, permisos, auditoría, storage privado, cierre de endpoints alternativos. Definir qué se garantiza y qué no sobre dispositivo. | Tests Django/PostgreSQL de ownership, replay, concurrencia, rollback y privacidad; ninguna creación financiera anticipada. |
| APROBADO-ID-01B: captura móvil y revisión | P0/P1, PENDIENTE | Híbrida nativa/web, frontal/posterior, repetir/aceptar, validación técnica y separación de identidad; conectar formularios al servicio sin duplicar reglas. | Tests de contenido y UX; desktop handoff y polling; ambos lados vinculados a una única solicitud, sin bypass de upload. |
| APROBADO-ID-01C: UAT y cierre documental | P1, PENDIENTE | Safari iPhone/Chrome Android físicos, resoluciones distintas, formatos, fallos/red/expiración; verificar storage real y continuidad desktop. | Evidencia reproducible, riesgos aceptados por negocio, regresiones y autorización de cierre. Después retomar APROBADO-03, BRE-B pendiente y finalmente WhatsApp. |

P2 (OCR, documento colombiano, contraste de identidad, antifraude, biometría/liveness) no se implementa en estos tickets. Repriorizar únicamente por un hallazgo P0 confirmado.

### 14.7 APROBADO-ID-00: privacidad documental historica

**Prioridad: P0. Estado: EN IMPLEMENTACIÓN.** Actualizacion posterior a la auditoria
historica anterior: el operador confirmo separacion real de PRIVATE_DOCUMENTS_ROOT
(/root/aprobado_web/private_documents, existente, no symlink, no expuesto por Nginx)
y MEDIA_ROOT (/var/www/aprobado/media). Las nuevas capturas ID-01A son privadas.
Existe exposicion potencial de documentos historicos por /media/ en los cuatro
hosts Aprobado, **sin evidencia confirmada de acceso indebido**.

- Fase 1: endpoints por objeto con propietario/pagador activo de misma empresa/
  staff con permiso explicito, previews y consumidores protegidos, token temporal
  ZapSign sin logs sensibles, no-store, X-Accel interno y bloqueo media por allowlist.
  No mover, renombrar, borrar documentos ni cambiar referencias historicas.
- Fase 2 independiente: inventario, copia verificada, hashes y respaldo, cambio
  controlado de referencias a almacenamiento privado y rollback por lotes.
- Puerta de cierre fase 1: regresiones locales + PostgreSQL/clon y UAT Nginx de
  propietario/pagador/admin/anonimo en aprobado, emprender, contratistas y market.
  Nginx no modificado por codigo; fundetec-staging fuera de alcance.

Procedimiento, matriz de permisos y snippet exacto propuesto:
[privacidad_documental_ID_00.md](privacidad_documental_ID_00.md).
No considerar solucionada la exposicion productiva solo por pasar tests Django.
