from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from gestion_creditos.models import Credito, HistorialPago
from django.utils import timezone
from django.template.loader import render_to_string
from django.conf import settings
from django.db.models import Case, When, F, DecimalField
import os
import pathlib

@login_required
def dashboard_view(request, credito_id=None):
    creditos_usuario = Credito.objects.filter(usuario=request.user).select_related(
        'detalle_emprendimiento', 'detalle_libranza'
    ).annotate(
        monto_aprobado_display=Case(
            When(linea=Credito.LineaCredito.EMPRENDIMIENTO, then=F('detalle_emprendimiento__monto_aprobado')),
            When(linea=Credito.LineaCredito.LIBRANZA, then=F('detalle_libranza__valor_credito')),
            default=0.0,
            output_field=DecimalField()
        )
    )

    # Si no hay créditos ni solicitudes, mostrar una vista especial
    if not creditos_usuario.exists():
        return render(request, 'usuariocreditos/sin_creditos.html', {
            'nombre_asociado': request.user.get_full_name() or request.user.username
        })

    # Determinar el crédito a mostrar en detalle
    if credito_id:
        credito_actual = get_object_or_404(creditos_usuario, id=credito_id, usuario=request.user)
    else:
        # Prioridad: Activo > En Revisión > Otros. El primero que encuentre.
        credito_actual = creditos_usuario.filter(estado=Credito.EstadoCredito.ACTIVO).first() or \
                         creditos_usuario.filter(estado=Credito.EstadoCredito.EN_REVISION).first() or \
                         creditos_usuario.first()

    # Inicializar variables
    historial_pagos = None
    monto_total_pagado = 0
    monto_aprobado = 0
    valor_cuota = 0
    saldo_pendiente = 0
    fecha_proximo_pago = None
    detalle = None

    # Solo calcular detalles financieros para créditos que han sido aprobados
    if credito_actual.estado in [Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.PAGADO, Credito.EstadoCredito.EN_MORA, Credito.EstadoCredito.FIRMADO]:
        historial_pagos = HistorialPago.objects.filter(credito=credito_actual).order_by('-fecha_pago')
        pagos_exitosos = historial_pagos.filter(estado=HistorialPago.EstadoPago.EXITOSO)
        monto_total_pagado = sum(p.monto for p in pagos_exitosos)

        if credito_actual.linea == Credito.LineaCredito.EMPRENDIMIENTO:
            detalle = credito_actual.detalle_emprendimiento
            monto_aprobado = detalle.monto_aprobado or 0
            valor_cuota = detalle.valor_cuota or 0
            saldo_pendiente = detalle.saldo_pendiente or 0
            fecha_proximo_pago = detalle.fecha_proximo_pago
        elif credito_actual.linea == Credito.LineaCredito.LIBRANZA:
            detalle = credito_actual.detalle_libranza
            monto_aprobado = detalle.valor_credito or 0
            valor_cuota = (monto_aprobado / detalle.plazo) if detalle.plazo > 0 else 0
            saldo_pendiente = monto_aprobado - monto_total_pagado
            fecha_proximo_pago = None # No modelado para libranza

    porcentaje_pagado = round((monto_total_pagado / monto_aprobado) * 100) if monto_aprobado > 0 else 0

    context = {
        'nombre_asociado': request.user.get_full_name() or request.user.username,
        'creditos_usuario': creditos_usuario, # Todas las solicitudes y créditos
        'credito_actual': credito_actual,
        'detalle_credito': detalle,
        'tiene_multiples_creditos': creditos_usuario.count() > 1,
        'monto_aprobado': monto_aprobado,
        'cuota_pendiente': valor_cuota,
        'proximo_vencimiento': fecha_proximo_pago,
        'monto_pagado': monto_total_pagado,
        'monto_pendiente': saldo_pendiente,
        'porcentaje_pagado': porcentaje_pagado,
        'historial_pagos': historial_pagos,
    }
    return render(request, 'usuariocreditos/dashboard.html', context)

def billetera_digital(request):
    """
    Vista para la billetera digital del usuario.
    """
    return render(request, 'billetera/billetera_digital.html', {
        'nombre_asociado': request.user.get_full_name() or request.user.username
    })