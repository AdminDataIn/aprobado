from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.conf import settings
from .models import Credito, CreditoLibranza, CreditoEmprendimiento, Empresa, HistorialPago
from .forms import CreditoLibranzaForm, CreditoEmprendimientoForm
from configuraciones.models import ConfiguracionPeso
from openai import OpenAI
from datetime import datetime, timedelta
from decimal import Decimal
import logging
import csv
import io
from django.db import transaction
from django.contrib import messages
from django.db.models import Q, Count, Sum, Case, When, DecimalField, F, Subquery, Value
from django.db.models.functions import Coalesce
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

    saldo_libranza_cartera = 0
    creditos_libranza_cartera = Credito.objects.filter(linea='LIBRANZA', estado__in=['ACTIVO', 'EN_MORA'])
    for credito in creditos_libranza_cartera:
        pagos = HistorialPago.objects.filter(credito=credito, estado='EXITOSO').aggregate(total=Sum('monto'))['total'] or 0
        saldo_libranza_cartera += (credito.detalle_libranza.valor_credito - pagos)

    saldo_cartera_total = saldo_emprendimiento_cartera + saldo_libranza_cartera

    monto_emprendimiento_en_mora = Credito.objects.filter(
        linea='EMPRENDIMIENTO',
        estado='EN_MORA'
    ).aggregate(total=Sum('detalle_emprendimiento__saldo_pendiente'))['total'] or 0

    monto_libranza_en_mora = 0
    creditos_libranza_mora = Credito.objects.filter(linea='LIBRANZA', estado='EN_MORA')
    for credito in creditos_libranza_mora:
        pagos = HistorialPago.objects.filter(credito=credito, estado='EXITOSO').aggregate(total=Sum('monto'))['total'] or 0
        monto_libranza_en_mora += (credito.detalle_libranza.valor_credito - pagos)

    monto_total_en_mora = monto_emprendimiento_en_mora + monto_libranza_en_mora
    
    creditos_por_linea = Credito.objects.values('linea').annotate(
        count=Count('id')
    )
    
    creditos_por_estado = Credito.objects.values('estado').annotate(
        count=Count('id')
    )
    
    proximos_vencer = Credito.objects.filter(
        linea='EMPRENDIMIENTO',
        estado='ACTIVO',
        detalle_emprendimiento__fecha_proximo_pago__lte=timezone.now().date() + timedelta(days=7)
    ).count()
    
    context = {
        'total_creditos': total_creditos,
        'creditos_activos': creditos_activos,
        'creditos_en_mora': creditos_en_mora_count,
        'saldo_cartera_total': saldo_cartera_total,
        'monto_total_en_mora': monto_total_en_mora,
        'creditos_por_linea': creditos_por_linea,
        'creditos_por_estado': creditos_por_estado,
        'proximos_vencer': proximos_vencer,
    }
    
    return render(request, 'gestion_creditos/admin_dashboard.html', context)


@staff_member_required
def admin_solicitudes_view(request):
    """Vista para gestionar solicitudes pendientes"""
    
    estado_filter = request.GET.get('estado', '')
    linea_filter = request.GET.get('linea', '')
    search = request.GET.get('search', '')
    
    solicitudes = Credito.objects.filter(estado__in=['SOLICITUD', 'EN_REVISION'])
    
    if estado_filter:
        solicitudes = solicitudes.filter(estado=estado_filter)
    
    if linea_filter:
        solicitudes = solicitudes.filter(linea=linea_filter)
    
    if search:
        solicitudes = solicitudes.filter(
            Q(usuario__username__icontains=search) |
            Q(usuario__first_name__icontains=search) |
            Q(usuario__last_name__icontains=search) |
            Q(numero_credito__icontains=search)
        )
    
    solicitudes = solicitudes.select_related('usuario').annotate(
        monto_solicitado=Case(
            When(linea='EMPRENDIMIENTO', then=F('detalle_emprendimiento__valor_credito')),
            When(linea='LIBRANZA', then=F('detalle_libranza__valor_credito')),
            default=Decimal(0),
            output_field=DecimalField()
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
    """Vista para gestionar créditos activos"""
    
    linea_filter = request.GET.get('linea', '')
    estado_filter = request.GET.get('estado', '')
    search = request.GET.get('search', '')
    
    creditos = Credito.objects.exclude(estado__in=['SOLICITUD', 'EN_REVISION', 'RECHAZADO'])
    
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
            When(linea='LIBRANZA', then=F('detalle_libranza__valor_credito')),
            default=Decimal(0),
            output_field=DecimalField()
        ),
        saldo_pendiente=Case(
            When(linea='EMPRENDIMIENTO', then=F('detalle_emprendimiento__saldo_pendiente')),
            When(linea='LIBRANZA', then=F('detalle_libranza__valor_credito')),  # Calcular con pagos
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
        'estados_choices': [choice for choice in Credito.EstadoCredito.choices if choice[0] not in ['SOLICITUD', 'EN_REVISION']],
        'lineas_choices': Credito.LineaCredito.choices,
    }
    
    return render(request, 'gestion_creditos/admin_creditos_activos.html', context)


@staff_member_required
def procesar_solicitud_view(request, credito_id):
    """Aprobar o rechazar una solicitud"""
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    
    credito = get_object_or_404(Credito, id=credito_id, estado__in=['SOLICITUD', 'EN_REVISION'])
    accion = request.POST.get('accion')
    observaciones = request.POST.get('observaciones', '')
    
    if accion == 'aprobar':
        if credito.linea == 'EMPRENDIMIENTO':
            monto_aprobado = request.POST.get('monto_aprobado')
            valor_cuota = request.POST.get('valor_cuota')
            fecha_proximo_pago = request.POST.get('fecha_proximo_pago')
            
            if not all([monto_aprobado, valor_cuota, fecha_proximo_pago]):
                return JsonResponse({'error': 'Faltan datos para aprobar crédito de emprendimiento'}, status=400)
            
            detalle = credito.detalle_emprendimiento
            detalle.monto_aprobado = float(monto_aprobado)
            detalle.saldo_pendiente = float(monto_aprobado)
            detalle.valor_cuota = float(valor_cuota)
            detalle.fecha_proximo_pago = datetime.strptime(fecha_proximo_pago, '%Y-%m-%d').date()
            detalle.save()
        
        credito.estado = 'APROBADO'
        credito.save()
        
        messages.success(request, f'Crédito {credito.numero_credito} aprobado exitosamente. Se enviará notificación para firma.')
        
    elif accion == 'rechazar':
        credito.estado = 'RECHAZADO'
        credito.save()
        
        messages.success(request, f'Crédito {credito.numero_credito} rechazado.')
        
    else:
        return JsonResponse({'error': 'Acción no válida'}, status=400)
    
    return JsonResponse({'success': True})


@staff_member_required
def detalle_credito_view(request, credito_id):
    """Ver detalles completos de un crédito"""
    
    credito = get_object_or_404(Credito, id=credito_id)
    
    historial_pagos = HistorialPago.objects.filter(credito=credito).order_by('-fecha_pago')
    
    pagos_exitosos = historial_pagos.filter(estado='EXITOSO')
    monto_total_pagado = sum(p.monto for p in pagos_exitosos)
    
    context = {
        'credito': credito,
        'historial_pagos': historial_pagos,
        'monto_total_pagado': monto_total_pagado,
        'puede_procesar': credito.estado in ['SOLICITUD', 'EN_REVISION'],
        'estados_choices': Credito.EstadoCredito.choices,
    }
    
    return render(request, 'gestion_creditos/admin_detalle_credito.html', context)


@staff_member_required
def cambiar_estado_credito_view(request, credito_id):
    """Cambiar estado de un crédito manualmente"""
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    
    credito = get_object_or_404(Credito, id=credito_id)
    nuevo_estado = request.POST.get('estado')
    observaciones = request.POST.get('observaciones', '')
    
    if nuevo_estado not in dict(Credito.EstadoCredito.choices):
        return JsonResponse({'error': 'Estado no válido'}, status=400)
    
    estado_anterior = credito.estado
    credito.estado = nuevo_estado
    credito.save()
    
    messages.success(request, f'Estado del crédito {credito.numero_credito} cambiado de {estado_anterior} a {nuevo_estado}')
    
    return JsonResponse({'success': True})


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

        HistorialPago.objects.create(
            credito=credito,
            monto=monto_decimal,
            referencia_pago=referencia,
            estado=HistorialPago.EstadoPago.EXITOSO
        )

        if hasattr(credito, 'detalle_emprendimiento'):
            detalle = credito.detalle_emprendimiento
            if detalle.saldo_pendiente is not None:
                detalle.saldo_pendiente -= monto_decimal
                if detalle.saldo_pendiente <= 0:
                    detalle.saldo_pendiente = 0
                    credito.estado = Credito.EstadoCredito.PAGADO
                    credito.save()
                detalle.save()
        
        messages.success(request, f"Abono de ${monto_decimal:,.2f} registrado exitosamente.")

    except (ValueError, TypeError) as e:
        messages.error(request, f"Error en el monto: {e}")
    except Exception as e:
        messages.error(request, f"Ocurrió un error inesperado: {e}")

    return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)


#! ==============================================================================
#! VISTAS DEL DASHBOARD DEL PAGADOR
#! ==============================================================================
@login_required
def pagador_dashboard_view(request):
    """
    Dashboard para el usuario pagador de una empresa.
    Muestra todos los créditos de libranza de los empleados de su empresa.
    """
    try:
        perfil_pagador = request.user.perfil_pagador
        empresa = perfil_pagador.empresa
    except PerfilPagador.DoesNotExist:
        messages.error(request, "No tiene permisos para acceder a esta página.")
        return redirect('index')

    # Subconsulta para calcular el total pagado por crédito
    total_pagado_subquery = HistorialPago.objects.filter(
        credito_id=F('pk'),
        estado=HistorialPago.EstadoPago.EXITOSO
    ).values('credito_id').annotate(total=Sum('monto')).values('total')

    # Filtrar créditos y anotar el saldo pendiente
    creditos_empresa = Credito.objects.filter(
        linea=Credito.LineaCredito.LIBRANZA,
        detalle_libranza__empresa=empresa
    ).exclude(
        estado__in=[Credito.EstadoCredito.RECHAZADO, Credito.EstadoCredito.SOLICITUD]
    ).select_related('detalle_libranza', 'usuario').annotate(
        total_pagado=Coalesce(Subquery(total_pagado_subquery, output_field=DecimalField()), Value(Decimal(0))),
        saldo_pendiente=F('detalle_libranza__valor_credito') - F('total_pagado')
    )
    
    # Obtener y limpiar errores de la sesión
    errores_pago_masivo = request.session.pop('errores_pago_masivo', None)

    context = {
        'empresa': empresa,
        'creditos': creditos_empresa,
        'errores_pago_masivo': errores_pago_masivo,
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