"""Run via Django shell stdin. Read-only discovery; never selects a subject to bill."""
from contextlib import ExitStack
from datetime import timedelta
import json
import logging
from unittest.mock import patch

from django.db import connection, transaction
from django.utils import timezone
from contractors.models import ContractorApplication
from contractors.services.autorizacion_datacredito import obtener_autorizacion_datacredito_vigente
from contractors.services.ingreso_neto import obtener_ingreso_neto_vigente
from integrations.models import ConsultaDatacreditoSnapshot


def descubrir():
    def bloquear(*args, **kwargs):
        raise RuntimeError('operacion_externa_prohibida')

    nivel = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with ExitStack() as stack:
            for target in ('requests.sessions.Session.request', 'httpx.Client.send',
                           'httpx.AsyncClient.send', 'urllib.request.urlopen',
                           'socket.socket.connect', 'socket.socket.connect_ex',
                           'socket.socket.sendto', 'socket.create_connection',
                           'celery.app.task.Task.apply_async', 'celery.app.base.Celery.send_task',
                           'django.core.mail.message.EmailMessage.send'):
                stack.enter_context(patch(target, side_effect=bloquear))
            assert connection.vendor == 'postgresql'
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION READ ONLY')
                try:
                    desde = timezone.now() - timedelta(days=7)
                    anteriores = set(ContractorApplication.objects.filter(pk__in=[4, 9])
                                     .values_list('tipo_documento', 'numero_documento'))
                    solicitudes = list(ContractorApplication.objects.select_related('usuario')
                        .filter(created_at__gte=desde).exclude(pk__in=[3, 4, 9])
                        .order_by('-created_at', '-pk')[:11])
                    filas = []
                    for s in solicitudes[:10]:
                        auth = obtener_autorizacion_datacredito_vigente(s)
                        ingreso = obtener_ingreso_neto_vigente(s)
                        referencias = list(s.autorizaciones_datacredito.values_list('pk', flat=True))
                        snapshots = ConsultaDatacreditoSnapshot.objects.filter(
                            autorizacion_referencia__in=[str(pk) for pk in referencias])
                        filas.append({
                            'solicitud_id': s.pk, 'propietario_id': s.usuario_id,
                            'propietario_activo': s.usuario.is_active,
                            'propietario_no_pagador': not hasattr(s.usuario, 'perfil_pagador'),
                            'identidad_presente': bool(s.tipo_documento and s.numero_documento),
                            'identidad_distinta_uat_previas': (s.tipo_documento, s.numero_documento) not in anteriores,
                            'estado': s.estado, 'analisis': s.estado_analisis_contractual,
                            'simulacion_registrada': s.simulada_en is not None,
                            'documentos': s.documentos.count(),
                            'consentimiento_id': auth.pk if auth else None,
                            'consentimiento_del_propietario': bool(auth and auth.usuario_id == s.usuario_id),
                            'ingreso_id': ingreso.registro_id if ingreso else None,
                            'ingreso_version': ingreso.version if ingreso else None,
                            'ingreso_vigente': ingreso is not None,
                            'auditorias': list(s.auditorias_predecision.values('pk', 'resultado', 'estado_ejecucion')),
                            'jobs': list(s.evaluacion_jobs.values('pk', 'estado')),
                            'gates_internos': list(s.aprobaciones_internas.values('pk', 'estado')),
                            'gates_pagador': list(s.aprobaciones_pagador.values('pk', 'estado')),
                            'snapshots': [{'id': str(pk), 'servicio': servicio, 'estado': estado}
                                for pk, servicio, estado in snapshots.values_list('pk', 'servicio', 'estado')],
                        })
                    return {'resultado': 'REQUIERE_IDENTIFICACION_OPERATIVA', 'ventana_dias': 7,
                            'timezone': str(timezone.get_current_timezone()),
                            'limite': 10, 'hay_mas': len(solicitudes) > 10, 'candidatas': filas,
                            'http': 0, 'escrituras': 0}
                finally:
                    transaction.set_rollback(True)
    except Exception:
        return {'resultado': 'BLOCKED_DESCUBRIMIENTO', 'http': 0, 'escrituras': 0}
    finally:
        logging.disable(nivel)


if __name__ == '__main__':
    print(json.dumps(descubrir(), ensure_ascii=True))
