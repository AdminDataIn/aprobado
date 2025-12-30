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
from django.db.models.functions import ExtractMonth, ExtractYear
from django.db.models import DateField
from decimal import Decimal
import decimal
import logging
import zipfile
import io
from django.db import transaction
from django.contrib import messages
from django.db.models import Q, Count, Sum, Case, When, DecimalField, F, Subquery, Value, CharField, Avg
from django.db.models.functions import Coalesce, TruncMonth, Concat
from django.utils import timezone
from django.core.paginator import Paginator
from usuarios.models import PerfilPagador
from django.contrib.admin.views.decorators import staff_member_required
from .decorators import pagador_required
from django.contrib.auth.models import User
from django.db.models import DurationField, ExpressionWrapper, Value

logger = logging.getLogger(__name__)

#! La función _registrar_cambio_estado fue eliminada.
#! La lógica ahora está centralizada en services.gestionar_cambio_estado_credito.

@csrf_exempt
@transaction.atomic
def webhook_firma_documento(request, numero_credito):
    """
    Webhook para simular la recepción de una firma de documento exitosa.
    """
    # Para una implementación real, aquí se validaría la autenticidad del webhook.
    
    try:
        credito = Credito.objects.get(numero_credito=numero_credito)
    except Credito.DoesNotExist:
        logger.warning(f"Webhook recibió una llamada para un crédito no existente: {numero_credito}")
        return JsonResponse({'status': 'error', 'message': 'Crédito no encontrado'}, status=404)

    # Validar que el crédito esté en el estado correcto
    if credito.estado != Credito.EstadoCredito.PENDIENTE_FIRMA:
        logger.warning(f"Webhook para crédito {numero_credito} en estado incorrecto: {credito.estado}")
        return JsonResponse({
            'status': 'error', 
            'message': f'El crédito no está en estado PENDIENTE_FIRMA (estado actual: {credito.estado})'
        }, status=409)

    try:
        # 1. Cambiar estado a FIRMADO
        services.gestionar_cambio_estado_credito(
            credito=credito,
            nuevo_estado=Credito.EstadoCredito.FIRMADO,
            motivo="Documento (pagaré) firmado exitosamente (simulado por webhook).",
            usuario_modificacion=None # Proceso automático
        )

        # 2. Iniciar el proceso de desembolso (cambia a PENDIENTE_TRANSFERENCIA)
        services.iniciar_proceso_desembolso(credito)
        
        logger.info(f"Webhook procesado exitosamente para crédito {numero_credito}. Estado ahora: {credito.estado}")
        return JsonResponse({'status': 'ok', 'message': 'Crédito actualizado a PENDIENTE_TRANSFERENCIA'})

    except Exception as e:
        logger.error(f"Error procesando el webhook para el crédito {numero_credito}: {e}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': 'Error interno del servidor'}, status=500)


@staff_member_required
@require_POST
def simular_firma_view(request, credito_id):
    """
    Simula la firma de un documento que normalmente se haría por un webhook externo.
    Uso temporal mientras la integración con el proveedor de firma no está lista.
    """
    credito = get_object_or_404(Credito, id=credito_id)

    if credito.estado != Credito.EstadoCredito.PENDIENTE_FIRMA:
        messages.error(request, f"El crédito no está en estado 'Pendiente Firma'. Estado actual: {credito.get_estado_display()}.")
        return redirect('gestion:credito_detalle', credito_id=credito.id)

    try:
        with transaction.atomic():
            # 1. Cambiar estado a FIRMADO
            services.gestionar_cambio_estado_credito(
                credito=credito,
                nuevo_estado=Credito.EstadoCredito.FIRMADO,
                motivo="Documento (pagaré) firmado exitosamente (SIMULADO por admin).",
                usuario_modificacion=request.user
            )

            # 2. Iniciar el proceso de desembolso (cambia a PENDIENTE_TRANSFERENCIA)
            services.iniciar_proceso_desembolso(credito)
        
        messages.success(request, f"Firma simulada exitosamente. El crédito {credito.numero_credito} ahora está PENDIENTE DE TRANSFERENCIA.")

    except Exception as e:
        messages.error(request, f"Error al simular la firma para el crédito {credito.id}: {e}")
        logger.error(f"Error en simular_firma_view para crédito {credito.id}: {e}", exc_info=True)

    return redirect('gestion:credito_detalle', credito_id=credito.id)


#? --------- VISTA DE CREDITO DE LIBRANZA ------------
@login_required(login_url='/accounts/google/login/')
def solicitud_credito_libranza_view(request):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if request.method == 'POST':
        form = CreditoLibranzaForm(request.POST, request.FILES)
        if form.is_valid():
            credito_principal = Credito.objects.create(
                usuario=request.user, #! le PASAMOS EL USUARIO LOGUEADO (PENDIENTE CAMBIARLO POR EL NOMBRE DE LA PERSONA QUE REGISTRA LA SOLICITUD)
                linea=Credito.LineaCredito.LIBRANZA, #! LE PASAMOS LINEA DE CREDITO
                estado=Credito.EstadoCredito.EN_REVISION, #! PONEMOS EL ESTADO INICIAL DE LA SOLICITUD (EN REVISION)
                monto_solicitado=form.cleaned_data['valor_credito'],
                plazo_solicitado=form.cleaned_data['plazo']
            )
            credito_libranza_detalle = form.save(commit=False)
            credito_libranza_detalle.credito = credito_principal
            credito_libranza_detalle.save()

            # Enviar email de confirmación de solicitud recibida
            try:
                from gestion_creditos.email_service import enviar_notificacion_cambio_estado
                enviar_notificacion_cambio_estado(
                    credito_principal,
                    Credito.EstadoCredito.EN_REVISION,
                    "Solicitud de crédito recibida y en proceso de revisión"
                )
            except Exception as e:
                logger.error(f"Error al enviar email de confirmación para crédito {credito_principal.id}: {e}")

            if is_ajax:
                return JsonResponse({'success': True})
            return redirect('usuariocreditos:dashboard_libranza')  # ⭐ Redirige al dashboard de LIBRANZA
        else:
            if is_ajax:
                return JsonResponse({'success': False, 'errors': form.errors}, status=400)
    else:
        form = CreditoLibranzaForm()
    return render(request, 'gestion_creditos/solicitud_libranza.html', {'form': form})

@login_required
def solicitud_credito_emprendimiento_view(request):
    """
    Vista para procesar solicitudes de crédito de emprendimiento.
    Integra scoring de imágenes con IA y evaluación de motivación.
    """
    if request.method == 'POST':
        # Validar autenticación para solicitudes AJAX
        if not request.user.is_authenticated:
            return JsonResponse({'success': False, 'error': 'Authentication required'}, status=403)

        # Detectar si es solicitud AJAX o viene del formulario HTML con imágenes múltiples
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        # Si viene del formulario con imágenes múltiples (del trabajo del compañero)
        if 'fotos_neg' in request.FILES:
            try:
                # Capturar imágenes múltiples
                imagenes_negocio = request.FILES.getlist('fotos_neg')
                desc_fotos_neg = request.POST.get('desc_fotos_neg', '').strip()

                # Capturar tipos de imagen
                tipos_imagen = []
                i = 0
                while f'tipo_imagen_{i}' in request.POST:
                    tipo = request.POST.get(f'tipo_imagen_{i}')
                    if tipo:
                        tipos_imagen.append(tipo)
                    i += 1

                # Validar mínimo 3 imágenes
                if len(imagenes_negocio) < 3:
                    return JsonResponse({
                        'success': False,
                        'error': 'Se requieren al menos 3 imágenes del negocio'
                    }, status=400)

                # Validar tipos para todas las imágenes
                if len(tipos_imagen) < len(imagenes_negocio):
                    return JsonResponse({
                        'success': False,
                        'error': 'Debe especificar el tipo de cada imagen'
                    }, status=400)

                # Validar formato y tamaño de imágenes
                for imagen in imagenes_negocio:
                    if not imagen.content_type.startswith('image/'):
                        return JsonResponse({
                            'success': False,
                            'error': f'El archivo {imagen.name} no es una imagen válida'
                        }, status=400)
                    if imagen.size > 10 * 1024 * 1024:  # 10MB
                        return JsonResponse({
                            'success': False,
                            'error': f'La imagen {imagen.name} excede el tamaño máximo de 10MB'
                        }, status=400)

                # SCORING DE IMÁGENES CON IA
                from .scoring_client import scoring_client

                resultado_scoring = scoring_client.enviar_imagenes_para_scoring(
                    imagenes_negocio,
                    tipos_imagen,
                    desc_fotos_neg
                )

                puntaje_imagenes = resultado_scoring.get('puntaje', 9.0)

                if resultado_scoring['success']:
                    logger.info(f"Puntaje de imágenes (1-18): {puntaje_imagenes}")
                else:
                    logger.warning(f"No se pudo obtener scoring de imágenes: {resultado_scoring.get('error')}")

                # Evaluar motivación con ChatGPT
                desc_cred_nec = request.POST.get('desc_cred_nec', '').strip()
                puntaje_motivacion = services.evaluar_motivacion_credito(desc_cred_nec)

                # Calcular puntaje interno
                datos_evaluacion = {
                    'Tiempo_operando': request.POST.get('tiempo_operando'),
                    'Actividad_diaria': request.POST.get('dias_trabajados_sem'),
                    'Ubicacion': request.POST.get('ubicacion_negocio'),
                    'Ingresos': request.POST.get('ingresos_prom_mes'),
                    'Herramientas digitales': request.POST.get('tipo_cta_mno'),
                    'Ahorro tandas': request.POST.get('ahorro_tand_alc'),
                    'Dependientes': request.POST.get('depend_h'),
                    'Redes sociales': request.POST.get('redes_soc'),
                }
                puntaje_interno = services.obtener_puntaje_interno(datos_evaluacion)

                # Puntaje total combinado
                puntaje_total = puntaje_interno + puntaje_motivacion + puntaje_imagenes

                logger.info(f"Puntaje total: {puntaje_total} (interno: {puntaje_interno}, motivación: {puntaje_motivacion}, imágenes: {puntaje_imagenes})")

                # Crear crédito principal (modelo refactorizado)
                credito_principal = Credito.objects.create(
                    usuario=request.user,
                    linea=Credito.LineaCredito.EMPRENDIMIENTO,
                    estado=Credito.EstadoCredito.EN_REVISION,
                    monto_solicitado=Decimal(request.POST.get('valor_cred', '0')),
                    plazo_solicitado=int(request.POST.get('plazo', '0'))
                )

                # Crear detalle de emprendimiento (SIN campos financieros)
                from .models import CreditoEmprendimiento
                detalle = CreditoEmprendimiento.objects.create(
                    credito=credito_principal,
                    nombre=request.POST.get('nombre', '').strip(),
                    numero_cedula=request.POST.get('numero_cedula', '').strip(),
                    fecha_nac=datetime.strptime(request.POST.get('fecha_nac', ''), '%Y-%m-%d').date(),
                    celular_wh=request.POST.get('celular_wh', '').strip(),
                    direccion=request.POST.get('direccion', '').strip(),
                    estado_civil=request.POST.get('estado_civil', '').strip(),
                    numero_personas_cargo=int(request.POST.get('numero_personas_cargo', '0')),
                    nombre_negocio=request.POST.get('nombre_negocio', '').strip(),
                    ubicacion_negocio=request.POST.get('ubicacion_negocio', '').strip(),
                    tiempo_operando=request.POST.get('tiempo_operando', '').strip(),
                    dias_trabajados_sem=int(request.POST.get('dias_trabajados_sem', '0')),
                    prod_serv_ofrec=request.POST.get('prod_serv_ofrec', '').strip(),
                    ingresos_prom_mes=request.POST.get('ingresos_prom_mes', '').strip(),
                    cli_aten_day=int(request.POST.get('cli_aten_day', '0')),
                    inventario=request.POST.get('inventario', '').strip(),
                    nomb_ref_per1=request.POST.get('nomb_ref_per1', '').strip(),
                    cel_ref_per1=request.POST.get('cel_ref_per1', '').strip(),
                    rel_ref_per1=request.POST.get('rel_ref_per1', '').strip(),
                    nomb_ref_cl1=request.POST.get('nomb_ref_cl1', '').strip(),
                    cel_ref_cl1=request.POST.get('cel_ref_cl1', '').strip(),
                    rel_ref_cl1=request.POST.get('rel_ref_cl1', '').strip(),
                    ref_conoc_lid_com=request.POST.get('ref_conoc_lid_com', '').strip(),
                    desc_fotos_neg=desc_fotos_neg,
                    tipo_cta_mno=request.POST.get('tipo_cta_mno', '').strip(),
                    ahorro_tand_alc=request.POST.get('ahorro_tand_alc', '').strip(),
                    depend_h=request.POST.get('depend_h', '').strip(),
                    desc_cred_nec=desc_cred_nec,
                    redes_soc=request.POST.get('redes_soc', '').strip(),
                    fotos_prod=request.POST.get('fotos_prod', '').strip(),
                    puntaje=int(puntaje_total),
                    puntaje_imagenes=puntaje_imagenes,
                    datos_scoring_imagenes=resultado_scoring.get('data', {})
                )

                # Guardar imágenes
                from .models import ImagenNegocio
                for imagen, tipo in zip(imagenes_negocio, tipos_imagen):
                    ImagenNegocio.objects.create(
                        credito_emprendimiento=detalle,
                        imagen=imagen,
                        tipo_imagen=tipo,
                        descripcion=f"{tipo} - {desc_fotos_neg}"
                    )

                logger.info(f"Guardadas {len(imagenes_negocio)} imágenes para crédito {credito_principal.numero_credito}")

                # Enviar email de confirmación
                try:
                    from .email_service import enviar_notificacion_cambio_estado
                    enviar_notificacion_cambio_estado(
                        credito_principal,
                        Credito.EstadoCredito.EN_REVISION,
                        "Solicitud de crédito recibida y en proceso de revisión"
                    )
                except Exception as e:
                    logger.error(f"Error al enviar email de confirmación: {e}")

                return JsonResponse({
                    'success': True,
                    'suma_estimaciones': puntaje_total,
                    'puntaje_imagenes': puntaje_imagenes,
                    'imagenes_guardadas': len(imagenes_negocio)
                })

            except Exception as e:
                logger.error(f"Error en solicitud con imágenes múltiples: {e}")
                return JsonResponse({'success': False, 'error': str(e)}, status=500)

        # Si viene del formulario normal (sin imágenes múltiples)
        else:
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
                        estado=Credito.EstadoCredito.EN_REVISION,
                        monto_solicitado=form.cleaned_data.get('valor_credito', 0),
                        plazo_solicitado=form.cleaned_data.get('plazo', 0)
                    )

                    detalle_emprendimiento = form.save(commit=False)
                    detalle_emprendimiento.credito = credito_principal
                    detalle_emprendimiento.puntaje = puntaje_total
                    detalle_emprendimiento.save()

                    # Enviar email de confirmación
                    try:
                        from .email_service import enviar_notificacion_cambio_estado
                        enviar_notificacion_cambio_estado(
                            credito_principal,
                            Credito.EstadoCredito.EN_REVISION,
                            "Solicitud de crédito recibida y en proceso de revisión"
                        )
                    except Exception as e:
                        logger.error(f"Error al enviar email de confirmación: {e}")

                    return JsonResponse({'success': True, 'suma_estimaciones': puntaje_total})

                except Exception as e:
                    logger.error(f"Error en solicitud_credito_emprendimiento_view: {e}")
                    return JsonResponse({'success': False, 'error': str(e)}, status=500)
            else:
                return JsonResponse({'success': False, 'errors': form.errors}, status=400)
    else:
        form = CreditoEmprendimientoForm()

    return render(request, 'aplicando.html', {
        'form': form,
        'es_empleado': False  # Formulario de emprendimiento siempre es False
    })

@staff_member_required
def admin_dashboard_view(request):
    """
    Muestra el dashboard principal administrativo.

    Delega la recolección y procesamiento de todos los datos de contexto
    a la función `get_admin_dashboard_context` en el módulo de servicios para
    mantener la vista limpia y centrada en la renderización.
    """
    context = services.get_admin_dashboard_context(request.user)
    return render(request, 'gestion_creditos/admin_dashboard.html', context)

@staff_member_required
def admin_solicitudes_view(request):
    """Vista para gestionar solicitudes pendientes"""
    
    solicitudes_base = Credito.objects.exclude(estado__in=['ACTIVO', 'PAGADO', 'EN_MORA'])
    solicitudes_filtradas = services.filtrar_creditos(request, solicitudes_base)
    
    solicitudes = solicitudes_filtradas.select_related(
        'usuario', 'detalle_libranza', 'detalle_emprendimiento'
    ).annotate(
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
    """Vista para gestionar créditos activos"""
    
    creditos_base = Credito.objects.filter(estado='ACTIVO')

    # Calculate stats for active credits
    stats_activos = creditos_base.aggregate(
        total_creditos=Count('id'),
        valor_total=Sum('saldo_pendiente'),
        valor_promedio=Avg('monto_aprobado')
    )

    desembolsos_hoy = Credito.objects.filter(
        estado=Credito.EstadoCredito.ACTIVO,
        fecha_desembolso__date=timezone.now().date()
    ).count()

    creditos_filtrados = services.filtrar_creditos(request, creditos_base)
    
    creditos = creditos_filtrados.select_related(
        'usuario', 'detalle_libranza', 'detalle_emprendimiento'
    ).order_by('-fecha_solicitud')
    
    paginator = Paginator(creditos, 20)
    page_number = request.GET.get('page')
    creditos_page = paginator.get_page(page_number)
    
    context = {
        'creditos': creditos_page,
        'total_creditos_activos': stats_activos.get('total_creditos') or 0,
        'valor_total_cartera_activa': stats_activos.get('valor_total') or 0,
        'valor_promedio_credito_activo': stats_activos.get('valor_promedio') or 0,
        'desembolsos_hoy': desembolsos_hoy,
        'linea_filter': request.GET.get('linea', ''),
        'search': request.GET.get('search', ''),
        'lineas_choices': Credito.LineaCredito.choices,
    }
    
    return render(request, 'gestion_creditos/admin_creditos_activos.html', context)


@staff_member_required
def admin_cartera_view(request):
    """
    Vista para la gestión de cartera, mostrando créditos en mora.
    """

    #? Base de créditos en mora
    creditos_en_mora = Credito.objects.filter(estado=Credito.EstadoCredito.EN_MORA)

    #? Aplicar filtros de búsqueda y línea de crédito
    creditos_filtrados = services.filtrar_creditos(request, creditos_en_mora)
    
    #? Anotaciones y ordenamiento
    # Se calcula la diferencia entre hoy y la fecha de vencimiento para poder ordenar por ella.
    today = timezone.now().date()
    creditos_con_dias_mora = creditos_filtrados.annotate(
        dias_en_mora_db=ExpressionWrapper(
            Value(today) - F('fecha_proximo_pago'),
            output_field=DurationField()
        )
    )

    creditos = creditos_con_dias_mora.select_related(
        'usuario', 'detalle_libranza', 'detalle_emprendimiento'
    ).order_by('-dias_en_mora_db') # Ordenar por el campo anotado

    #? Paginación
    paginator = Paginator(creditos, 20)
    page_number = request.GET.get('page')
    creditos_page = paginator.get_page(page_number)

    #? Estadísticas de la cartera en mora
    stats_cartera_mora = creditos_en_mora.aggregate(
        total_creditos=Count('id'),
        monto_total_en_mora=Sum('saldo_pendiente'),
        monto_original_en_mora=Sum('total_a_pagar')
    )

    monto_original = stats_cartera_mora.get('monto_original_en_mora') or 0
    monto_pendiente = stats_cartera_mora.get('monto_total_en_mora') or 0
    
    monto_pagado = monto_original - monto_pendiente

    #? calculo de tasa de recuperación (puede mejorar mas adelante)
    tasa_recuperacion = (monto_pagado / monto_original) * 100 if monto_original > 0 else 0

    context = {
        'creditos': creditos_page,
        'stats': stats_cartera_mora,
        'tasa_recuperacion': round(tasa_recuperacion, 2),
        'linea_filter': request.GET.get('linea', ''),
        'search': request.GET.get('search', ''),
        'lineas_choices': Credito.LineaCredito.choices,
    }
    
    return render(request, 'gestion_creditos/admin_cartera.html', context)


@staff_member_required
def procesar_solicitud_view(request, credito_id):
    """Aprobar o rechazar una solicitud usando el servicio centralizado."""
    if request.method != 'POST':
        messages.error(request, "Método no permitido.")
        return redirect('gestion:credito_detalle', credito_id=credito_id)

    credito = get_object_or_404(Credito, id=credito_id, estado__in=['SOLICITUD', 'EN_REVISION'])
    action = request.POST.get('action')
    
    nuevo_estado = None
    if action == 'approve':
        nuevo_estado = Credito.EstadoCredito.APROBADO
        try:
            # El frontend ya envía el número en formato "1400000.50".
            # No se necesita limpieza manual, solo convertir a Decimal.
            monto_aprobado_str = request.POST.get('monto_aprobado', '0')
            plazo_str = request.POST.get('plazo_aprobado', '')

            if not monto_aprobado_str or not plazo_str:
                messages.error(request, "Para aprobar, el monto y el plazo son obligatorios.")
                return redirect('gestion:credito_detalle', credito_id=credito_id)

            credito.monto_aprobado = Decimal(monto_aprobado_str)
            credito.plazo = int(plazo_str)
            credito.save(update_fields=['monto_aprobado', 'plazo'])
        
        except (ValueError, TypeError, decimal.InvalidOperation) as e:
            messages.error(request, f"Monto o plazo inválido: {e}")
            return redirect('gestion:credito_detalle', credito_id=credito_id)

    elif action == 'reject':
        nuevo_estado = Credito.EstadoCredito.RECHAZADO
        messages.warning(request, f'Crédito {credito.numero_credito} rechazado.')
    else:
        messages.error(request, "Acción no válida.")
        return redirect('gestion:credito_detalle', credito_id=credito_id)

    motivo = request.POST.get('observations', 'Decisión inicial de la solicitud.')
    
    try:
        # Primer paso: Cambiar a APROBADO o RECHAZADO
        services.gestionar_cambio_estado_credito(
            credito=credito,
            nuevo_estado=nuevo_estado,
            usuario_modificacion=request.user,
            motivo=motivo
        )

        # Segundo paso (solo si se aprueba): Iniciar la preparación para la firma
        if nuevo_estado == Credito.EstadoCredito.APROBADO:
            services.preparar_documento_para_firma(
                credito=credito,
                usuario_modificacion=request.user
            )
            messages.success(request, f'Crédito {credito.numero_credito} aprobado y pasado a PENDIENTE DE FIRMA.')

    except Exception as e:
        messages.error(request, f"Ocurrió un error inesperado durante el procesamiento: {e}")
        logger.error(f"Error al procesar solicitud del crédito {credito.id}: {e}", exc_info=True)


    return redirect('gestion:credito_detalle', credito_id=credito_id)


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

    # Tabla de amortización (si existe)
    tabla_amortizacion = credito.tabla_amortizacion.all().order_by('numero_cuota')

    # Determinar si el crédito puede ser procesado (aprobado/rechazado)
    puede_procesar = credito.estado in [Credito.EstadoCredito.SOLICITUD, Credito.EstadoCredito.EN_REVISION]
    print("Monto solicitado: ",credito.monto_solicitado)
    context = {
        'credito': credito,

        # ✅ Campos ahora vienen del modelo Credito
        'monto_solicitado': credito.monto_solicitado,
        'plazo_solicitado': credito.plazo_solicitado,
        'monto_aprobado': credito.monto_aprobado,
        'plazo': credito.plazo,
        'tasa_interes': credito.tasa_interes,
        'saldo_pendiente': credito.saldo_pendiente,
        'valor_cuota': credito.valor_cuota,
        'fecha_proximo_pago': credito.fecha_proximo_pago,
        'total_a_pagar': credito.total_a_pagar,
        'comision': credito.comision,
        'iva_comision': credito.iva_comision,

        # Campos que SÍ vienen del detalle
        'detalle': credito.detalle,  # Usa la property
        'historial_pagos': historial_pagos,
        'historial_estados': historial_estados,
        'puede_procesar': puede_procesar,
        'cuotas_pagadas': cuotas_pagadas,
        'cuotas_restantes': cuotas_restantes,
        'tabla_amortizacion': tabla_amortizacion,  # ⭐ NUEVA: Tabla de amortización
    }
    
    return render(request, 'gestion_creditos/admin_detalle_credito.html', context)



@staff_member_required
@require_POST
def confirmar_desembolso_view(request, credito_id):
    """
    Vista dedicada para que finanzas confirme el desembolso y active el crédito.
    """
    credito = get_object_or_404(Credito, id=credito_id)
    comprobante = request.FILES.get('comprobante_pago')

    # 1. Validar estado actual
    if credito.estado != Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA:
        messages.error(request, f"El crédito no está en estado 'Pendiente de Transferencia'. Estado actual: {credito.get_estado_display()}.")
        return redirect('gestion:credito_detalle', credito_id=credito.id)

    # 2. Validar que se haya subido el comprobante
    if not comprobante:
        messages.error(request, "Es obligatorio adjuntar el comprobante de desembolso.")
        return redirect('gestion:credito_detalle', credito_id=credito.id)

    # 3. Ejecutar el cambio de estado a ACTIVO
    try:
        services.gestionar_cambio_estado_credito(
            credito=credito,
            nuevo_estado=Credito.EstadoCredito.ACTIVO,
            motivo="Desembolso confirmado y comprobante adjuntado por el equipo de finanzas.",
            comprobante=comprobante,
            usuario_modificacion=request.user
        )
        messages.success(request, f"¡Crédito {credito.numero_credito} activado exitosamente!")
    except Exception as e:
        messages.error(request, f"Ocurrió un error inesperado al activar el crédito: {e}")
        logger.error(f"Error al activar crédito {credito.id} vía confirmación de desembolso: {e}", exc_info=True)

    return redirect('gestion:credito_detalle', credito_id=credito.id)



@staff_member_required
@require_POST
def agregar_pago_manual_view(request, credito_id):
    credito = get_object_or_404(Credito, id=credito_id)
    monto = request.POST.get('monto')
    auth_key = request.POST.get('auth_key')

    if not monto or not auth_key:
        messages.error(request, "Monto y clave de autorización son requeridos.")
        return redirect('gestion:credito_detalle', credito_id=credito.id)

    if auth_key != getattr(settings, 'MANUAL_PAYMENT_AUTH_KEY', None):
        messages.error(request, "Clave de autorización no válida.")
        return redirect('gestion:credito_detalle', credito_id=credito.id)

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

    return redirect('gestion:credito_detalle', credito_id=credito.id)

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
    sort_by = request.GET.get('sort_by', '-monto_aprobado') # Ordenar por monto de crédito descendente por defecto

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
        'monto_aprobado', '-monto_aprobado',
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
    saldo_pendiente = credito.saldo_pendiente

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

    valor_cuota = credito.valor_cuota
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
    print("monto: ",monto)
    referencia = request.POST.get('referencia')

    credito = get_object_or_404(Credito, id=credito_id)
    if status == 'success':
        try:
            if not monto:
                raise ValueError("El monto recibido está vacío.")

            with transaction.atomic():
                #? Eliminamos los puntos y reemplazamos la coma por punto para Decimal
                monto_limpio = monto.replace('.', '').replace(',', '.')
                #? Convertir a Decimal (Ej: "1,234.56" -> Decimal("1234.56")
                monto_decimal = Decimal(monto_limpio)
                
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

        except (ValueError, TypeError, decimal.ConversionSyntax) as e:
            messages.error(request, f"Ocurrió un error al procesar el pago: {e}")
        except Exception as e:
            messages.error(request, f"Ocurrió un error inesperado al procesar el pago: {e}")
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
    return render(request, 'Billetera/billetera_digital.html', context)


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