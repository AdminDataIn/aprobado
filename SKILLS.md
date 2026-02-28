# Skills

Este archivo centraliza el criterio operativo para subir cambios al proyecto sin improvisar en produccion.

## Flujo Base

1. Desarrollar y probar localmente.
2. Identificar el alcance del cambio.
3. Confirmar si requiere:
   - variables nuevas en `.env`
   - migraciones
   - `collectstatic`
   - reinicio de servicios
4. Hacer commit con mensaje claro.
5. Subir a repositorio.
6. Actualizar servidor solo desde `main`.
7. Validar flujo real despues del despliegue.

## Tipos De Cambio

### 1. Cambio Seguro

Cambio visual, textos, emails, templates, CSS, JS o validaciones simples que no alteran datos historicos.

Checklist:

- `python manage.py check`
- revisar templates afectados
- validar flujo manual local
- `git add -A`
- `git commit -m "tipo: descripcion"`
- `git push`

### 2. Cambio Con Riesgo De Negocio

Cambio que afecta tasas, estados, calculos, aprobaciones, desembolsos, pagos, pagares, Wompi o ZapSign.

Checklist:

- validar flujo completo local
- revisar si impacta creditos existentes
- definir si aplica solo a creditos nuevos o tambien historicos
- tomar backup logico si el cambio afecta produccion
- `python manage.py check`
- pruebas manuales de punta a punta
- desplegar solo cuando la regla este clara

### 3. Cambio Con Datos O Infraestructura

Cambio en base de datos, `.env`, nginx, gunicorn, webhooks, SMTP, dominios o subdominios.

Checklist:

- documentar variable o servicio nuevo
- validar compatibilidad con produccion
- confirmar si requiere restart
- no dejar valores quemados en codigo

## Estructura Recomendada De Ramas

- `main`: produccion
- `develop`: integracion
- `feature/*`: funcionalidades nuevas
- `hotfix/*`: correcciones urgentes

## Antes De Subir Al Servidor

- `git status`
- confirmar que `settings.py` no lleve cambios locales del servidor
- confirmar si hay archivos locales que no deben ir al repo
- confirmar variables nuevas de `.env`
- confirmar si hay migraciones pendientes

## Despliegue Servidor

Orden recomendado:

1. `git pull --ff-only`
2. activar entorno virtual
3. `python manage.py migrate`
4. `python manage.py collectstatic --noinput`
5. reiniciar gunicorn si aplica

## Variables Nuevas

Registrar aqui cada variable nueva usada en produccion:

- `CREDIT_INTERNAL_NOTIFICATION_EMAILS`
- `LIBRANZA_TASA_MENSUAL`
- `EMPRENDIMIENTO_TASA_MENSUAL`

## Notas Operativas

- No tocar produccion directamente si el cambio puede salir desde Git.
- Si un credito historico necesita conservar condiciones anteriores, fijar sus datos explicitamente en BD antes de desplegar la nueva regla.
- Si un cambio afecta calculos, no asumir: validar con un caso real.
