# Prestadores: operacion de evaluacion P0-C

Base: dc9671569685ef70803e5c167c60dd1a48fe066f. Instalar el codigo no crea usuarios,
jobs ni politicas y no instala ni habilita el reloj. No reactivar solicitudes anteriores.

## Actor y lectura

Desde la consola administrativa restringida del release, `manage.py operar_evaluacion_prestadores estado`
solo devuelve ID tecnico, perfil efectivo, flags y contadores. En la ventana autorizada,
con operador administrativo autenticado por el acceso restringido al host (el ID por si
solo no autentica), ejecutar:

    python manage.py operar_evaluacion_prestadores provisionar --perfil v1 --operador-id ID --motivo TICKET --aplicar

El usuario de servicio es staff, no superusuario, sin clave utilizable, grupos ni perfil
pagador. V1 solo permite evaluar; el cambio explicito a perfil v3 agrega decision interna.
No incluye publicar politicas, aprobar como pagador, subsanar ni resolver rechazos. El catalogo
real de exclusiones sigue vacio. Una cuenta homonima no administrada o con permisos distintos
no se reutiliza. Provision y retiro registran operador, perfil solicitado y motivo en Admin.
Configurar su ID en PRESTADORES_EVALUACION_ACTOR_ID de web y worker, sin incluir credenciales.
Antes de habilitar: cotejar el SHA y el ID efectivo de procesos Gunicorn/Celery, los dos flags
DataCredito y PRESTADORES_AUTO_EVALUACION_ENABLED contra el alcance autorizado; ejecutar
`estado` en cada contexto de servicio. No interpretar una shell con otro entorno como evidencia.
Web y worker deben leer el mismo almacenamiento privado: una evidencia de ingreso
inaccesible cambia la version de datos y hace obsoleto el job; no relajar esa validacion.
Con flags apagados, no verificar mediante el envio de una solicitud real.

## Reloj exclusivo y observacion

No arrancar Beat general: puede ejecutar entradas ajenas. Las unidades de deploy/systemd
publican unicamente contractors.tasks.publicar_evaluaciones_pendientes_task, cada minuto,
sin evaluacion directa. El mensaje expira en 55 s. Un tick perdido se recupera desde los
jobs persistidos; no se recorre la tabla de solicitudes para crear jobs.

Preparacion de host propuesta, no provisionada: instalar un export de los archivos Git del
mismo SHA en /opt/aprobado-tick/current y el entorno Python de ese release en
/opt/aprobado-tick/venv, ambos root, legibles pero no escribibles por el usuario dinamico.
No copiar .env, respaldos, archivos privados ni media. La cuenta del timer no requiere
acceso a /root ni claves de centrales. Entregar /etc/aprobado-prestadores/tick.env (root:0600)
por custodia, con DEBUG=false, USE_SQLITE=false, DATABASE_URL de lectura local,
REDIS_URL del broker local, ID y flags operativos coherentes con web/worker.
PRESTADORES_RECUPERACION_ENABLED es independiente y falso por defecto. No habilitar ante
un backend remoto, un broker distinto o una segunda programacion del mismo dispatcher.
Validar los permisos SELECT para usuarios/permisos y django_celery_beat; usar el acceso de
lectura de observabilidad para consultar tambien los contadores. No usar el usuario PostgreSQL
de escritura de Django en el reloj. La tarea consumida por el worker usa su entorno habitual.

Solo con autorizacion expresa de activacion, sin jobs anteriores inesperados, y gates
de fuentes y politica completos:

    systemd-analyze verify deploy/systemd/prestadores-evaluaciones.{service,timer}
    install -m 0644 deploy/systemd/prestadores-evaluaciones.{service,timer} /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now prestadores-evaluaciones.timer
    systemctl list-timers prestadores-evaluaciones.timer
    journalctl -u prestadores-evaluaciones.service --since=-5min

Verificar que el worker consume celery y registra ambas tareas, que llega el tick y que
el resultado concuerda con `estado`; la entrega al broker no acredita consumo. Alertar si
faltan dos ticks consecutivos, hay publicacion atrasada, lease vencido, ejecucion incierta,
suspension operativa o crece pendientes_sin_job sobre la linea base. Para incertidumbre:
conciliar reserva, snapshots y auditoria bajo permisos propios; no repetir la consulta ni
fabricar un resultado. ENCOLADA vencida sin claim admite reentrega; EN_EJECUCION vencida
pasa a revision, nunca a reconsulta automatica. No trasladar ni reencolar la #13.

Para detener: `systemctl disable --now prestadores-evaluaciones.timer`, impedir nuevas
reservas, drenar inventario/leases y conciliar antes de retirar flags de web y worker.
Deshabilitar la recuperacion. No borrar mensajes, snapshots ni jobs inciertos. Registrar:

    python manage.py operar_evaluacion_prestadores retirar --operador-id ID --motivo TICKET --aplicar

## Politica individual

Una publicacion autorizada usa un actor humano staff habilitado con
contractors.can_activate_contractor_score_policy, origen legacy activo/vigente, configuracion
financiera y bandas validas, fecha expresa actual o futura, motivo y exclusiones reales vacias.
Verificar origen y vigencia antes de la ventana. V1 sigue activa; V2 no se edita.

    python manage.py gestionar_politica_ingreso publicar --origen-id 1 --version-destino prestadores-score-prod-v3-decision --vigente-desde AAAA-MM-DD --descuento 25.00 --actor-id ID --motivo TICKET --dry-run

El dry-run no es una validacion integral. Solo autorizada la escritura: repetir con
--aplicar en lugar de --dry-run. Comprobar nueva politica inactiva, huella, auditoria y V1 activa.
Publicar no adopta ni reserva. Para el primer piloto: confirmar una solicitud NUEVA con reserva
automatica desactivada; comprobar consentimiento, titular, evidencia contractual, simulacion,
version financiera vigente, ausencia de jobs, evaluaciones, gates o reservas incompatibles.
No encender confirmacion general antes de elegir el caso: V1 podria reservarlo primero.
Coordinar la ventana sin otras confirmaciones; los flags generales no son un allowlist.
Si no se puede aislar esa ventana, no habilitar un piloto individual mediante flags globales.
Una vez acreditadas fuentes y autorizada la operacion, usar el perfil tecnico V3 y un
operador separado con permisos de publicacion y evaluacion:

    python manage.py gestionar_politica_ingreso adoptar --solicitud-id NUEVA --politica-id V3 --actor-id ID --motivo TICKET --reservar-al-adoptar --aplicar

Adopcion y reserva son atomicas. Se publica al commit. Repetir la adopcion no inicia otra
evaluacion ni recupera una adopcion anterior sin job. Sin el opt-in, la adopcion no reserva.
V1 favorable es predecision, no aprobacion. V3 favorable requiere fuentes oficiales validas,
ingreso documental vigente, oferta viable, expediente coherente y sin pendientes; no sustituye
al pagador ni firma u originacion. Sin evidencia suficiente: revision, no rechazo definitivo.

## Gate externo Experian

Antes de todo consumo real conservar acreditacion escrita de producto HDC/MiDecisor PN,
canal y ambiente productivos, credencial custodiada, finalidad y consentimiento vigente
del titular. Exigir semantica contractual HC14/TX05 por modulo, modelo, rango, vigencia
y valores especiales del score, fuente, periodicidad y unidades de cuotas y obligaciones.
HTTP aceptado o presencia de datos no valida un modulo fallido; HDC sin informacion no
equivale a deuda cero. Indeterminado, timeout o contradiccion: no aprobar, no forzar retry,
conservar auditoria sanitizada y conciliar. No modificar normalizadores para habilitar el piloto.
