# Estado Pre-Deploy

## Listo para demo / despliegue
- Auth separado por flujo: libranza, emprendimiento, marketplace buyer/admin, pagador e inversionista.
- Adelanto de nomina con simulacion publica, elegibilidad y solicitud autenticada.
- Carga controlada de empleados por pagador y reconciliacion base de usuarios legacy.
- Marketplace con separacion entre empresas de convenio y empresas externas.
- Activacion de inversionista por token y establecimiento de contrasena.
- Ajuste seguro de fechas de pago por comando y desactivacion selectiva de automatizaciones de mora/recordatorios para Libranza.

## Pendiente o fase 2
- Integracion real de Mercado Pago para checkout productivo. El dominio y el flujo base ya estan listos.
- Validacion operativa final de correos SMTP en servidor para todos los flujos nuevos.
- Barrido final de wrappers residuales de templates compartidos si se decide eliminar compatibilidad hacia atras.

## Riesgos operativos conocidos
- Produccion tiene personalizaciones locales en `aprobado_web/settings.py` que no deben sobreescribirse sin revision.
- Las automatizaciones de Libranza dependen de variables de entorno y reinicio de `celery`/`celerybeat`.
