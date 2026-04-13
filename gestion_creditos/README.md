# gestion_creditos

Documentación específica de la app más cargada del proyecto. Esta guía describe la estructura real de `gestion_creditos`, qué piezas son nuevas, qué sigue siendo legacy y cómo trabajar sobre la app sin empeorar el monolito.

## Qué contiene esta app

`gestion_creditos` concentra hoy:

- créditos de libranza y emprendimiento
- dashboards admin y pagador
- pagos offline y pagos masivos
- marketplace
- billetera
- notificaciones y tareas programadas
- comandos operativos y de QA

Es la app más crítica del proyecto y también la que más deuda técnica acumula.

## Estructura actual real

### Modelos y dominio

- [models.py](c:\.vscode\Project_aprobado\gestion_creditos\models.py)

### Servicios legacy

- [credit_services.py](c:\.vscode\Project_aprobado\gestion_creditos\credit_services.py)
- [email_service.py](c:\.vscode\Project_aprobado\gestion_creditos\email_service.py)
- [tasks.py](c:\.vscode\Project_aprobado\gestion_creditos\tasks.py)

Estos siguen siendo piezas centrales y todavía concentran demasiado.

### Servicios nuevos o más focalizados

- [services/dashboard_metrics.py](c:\.vscode\Project_aprobado\gestion_creditos\services\dashboard_metrics.py)
- [services/credit_lifecycle.py](c:\.vscode\Project_aprobado\gestion_creditos\services\credit_lifecycle.py)
- [services/payments.py](c:\.vscode\Project_aprobado\gestion_creditos\services\payments.py)
- [services/name_normalization.py](c:\.vscode\Project_aprobado\gestion_creditos\services\name_normalization.py)
- [services/interest_proration.py](c:\.vscode\Project_aprobado\gestion_creditos\services\interest_proration.py)
- [services/empleados_service.py](c:\.vscode\Project_aprobado\gestion_creditos\services\empleados_service.py)

### Vistas

Fuente real todavía:

- [views.py](c:\.vscode\Project_aprobado\gestion_creditos\views.py)

Wrappers creados para comenzar modularización:

- [views_admin.py](c:\.vscode\Project_aprobado\gestion_creditos\views_admin.py)
- [views_pagador.py](c:\.vscode\Project_aprobado\gestion_creditos\views_pagador.py)
- [views_marketplace.py](c:\.vscode\Project_aprobado\gestion_creditos\views_marketplace.py)
- [views_inversionista.py](c:\.vscode\Project_aprobado\gestion_creditos\views_inversionista.py)
- [views_marketplace_checkout.py](c:\.vscode\Project_aprobado\gestion_creditos\views_marketplace_checkout.py)

Estado real:

- `views_admin.py`, `views_pagador.py` y `views_marketplace.py` son wrappers de compatibilidad; todavía reexportan desde `views.py`
- `views_inversionista.py` y `views_marketplace_checkout.py` ya son módulos más separados

### URLs

- [urls.py](c:\.vscode\Project_aprobado\gestion_creditos\urls.py)  
  Legacy.
- [urls_gestion.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_gestion.py)
- [urls_pagador.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_pagador.py)
- [urls_marketplace.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_marketplace.py)
- [urls_inversionista.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_inversionista.py)
- [urls_billetera.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_billetera.py)
- [urls_solicitudes.py](c:\.vscode\Project_aprobado\gestion_creditos\urls_solicitudes.py)

Estado real:

- `urls_gestion.py`, `urls_pagador.py` y `urls_marketplace.py` ya usan wrappers
- `urls.py`, `urls_billetera.py` y `urls_solicitudes.py` siguen dependiendo directo de `views.py`

## Qué es legacy y qué es nuevo

### Legacy todavía dominante

- `views.py`
- `credit_services.py`
- parte de `tasks.py`
- parte de `forms.py`

### Nuevo / ya incorporado

- lifecycle formal de créditos
- pago de obligaciones sin Excel
- métricas de dashboard admin separadas
- política nueva de notificaciones de cuotas pendientes
- normalización de nombres

## Reglas para trabajar en esta app

### No seguir creciendo así

No agregues funcionalidad nueva directamente en:

- `views.py`
- `credit_services.py`

salvo que sea un hotfix pequeño o una compatibilidad inevitable.

### Preferencia actual

Nuevos cambios deben caer, cuando sea posible, en:

- `services/`
- módulos de vista por dominio
- comandos explícitos
- tests focalizados

### Qué no está cerrado

- La refactorización no está terminada.
- `interest_proration.py` no está activo.
- Mercado Pago no está integrado productivamente.
- Los tests siguen sueltos en la raíz.

## Tests de esta app

Hoy siguen en raíz:

- [tests.py](c:\.vscode\Project_aprobado\gestion_creditos\tests.py)
- [tests_marketplace_flow.py](c:\.vscode\Project_aprobado\gestion_creditos\tests_marketplace_flow.py)
- [test_credit_lifecycle.py](c:\.vscode\Project_aprobado\gestion_creditos\test_credit_lifecycle.py)
- [test_name_normalization.py](c:\.vscode\Project_aprobado\gestion_creditos\test_name_normalization.py)
- [test_pagos_offline.py](c:\.vscode\Project_aprobado\gestion_creditos\test_pagos_offline.py)
- [test_resumen_pagos_credito.py](c:\.vscode\Project_aprobado\gestion_creditos\test_resumen_pagos_credito.py)
- otros `test_*.py` de ajustes específicos

No es la estructura ideal. Sigue pendiente moverlos a `gestion_creditos/tests/`.

## Checks y tests recomendados

### Check

```powershell
venv\Scripts\python.exe manage.py check
```

### Suite dirigida hoy confiable

```powershell
venv\Scripts\python.exe manage.py test gestion_creditos.test_credit_lifecycle gestion_creditos.test_name_normalization gestion_creditos.test_pagos_offline gestion_creditos.tests_marketplace_flow --verbosity 1
```

## Comandos operativos relevantes

- [management/commands/saldar_credito_formalmente.py](c:\.vscode\Project_aprobado\gestion_creditos\management\commands\saldar_credito_formalmente.py)
- [management/commands/normalizar_nombres_mayuscula.py](c:\.vscode\Project_aprobado\gestion_creditos\management\commands\normalizar_nombres_mayuscula.py)
- comandos QA ya existentes:
  - `preparar_pagaduria_qa`
  - `preparar_prueba_adelanto_nomina`

## Estado recomendado para considerar esta app “cerrada”

Antes de dar por cerrada la refactorización de `gestion_creditos`, falta:

1. extraer lógica real de `views.py`
2. reducir `credit_services.py`
3. mover tests a paquete por dominio
4. limpiar tareas legacy
5. consolidar contratos internos de servicio
