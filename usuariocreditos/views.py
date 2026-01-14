from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.urls import reverse
from gestion_creditos.models import Credito, HistorialPago, HistorialEstado, CuentaAhorro, MovimientoAhorro, ConfiguracionTasaInteres, CuotaAmortizacion
from django.utils import timezone
from django.db.models import Sum
from django.template.loader import get_template
from django.contrib.staticfiles import finders
from weasyprint import HTML
from decimal import Decimal
import json
import base64
import io
from pypdf import PdfReader, PdfWriter

def get_logo_base64():
    logo_path = finders.find('images/logo-dark.png')
    if not logo_path:
        return None
    try:
        with open(logo_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        return f"data:image/png;base64,{encoded_string}"
    except (IOError, FileNotFoundError):
        return None

@login_required
def dashboard_libranza_view(request, credito_id=None):
    """
    Dashboard EXCLUSIVO para créditos de LIBRANZA.
    Muestra SOLO los créditos de libranza del usuario, sin redirecciones.
    """
    # Filtrar SOLO créditos de libranza
    creditos_usuario = Credito.objects.filter(
        usuario=request.user,
        linea=Credito.LineaCredito.LIBRANZA
    ).select_related('detalle_libranza')

    if not creditos_usuario.exists():
        # Usuario sin créditos de libranza - mostrar página genérica
        return render(request, 'usuariocreditos/sin_creditos.html', {
            'nombre_asociado': request.user.get_full_name() or request.user.username,
            'es_empleado': True  # En libranza, asumimos que es empleado
        })

    if credito_id:
        credito_actual = get_object_or_404(creditos_usuario, id=credito_id, usuario=request.user)
    else:
        # Lógica mejorada para seleccionar el crédito por defecto
        credito_actual = (
            creditos_usuario.filter(estado=Credito.EstadoCredito.ACTIVO).first() or
            creditos_usuario.filter(estado=Credito.EstadoCredito.EN_MORA).first() or
            creditos_usuario.order_by('-fecha_solicitud').first()
        )

    # --- Inicialización de variables ---
    historial_pagos = None
    monto_total_pagado = Decimal(0)
    detalle = credito_actual.detalle  # Usar la propiedad del modelo
    cuotas_pagadas = 0
    cuotas_restantes = 0
    plan_pagos = []

    # --- Cálculos para créditos activos o finalizados ---
    if credito_actual.estado in [
        Credito.EstadoCredito.ACTIVO,
        Credito.EstadoCredito.PAGADO,
        Credito.EstadoCredito.EN_MORA,
        Credito.EstadoCredito.FIRMADO,
        Credito.EstadoCredito.APROBADO,
        Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA
    ]:
        historial_pagos = HistorialPago.objects.filter(
            credito=credito_actual,
            estado=HistorialPago.EstadoPago.EXITOSO
        ).order_by('-fecha_pago')

        monto_total_pagado = historial_pagos.aggregate(total=Sum('monto'))['total'] or Decimal(0)

        plan_pagos = credito_actual.tabla_amortizacion.all().order_by('numero_cuota')
        cuotas_pagadas = plan_pagos.filter(pagada=True).count()

        if credito_actual.plazo:
            cuotas_restantes = credito_actual.plazo - cuotas_pagadas

    # --- Usar propiedades del modelo para cálculos financieros ---
    capital_pagado_monto = credito_actual.capital_pagado
    porcentaje_capital_pagado = int(credito_actual.porcentaje_pagado)

    cuota_proxima = credito_actual.tabla_amortizacion.filter(pagada=False).order_by('numero_cuota').first()
    valor_cuota_pendiente = credito_actual.valor_cuota
    if cuota_proxima and cuota_proxima.monto_pagado:
        valor_cuota_pendiente = max(Decimal('0.00'), cuota_proxima.valor_cuota - cuota_proxima.monto_pagado)

    # --- Calcular días transcurridos desde la activación ---
    dias_transcurridos = 0
    fecha_activacion = HistorialEstado.objects.filter(
        credito=credito_actual,
        estado_nuevo=Credito.EstadoCredito.ACTIVO
    ).order_by('-fecha').first()

    if fecha_activacion:
        dias_transcurridos = (timezone.now() - fecha_activacion.fecha).days

    # --- Contexto para la plantilla ---
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
        'valor_cuota_pendiente': valor_cuota_pendiente,
        'porcentaje_capital_pagado': porcentaje_capital_pagado,
        'capital_pagado_monto': capital_pagado_monto,
        'plan_pagos': plan_pagos,
        'es_libranza': True,  # ⭐ Flag para identificar dashboard de Libranza
    }
    return render(request, 'usuariocreditos/dashboard_libranza.html', context)


@login_required
def dashboard_view(request, credito_id=None):
    """
    Dashboard EXCLUSIVO para créditos de EMPRENDIMIENTO.
    Muestra SOLO los créditos de emprendimiento del usuario, sin redirecciones.
    """
    # Filtrar SOLO créditos de emprendimiento
    creditos_usuario = Credito.objects.filter(
        usuario=request.user,
        linea=Credito.LineaCredito.EMPRENDIMIENTO
    ).select_related('detalle_emprendimiento')

    if not creditos_usuario.exists():
        # Usuario sin créditos de emprendimiento - mostrar página genérica
        return render(request, 'usuariocreditos/sin_creditos.html', {
            'nombre_asociado': request.user.get_full_name() or request.user.username,
            'es_empleado': False  # En emprendimiento nunca es empleado
        })

    if credito_id:
        credito_actual = get_object_or_404(creditos_usuario, id=credito_id, usuario=request.user)
    else:
        # Lógica mejorada para seleccionar el crédito por defecto
        credito_actual = (
            creditos_usuario.filter(estado=Credito.EstadoCredito.ACTIVO).first() or
            creditos_usuario.filter(estado=Credito.EstadoCredito.EN_MORA).first() or
            creditos_usuario.order_by('-fecha_solicitud').first()
        )

    # --- Inicialización de variables ---
    historial_pagos = None
    monto_total_pagado = Decimal(0)
    detalle = credito_actual.detalle  # Usar la propiedad del modelo
    cuotas_pagadas = 0
    cuotas_restantes = 0
    plan_pagos = []

    # --- Cálculos para créditos activos o finalizados ---
    if credito_actual.estado in [
        Credito.EstadoCredito.ACTIVO, 
        Credito.EstadoCredito.PAGADO, 
        Credito.EstadoCredito.EN_MORA, 
        Credito.EstadoCredito.FIRMADO,
        Credito.EstadoCredito.APROBADO,
        Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA
    ]:
        historial_pagos = HistorialPago.objects.filter(
            credito=credito_actual, 
            estado=HistorialPago.EstadoPago.EXITOSO
        ).order_by('-fecha_pago')
        
        monto_total_pagado = historial_pagos.aggregate(total=Sum('monto'))['total'] or Decimal(0)
        
        plan_pagos = credito_actual.tabla_amortizacion.all().order_by('numero_cuota')
        cuotas_pagadas = plan_pagos.filter(pagada=True).count()

        if credito_actual.plazo:
            cuotas_restantes = credito_actual.plazo - cuotas_pagadas

    # --- Usar propiedades del modelo para cálculos financieros ---
    capital_pagado_monto = credito_actual.capital_pagado
    porcentaje_capital_pagado = int(credito_actual.porcentaje_pagado)

    # --- Calcular días transcurridos desde la activación ---
    dias_transcurridos = 0
    fecha_activacion = HistorialEstado.objects.filter(
        credito=credito_actual, 
        estado_nuevo=Credito.EstadoCredito.ACTIVO
    ).order_by('-fecha').first()
    
    if fecha_activacion:
        dias_transcurridos = (timezone.now() - fecha_activacion.fecha).days

    # --- Contexto para la plantilla ---
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
        'plan_pagos': plan_pagos,
        'es_empleado': False,  # Dashboard de emprendimiento nunca es empleado
        'es_libranza': False,  # Dashboard de emprendimiento nunca es libranza
    }
    return render(request, 'usuariocreditos/dashboard_emprendimiento.html', context)


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
        
        crecimiento_porcentaje = 5.2
        dias_ahorrando = (timezone.now() - cuenta.fecha_apertura).days
        emprendimientos_financiados = cuenta.emprendimientos_financiados
        familias_beneficiadas = cuenta.familias_beneficiadas
        interes_estimado = 12345
        tasa_actual = ConfiguracionTasaInteres.objects.filter(activa=True).first()

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

    # Detectar si el usuario tiene créditos de Libranza
    from gestion_creditos.models import Credito
    tiene_libranza = Credito.objects.filter(
        usuario=request.user,
        linea=Credito.LineaCredito.LIBRANZA
    ).exists()

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
        'es_empleado': request.user.groups.filter(name='empleado').exists(),
        'es_libranza': tiene_libranza,  # ⭐ Nuevo contexto
    }
    return render(request, 'Billetera/billetera_digital.html', context)


@login_required
def descargar_extracto(request, credito_id):
    """
    Genera y descarga un PDF con el extracto de pagos de un crédito.
    El PDF está protegido con la cédula del cliente como contraseña.
    """
    credito = get_object_or_404(Credito, id=credito_id, usuario=request.user)

    detalle = credito.detalle

    historial_pagos = HistorialPago.objects.filter(credito=credito, estado=HistorialPago.EstadoPago.EXITOSO).order_by('fecha_pago')
    monto_total_pagado = historial_pagos.aggregate(total=Sum('monto'))['total'] or Decimal(0)

    progreso_credito = 0
    if credito.total_a_pagar and credito.total_a_pagar > 0:
        progreso_credito = round((monto_total_pagado / credito.total_a_pagar) * 100)

    context = {
        'credito': credito,
        'usuario': request.user,
        'detalle': detalle,
        'historial_pagos': historial_pagos,
        'monto_total_pagado': monto_total_pagado,
        'progreso_credito': progreso_credito,
        'fecha_generacion': timezone.now(),
        'logo_base64': get_logo_base64(),
    }

    # Obtener la cédula del cliente para encriptar el PDF
    cedula = None
    if credito.linea == Credito.LineaCredito.EMPRENDIMIENTO and hasattr(detalle, 'numero_cedula'):
        cedula = detalle.numero_cedula
    elif credito.linea == Credito.LineaCredito.LIBRANZA and hasattr(detalle, 'cedula'):
        cedula = detalle.cedula

    template = get_template('usuariocreditos/extracto_pdf.html')
    html_content = template.render(context)

    # Generar PDF con WeasyPrint
    pdf_bytes = HTML(string=html_content).write_pdf()

    # Encriptar el PDF si hay cédula disponible
    if cedula:
        # Crear reader y writer para pypdf
        pdf_reader = PdfReader(io.BytesIO(pdf_bytes))
        pdf_writer = PdfWriter()

        # Copiar todas las páginas
        for page in pdf_reader.pages:
            pdf_writer.add_page(page)

        # Encriptar con la cédula como contraseña
        pdf_writer.encrypt(user_password=str(cedula), owner_password=str(cedula))

        # Generar el PDF encriptado
        encrypted_pdf = io.BytesIO()
        pdf_writer.write(encrypted_pdf)
        pdf_file = encrypted_pdf.getvalue()
    else:
        pdf_file = pdf_bytes

    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="extracto_{credito.numero_credito}.pdf"'

    return response


@login_required
def descargar_plan_pagos_pdf(request, credito_id):
    """
    Genera y descarga un PDF con el plan de pagos detallado de un crédito,
    utilizando el modelo CuotaAmortizacion como fuente de verdad.
    El PDF está protegido con la cédula del cliente como contraseña.
    """
    credito = get_object_or_404(Credito, id=credito_id, usuario=request.user)

    detalle = credito.detalle
    plan_pagos = credito.tabla_amortizacion.all().order_by('numero_cuota')

    context = {
        'credito': credito,
        'usuario': request.user,
        'detalle': detalle,
        'plan_pagos': plan_pagos,
        'fecha_generacion': timezone.now(),
        'logo_base64': get_logo_base64(),
    }

    # Obtener la cédula del cliente para encriptar el PDF
    cedula = None
    if credito.linea == Credito.LineaCredito.EMPRENDIMIENTO and hasattr(detalle, 'numero_cedula'):
        cedula = detalle.numero_cedula
    elif credito.linea == Credito.LineaCredito.LIBRANZA and hasattr(detalle, 'cedula'):
        cedula = detalle.cedula

    template = get_template('usuariocreditos/plan_pagos_pdf.html')
    html_content = template.render(context)

    # Generar PDF con WeasyPrint
    pdf_bytes = HTML(string=html_content).write_pdf()

    # Encriptar el PDF si hay cédula disponible
    if cedula:
        # Crear reader y writer para pypdf
        pdf_reader = PdfReader(io.BytesIO(pdf_bytes))
        pdf_writer = PdfWriter()

        # Copiar todas las páginas
        for page in pdf_reader.pages:
            pdf_writer.add_page(page)

        # Encriptar con la cédula como contraseña
        pdf_writer.encrypt(user_password=str(cedula), owner_password=str(cedula))

        # Generar el PDF encriptado
        encrypted_pdf = io.BytesIO()
        pdf_writer.write(encrypted_pdf)
        pdf_file = encrypted_pdf.getvalue()
    else:
        pdf_file = pdf_bytes

    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="plan_de_pagos_{credito.numero_credito}.pdf"'

    return response
