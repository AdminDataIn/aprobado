# Hitos Cerrados

Historial compacto y cronológico de hitos cerrados. No usar este archivo como backlog ni como estado actual.

## 2026-04-10

- Se corrigió el flujo admin del marketplace y el reset de contraseña del panel admin.
- Se cerró el auth de pagador e inversionista por correo/contraseña, sin Google ni autogestión abierta.
- Se incorporó cierre formal de créditos a `PAGADO` sin residuos.
- Se añadió normalización de nombres a MAYÚSCULA en nuevos write paths relevantes.
- Se reforzó el dashboard admin con filtro por empresa, columna empresa y métricas más útiles.
- Se añadió selección directa de obligaciones en el dashboard del pagador como alternativa al archivo Excel.
- Se implementó la política nueva de notificaciones de cuotas pendientes para pagador y usuario.
- Se dejó base técnica para prorrateo de intereses, sin activarlo aún.
- Se estabilizó la suite dirigida de pruebas para auth, pagos offline, marketplace y lifecycle de crédito.

## 2026-03-18

- Se aceptó la decisión técnica de usar Mercado Pago para el marketplace MVP y WOMPI para el core crediticio existente.
- Se agregó el dominio inicial de marketplace para pedidos, pagos, direcciones y liquidaciones.
- Se habilitó el portal del inversionista con ledger base, posiciones, cashflows y snapshots.
- Se consolidó el acceso por correo y recuperación de contraseña para varios flujos de producto.

## 2026-03-17

- Se consolidó la arquitectura por subdominios para libranza, emprendimiento y marketplace.
- Se implementó el hub de documentación del crédito.
- Se incorporó activación segura de pagadores por token y recuperación de acceso.
- Se robusteció el webhook de ZapSign para evitar avances falsos del flujo.
- Se implementaron pagos masivos desde el dashboard del pagador.

## 2026-01-11

- Se completaron reportes operativos del pagador y ajustes visuales iniciales del dashboard.
- Se corrigieron varios flujos de aprobación y registro de estados.
