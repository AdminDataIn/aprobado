from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def webhook_firma_documento(request, numero_credito):
    # Esta es una vista temporal. La lógica real se implementará aquí.
    return JsonResponse({'status': 'ok'})


from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.conf import settings
from .models import Credito, CreditoLibranza, CreditoEmprendimiento, Empresa, HistorialPago, HistorialEstado
from .forms import CreditoLibranzaForm, CreditoEmprendimientoForm
from configuraciones.models import ConfiguracionPeso
from openai import OpenAI
from datetime import datetime, timedelta
from decimal import Decimal
import logging
import csv
import io
import json
import zipfile
from django.db import transaction
from django.contrib import messages
from django.db.models import Q, Count, Sum, Case, When, DecimalField, F, Subquery, Value, CharField
from django.db.models.functions import Coalesce, TruncMonth, Concat
from django.utils import timezone
from django.core.paginator import Paginator
from usuarios.models import PerfilPagador
from django.contrib.admin.views.decorators import staff_member_required

logger = logging.getLogger(__name__)

#? --------- VISTA DE CREDITO DE LIBRANZA ------------
@login_required
def solicitud_credito_libranza_view(request):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if request.method == 'POST':
        form = CreditoLibranzaForm(request.POST, request.FILES)
        if form.is_valid():
            credito_principal = Credito.objects.create(
                usuario=request.user, # le PASAMOS EL USUARIO LOGUEADO (PENDIENTE CAMBIARLO POR EL NOMBRE DE LA PERSONA QUE REGISTRA LA SOLICITUD)
                linea=Credito.LineaCredito.LIBRANZA, # LE PASAMOS LINEA DE CREDITO
                estado=Credito.EstadoCredito.EN_REVISION # PONEMOS EL ESTADO INICIAL DE LA SOLICITUD (EN REVISION)
            )
            credito_libranza_detalle = form.save(commit=False)
            credito_libranza_detalle.credito = credito_principal
            credito_libranza_detalle.save()
            if is_ajax:
                return JsonResponse({'success': True})
            return redirect('usuariocreditos:dashboard_view')
        else:
            if is_ajax:
                return JsonResponse({'success': False, 'errors': form.errors}, status=400)
    else:
        form = CreditoLibranzaForm()
    return render(request, 'gestion_creditos/solicitud_libranza.html', {'form': form})


@login_required
def solicitud_credito_emprendimiento_view(request):
    if request.method == 'POST':
        form = CreditoEmprendimientoForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                datos_evaluacion = {
                    'Tiempo_operando': form.cleaned_data.get('tiempo_operando'),
                    'Actividad_diaria': str(form.cleaned_data.get('dias_trabajados_sem')),
                    'Ubicacion': form.cleaned_data.get('ubicacion_negocio'),
                    'Ingresos': form.cleaned_data.get('ingresos_prom_mes'),
                    'Herramientas digitales': form.cleaned_data.get('tipo_cta_mno'),
                    'Ahorro tandas': form.cleaned_data.get('ahorro_tand_alc'),
                    'Dependientes': form.cleaned_data.get('depend_h'),
                    'Redes sociales': form.cleaned_data.get('redes_soc'),
                }
                puntaje_interno = obtener_estimacion(datos_evaluacion)
                puntaje_motivacion = evaluar_motivacion_credito(form.cleaned_data.get('desc_cred_nec'))
                puntaje_total = puntaje_interno + puntaje_motivacion

                credito_principal = Credito.objects.create(
                    usuario=request.user,
                    linea=Credito.LineaCredito.EMPRENDIMIENTO,
                    estado=Credito.EstadoCredito.EN_REVISION
                )
                
                detalle_emprendimiento = form.save(commit=False)
                detalle_emprendimiento.credito = credito_principal
                detalle_emprendimiento.puntaje = puntaje_total
                detalle_emprendimiento.save()

                return JsonResponse({'success': True, 'suma_estimaciones': puntaje_total})

            except Exception as e:
                logger.error(f"Error en solicitud_credito_emprendimiento_view: {e}")
                return JsonResponse({'success': False, 'error': str(e)}, status=500)
        else:
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)
    else:
        form = CreditoEmprendimientoForm()
    
    return render(request, 'gestion_creditos/solicitud_emprendimiento.html', {'form': form})


def evaluar_motivacion_credito(texto):
    if not texto or len(texto) < 10:
        return 3
    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        prompt = f"""Evalúa esta justificación para un crédito y asigna un puntaje del 1 al 5:
        - 1: Muy pobre
        - 2: Pobre
        - 3: Aceptable
        - 4: Bueno
        - 5: Excelente

        Justificación: "{texto}"

        Responde SOLO con el número del puntaje (1-5)."""
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Eres un analista financiero experto."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=2
        )
        respuesta = response.choices[0].message.content.strip()
        puntaje = int(respuesta) if respuesta.isdigit() else 3
        return max(1, min(5, puntaje))
    except Exception as e:
        logger.error(f"Error al evaluar con ChatGPT: {e}")
        return 0

def obtener_estimacion(parametros):
    suma_estimaciones = 0
    for parametro, nivel in parametros.items():
        if nivel:
            try:
                configuracion = ConfiguracionPeso.objects.get(parametro=parametro, nivel=nivel)
                suma_estimaciones += configuracion.estimacion
            except ConfiguracionPeso.DoesNotExist:
                logger.warning(f"No se encontró configuración para {parametro} con nivel {nivel}")
    return suma_estimaciones


@staff_member_required
def admin_dashboard_view(request):
    """Dashboard principal administrativo"""
    
    total_creditos = Credito.objects.count()
    creditos_activos = Credito.objects.filter(estado='ACTIVO').count()
    creditos_en_mora_count = Credito.objects.filter(estado='EN_MORA').count()

    saldo_emprendimiento_cartera = Credito.objects.filter(
        linea='EMPRENDIMIENTO',
        estado__in=['ACTIVO', 'EN_MORA']
    ).aggregate(total=Sum('detalle_emprendimiento__saldo_pendiente'))['total'] or 0

    saldo_libranza_cartera = Credito.objects.filter(
        linea='LIBRANZA',
        estado__in=['ACTIVO', 'EN_MORA']
    ).aggregate(total=Sum('detalle_libranza__saldo_pendiente'))['total'] or 0

    saldo_cartera_total = saldo_emprendimiento_cartera + saldo_libranza_cartera

    monto_emprendimiento_en_mora = Credito.objects.filter(
        linea='EMPRENDIMIENTO',
        estado='EN_MORA'
    ).aggregate(total=Sum('detalle_emprendimiento__saldo_pendiente'))['total'] or 0

    monto_libranza_en_mora = Credito.objects.filter(
        linea='LIBRANZA',
        estado='EN_MORA'
    ).aggregate(total=Sum('detalle_libranza__saldo_pendiente'))['total'] or 0

    monto_total_en_mora = monto_emprendimiento_en_mora + monto_libranza_en_mora
    
    creditos_por_linea = Credito.objects.values('linea').annotate(
        count=Count('id')
    )
    
    creditos_por_estado_list = list(Credito.objects.values('estado').annotate(count=Count('id')))
    for item in creditos_por_estado_list:
        if total_creditos > 0:
            item['porcentaje'] = (item['count'] / total_creditos) * 100
        else:
            item['porcentaje'] = 0

    proximos_vencer = Credito.objects.filter(
        linea='EMPRENDIMIENTO',
        estado='ACTIVO',
        detalle_emprendimiento__fecha_proximo_pago__lte=timezone.now().date() + timedelta(days=7)
    ).count()

    # Datos para gráficos
    six_months_ago = timezone.now() - timedelta(days=180)
    portfolio_evolution = Credito.objects.filter(
        fecha_solicitud__gte=six_months_ago
    ).annotate(
        month=TruncMonth('fecha_solicitud')
    ).values('month').annotate(
        total_value=Sum(
            Case(
                When(linea='EMPRENDIMIENTO', then='detalle_emprendimiento__monto_aprobado'),
                When(linea='LIBRANZA', then='detalle_libranza__monto_aprobado'),
                default=0,
                output_field=DecimalField()
            )
        )
    ).order_by('month')

    portfolio_labels = [item['month'].strftime('%b %Y') for item in portfolio_evolution]
    portfolio_data = [float(item['total_value']) for item in portfolio_evolution if item['total_value'] is not None]

    distribution_labels = [item['linea'] for item in creditos_por_linea]
    distribution_data = [item['count'] for item in creditos_por_linea]
    
    context = {
        'total_creditos': total_creditos,
        'creditos_activos': creditos_activos,
        'creditos_en_mora': creditos_en_mora_count,
        'saldo_cartera_total': saldo_cartera_total,
        'monto_total_en_mora': monto_total_en_mora,
        'creditos_por_linea': creditos_por_linea,
        'creditos_por_estado': creditos_por_estado_list,
        'proximos_vencer': proximos_vencer,
        'portfolio_labels': json.dumps(portfolio_labels),
        'portfolio_data': json.dumps(portfolio_data),
        'distribution_labels': json.dumps(distribution_labels),
        'distribution_data': json.dumps(distribution_data),
    }
    
    return render(request, 'gestion_creditos/admin_dashboard.html', context)


@staff_member_required
def admin_solicitudes_view(request):
    """Vista para gestionar solicitudes pendientes"""
    
    estado_filter = request.GET.get('estado', '')
    linea_filter = request.GET.get('linea', '')
    search = request.GET.get('search', '')
    
    solicitudes = Credito.objects.exclude(estado__in=['ACTIVO', 'PAGADO'])
    
    if estado_filter:
        solicitudes = solicitudes.filter(estado=estado_filter)
    
    if linea_filter:
        solicitudes = solicitudes.filter(linea=linea_filter)
    
    if search:
        solicitudes = solicitudes.filter(
            Q(usuario__username__icontains=search) |
            Q(usuario__first_name__icontains=search) |
            Q(usuario__last_name__icontains=search) |
            Q(numero_credito__icontains=search) |
            Q(detalle_libranza__nombres__icontains=search) |
            Q(detalle_libranza__apellidos__icontains=search) |
            Q(detalle_emprendimiento__nombre__icontains=search)
        )
    
    solicitudes = solicitudes.select_related(
        'usuario', 'detalle_libranza', 'detalle_emprendimiento'
    ).annotate(
        monto_solicitado=Case(
            When(linea='EMPRENDIMIENTO', then=F('detalle_emprendimiento__valor_credito')),
            When(linea='LIBRANZA', then=F('detalle_libranza__valor_credito')),
            default=Decimal(0),
            output_field=DecimalField()
        ),
        nombre_solicitante=Case(
            When(linea='LIBRANZA', then=Concat('detalle_libranza__nombres', Value(' '), 'detalle_libranza__apellidos')),
            When(linea='EMPRENDIMIENTO', then=F('detalle_emprendimiento__nombre')),
            default=Concat('usuario__first_name', Value(' '), 'usuario__last_name'),
            output_field=CharField()
        ),
        documento_solicitante=Case(
            When(linea='LIBRANZA', then=F('detalle_libranza__cedula')),
            When(linea='EMPRENDIMIENTO', then=F('detalle_emprendimiento__numero_cedula')),
            default=Value(''),
            output_field=CharField()
        )
    ).order_by('-fecha_solicitud')
    
    paginator = Paginator(solicitudes, 20)
    page_number = request.GET.get('page')
    solicitudes_page = paginator.get_page(page_number)
    
    context = {
        'solicitudes': solicitudes_page,
        'estado_filter': estado_filter,
        'linea_filter': linea_filter,
        'search': search,
        'estados_choices': Credito.EstadoCredito.choices,
        'lineas_choices': Credito.LineaCredito.choices,
    }
    
    return render(request, 'gestion_creditos/admin_solicitudes.html', context)


@staff_member_required
def admin_creditos_activos_view(request):
    """Vista para gestionar créditos activos y pagados"""
    
    linea_filter = request.GET.get('linea', '')
    estado_filter = request.GET.get('estado', '')
    search = request.GET.get('search', '')
    
    # Mostrar solo créditos en estado ACTIVO o PAGADO
    creditos = Credito.objects.filter(estado__in=['ACTIVO', 'PAGADO'])
    
    if linea_filter:
        creditos = creditos.filter(linea=linea_filter)
    
    if estado_filter:
        creditos = creditos.filter(estado=estado_filter)
    
    if search:
        creditos = creditos.filter(
            Q(usuario__username__icontains=search) |
            Q(usuario__first_name__icontains=search) |
            Q(usuario__last_name__icontains=search) |
            Q(numero_credito__icontains=search) |
            Q(detalle_libranza__cedula__icontains=search)
        )
    
    creditos = creditos.select_related('usuario').annotate(
        monto_aprobado=Case(
            When(linea='EMPRENDIMIENTO', then=F('detalle_emprendimiento__monto_aprobado')),
            When(linea='LIBRANZA', then=F('detalle_libranza__monto_aprobado')),
            default=Decimal(0),
            output_field=DecimalField()
        ),
        saldo_pendiente=Case(
            When(linea='EMPRENDIMIENTO', then=F('detalle_emprendimiento__saldo_pendiente')),
            When(linea='LIBRANZA', then=F('detalle_libranza__saldo_pendiente')), 
            default=Decimal(0),
            output_field=DecimalField()
        )
    ).order_by('-fecha_solicitud')
    
    paginator = Paginator(creditos, 20)
    page_number = request.GET.get('page')
    creditos_page = paginator.get_page(page_number)
    
    context = {
        'creditos': creditos_page,
        'linea_filter': linea_filter,
        'estado_filter': estado_filter,
        'search': search,
        'estados_choices': [choice for choice in Credito.EstadoCredito.choices if choice[0] in ['ACTIVO', 'PAGADO']],
        'lineas_choices': Credito.LineaCredito.choices,
    }
    
    return render(request, 'gestion_creditos/admin_creditos_activos.html', context)


@staff_member_required
def procesar_solicitud_view(request, credito_id):
    """Aprobar o rechazar una solicitud"""
    
    if request.method != 'POST':
        messages.error(request, "Método no permitido.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito_id)

    credito = get_object_or_404(Credito, id=credito_id, estado__in=['SOLICITUD', 'EN_REVISION'])
    action = request.POST.get('action')
    
    with transaction.atomic():
        estado_anterior = credito.estado
        if action == 'approve':
            credito.estado = Credito.EstadoCredito.APROBADO
            messages.success(request, f'Crédito {credito.numero_credito} aprobado exitosamente.')
        elif action == 'reject':
            credito.estado = Credito.EstadoCredito.RECHAZADO
            messages.warning(request, f'Crédito {credito.numero_credito} rechazado.')
        else:
            messages.error(request, "Acción no válida.")
            return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito_id)

        credito.save()
        HistorialEstado.objects.create(
            credito=credito,
            estado_anterior=estado_anterior,
            estado_nuevo=credito.estado,
            usuario_modificacion=request.user,
            motivo=request.POST.get('observations', 'Decisión inicial de la solicitud.')
        )

    return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito_id)


@staff_member_required
def detalle_credito_view(request, credito_id):
    """Ver detalles completos de un crédito"""
    
    credito = get_object_or_404(Credito.objects.select_related('detalle_libranza', 'detalle_emprendimiento'), id=credito_id)
    
    historial_pagos = HistorialPago.objects.filter(credito=credito).order_by('-fecha_pago')
    historial_estados = HistorialEstado.objects.filter(credito=credito).order_by('-fecha')

    monto_total_pagado = historial_pagos.filter(estado='EXITOSO').aggregate(Sum('monto'))['monto__sum'] or 0
    
    context = {
        'credito': credito,
        'historial_pagos': historial_pagos,
        'historial_estados': historial_estados,
        'monto_total_pagado': monto_total_pagado,
        'puede_procesar': credito.estado in ['SOLICITUD', 'EN_REVISION'],
        'estados_choices': Credito.EstadoCredito.choices,
    }
    
    return render(request, 'gestion_creditos/admin_detalle_credito.html', context)



@staff_member_required
@require_POST
def cambiar_estado_credito_view(request, credito_id):
    """Cambiar estado de un crédito manualmente, registrando el motivo y validando la transición."""
    credito = get_object_or_404(Credito, id=credito_id)
    nuevo_estado = request.POST.get('status')
    motivo = request.POST.get('motivo', '')
    comprobante_pago = request.FILES.get('comprobante_pago')
    estado_anterior = credito.estado

    # --- Lógica de automatización para desembolso ---
    # Si el crédito está pendiente de transferencia y se sube un comprobante,
    # se fuerza el estado a ACTIVO automáticamente.
    if estado_anterior == Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA and comprobante_pago:
        nuevo_estado = Credito.EstadoCredito.ACTIVO
        if not motivo:
            motivo = "Desembolso de crédito realizado por el administrador."

    if not nuevo_estado or nuevo_estado not in dict(Credito.EstadoCredito.choices):
        messages.error(request, "Estado no válido.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)

    # Reglas de transición de estados válidas (estado_anterior: [estados_nuevos_permitidos])
    transiciones_validas = {
        Credito.EstadoCredito.EN_REVISION: [Credito.EstadoCredito.APROBADO, Credito.EstadoCredito.RECHAZADO],
        Credito.EstadoCredito.SOLICITUD: [Credito.EstadoCredito.RECHAZADO],
        Credito.EstadoCredito.PENDIENTE_FIRMA: [Credito.EstadoCredito.FIRMADO], # Transición manual temporal
        Credito.EstadoCredito.FIRMADO: [Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA],
        Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA: [Credito.EstadoCredito.ACTIVO],
        Credito.EstadoCredito.ACTIVO: [Credito.EstadoCredito.EN_MORA, Credito.EstadoCredito.PAGADO],
        Credito.EstadoCredito.EN_MORA: [Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.PAGADO],
    }

    if estado_anterior in transiciones_validas and nuevo_estado not in transiciones_validas[estado_anterior]:
        estados_permitidos = ", ".join([dict(Credito.EstadoCredito.choices)[s] for s in transiciones_validas[estado_anterior]])
        messages.error(request, f'No se puede cambiar de "{credito.get_estado_display()}" a "{dict(Credito.EstadoCredito.choices)[nuevo_estado]}". Estados permitidos: {estados_permitidos}.')
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)
    
    # Si el estado anterior no está en las reglas, se asume que cualquier cambio es una acción administrativa especial (pero se puede restringir más si es necesario)
    if estado_anterior not in transiciones_validas and estado_anterior != nuevo_estado:
        pass # Permitir cambios no definidos explícitamente por ahora, o añadir una regla por defecto.

    if not motivo:
        messages.error(request, "El motivo del cambio de estado es obligatorio.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)

    if estado_anterior == Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA and nuevo_estado == Credito.EstadoCredito.ACTIVO and not comprobante_pago:
        messages.error(request, "Debe adjuntar el comprobante de pago para marcar el crédito como Activo.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)

    try:
        with transaction.atomic():
            credito.estado = nuevo_estado
            credito.save()

            HistorialEstado.objects.create(
                credito=credito,
                estado_anterior=estado_anterior,
                estado_nuevo=nuevo_estado,
                motivo=motivo,
                comprobante_pago=comprobante_pago,
                usuario_modificacion=request.user
            )
            messages.success(request, f'Estado del crédito cambiado a {credito.get_estado_display()}.')
    except Exception as e:
        messages.error(request, f"Ocurrió un error inesperado: {e}")

    return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)



@staff_member_required
@require_POST
def agregar_pago_manual_view(request, credito_id):
    credito = get_object_or_404(Credito, id=credito_id)
    monto = request.POST.get('monto')
    referencia = request.POST.get('referencia', 'Pago Manual')
    auth_key = request.POST.get('auth_key')

    if not monto or not auth_key:
        messages.error(request, "Monto y clave de autorización son requeridos.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)

    if auth_key != getattr(settings, 'MANUAL_PAYMENT_AUTH_KEY', None):
        messages.error(request, "Clave de autorización no válida.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)

    try:
        monto_decimal = Decimal(monto)
        if monto_decimal <= 0:
            raise ValueError("El monto debe ser positivo.")

        with transaction.atomic():
            HistorialPago.objects.create(
                credito=credito,
                monto=monto_decimal,
                referencia_pago=referencia,
                estado=HistorialPago.EstadoPago.EXITOSO
            )

            detalle = None
            if hasattr(credito, 'detalle_emprendimiento'):
                detalle = credito.detalle_emprendimiento
            elif hasattr(credito, 'detalle_libranza'):
                detalle = credito.detalle_libranza

            if detalle and detalle.saldo_pendiente is not None:
                detalle.saldo_pendiente -= monto_decimal
                if detalle.saldo_pendiente <= 0:
                    detalle.saldo_pendiente = 0
                    credito.estado = Credito.EstadoCredito.PAGADO
                    credito.save()
                    HistorialEstado.objects.create(
                        credito=credito,
                        estado_anterior=credito.estado,
                        estado_nuevo=Credito.EstadoCredito.PAGADO,
                        motivo="Crédito saldado automáticamente por pago.",
                        usuario_modificacion=request.user
                    )
                detalle.save()
            
            messages.success(request, f"Abono de ${monto_decimal:,.2f} registrado exitosamente.")

    except (ValueError, TypeError) as e:
        messages.error(request, f"Error en el monto: {e}")
    except Exception as e:
        messages.error(request, f"Ocurrió un error inesperado: {e}")

    return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)

@staff_member_required
def descargar_documentos_view(request, credito_id):
    credito = get_object_or_404(Credito, id=credito_id)
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, 'w') as zip_file:
        document_fields = []
        if credito.linea == Credito.LineaCredito.LIBRANZA:
            detalle = credito.detalle_libranza
            document_fields = [
                'cedula_frontal', 'cedula_trasera', 'certificado_laboral', 
                'desprendible_nomina', 'certificado_bancario'
            ]
        elif credito.linea == Credito.LineaCredito.EMPRENDIMIENTO:
            detalle = credito.detalle_emprendimiento
            document_fields = ['foto_negocio']

        for field_name in document_fields:
            if hasattr(detalle, field_name):
                file_field = getattr(detalle, field_name)
                if file_field:
                    try:
                        zip_file.write(file_field.path, file_field.name)
                    except FileNotFoundError:
                        logger.warning(f"Archivo no encontrado para el crédito {credito.id}: {file_field.path}")

    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="documentos_credito_{credito.id}.zip"'
    return response


#! ==============================================================================
#! VISTAS DEL DASHBOARD DEL PAGADOR
#! ==============================================================================
@login_required
def pagador_dashboard_view(request):
    """
    Dashboard para el usuario pagador de una empresa.
    Muestra todos los créditos de libranza de los empleados de su empresa, con filtros y ordenamiento.
    """
    try:
        perfil_pagador = request.user.perfil_pagador
        empresa = perfil_pagador.empresa
    except PerfilPagador.DoesNotExist:
        messages.error(request, "No tiene permisos para acceder a esta página.")
        return redirect('index')

    # --- Filtros y Búsqueda ---
    search_query = request.GET.get('search', '')
    estado_filter = request.GET.get('estado', '')
    sort_by = request.GET.get('sort_by', '-detalle_libranza__valor_credito') # Ordenar por monto de crédito descendente por defecto

    # Subconsulta para calcular el total pagado por crédito
    total_pagado_subquery = HistorialPago.objects.filter(
        credito_id=F('pk'),
        estado=HistorialPago.EstadoPago.EXITOSO
    ).values('credito_id').annotate(total=Sum('monto')).values('total')

    # Filtrar créditos base
    creditos_empresa = Credito.objects.filter(
        linea=Credito.LineaCredito.LIBRANZA,
        detalle_libranza__empresa=empresa
    ).exclude(
        estado__in=[Credito.EstadoCredito.RECHAZADO, Credito.EstadoCredito.SOLICITUD]
    ).select_related('detalle_libranza', 'usuario').annotate(
        total_pagado=Coalesce(Subquery(total_pagado_subquery, output_field=DecimalField()), Value(Decimal(0))),
        saldo_pendiente=F('detalle_libranza__valor_credito') - F('total_pagado')
    )

    # Aplicar filtros de búsqueda
    if search_query:
        creditos_empresa = creditos_empresa.filter(
            Q(detalle_libranza__nombre_completo__icontains=search_query) |
            Q(detalle_libranza__cedula__icontains=search_query)
        )

    if estado_filter:
        creditos_empresa = creditos_empresa.filter(estado=estado_filter)

    # Aplicar ordenamiento
    valid_sort_fields = [
        'detalle_libranza__nombre_completo', '-detalle_libranza__nombre_completo',
        'detalle_libranza__cedula', '-detalle_libranza__cedula',
        'detalle_libranza__valor_credito', '-detalle_libranza__valor_credito',
        'saldo_pendiente', '-saldo_pendiente',
        'estado', '-estado'
    ]
    if sort_by in valid_sort_fields:
        creditos_empresa = creditos_empresa.order_by(sort_by)

    # Obtener y limpiar errores de la sesión
    errores_pago_masivo = request.session.pop('errores_pago_masivo', None)

    context = {
        'empresa': empresa,
        'creditos': creditos_empresa,
        'errores_pago_masivo': errores_pago_masivo,
        'search_query': search_query,
        'estado_filter': estado_filter,
        'sort_by': sort_by,
        'estados_choices': [choice for choice in Credito.EstadoCredito.choices if choice[0] not in ['RECHAZADO', 'SOLICITUD']],
    }
    
    return render(request, 'gestion_creditos/pagador_dashboard.html', context)

@login_required
def pagador_detalle_credito_view(request, credito_id):
    """
    Muestra el detalle de un crédito específico para el pagador.
    """
    try:
        perfil_pagador = request.user.perfil_pagador
        empresa = perfil_pagador.empresa
    except PerfilPagador.DoesNotExist:
        messages.error(request, "No tiene permisos para acceder a esta página.")
        return redirect('index')

    credito = get_object_or_404(Credito, id=credito_id, linea=Credito.LineaCredito.LIBRANZA)

    # Verificar que el crédito pertenece a la empresa del pagador
    if credito.detalle_libranza.empresa != empresa:
        messages.error(request, "No tiene permiso para ver este crédito.")
        return redirect('gestion_creditos:pagador_dashboard')

    historial_pagos = HistorialPago.objects.filter(credito=credito, estado=HistorialPago.EstadoPago.EXITOSO).order_by('-fecha_pago')
    total_pagado = historial_pagos.aggregate(total=Sum('monto'))['total'] or Decimal(0)
    saldo_pendiente = credito.detalle_libranza.valor_credito - total_pagado

    context = {
        'credito': credito,
        'historial_pagos': historial_pagos,
        'total_pagado': total_pagado,
        'saldo_pendiente': saldo_pendiente,
    }
    
    return render(request, 'gestion_creditos/pagador_detalle_credito.html', context)


@login_required
@require_POST
def pagador_procesar_pagos_view(request):
    """
    Procesa un archivo CSV de pagos masivos para los créditos de una empresa.
    """
    try:
        perfil_pagador = request.user.perfil_pagador
        empresa = perfil_pagador.empresa
    except PerfilPagador.DoesNotExist:
        messages.error(request, "No tiene permisos para realizar esta acción.")
        return redirect('index')

    csv_file = request.FILES.get('csv_file')
    if not csv_file or not csv_file.name.endswith('.csv'):
        messages.error(request, "Por favor, suba un archivo CSV válido.")
        return redirect('gestion_creditos:pagador_dashboard')

    pagos_exitosos = 0
    errores = []
    
    try:
        # Usamos io.TextIOWrapper para decodificar el archivo en memoria
        decoded_file = io.TextIOWrapper(csv_file, encoding='utf-8')
        reader = csv.DictReader(decoded_file)

        with transaction.atomic():
            for i, row in enumerate(reader, start=2):
                cedula = row.get('cedula')
                monto_str = row.get('monto_a_pagar')

                if not cedula or not monto_str:
                    errores.append(f"Fila {i}: Faltan datos de cédula o monto.")
                    continue

                try:
                    monto_a_pagar = Decimal(monto_str)
                    if monto_a_pagar <= 0:
                        raise ValueError("El monto debe ser positivo.")
                except (ValueError, TypeError):
                    errores.append(f"Fila {i} (Cédula {cedula}): Monto '{monto_str}' no es un número válido.")
                    continue

                # Buscar el crédito activo para esa cédula en la empresa
                credito = Credito.objects.filter(
                    linea=Credito.LineaCredito.LIBRANZA,
                    detalle_libranza__empresa=empresa,
                    detalle_libranza__cedula=cedula,
                    estado=Credito.EstadoCredito.ACTIVO
                ).first()

                if not credito:
                    errores.append(f"Fila {i}: No se encontró un crédito activo para la cédula {cedula}.")
                    continue

                # Crear el registro de pago
                HistorialPago.objects.create(
                    credito=credito,
                    monto=monto_a_pagar,
                    referencia_pago=f"Pago masivo nómina {datetime.now().strftime('%Y-%m-%d')}",
                    estado=HistorialPago.EstadoPago.EXITOSO
                )

                # Actualizar el saldo del crédito (recalculando)
                total_pagado_actual = HistorialPago.objects.filter(
                    credito=credito,
                    estado=HistorialPago.EstadoPago.EXITOSO
                ).aggregate(total=Sum('monto'))['total'] or Decimal(0)

                saldo_actual = credito.detalle_libranza.valor_credito - total_pagado_actual

                if saldo_actual <= 0:
                    credito.estado = Credito.EstadoCredito.PAGADO
                    credito.save()
                
                pagos_exitosos += 1

    except Exception as e:
        messages.error(request, f"Ocurrió un error inesperado al procesar el archivo: {e}")
        return redirect('gestion_creditos:pagador_dashboard')

    # Enviar mensajes de resumen
    if pagos_exitosos > 0:
        messages.success(request, f"Se procesaron {pagos_exitosos} pagos exitosamente.")
    if errores:
        # Guardar errores en la sesión para mostrarlos
        request.session['errores_pago_masivo'] = errores
        messages.warning(request, f"Se encontraron {len(errores)} errores. Revise los detalles.")

    return redirect('gestion_creditos:pagador_dashboard')
