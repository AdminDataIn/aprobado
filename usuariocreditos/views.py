from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from gestion_creditos.models import Credito, HistorialPago, HistorialEstado, CuentaAhorro, MovimientoAhorro, ConfiguracionTasaInteres
from django.utils import timezone
from django.template.loader import render_to_string
from django.conf import settings
from django.db.models import Case, When, F, DecimalField
from django.db.models import Sum
from django.template import Context, Template
from django.template.loader import get_template
from django.contrib.staticfiles import finders
from weasyprint import HTML, CSS
from decimal import Decimal
import os
import pathlib
import json

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

    if not creditos_usuario.exists():
        return render(request, 'usuariocreditos/sin_creditos.html', {
            'nombre_asociado': request.user.get_full_name() or request.user.username
        })

    if credito_id:
        credito_actual = get_object_or_404(creditos_usuario, id=credito_id, usuario=request.user)
    else:
        credito_actual = creditos_usuario.filter(estado=Credito.EstadoCredito.ACTIVO).first() or \
                         creditos_usuario.filter(estado=Credito.EstadoCredito.EN_REVISION).first() or \
                         creditos_usuario.first()

    # Inicializar variables con valores por defecto
    historial_pagos = None
    monto_total_pagado = 0
    detalle = None
    cuotas_pagadas = 0
    cuotas_restantes = 0
    capital_pagado_monto = 0

    if credito_actual.estado in [Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.PAGADO, Credito.EstadoCredito.EN_MORA, Credito.EstadoCredito.FIRMADO]:
        historial_pagos = HistorialPago.objects.filter(credito=credito_actual, estado=HistorialPago.EstadoPago.EXITOSO).order_by('-fecha_pago')
        monto_total_pagado = historial_pagos.aggregate(total=Sum('monto'))['total'] or Decimal(0)
        cuotas_pagadas = historial_pagos.count()

        if credito_actual.linea == Credito.LineaCredito.EMPRENDIMIENTO:
            detalle = credito_actual.detalle_emprendimiento
        elif credito_actual.linea == Credito.LineaCredito.LIBRANZA:
            detalle = credito_actual.detalle_libranza

        if detalle and detalle.plazo:
            cuotas_restantes = detalle.plazo - cuotas_pagadas

    # Calcular días transcurridos desde la activación
    dias_transcurridos = 0
    fecha_activacion = HistorialEstado.objects.filter(
        credito=credito_actual, 
        estado_nuevo=Credito.EstadoCredito.ACTIVO
    ).order_by('-fecha').first()
    
    if fecha_activacion:
        dias_transcurridos = (timezone.now() - fecha_activacion.fecha).days
    elif credito_actual.fecha_actualizacion and credito_actual.estado == Credito.EstadoCredito.ACTIVO:
        dias_transcurridos = (timezone.now() - credito_actual.fecha_actualizacion).days

    # Calcular porcentaje de capital pagado
    porcentaje_capital_pagado = 0
    if detalle and detalle.monto_aprobado and detalle.monto_aprobado > 0 and detalle.capital_original_pendiente is not None:
        capital_pagado_monto = detalle.monto_aprobado - detalle.capital_original_pendiente
        porcentaje_capital_pagado = round((capital_pagado_monto / detalle.monto_aprobado) * 100)

    context = {
        'nombre_asociado': request.user.get_full_name() or request.user.username,
        'creditos_usuario': creditos_usuario,
        'credito_actual': credito_actual,
        'detalle_credito': detalle,
        'tiene_multiples_creditos': creditos_usuario.count() > 1,
        'monto_total_pagado': monto_total_pagado,
        'historial_pagos': historial_pagos,
        'dias_transcurridos': dias_transcurridos,
        'cuotas_pagadas': cuotas_pagadas,
        'cuotas_restantes': cuotas_restantes,
        'porcentaje_capital_pagado': porcentaje_capital_pagado,
        'capital_pagado_monto': capital_pagado_monto,
    }
    return render(request, 'usuariocreditos/dashboard.html', context)


def billetera_digital(request):
    """
    Vista para la billetera digital del usuario.
    """
    try:
        cuenta = CuentaAhorro.objects.get(usuario=request.user)
        movimientos_recientes = MovimientoAhorro.objects.filter(cuenta=cuenta, estado=MovimientoAhorro.EstadoMovimiento.APROBADO).order_by('-fecha_creacion')[:5]
        
        saldo_disponible = cuenta.saldo_disponible
        saldo_objetivo = cuenta.saldo_objetivo
        progreso_porcentaje = round((saldo_disponible / saldo_objetivo) * 100) if saldo_objetivo > 0 else 0
        
        # Dummy data for now, as logic is not specified
        crecimiento_porcentaje = 5.2
        dias_ahorrando = (timezone.now() - cuenta.fecha_apertura).days
        emprendimientos_financiados = cuenta.emprendimientos_financiados
        familias_beneficiadas = cuenta.familias_beneficiadas
        interes_estimado = 12345
        tasa_actual = ConfiguracionTasaInteres.objects.filter(activa=True).first()

        # Dummy chart data
        chart_data = {
            "labels": ["Ene", "Feb", "Mar", "Abr", "May", "Jun"],
            "data": [10000, 25000, 40000, 30000, 50000, 70000],
        }

    except CuentaAhorro.DoesNotExist:
        cuenta = None
        movimientos_recientes = []
        saldo_disponible = 0
        saldo_objetivo = 1000000
        progreso_porcentaje = 0
        crecimiento_porcentaje = 0
        dias_ahorrando = 0
        emprendimientos_financiados = 0
        familias_beneficiadas = 0
        interes_estimado = 0
        tasa_actual = None
        chart_data = {"labels": [], "data": []}

    context = {
        'nombre_asociado': request.user.get_full_name() or request.user.username,
        'cuenta': cuenta,
        'saldo_disponible': saldo_disponible,
        'saldo_objetivo': saldo_objetivo,
        'progreso_porcentaje': progreso_porcentaje,
        'crecimiento_porcentaje': crecimiento_porcentaje,
        'dias_ahorrando': dias_ahorrando,
        'emprendimientos_financiados': emprendimientos_financiados,
        'familias_beneficiadas': familias_beneficiadas,
        'interes_estimado': interes_estimado,
        'tasa_actual': tasa_actual,
        'movimientos_recientes': movimientos_recientes,
        'chart_data': json.dumps(chart_data),
    }
    return render(request, 'Billetera/billetera_digital.html', context)


@login_required
def descargar_extracto(request, credito_id):
    """
    Genera y descarga un extracto en PDF del crédito del usuario
    """
    credito = get_object_or_404(Credito, id=credito_id, usuario=request.user)
    
    # Obtener detalle según el tipo de crédito
    if credito.linea == Credito.LineaCredito.EMPRENDIMIENTO:
        detalle = credito.detalle_emprendimiento
    else:
        detalle = credito.detalle_libranza
    
    # Calcular datos
    historial_pagos = HistorialPago.objects.filter(
        credito=credito,
        estado=HistorialPago.EstadoPago.EXITOSO
    ).order_by('-fecha_pago')
    
    monto_pagado = historial_pagos.aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
    monto_pendiente = (detalle.saldo_pendiente or Decimal('0.00'))
    monto_total = (detalle.monto_aprobado or Decimal('0.00'))
    
    # Preparar datos para el template
    logo_path = finders.find('images/logo-dark.png')
    if not logo_path:
        logo_path = ''
    else:
        logo_path = 'file://' + logo_path
    
    context = {
        'credito': credito,
        'usuario': request.user,
        'pagos': historial_pagos,
        'monto_pagado': monto_pagado,
        'monto_pendiente': monto_pendiente,
        'monto_total': monto_total,
        'numero_extracto': f"EXT-{credito.id:06d}",
        'fecha_extracto': timezone.now(),
        'logo_path': logo_path,
    }
    
    # Renderizar template
    template = get_template('usuariocreditos/extracto_pdf.html')
    html_content = template.render(context)
    
    # Generar PDF
    pdf_file = HTML(string=html_content, base_url=request.build_absolute_uri('/')).write_pdf()
    
    # Crear respuesta
    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="extracto_credito_{credito.id}.pdf"'
    
    return response