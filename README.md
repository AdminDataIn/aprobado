# Aprobado

Fuente principal de verdad del proyecto. Este archivo describe el estado operativo actual del sistema, su estructura general y cómo trabajar sobre él sin apoyarse en documentación obsoleta.

## Qué es

`Aprobado` es una plataforma Django multiproducto con separación por dominio y subdominio:

- `aprobado.com.co`: libranza, panel administrativo, pagador y billetera.
- `emprender.aprobado.com.co`: flujo de emprendimiento.
- `market.aprobado.com.co`: marketplace.
- `inversionista`: portal de inversionista dentro del mismo proyecto.

El núcleo funcional vive en:

- [gestion_creditos](c:\.vscode\Project_aprobado\gestion_creditos)
- [usuarios](c:\.vscode\Project_aprobado\usuarios)
- [aprobado_web](c:\.vscode\Project_aprobado\aprobado_web)

## Estado actual

Lo sólido hoy:

- Libranza con simulación, originación, activación, dashboards y pagos.
- Pagador con carga de empleados, pago offline, pago masivo por archivo y selección directa de obligaciones.
- Dashboard administrativo con filtro por empresa y métricas de cartera más útiles.
- Marketplace con panel admin y reset de contraseña corregidos.
- Auth cerrado para pagador e inversionista por correo/contraseña.
- Cierre formal de créditos a `PAGADO`.
- Normalización de nombres a MAYÚSCULA en nuevos writes relevantes.

Lo que sigue parcial:

- Refactorización interna de `gestion_creditos`: sigue habiendo dependencia fuerte de `views.py` y `credit_services.py`.
- Mercado Pago: no hay checkout productivo completo.
- Prorrateo de intereses por pago anticipado: solo base técnica, no lógica activa.
- Documentación especializada en `docs/`: útil como referencia, no toda es canónica.

El backlog vivo está en [ESTADO_Y_BACKLOG.md](c:\.vscode\Project_aprobado\ESTADO_Y_BACKLOG.md).

## Estructura general

### Configuración principal

- [aprobado_web/settings.py](c:\.vscode\Project_aprobado\aprobado_web\settings.py)
- [aprobado_web/urls_main.py](c:\.vscode\Project_aprobado\aprobado_web\urls_main.py)
- [aprobado_web/urls_emprender.py](c:\.vscode\Project_aprobado\aprobado_web\urls_emprender.py)
- [aprobado_web/urls_market.py](c:\.vscode\Project_aprobado\aprobado_web\urls_market.py)
- [aprobado_web/middleware.py](c:\.vscode\Project_aprobado\aprobado_web\middleware.py)
- [aprobado_web/celery.py](c:\.vscode\Project_aprobado\aprobado_web\celery.py)

### Módulos principales

- [usuarios](c:\.vscode\Project_aprobado\usuarios)
  - autenticación y activación por tipo de usuario
  - landings y formularios de acceso
- [gestion_creditos](c:\.vscode\Project_aprobado\gestion_creditos)
  - modelos de crédito, pagos, marketplace, billetera y vínculos laborales
  - dashboards admin, pagador, inversionista y marketplace
  - servicios de lifecycle, pagos, emails y métricas

### URLConfs funcionales

- [gestion_creditos/urls_gestion.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_gestion.py)
- [gestion_creditos/urls_pagador.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_pagador.py)
- [gestion_creditos/urls_marketplace.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_marketplace.py)
- [gestion_creditos/urls_inversionista.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_inversionista.py)
- [gestion_creditos/urls_solicitudes.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_solicitudes.py)
- [gestion_creditos/urls_billetera.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_billetera.py)

## Cómo correrlo local

### 1. Entorno

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Variables de entorno

El archivo local es `.env`. No se sube al repo.

Campos sensibles mínimos:

- `SECRET_KEY`
- `DEBUG`
- `DATABASE_URL` o flags locales de SQLite
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `DEFAULT_FROM_EMAIL`
- `CONTACT_EMAIL`
- `REDIS_URL`

Pasarelas:

- WOMPI sigue siendo parte del core crediticio existente.
- Mercado Pago no está cerrado productivamente.

### 3. Base de datos y servidor

```powershell
venv\Scripts\python.exe manage.py migrate
venv\Scripts\python.exe manage.py runserver
```

### 4. Celery

```powershell
venv\Scripts\celery.exe -A aprobado_web worker -l info --pool=solo
venv\Scripts\celery.exe -A aprobado_web beat -l info
```

## Checks y tests recomendados

### Check general

```powershell
venv\Scripts\python.exe manage.py check
```

### Suite dirigida hoy confiable

```powershell
venv\Scripts\python.exe manage.py test gestion_creditos.test_credit_lifecycle gestion_creditos.test_name_normalization gestion_creditos.test_pagos_offline gestion_creditos.tests_marketplace_flow usuarios.tests --verbosity 1
```

### Cuando toques `gestion_creditos`

Empieza por [gestion_creditos/README.md](c:\.vscode\Project_aprobado\gestion_creditos\README.md).

## Reglas documentales

Jerarquía documental vigente:

1. [README.md](c:\.vscode\Project_aprobado\README.md)
   Fuente principal.
2. [ESTADO_Y_BACKLOG.md](c:\.vscode\Project_aprobado\ESTADO_Y_BACKLOG.md)
   Estado real + backlog vivo.
3. [HITOS_CERRADOS.md](c:\.vscode\Project_aprobado\HITOS_CERRADOS.md)
   Historial compacto de hitos cerrados.
4. [gestion_creditos/README.md](c:\.vscode\Project_aprobado\gestion_creditos\README.md)
   Estado y estructura real de la app más cargada.
5. [MAPA_DOCUMENTAL.md](c:\.vscode\Project_aprobado\MAPA_DOCUMENTAL.md)
   Mapa documental y guía operativa corta.

Documentos secundarios:

- [docs](c:\.vscode\Project_aprobado\docs): notas especializadas/históricas.
- archivos especializados como [FLUJO_DE_PAGO_WOMPI_EMPRENDIMIENTO.md](c:\.vscode\Project_aprobado\FLUJO_DE_PAGO_WOMPI_EMPRENDIMIENTO.md), [ZAPSIGN_PAGARES.md](c:\.vscode\Project_aprobado\ZAPSIGN_PAGARES.md) o [CALCULO_CAPACIDAD_DESCUENTO_LIBRANZA.md](c:\.vscode\Project_aprobado\CALCULO_CAPACIDAD_DESCUENTO_LIBRANZA.md): referencia puntual, no fuente principal.

## Estado de pasarelas y pagos

### WOMPI

- Sigue presente en flujos existentes del core crediticio.
- No es la fuente principal del pago offline del pagador.
- No debe presentarse como la única estrategia futura de recaudo.

### Pago offline

El flujo vigente y soportado hoy para pagador es:

- carga de pagos por archivo Excel
- pago offline manual
- selección directa de obligaciones desde dashboard

El CSV quedó como compatibilidad residual; no es el formato oficial recomendado.

### Mercado Pago

Estado real:

- existe dominio base de marketplace para pedido/pago/liquidación
- no existe integración productiva cerrada de preferencia + webhook + conciliación final

Conclusión:

- no debe venderse como listo para producción

## Advertencias

- `gestion_creditos` sigue parcialmente refactorizado. No crecer directamente sobre [gestion_creditos/views.py](c:\.vscode\Project_aprobado\gestion_creditos\views.py) si puedes evitarlo.
- Si haces deploy, revisa migraciones nuevas y tareas programadas.
- No asumas que toda la documentación en `docs/` está al día; valida contra este README y el código.
