import json

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from contractors.models import AutorizacionConsultaDatacreditoPrestador, PredecisionPrestadorAudit
from contractors.services.autorizacion_datacredito import (
    ErrorAutorizacionDatacredito,
    obtener_autorizacion_datacredito_vigente,
    registrar_autorizacion_datacredito_prestador,
)
from contractors.services.datacredito_evaluacion import (
    MODO_CONSULTAR_SI_NO_EXISTE,
    MODO_FORZAR_CONSULTA,
    MODO_REUTILIZAR_SNAPSHOT,
)
from contractors.services.originacion import (
    ErrorOriginacionPrestador,
    originar_credito_prestador_desde_auditoria,
    puede_originar_credito_prestador,
)
from contractors.services.notificacion_pagador import (
    ErrorNovedadPagadorPrestador,
    notificar_pagador_credito_prestador_en_revision,
    obtener_novedad_pagador_prestador,
    puede_notificar_pagador_prestador,
)
from contractors.services.timeline import listar_timeline_por_solicitud
from integrations.datacredito.auth import SERVICIO_DECISOR, SERVICIO_HISTORIAL
from integrations.datacredito.snapshots import buscar_snapshot_datacredito_vigente
from integrations.datacredito.settings import obtener_configuracion_datacredito


DECISIONES_BANDEJA_RIESGO = (
    'PREAPROBADO_READ_ONLY',
    'REQUIERE_REVISION_MANUAL',
    'BLOQUEADO_READ_ONLY',
    'INCOMPLETO',
)


def _empresa_solicitud(solicitud):
    informacion = getattr(solicitud, 'informacion_laboral', None)
    if not informacion:
        return ''
    if informacion.empresa_id:
        return informacion.empresa.nombre
    return informacion.empresa_contratante_nombre


def _fila_auditoria(auditoria):
    solicitud = auditoria.solicitud
    return {
        'auditoria': auditoria,
        'solicitud': solicitud,
        'documento': solicitud.document_number,
        'solicitante': f'{solicitud.first_name} {solicitud.last_name}'.strip(),
        'empresa': _empresa_solicitud(solicitud),
    }


def _json_legible(valor):
    if valor in (None, '', [], {}):
        return ''
    return json.dumps(valor, ensure_ascii=False, indent=2, default=str)


def _secciones_snapshot(auditoria):
    snapshot = auditoria.resultado_sanitizado or {}
    secciones = [
        ('Documental', snapshot.get('documental') or snapshot.get('documental_resultado')),
        ('Capacidad', snapshot.get('capacidad_contractual') or snapshot.get('capacidad_resultado')),
        ('Score', snapshot.get('score_resultado')),
        ('DataCredito', snapshot.get('datacredito_resultado')),
        ('Segundo credito', snapshot.get('segundo_credito_resultado')),
        ('Recogida cartera', snapshot.get('recogida_cartera_resultado')),
        ('Bloqueos', auditoria.bloqueos),
        ('Advertencias', auditoria.advertencias),
        ('Razones', auditoria.razones),
    ]
    return [
        {'titulo': titulo, 'contenido': _json_legible(contenido)}
        for titulo, contenido in secciones
    ]


@staff_member_required
@permission_required('contractors.can_view_contractor_risk_queue', raise_exception=True)
def bandeja_riesgo_prestadores_view(request):
    decision = request.GET.get('decision', '').strip()
    auditorias = (
        PredecisionPrestadorAudit.objects
        .select_related(
            'solicitud',
            'usuario',
            'solicitud__informacion_laboral',
            'solicitud__informacion_laboral__empresa',
        )
        .order_by('-created_at', '-id')
    )
    if decision in DECISIONES_BANDEJA_RIESGO:
        auditorias = auditorias.filter(decision=decision)
    else:
        decision = ''

    paginador = Paginator(auditorias, 20)
    pagina = paginador.get_page(request.GET.get('page'))
    filas = [_fila_auditoria(auditoria) for auditoria in pagina.object_list]

    return render(
        request,
        'contractors/bandeja_riesgo_prestadores.html',
        {
            'decision_actual': decision,
            'decisiones': DECISIONES_BANDEJA_RIESGO,
            'page_obj': pagina,
            'filas': filas,
        },
    )


@staff_member_required
@permission_required('contractors.can_view_contractor_risk_queue', raise_exception=True)
def detalle_riesgo_prestador_view(request, audit_id):
    auditoria = get_object_or_404(
        PredecisionPrestadorAudit.objects.select_related(
            'solicitud',
            'usuario',
            'solicitud__informacion_laboral',
            'solicitud__informacion_laboral__empresa',
        ),
        pk=audit_id,
    )
    if request.method == 'POST':
        accion = request.POST.get('accion')
        try:
            if accion == 'notificar_pagador':
                resultado = notificar_pagador_credito_prestador_en_revision(
                    auditoria.solicitud.credito,
                    auditoria.solicitud,
                    request=request,
                )
                if resultado.notificaciones_creadas:
                    messages.success(
                        request,
                        f'Novedad al pagador registrada para {resultado.notificaciones_creadas} destinatarios.',
                    )
                else:
                    messages.warning(request, 'Novedad registrada sin destinatarios pagador activos.')
            else:
                resultado = originar_credito_prestador_desde_auditoria(
                    auditoria,
                    request=request,
                )
                messages.success(
                    request,
                    f'Credito {resultado.credito.numero_credito} originado en revision.',
                )
        except PermissionDenied:
            raise
        except (ErrorOriginacionPrestador, ErrorNovedadPagadorPrestador) as exc:
            messages.error(request, f'No fue posible completar la accion: {exc}')
        return redirect('gestion:prestadores_riesgo_detalle', audit_id=auditoria.id)

    novedad_pagador = obtener_novedad_pagador_prestador(auditoria.solicitud.credito)
    timeline_operativo = listar_timeline_por_solicitud(auditoria.solicitud)[:20]

    return render(
        request,
        'contractors/detalle_riesgo_prestador.html',
        {
            'auditoria': auditoria,
            'solicitud': auditoria.solicitud,
            'empresa': _empresa_solicitud(auditoria.solicitud),
            'secciones_snapshot': _secciones_snapshot(auditoria),
            'puede_originar': (
                puede_originar_credito_prestador(auditoria)
                and request.user.has_perm('contractors.can_originate_contractor_credit')
            ),
            'puede_notificar_pagador': (
                puede_notificar_pagador_prestador(auditoria)
                and request.user.has_perm('contractors.can_notify_contractor_payer')
            ),
            'puede_evaluar_datacredito': request.user.has_perm('contractors.can_run_contractor_datacredito_evaluation'),
            'puede_forzar_datacredito': request.user.has_perm('contractors.can_force_contractor_datacredito_refresh'),
            'novedad_pagador': novedad_pagador,
            'timeline_operativo': timeline_operativo,
        },
    )


@staff_member_required
@permission_required('contractors.can_run_contractor_datacredito_evaluation', raise_exception=True)
def evaluar_datacredito_prestador_view(request, audit_id):
    auditoria = get_object_or_404(
        PredecisionPrestadorAudit.objects.select_related(
            'solicitud',
            'solicitud__informacion_laboral',
            'solicitud__informacion_laboral__empresa',
        ),
        pk=audit_id,
    )
    solicitud = auditoria.solicitud
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'registrar_autorizacion_uat':
            if not request.user.has_perm('contractors.can_register_uat_datacredito_authorization'):
                raise PermissionDenied
            try:
                registrar_autorizacion_datacredito_prestador(
                    solicitud=solicitud,
                    usuario=request.user,
                    source=AutorizacionConsultaDatacreditoPrestador.Fuente.STAFF_UAT,
                    request=request,
                    justificacion=(request.POST.get('justificacion_autorizacion') or '').strip(),
                )
                messages.success(request, 'Autorizacion DataCredito UAT registrada.')
            except PermissionDenied:
                raise
            except (ErrorAutorizacionDatacredito, ValidationError) as exc:
                messages.error(request, f'No fue posible registrar la autorizacion UAT: {_mensaje_error(exc)}')
            return redirect('gestion:prestadores_riesgo_datacredito', audit_id=auditoria.id)

        modo = request.POST.get('modo_datacredito') or MODO_REUTILIZAR_SNAPSHOT
        justificacion = (request.POST.get('justificacion') or '').strip()
        estado_autorizacion = obtener_autorizacion_datacredito_vigente(solicitud)
        if modo == MODO_FORZAR_CONSULTA:
            if not request.user.has_perm('contractors.can_force_contractor_datacredito_refresh'):
                raise PermissionDenied
            if not justificacion:
                messages.error(request, 'La consulta forzada requiere justificacion.')
                return redirect('gestion:prestadores_riesgo_datacredito', audit_id=auditoria.id)
            if not estado_autorizacion.vigente:
                messages.error(request, 'No existe autorizacion DataCredito vigente para forzar una consulta.')
                return redirect('gestion:prestadores_riesgo_datacredito', audit_id=auditoria.id)
        elif modo not in {MODO_REUTILIZAR_SNAPSHOT, MODO_CONSULTAR_SI_NO_EXISTE}:
            modo = MODO_REUTILIZAR_SNAPSHOT
        elif modo == MODO_CONSULTAR_SI_NO_EXISTE and not estado_autorizacion.vigente:
            messages.error(request, 'No existe autorizacion DataCredito vigente para consultar servicios faltantes.')
            return redirect('gestion:prestadores_riesgo_datacredito', audit_id=auditoria.id)

        from contractors.services.evaluacion_formal import evaluar_formalmente_solicitud_prestador

        evaluacion = evaluar_formalmente_solicitud_prestador(
            solicitud,
            usuario=request.user,
            request=request,
            modo_datacredito=modo,
            justificacion=justificacion,
        )
        messages.success(request, 'Evaluacion formal DataCredito registrada.')
        return redirect('gestion:prestadores_riesgo_detalle', audit_id=evaluacion.auditoria.id)

    snapshot_decisor = _snapshot_vigente_solicitud(solicitud, SERVICIO_DECISOR)
    snapshot_historial = _snapshot_vigente_solicitud(solicitud, SERVICIO_HISTORIAL)
    estado_autorizacion = obtener_autorizacion_datacredito_vigente(solicitud)
    configuracion_datacredito = obtener_configuracion_datacredito()
    return render(
        request,
        'contractors/evaluar_datacredito_prestador.html',
        {
            'auditoria': auditoria,
            'solicitud': solicitud,
            'empresa': _empresa_solicitud(solicitud),
            'documento_enmascarado': _enmascarar_documento(solicitud.document_number),
            'snapshot_decisor': snapshot_decisor,
            'snapshot_historial': snapshot_historial,
            'modo_reutilizar': MODO_REUTILIZAR_SNAPSHOT,
            'modo_consultar_si_no_existe': MODO_CONSULTAR_SI_NO_EXISTE,
            'modo_forzar': MODO_FORZAR_CONSULTA,
            'puede_forzar': request.user.has_perm('contractors.can_force_contractor_datacredito_refresh'),
            'estado_autorizacion': estado_autorizacion,
            'puede_registrar_autorizacion_uat': (
                request.user.has_perm('contractors.can_register_uat_datacredito_authorization')
                and configuracion_datacredito.environment != 'prod'
            ),
        },
    )


def _snapshot_vigente_solicitud(solicitud, servicio):
    return buscar_snapshot_datacredito_vigente(
        servicio=servicio,
        tipo_documento=solicitud.document_type,
        numero_documento=solicitud.document_number,
        apellido=solicitud.last_name,
    )


def _enmascarar_documento(documento):
    texto = ''.join(caracter for caracter in str(documento or '') if caracter.isalnum())
    if len(texto) <= 4:
        return '*' * len(texto)
    return f"{'*' * (len(texto) - 4)}{texto[-4:]}"


def _mensaje_error(exc):
    if hasattr(exc, 'messages'):
        return ' '.join(str(mensaje) for mensaje in exc.messages)
    return str(exc)
