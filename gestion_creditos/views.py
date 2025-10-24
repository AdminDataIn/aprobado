from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
import uuid
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.conf import settings
from .models import Credito, CreditoLibranza, CreditoEmprendimiento, Empresa, HistorialPago, HistorialEstado, CuentaAhorro, MovimientoAhorro
from .forms import CreditoLibranzaForm, CreditoEmprendimientoForm, AbonoManualAdminForm, ConsignacionOfflineForm
from . import services
from datetime import datetime, timedelta
from decimal import Decimal
import logging
import zipfile
import io
from django.db import transaction
from django.contrib import messages
from django.db.models import Q, Count, Sum, Case, When, DecimalField, F, Subquery, Value, CharField
from django.db.models.functions import Coalesce, TruncMonth, Concat
from django.utils import timezone
from django.core.paginator import Paginator
from usuarios.models import PerfilPagador
from django.contrib.admin.views.decorators import staff_member_required
from .decorators import pagador_required
from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

#! La función _registrar_cambio_estado fue eliminada.
#! La lógica ahora está centralizada en services.gestionar_cambio_estado_credito.

@csrf_exempt
def webhook_firma_documento(request, numero_credito):
    #* Esta es una vista temporal. La lógica real se implementará aquí.
    return JsonResponse({'status': 'ok'})

#? --------- VISTA DE CREDITO DE LIBRANZA ------------
@login_required
def solicitud_credito_libranza_view(request):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if request.method == 'POST':
        form = CreditoLibranzaForm(request.POST, request.FILES)
        if form.is_valid():
            credito_principal = Credito.objects.create(
                usuario=request.user, #! le PASAMOS EL USUARIO LOGUEADO (PENDIENTE CAMBIARLO POR EL NOMBRE DE LA PERSONA QUE REGISTRA LA SOLICITUD)
                linea=Credito.LineaCredito.LIBRANZA, #! LE PASAMOS LINEA DE CREDITO
                estado=Credito.EstadoCredito.EN_REVISION #! PONEMOS EL ESTADO INICIAL DE LA SOLICITUD (EN REVISION)
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
                puntaje_interno = services.obtener_puntaje_interno(datos_evaluacion)
                puntaje_motivacion = services.evaluar_motivacion_credito(form.cleaned_data.get('desc_cred_nec'))
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

@staff_member_required
def admin_dashboard_view(request):
    """
    Muestra el dashboard principal administrativo.

    Delega la recolección y procesamiento de todos los datos de contexto
    a la función `get_dashboard_context` en el módulo de servicios para
    mantener la vista limpia y centrada en la renderización.
    """
    context = services.get_dashboard_context()
    return render(request, 'gestion_creditos/admin_dashboard.html', context)

@staff_member_required
def admin_solicitudes_view(request):
    """Vista para gestionar solicitudes pendientes"""
    
    solicitudes_base = Credito.objects.exclude(estado__in=['ACTIVO', 'PAGADO'])
    solicitudes_filtradas = services.filtrar_creditos(request, solicitudes_base)
    
    solicitudes = solicitudes_filtradas.select_related(
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
        'estado_filter': request.GET.get('estado', ''),
        'linea_filter': request.GET.get('linea', ''),
        'search': request.GET.get('search', ''),
        'estados_choices': Credito.EstadoCredito.choices,
        'lineas_choices': Credito.LineaCredito.choices,
    }
    
    return render(request, 'gestion_creditos/admin_solicitudes.html', context)


@staff_member_required
def admin_creditos_activos_view(request):
    """Vista para gestionar créditos activos y pagados"""
    
    creditos_base = Credito.objects.filter(estado__in=['ACTIVO', 'PAGADO'])
    creditos_filtrados = services.filtrar_creditos(request, creditos_base)
    
    creditos = creditos_filtrados.select_related(
        'usuario', 'detalle_libranza', 'detalle_emprendimiento'
    ).order_by('-fecha_solicitud')
    
    paginator = Paginator(creditos, 20)
    page_number = request.GET.get('page')
    creditos_page = paginator.get_page(page_number)
    
    context = {
        'creditos': creditos_page,
        'linea_filter': request.GET.get('linea', ''),
        'estado_filter': request.GET.get('estado', ''),
        'search': request.GET.get('search', ''),
        'estados_choices': [choice for choice in Credito.EstadoCredito.choices if choice[0] in ['ACTIVO', 'PAGADO']],
        'lineas_choices': Credito.LineaCredito.choices,
    }
    
    return render(request, 'gestion_creditos/admin_creditos_activos.html', context)


@staff_member_required
def procesar_solicitud_view(request, credito_id):
    """Aprobar o rechazar una solicitud usando el servicio centralizado."""
    if request.method != 'POST':
        messages.error(request, "Método no permitido.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito_id)

    credito = get_object_or_404(Credito, id=credito_id, estado__in=['SOLICITUD', 'EN_REVISION'])
    action = request.POST.get('action')
    
    nuevo_estado = None
    if action == 'approve':
        nuevo_estado = Credito.EstadoCredito.APROBADO
        messages.success(request, f'Crédito {credito.numero_credito} aprobado exitosamente.')
    elif action == 'reject':
        nuevo_estado = Credito.EstadoCredito.RECHAZADO
        messages.warning(request, f'Crédito {credito.numero_credito} rechazado.')
    else:
        messages.error(request, "Acción no válida.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito_id)

    motivo = request.POST.get('observations', 'Decisión inicial de la solicitud.')
    
    try:
        services.gestionar_cambio_estado_credito(
            credito=credito,
            nuevo_estado=nuevo_estado,
            usuario_modificacion=request.user,
            motivo=motivo
        )
    except Exception as e:
        messages.error(request, f"Ocurrió un error inesperado: {e}")
        logger.error(f"Error al procesar solicitud del crédito {credito.id}: {e}", exc_info=True)

    return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito_id)


@staff_member_required
def detalle_credito_view(request, credito_id):
    """Ver detalles completos de un crédito"""
    
    credito = get_object_or_404(Credito.objects.select_related('detalle_libranza', 'detalle_emprendimiento'), id=credito_id)
    
    historial_pagos = HistorialPago.objects.filter(credito=credito, estado='EXITOSO').order_by('-fecha_pago')
    historial_estados = HistorialEstado.objects.filter(credito=credito).order_by('-fecha')

    monto_total_pagado = historial_pagos.aggregate(Sum('monto'))['monto__sum'] or 0

    #! Unificar el acceso a los detalles del crédito (NO SE ESTÁ USANDO)
    detalle_credito = credito.detalle

    #! Los cálculos ahora se manejan en el modelo o en servicios,
    #! la vista solo se encarga de mostrar la información.

    #? Nuevos cálculos para la vista de detalle
    cuotas_pagadas = historial_pagos.count()
    cuotas_restantes = (credito.plazo - cuotas_pagadas) if credito.plazo else 0

    context = {
        'credito': credito,
        'historial_pagos': historial_pagos,
        'historial_estados': historial_estados,
        'monto_total_pagado': monto_total_pagado,
        'puede_procesar': credito.estado in ['SOLICITUD', 'EN_REVISION'],
        'estados_choices': Credito.EstadoCredito.choices,
        'cuotas_pagadas': cuotas_pagadas,
        'cuotas_restantes': cuotas_restantes,
    }
    
    return render(request, 'gestion_creditos/admin_detalle_credito.html', context)



@staff_member_required
@require_POST
def cambiar_estado_credito_view(request, credito_id):
    """
    Cambia el estado de un crédito manualmente, usando el servicio centralizado.
    """
    credito = get_object_or_404(Credito, id=credito_id)
    nuevo_estado = request.POST.get('status')
    motivo = request.POST.get('motivo', '')
    comprobante_pago = request.FILES.get('comprobante_pago')
    estado_anterior = credito.estado

    #? --- Validaciones ---
    if estado_anterior == nuevo_estado:
        messages.info(request, "El estado seleccionado es el mismo que el actual. No se realizaron cambios.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)

    if not nuevo_estado or nuevo_estado not in dict(Credito.EstadoCredito.choices):
        messages.error(request, "Estado no válido.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)

    if not motivo:
        messages.error(request, "El motivo del cambio de estado es obligatorio.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)

    #? Validación específica para la transición a ACTIVO
    if estado_anterior == Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA and nuevo_estado == Credito.EstadoCredito.ACTIVO and not comprobante_pago:
        messages.error(request, "Debe adjuntar el comprobante de pago para marcar el crédito como Activo.")
        return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)

    #? --- Lógica de Negocio y Persistencia ---
    try:
        services.gestionar_cambio_estado_credito(
            credito=credito,
            nuevo_estado=nuevo_estado,
            motivo=motivo,
            comprobante=comprobante_pago,
            usuario_modificacion=request.user
        )
        messages.success(request, f'Estado del crédito cambiado a {credito.get_estado_display()}.')

    except Exception as e:
        messages.error(request, f"Ocurrió un error inesperado: {e}")
        logger.error(f"Error al cambiar estado del crédito {credito.id}: {e}", exc_info=True)

    return redirect('gestion_creditos:admin_detalle_credito', credito_id=credito.id)



@staff_member_required
@require_POST
def agregar_pago_manual_view(request, credito_id):
    credito = get_object_or_404(Credito, id=credito_id)
    monto = request.POST.get('monto')
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
                referencia_pago=f"MANUAL-{credito.id}-{timezone.now().strftime('%Y%m%d%H%M%S%f')}",
                estado=HistorialPago.EstadoPago.EXITOSO
            )

            detalle = credito.detalle

            if detalle:
                #? Actualizar saldo y estado usando el helper
                services.actualizar_saldo_tras_pago(credito, monto_decimal)
            
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
        detalle = credito.detalle
        
        document_map = {
            Credito.LineaCredito.LIBRANZA: [
                'cedula_frontal', 'cedula_trasera', 'certificado_laboral', 
                'desprendible_nomina', 'certificado_bancario'
            ],
            Credito.LineaCredito.EMPRENDIMIENTO: ['foto_negocio']
        }
        
        document_fields = document_map.get(credito.linea, [])

        if detalle:
            for field_name in document_fields:
                file_field = getattr(detalle, field_name, None)
                if file_field and hasattr(file_field, 'path'):
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
@pagador_required
def pagador_dashboard_view(request):
    """
    Dashboard para el usuario pagador de una empresa.
    Muestra todos los créditos de libranza de los empleados de su empresa, con filtros y ordenamiento.
    """
    empresa = request.empresa

    #? --- Filtros y Búsqueda ---
    search_query = request.GET.get('search', '')
    estado_filter = request.GET.get('estado', '')
    sort_by = request.GET.get('sort_by', '-detalle_libranza__valor_credito') # Ordenar por monto de crédito descendente por defecto

    #? Subconsulta para calcular el total pagado por crédito
    total_pagado_subquery = HistorialPago.objects.filter(
        credito_id=F('pk'),
        estado=HistorialPago.EstadoPago.EXITOSO
    ).values('credito_id').annotate(total=Sum('monto')).values('total')

    #? Filtrar créditos base
    creditos_empresa = Credito.objects.filter(
        linea=Credito.LineaCredito.LIBRANZA,
        detalle_libranza__empresa=empresa
    ).exclude(
        estado__in=[Credito.EstadoCredito.RECHAZADO, Credito.EstadoCredito.SOLICITUD]
    ).select_related('detalle_libranza', 'usuario').annotate(
        total_pagado=Coalesce(Subquery(total_pagado_subquery, output_field=DecimalField()), Value(Decimal(0)))
    )

    #? Aplicar filtros de búsqueda
    if search_query:
        creditos_empresa = creditos_empresa.filter(
            Q(detalle_libranza__nombre_completo__icontains=search_query) |
            Q(detalle_libranza__cedula__icontains=search_query)
        )

    if estado_filter:
        creditos_empresa = creditos_empresa.filter(estado=estado_filter)

    #? Aplicar ordenamiento
    valid_sort_fields = [
        'detalle_libranza__nombre_completo', '-detalle_libranza__nombre_completo',
        'detalle_libranza__cedula', '-detalle_libranza__cedula',
        'detalle_libranza__valor_credito', '-detalle_libranza__valor_credito',
        'saldo_pendiente', '-saldo_pendiente',
        'estado', '-estado'
    ]
    if sort_by in valid_sort_fields:
        creditos_empresa = creditos_empresa.order_by(sort_by)

    #? Obtener y limpiar errores de la sesión
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
@pagador_required
def pagador_detalle_credito_view(request, credito_id):
    """
    Muestra el detalle de un crédito específico para el pagador.
    """
    empresa = request.empresa

    credito = get_object_or_404(Credito, id=credito_id, linea=Credito.LineaCredito.LIBRANZA)

    #? Verificar que el crédito pertenece a la empresa del pagador
    if credito.detalle_libranza.empresa != empresa:
        messages.error(request, "No tiene permiso para ver este crédito.")
        return redirect('gestion_creditos:pagador_dashboard')

    historial_pagos = HistorialPago.objects.filter(credito=credito, estado=HistorialPago.EstadoPago.EXITOSO).order_by('-fecha_pago')
    total_pagado = historial_pagos.aggregate(total=Sum('monto'))['total'] or Decimal(0)
    
    #? Usar el saldo pendiente del modelo que ya se actualiza correctamente
    saldo_pendiente = credito.detalle_libranza.saldo_pendiente

    context = {
        'credito': credito,
        'historial_pagos': historial_pagos,
        'total_pagado': total_pagado,
        'saldo_pendiente': saldo_pendiente,
    }
    
    return render(request, 'gestion_creditos/pagador_detalle_credito.html', context)


@login_required
@require_POST
@pagador_required
def pagador_procesar_pagos_view(request):
    """
    Procesa un archivo CSV de pagos masivos para los créditos de una empresa.
    """
    empresa = request.empresa
    csv_file = request.FILES.get('csv_file')

    if not csv_file or not csv_file.name.endswith('.csv'):
        messages.error(request, "Por favor, suba un archivo CSV válido.")
        return redirect('gestion_creditos:pagador_dashboard')

    pagos_exitosos, errores = services.procesar_pagos_masivos_csv(csv_file, empresa)

    if pagos_exitosos > 0:
        messages.success(request, f"Se procesaron {pagos_exitosos} pagos exitosamente.")
    if errores:
        request.session['errores_pago_masivo'] = errores
        messages.warning(request, f"Se encontraron {len(errores)} errores. Revise los detalles.")

    return redirect('gestion_creditos:pagador_dashboard')

@login_required
@pagador_required
def iniciar_pago_view(request, credito_id):
    """Inicia el flujo de pago para una cuota de un crédito de libranza."""
    credito = get_object_or_404(Credito, id=credito_id, linea=Credito.LineaCredito.LIBRANZA)
    
    #? Asegurarse de que el pagador solo pueda pagar créditos de su empresa
    if credito.detalle_libranza.empresa != request.empresa:
        messages.error(request, "No tiene permisos para pagar este crédito.")
        return redirect('gestion_creditos:pagador_dashboard')

    valor_cuota = credito.detalle_libranza.valor_cuota
    if not valor_cuota or valor_cuota <= 0:
        messages.error(request, "El crédito no tiene un valor de cuota válido para pagar.")
        return redirect('gestion_creditos:pagador_dashboard')

    context = {
        'credito': credito,
        'valor_cuota': valor_cuota,
        'referencia_pago': f"ONLINE-{credito.id}-{timezone.now().strftime('%Y%m%d%H%M%S%f')}"
    }
    return render(request, 'gestion_creditos/simulacion_pago.html', context)


@login_required
@require_POST
def procesar_pago_callback_view(request):
    """Procesa el callback de la pasarela de pagos simulada."""
    status = request.POST.get('status')
    credito_id = request.POST.get('credito_id')
    monto = request.POST.get('monto')
    referencia = request.POST.get('referencia')

    credito = get_object_or_404(Credito, id=credito_id)

    if status == 'success':
        try:
            with transaction.atomic():
                monto_decimal = Decimal(monto)
                
                #! Crear el registro del pago
                HistorialPago.objects.create(
                    credito=credito,
                    monto=monto_decimal,
                    referencia_pago=referencia,
                    estado=HistorialPago.EstadoPago.EXITOSO
                )

                #! Actualizar saldo y estado usando el helper
                services.actualizar_saldo_tras_pago(credito, monto_decimal)
                
                messages.success(request, f"Pago de ${monto_decimal:,.2f} para el crédito #{credito.id} procesado exitosamente.")

        except Exception as e:
            messages.error(request, f"Ocurrió un error al procesar el pago: {e}")
    else:
        messages.error(request, f"El pago para el crédito #{credito.id} fue fallido o cancelado.")

    return redirect('gestion_creditos:pagador_dashboard')


#? ============================================================================
#? VISTAS DE BILLETERA DIGITAL - USUARIO
#? ============================================================================

@login_required
def billetera_digital_view(request):
    """
    Vista principal de la billetera digital del usuario.
    Muestra saldo, estadísticas, movimientos e impacto social.
    """
    context = services.get_billetera_context(request.user)
    return render(request, 'billetera/billetera_digital.html', context)


@login_required
@require_POST
def consignacion_offline_view(request):
    """
    Procesa una consignación offline (con comprobante).
    El movimiento queda en estado PENDIENTE hasta que el admin lo apruebe.
    """
    cuenta, created = CuentaAhorro.objects.get_or_create(
        usuario=request.user,
        defaults={
            'tipo_usuario': CuentaAhorro.TipoUsuario.NATURAL,
            'saldo_disponible': Decimal('0.00'),
            'saldo_objetivo': Decimal('1000000.00')
        }
    )
    
    form = ConsignacionOfflineForm(request.POST, request.FILES)
    
    if form.is_valid():
        with transaction.atomic():
            movimiento = form.save(commit=False)
            movimiento.cuenta = cuenta
            movimiento.tipo = MovimientoAhorro.TipoMovimiento.DEPOSITO_OFFLINE
            movimiento.estado = MovimientoAhorro.EstadoMovimiento.PENDIENTE
            movimiento.referencia = f"OFFLINE-{uuid.uuid4().hex[:12].upper()}"
            
            if not movimiento.descripcion:
                movimiento.descripcion = 'Consignación offline pendiente de aprobación'
            
            movimiento.save()
            
            messages.success(request, '¡Comprobante enviado! Tu consignación será revisada pronto.')
            
            return JsonResponse({
                'success': True,
                'mensaje': 'Consignación enviada exitosamente',
                'referencia': movimiento.referencia
            })
    else:
        return JsonResponse({
            'success': False,
            'errors': form.errors
        }, status=400)


#? ============================================================================
#? VISTAS ADMINISTRATIVAS - BILLETERA
#? ============================================================================

@staff_member_required
def admin_billetera_dashboard_view(request):
    """
    Dashboard administrativo para gestionar la billetera digital.
    Muestra estadísticas generales y consignaciones pendientes.
    """
    #* Estadísticas generales
    total_usuarios_ahorrando = CuentaAhorro.objects.filter(activa=True).count()
    
    monto_total_ahorrado = CuentaAhorro.objects.filter(activa=True).aggregate(
        total=Sum('saldo_disponible')
    )['total'] or Decimal('0.00')
    
    #* Consignaciones pendientes
    consignaciones_pendientes = MovimientoAhorro.objects.filter(
        estado=MovimientoAhorro.EstadoMovimiento.PENDIENTE,
        tipo=MovimientoAhorro.TipoMovimiento.DEPOSITO_OFFLINE
    ).select_related('cuenta__usuario').order_by('-fecha_creacion')
    
    #* Movimientos recientes (últimos 20)
    movimientos_recientes = MovimientoAhorro.objects.filter(
        estado__in=['APROBADO', 'PROCESADO', 'RECHAZADO']
    ).select_related('cuenta__usuario', 'procesado_por').order_by('-fecha_procesamiento')[:20]
    
    #* Formulario para cargar abonos manuales
    form_abono_manual = AbonoManualAdminForm()
    
    context = {
        'total_usuarios_ahorrando': total_usuarios_ahorrando,
        'monto_total_ahorrado': monto_total_ahorrado,
        'consignaciones_pendientes': consignaciones_pendientes,
        'movimientos_recientes': movimientos_recientes,
        'form_abono_manual': form_abono_manual,
    }
    
    return render(request, 'billetera/admin_billetera_dashboard.html', context)


@staff_member_required
@require_POST
def aprobar_consignacion_view(request, movimiento_id):
    """
    Aprueba una consignación pendiente usando el servicio centralizado.
    """
    nota_admin = request.POST.get('nota_admin', 'Consignación aprobada')
    try:
        movimiento = services.gestionar_consignacion_billetera(
            movimiento_id=movimiento_id,
            es_aprobado=True,
            usuario_admin=request.user,
            nota=nota_admin
        )
        messages.success(
            request, 
            f'Consignación de ${movimiento.monto:,.0f} aprobada para {movimiento.cuenta.usuario.get_full_name()}'
        )
        return JsonResponse({
            'success': True,
            'nuevo_saldo': float(movimiento.cuenta.saldo_disponible)
        })
    except Exception as e:
        logger.error(f"Error al aprobar consignación {movimiento_id}: {e}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@staff_member_required
@require_POST
def rechazar_consignacion_view(request, movimiento_id):
    """
    Rechaza una consignación pendiente usando el servicio centralizado.
    """
    motivo_rechazo = request.POST.get('motivo', 'Sin motivo especificado')
    try:
        movimiento = services.gestionar_consignacion_billetera(
            movimiento_id=movimiento_id,
            es_aprobado=False,
            usuario_admin=request.user,
            nota=f"Rechazado: {motivo_rechazo}"
        )
        messages.warning(
            request,
            f'Consignación de {movimiento.cuenta.usuario.get_full_name()} rechazada.'
        )
        return JsonResponse({'success': True})
    except Exception as e:
        logger.error(f"Error al rechazar consignación {movimiento_id}: {e}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@staff_member_required
@require_POST
def cargar_abono_manual_view(request):
    """
    Permite al admin cargar un abono manual a la cuenta de un usuario, usando un servicio.
    """
    form = AbonoManualAdminForm(request.POST, request.FILES)
    
    if form.is_valid():
        try:
            movimiento = services.crear_ajuste_manual_billetera(
                admin_user=request.user,
                user_email=form.cleaned_data['usuario_email'],
                monto=form.cleaned_data['monto'],
                nota=form.cleaned_data.get('nota', ''),
                comprobante=form.cleaned_data.get('comprobante')
            )
            messages.success(
                request,
                f'Abono de ${movimiento.monto:,.0f} cargado exitosamente a la cuenta de {movimiento.cuenta.usuario.get_full_name()}'
            )
        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            logger.error(f"Error al cargar abono manual: {e}")
            messages.error(request, f'Error al procesar el abono: {str(e)}')
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f'{field}: {error}')
    
    return redirect('gestion_creditos:admin_billetera_dashboard')