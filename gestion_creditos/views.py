from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
import uuid
import json
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_http_methods
from django.conf import settings
from .models import Credito, CreditoLibranza, CreditoEmprendimiento, Empresa, HistorialPago, HistorialEstado, CuentaAhorro, MovimientoAhorro, Pagare, ZapSignWebhookLog
from .forms import CreditoLibranzaForm, CreditoEmprendimientoForm, AbonoManualAdminForm, ConsignacionOfflineForm
from . import credit_services
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
#! La lógica ahora está centralizada en credit_services.gestionar_cambio_estado_credito.

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

            try:
                from gestion_creditos.email_service import enviar_email_simple
                pagadores = PerfilPagador.objects.filter(
                    empresa=credito_libranza_detalle.empresa,
                    es_pagador=True
                ).select_related('usuario')
                if pagadores:
                    monto_formateado = f"{credito_principal.monto_solicitado:,.0f}"
                    dashboard_url = request.build_absolute_uri(reverse('pagador:dashboard'))
                    login_url = request.build_absolute_uri(reverse('pagador:login'))
                    for perfil in pagadores:
                        if not perfil.usuario.email:
                            continue
                        mensaje = (
                            "Se registro una nueva solicitud de credito de libranza.\n\n"
                            f"Empleado: {credito_libranza_detalle.nombre_completo}\n"
                            f"Cedula: {credito_libranza_detalle.cedula}\n"
                            f"Monto solicitado: ${monto_formateado}\n"
                            f"Plazo solicitado: {credito_principal.plazo_solicitado} meses\n\n"
                            "Ingresa para aprobar o rechazar:\n"
                            f"{dashboard_url}\n\n"
                            "Si no has iniciado sesion:\n"
                            f"{login_url}\n"
                        )
                        enviar_email_simple(
                            perfil.usuario.email,
                            "Nueva solicitud de credito de libranza",
                            mensaje
                        )
            except Exception as e:
                logger.error(f"Error al notificar a pagadores para credito {credito_principal.id}: {e}")

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
                puntaje_motivacion = credit_services.evaluar_motivacion_credito(desc_cred_nec)

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
                puntaje_interno = credit_services.obtener_puntaje_interno(datos_evaluacion)

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
                    puntaje_interno = credit_services.obtener_puntaje_interno(datos_evaluacion)
                    puntaje_motivacion = credit_services.evaluar_motivacion_credito(form.cleaned_data.get('desc_cred_nec'))
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

    return render(request, 'emprendimiento/aplicando.html', {
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
    context = credit_services.get_admin_dashboard_context(request.user)
    return render(request, 'gestion_creditos/admin_dashboard.html', context)

@staff_member_required
def admin_solicitudes_view(request):
    """Vista para gestionar solicitudes pendientes"""
    
    solicitudes_base = Credito.objects.exclude(estado__in=['ACTIVO', 'PAGADO', 'EN_MORA'])
    solicitudes_filtradas = credit_services.filtrar_creditos(request, solicitudes_base)
    
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

    creditos_filtrados = credit_services.filtrar_creditos(request, creditos_base)
    
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
    creditos_filtrados = credit_services.filtrar_creditos(request, creditos_en_mora)
    
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
        saldo_pendiente_total=Sum('saldo_pendiente'),
        monto_original_en_mora=Sum('total_a_pagar')
    )
    monto_total_en_mora = credit_services.calcular_total_en_mora(creditos_en_mora)
    stats_cartera_mora['monto_total_en_mora'] = monto_total_en_mora

    monto_original = stats_cartera_mora.get('monto_original_en_mora') or 0
    monto_pendiente = stats_cartera_mora.get('saldo_pendiente_total') or 0
    
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
        credit_services.gestionar_cambio_estado_credito(
            credito=credito,
            nuevo_estado=nuevo_estado,
            usuario_modificacion=request.user,
            motivo=motivo
        )

        # Segundo paso (solo si se aprueba): Iniciar la preparación para la firma
        if nuevo_estado == Credito.EstadoCredito.APROBADO:
            credit_services.preparar_documento_para_firma(
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
        credit_services.gestionar_cambio_estado_credito(
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
                credit_services.actualizar_saldo_tras_pago(credito, monto_decimal)
            
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
    
    return render(request, 'pagador/pagador_dashboard.html', context)

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
    
    return render(request, 'pagador/pagador_detalle_credito.html', context)


@login_required
@require_POST
@pagador_required
def pagador_decidir_solicitud_view(request, credito_id):
    """
    Permite al pagador aprobar o rechazar solicitudes de libranza de su empresa.
    """
    empresa = request.empresa
    credito = get_object_or_404(
        Credito,
        id=credito_id,
        linea=Credito.LineaCredito.LIBRANZA,
        estado__in=[Credito.EstadoCredito.SOLICITUD, Credito.EstadoCredito.EN_REVISION]
    )

    if credito.detalle_libranza.empresa != empresa:
        messages.error(request, "No tiene permiso para gestionar este credito.")
        return redirect('pagador:dashboard')

    action = request.POST.get('action')
    motivo = (request.POST.get('motivo') or '').strip()

    if action not in ['approve', 'reject']:
        messages.error(request, "Accion no valida.")
        return redirect('pagador:dashboard')

    try:
        if action == 'approve':
            if credito.monto_aprobado is None:
                credito.monto_aprobado = credito.monto_solicitado
            if credito.plazo is None:
                credito.plazo = credito.plazo_solicitado
            credito.save(update_fields=['monto_aprobado', 'plazo'])

            motivo_final = motivo or "Aprobado por pagador."
            credit_services.gestionar_cambio_estado_credito(
                credito=credito,
                nuevo_estado=Credito.EstadoCredito.APROBADO,
                usuario_modificacion=request.user,
                motivo=motivo_final
            )
            credit_services.preparar_documento_para_firma(
                credito=credito,
                usuario_modificacion=request.user
            )
            messages.success(request, f"Credito {credito.numero_credito} aprobado.")
        else:
            motivo_final = motivo or "Rechazado por pagador."
            credit_services.gestionar_cambio_estado_credito(
                credito=credito,
                nuevo_estado=Credito.EstadoCredito.RECHAZADO,
                usuario_modificacion=request.user,
                motivo=motivo_final
            )
            messages.warning(request, f"Credito {credito.numero_credito} rechazado.")
    except Exception as e:
        messages.error(request, f"Ocurrio un error al procesar la solicitud: {e}")
        logger.error(f"Error al decidir solicitud {credito.id} por pagador: {e}", exc_info=True)

    return redirect('pagador:dashboard')

@login_required
@require_POST
@pagador_required
def pagador_procesar_pagos_view(request):
    """
    Valida un archivo CSV de pagos masivos y muestra confirmación para pagar con WOMPI.
    """
    from .services.wompi_client import WompiClient, WompiAPIException

    empresa = request.empresa
    csv_file = request.FILES.get('csv_file')

    if not csv_file or not csv_file.name.endswith('.csv'):
        messages.error(request, "Por favor, suba un archivo CSV válido.")
        return redirect('pagador:dashboard')

    # Validar CSV sin aplicar pagos
    pagos_validos, errores = credit_services.validar_csv_pagos_masivos(csv_file, empresa)

    if errores:
        request.session['errores_pago_masivo'] = errores
        messages.error(request, f"Se encontraron {len(errores)} errores en el archivo. Por favor corrija y vuelva a intentar.")
        return redirect('pagador:dashboard')

    if not pagos_validos:
        messages.warning(request, "No se encontraron pagos válidos en el archivo.")
        return redirect('pagador:dashboard')

    # Calcular monto total
    monto_total = sum(p['monto'] for p in pagos_validos)

    # Guardar en sesión para procesarlos después del pago exitoso
    request.session['pagos_csv_pendientes'] = [
        {
            'credito_id': p['credito_id'],
            'cedula': p['cedula'],
            'nombre': p['nombre'],
            'monto': str(p['monto'])  # Convertir Decimal a string para JSON
        }
        for p in pagos_validos
    ]

    # Obtener tokens de WOMPI
    client = WompiClient()
    try:
        acceptance_response = client.get_acceptance_token()
        acceptance_token = acceptance_response['data']['presigned_acceptance']['acceptance_token']
        bancos_pse = client.get_pse_financial_institutions()
    except WompiAPIException as e:
        logger.error(f"Error al obtener datos de WOMPI: {str(e)}")
        messages.error(request, "Error al conectar con la pasarela de pagos. Por favor intenta más tarde.")
        return redirect('pagador:dashboard')

    context = {
        'pagos_validos': pagos_validos,
        'cantidad_pagos': len(pagos_validos),
        'monto_total': int(monto_total),
        'monto_total_centavos': int(monto_total * 100),
        'referencia_pago': f"CSV-MASIVO-{timezone.now().strftime('%Y%m%d%H%M%S')}",
        'acceptance_token': acceptance_token,
        'bancos_pse': bancos_pse,
        'customer_email': empresa.correo_contacto if hasattr(empresa, 'correo_contacto') else request.user.email,
        'customer_name': empresa.nombre,
        'customer_phone': empresa.telefono if hasattr(empresa, 'telefono') else '',
        'wompi_public_key': settings.WOMPI_PUBLIC_KEY,
    }

    return render(request, 'pagador/confirmacion_pago_masivo.html', context)


@login_required
@pagador_required
def descargar_csv_cuotas_pendientes_view(request):
    """
    Genera y descarga un archivo CSV con todas las cuotas pendientes de los empleados.
    Los montos se redondean hacia arriba para evitar centavos.
    """
    import csv
    import math
    from django.http import HttpResponse

    empresa = request.empresa

    # Obtener todos los créditos activos de la empresa (incluye activos y en mora)
    creditos = Credito.objects.filter(
        linea=Credito.LineaCredito.LIBRANZA,
        detalle_libranza__empresa=empresa,
        estado__in=[Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA]
    ).select_related('detalle_libranza').order_by('detalle_libranza__cedula')

    # Crear la respuesta HTTP con el tipo de contenido CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="cuotas_pendientes_{empresa.nombre}_{timezone.now().strftime("%Y%m%d")}.csv"'

    # Agregar BOM y separador para que Excel respete la coma como delimitador
    response.write('\ufeffsep=,\n')

    writer = csv.writer(response)
    writer.writerow(['cedula', 'monto_a_pagar'])

    from decimal import Decimal, ROUND_CEILING
    for credito in creditos:
        cedula = str(credito.detalle_libranza.cedula)

        cuota = credito.tabla_amortizacion.filter(pagada=False).order_by('numero_cuota').first()
        if cuota:
            valor = cuota.valor_cuota - (cuota.monto_pagado or Decimal('0.00'))
        else:
            valor = credito.valor_cuota or Decimal('0.00')

        if valor < 0:
            valor = Decimal('0.00')

        monto = int(valor.to_integral_value(rounding=ROUND_CEILING))

        writer.writerow([cedula, monto])

    return response


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
    return render(request, 'pagador/simulacion_pago.html', context)


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
                credit_services.actualizar_saldo_tras_pago(credito, monto_decimal)
                
                messages.success(request, f"Pago de ${monto_decimal:,.2f} para el crédito #{credito.id} procesado exitosamente.")

        except (ValueError, TypeError, decimal.ConversionSyntax) as e:
            messages.error(request, f"Ocurrió un error al procesar el pago: {e}")
        except Exception as e:
            messages.error(request, f"Ocurrió un error inesperado al procesar el pago: {e}")
    else:
        messages.error(request, f"El pago para el crédito #{credito.id} fue fallido o cancelado.")

    return redirect('gestion_creditos:pagador_dashboard')


#? ============================================================================
#? VISTAS DE WOMPI - PASARELA DE PAGOS
#? ============================================================================

@login_required
@pagador_required
def iniciar_pago_wompi_view(request, credito_id):
    """
    Muestra el formulario de selección de método de pago con WOMPI
    """
    from .services.wompi_client import WompiClient, WompiAPIException

    credito = get_object_or_404(Credito, id=credito_id, linea=Credito.LineaCredito.LIBRANZA)

    # Verificar que el pagador tenga permisos
    if credito.detalle_libranza.empresa != request.empresa:
        messages.error(request, "No tiene permisos para pagar este crédito.")
        return redirect('pagador:dashboard')

    valor_cuota = credito.valor_cuota
    if not valor_cuota or valor_cuota <= 0:
        messages.error(request, "El crédito no tiene un valor de cuota válido para pagar.")
        return redirect('pagador:dashboard')

    # Obtener acceptance token de WOMPI
    client = WompiClient()
    try:
        acceptance_response = client.get_acceptance_token()
        acceptance_token = acceptance_response['data']['presigned_acceptance']['acceptance_token']

        # Obtener lista de bancos PSE
        bancos_pse = client.get_pse_financial_institutions()
    except WompiAPIException as e:
        logger.error(f"Error al obtener datos de WOMPI: {str(e)}")
        messages.error(request, "Error al conectar con la pasarela de pagos. Por favor intenta más tarde.")
        return redirect('pagador:dashboard')

    context = {
        'credito': credito,
        'valor_cuota': int(valor_cuota),
        'valor_cuota_centavos': int(valor_cuota * 100),  # Convertir a centavos
        'referencia_pago': f"CUOTA-{credito.id}-{timezone.now().strftime('%Y%m%d%H%M%S')}",
        'acceptance_token': acceptance_token,
        'bancos_pse': bancos_pse,
        'customer_email': credito.detalle_libranza.correo_electronico,
        'customer_name': credito.detalle_libranza.nombre_completo,
        'customer_phone': credito.detalle_libranza.telefono,
        'wompi_public_key': settings.WOMPI_PUBLIC_KEY,
    }

    return render(request, 'pagador/pago_wompi.html', context)


@login_required
def iniciar_pago_wompi_emprendimiento_view(request, credito_id):
    """
    Muestra el formulario de pago con WOMPI para clientes de emprendimiento.
    """
    from .services.wompi_client import WompiClient, WompiAPIException

    if not settings.WOMPI_PUBLIC_KEY or not settings.WOMPI_PRIVATE_KEY:
        messages.error(request, "Configuración WOMPI incompleta. Verifica las llaves en el entorno.")
        return redirect('emprendimiento:mi_credito')

    credito = get_object_or_404(
        Credito,
        id=credito_id,
        linea=Credito.LineaCredito.EMPRENDIMIENTO,
        usuario=request.user
    )

    tipo_pago = (request.GET.get('tipo') or 'CUOTA').upper()
    monto_param = request.GET.get('monto')
    monto = None

    if monto_param:
        try:
            monto = Decimal(str(monto_param))
        except Exception:
            monto = None

    cuotas_pendientes = credito.tabla_amortizacion.filter(pagada=False)
    total_pagar = sum((cuota.valor_cuota for cuota in cuotas_pendientes), Decimal('0.00'))

    if tipo_pago == 'TOTAL':
        monto = total_pagar
    elif tipo_pago == 'CAPITAL':
        if monto is None or monto <= 0 or monto > credito.capital_pendiente:
            messages.error(request, "El monto del abono a capital no es válido.")
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)
    elif tipo_pago == 'NORMAL':
        if monto is None or monto <= 0 or not credito.valor_cuota or monto > credito.valor_cuota:
            messages.error(request, "El monto del abono normal no es válido.")
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)
    else:
        tipo_pago = 'CUOTA'

    if monto is None:
        monto = credito.valor_cuota

    if not monto or monto <= 0:
        messages.error(request, "El crédito no tiene un valor válido para pagar.")
        return redirect('emprendimiento:mi_credito')

    client = WompiClient()
    try:
        acceptance_response = client.get_acceptance_token()
        acceptance_token = acceptance_response['data']['presigned_acceptance']['acceptance_token']
        bancos_pse = client.get_pse_financial_institutions()
    except WompiAPIException as e:
        logger.error(f"Error al obtener datos de WOMPI: {str(e)}")
        messages.error(request, "Error al conectar con la pasarela de pagos. Por favor intenta más tarde.")
        return redirect('emprendimiento:mi_credito')

    detalle = getattr(credito, 'detalle_emprendimiento', None)
    customer_name = detalle.nombre if detalle else request.user.get_full_name()
    customer_cedula = detalle.numero_cedula if detalle else ''
    customer_phone = detalle.celular_wh if detalle else ''

    context = {
        'credito': credito,
        'valor_cuota': int(monto),
        'valor_cuota_centavos': int(monto * 100),
        'referencia_pago': f"CUOTA-{credito.id}-{timezone.now().strftime('%Y%m%d%H%M%S')}",
        'acceptance_token': acceptance_token,
        'bancos_pse': bancos_pse,
        'customer_email': request.user.email,
        'customer_name': customer_name or request.user.username,
        'customer_cedula': customer_cedula,
        'customer_phone': customer_phone,
        'wompi_public_key': settings.WOMPI_PUBLIC_KEY,
        'tipo_pago': tipo_pago,
    }

    return render(request, 'usuariocreditos/pago_wompi_emprendimiento.html', context)


@login_required
@require_POST
def procesar_pago_wompi_emprendimiento_view(request):
    """
    Procesa el pago con WOMPI para clientes de emprendimiento.
    """
    from .services.wompi_client import WompiClient, WompiAPIException

    try:
        payment_method_type = request.POST.get('payment_method')
        credito_id = request.POST.get('credito_id')
        amount_in_cents = int(request.POST.get('amount_in_cents'))
        reference = request.POST.get('reference')
        customer_email = request.POST.get('customer_email')
        acceptance_token = request.POST.get('acceptance_token')
        tipo_pago = (request.POST.get('tipo_pago') or 'CUOTA').upper()

        credito = get_object_or_404(
            Credito,
            id=credito_id,
            linea=Credito.LineaCredito.EMPRENDIMIENTO,
            usuario=request.user
        )

        client = WompiClient()
        redirect_url = request.build_absolute_uri(reverse('emprendimiento:pago_wompi_callback'))

        if payment_method_type == 'CARD':
            card_token_response = client.tokenize_card(
                card_number=request.POST.get('card_number').replace(' ', ''),
                cvc=request.POST.get('cvc'),
                exp_month=request.POST.get('exp_month'),
                exp_year=request.POST.get('exp_year'),
                card_holder=request.POST.get('card_holder')
            )
            card_token = card_token_response['data']['id']

            payment_method = WompiClient.build_card_payment_method(
                token=card_token,
                installments=int(request.POST.get('installments', 1))
            )
            customer_data = None

        elif payment_method_type == 'PSE':
            payment_method = WompiClient.build_pse_payment_method(
                financial_institution_code=request.POST.get('financial_institution_code'),
                user_type=int(request.POST.get('user_type')),
                user_legal_id_type=request.POST.get('user_legal_id_type'),
                user_legal_id=request.POST.get('user_legal_id'),
                payment_description=f"Pago cuota {reference}"
            )
            customer_data = WompiClient.build_customer_data(
                phone_number=f"57{request.POST.get('phone_number')}",
                full_name=request.POST.get('full_name')
            )

        elif payment_method_type == 'NEQUI':
            payment_method = WompiClient.build_nequi_payment_method(
                phone_number=request.POST.get('nequi_phone')
            )
            customer_data = None

        elif payment_method_type == 'BANCOLOMBIA_TRANSFER':
            payment_method = WompiClient.build_bancolombia_transfer_payment_method(
                payment_description=f"Pago cuota {reference}"
            )
            customer_data = None
        else:
            messages.error(request, "Método de pago no válido.")
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

        transaction = client.create_transaction(
            amount_in_cents=amount_in_cents,
            currency='COP',
            customer_email=customer_email,
            payment_method=payment_method,
            reference=reference,
            acceptance_token=acceptance_token,
            redirect_url=redirect_url,
            customer_data=customer_data
        )

        request.session['wompi_transaction_id_empr'] = transaction['data']['id']
        request.session['wompi_credito_id_empr'] = credito_id
        request.session['wompi_reference_empr'] = reference
        request.session['wompi_tipo_pago_empr'] = tipo_pago

        if payment_method_type in ['PSE', 'NEQUI', 'BANCOLOMBIA_TRANSFER']:
            async_url = transaction['data']['payment_method']['extra']['async_payment_url']
            return redirect(async_url)

        status = transaction['data']['status']
        if status == 'APPROVED':
            monto_decimal = Decimal(amount_in_cents) / 100
            if tipo_pago == 'CAPITAL':
                credit_services.aplicar_abono_credito(
                    credito=credito,
                    monto_abono=monto_decimal,
                    tipo_abono='CAPITAL',
                    usuario=request.user,
                    referencia_pago=reference
                )
                messages.success(request, f"Abono a capital de ${monto_decimal:,.2f} aplicado exitosamente.")
            else:
                HistorialPago.objects.create(
                    credito=credito,
                    monto=monto_decimal,
                    referencia_pago=reference,
                    estado=HistorialPago.EstadoPago.EXITOSO
                )
                credit_services.actualizar_saldo_tras_pago(credito, monto_decimal)
                messages.success(request, f"Pago de ${monto_decimal:,.2f} procesado exitosamente.")
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)
        elif status == 'DECLINED':
            messages.error(request, "El pago fue rechazado. Por favor intenta con otro método.")
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)
        else:
            messages.warning(request, "El pago está pendiente de confirmación.")
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

    except WompiAPIException as e:
        logger.error(f"Error en WOMPI: {str(e)}")
        messages.error(request, f"Error al procesar el pago: {str(e)}")
        return redirect('emprendimiento:mi_credito')
    except Exception as e:
        logger.error(f"Error inesperado: {str(e)}")
        messages.error(request, "Ocurrió un error inesperado. Por favor intenta de nuevo.")
        return redirect('emprendimiento:mi_credito')


@login_required
@require_http_methods(["GET"])
def pago_wompi_emprendimiento_callback_view(request):
    """
    Callback de WOMPI para clientes de emprendimiento.
    """
    from .services.wompi_client import WompiClient, WompiAPIException

    transaction_id = request.GET.get('id') or request.session.get('wompi_transaction_id_empr')

    if not transaction_id:
        messages.error(request, "No se encontró información de la transacción.")
        return redirect('emprendimiento:mi_credito')

    client = WompiClient()

    try:
        transaction = client.get_transaction(transaction_id)
        status = transaction['data']['status']

        credito_id = request.session.get('wompi_credito_id_empr')
        reference = request.session.get('wompi_reference_empr')
        tipo_pago = (request.session.get('wompi_tipo_pago_empr') or 'CUOTA').upper()

        if not credito_id:
            messages.error(request, "Sesión expirada. Por favor intenta de nuevo.")
            return redirect('emprendimiento:mi_credito')

        credito = get_object_or_404(
            Credito,
            id=credito_id,
            linea=Credito.LineaCredito.EMPRENDIMIENTO,
            usuario=request.user
        )

        if status == 'APPROVED':
            monto_decimal = Decimal(transaction['data']['amount_in_cents']) / 100
            if tipo_pago == 'CAPITAL':
                credit_services.aplicar_abono_credito(
                    credito=credito,
                    monto_abono=monto_decimal,
                    tipo_abono='CAPITAL',
                    usuario=request.user,
                    referencia_pago=reference
                )
                messages.success(request, f"Abono a capital de ${monto_decimal:,.2f} aplicado exitosamente.")
            else:
                HistorialPago.objects.create(
                    credito=credito,
                    monto=monto_decimal,
                    referencia_pago=reference,
                    estado=HistorialPago.EstadoPago.EXITOSO
                )
                credit_services.actualizar_saldo_tras_pago(credito, monto_decimal)
                messages.success(request, f"Pago de ${monto_decimal:,.2f} procesado exitosamente.")
        elif status == 'DECLINED':
            messages.error(request, "El pago fue rechazado.")
        else:
            messages.warning(request, f"El pago está en estado: {status}")

        request.session.pop('wompi_transaction_id_empr', None)
        request.session.pop('wompi_credito_id_empr', None)
        request.session.pop('wompi_reference_empr', None)
        request.session.pop('wompi_tipo_pago_empr', None)

        return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

    except WompiAPIException as e:
        logger.error(f"Error al consultar transacción: {str(e)}")
        messages.error(request, "Error al verificar el estado del pago.")
        return redirect('emprendimiento:mi_credito')
@login_required
@pagador_required
@require_POST
def procesar_pago_wompi_view(request):
    """
    Procesa el pago con WOMPI según el método seleccionado
    """
    from .services.wompi_client import WompiClient, WompiAPIException

    try:
        # Obtener datos del formulario
        payment_method_type = request.POST.get('payment_method')
        credito_id = request.POST.get('credito_id')
        amount_in_cents = int(request.POST.get('amount_in_cents'))
        reference = request.POST.get('reference')
        customer_email = request.POST.get('customer_email')
        acceptance_token = request.POST.get('acceptance_token')

        credito = get_object_or_404(Credito, id=credito_id)

        # Verificar permisos
        if credito.detalle_libranza.empresa != request.empresa:
            messages.error(request, "No tiene permisos para pagar este crédito.")
            return redirect('pagador:dashboard')

        client = WompiClient()
        redirect_url = request.build_absolute_uri('/pagador/pago/callback/')

        # Construir payment_method según el tipo seleccionado
        if payment_method_type == 'CARD':
            # Tokenizar tarjeta
            card_token_response = client.tokenize_card(
                card_number=request.POST.get('card_number').replace(' ', ''),
                cvc=request.POST.get('cvc'),
                exp_month=request.POST.get('exp_month'),
                exp_year=request.POST.get('exp_year'),
                card_holder=request.POST.get('card_holder')
            )
            card_token = card_token_response['data']['id']

            payment_method = WompiClient.build_card_payment_method(
                token=card_token,
                installments=int(request.POST.get('installments', 1))
            )
            customer_data = None

        elif payment_method_type == 'PSE':
            payment_method = WompiClient.build_pse_payment_method(
                financial_institution_code=request.POST.get('financial_institution_code'),
                user_type=int(request.POST.get('user_type')),
                user_legal_id_type=request.POST.get('user_legal_id_type'),
                user_legal_id=request.POST.get('user_legal_id'),
                payment_description=f"Pago cuota {reference}"
            )
            customer_data = WompiClient.build_customer_data(
                phone_number=f"57{request.POST.get('phone_number')}",
                full_name=request.POST.get('full_name')
            )

        elif payment_method_type == 'NEQUI':
            payment_method = WompiClient.build_nequi_payment_method(
                phone_number=request.POST.get('nequi_phone')
            )
            customer_data = None

        elif payment_method_type == 'BANCOLOMBIA_TRANSFER':
            payment_method = WompiClient.build_bancolombia_transfer_payment_method(
                payment_description=f"Pago cuota {reference}"
            )
            customer_data = None
        else:
            messages.error(request, "Método de pago no válido.")
            return redirect('pagador:dashboard')

        # Crear transacción en WOMPI
        transaction = client.create_transaction(
            amount_in_cents=amount_in_cents,
            currency='COP',
            customer_email=customer_email,
            payment_method=payment_method,
            reference=reference,
            acceptance_token=acceptance_token,
            redirect_url=redirect_url,
            customer_data=customer_data
        )

        # Guardar transaction_id en sesión
        request.session['wompi_transaction_id'] = transaction['data']['id']
        request.session['credito_id'] = credito_id
        request.session['reference'] = reference

        # Si es método asíncrono, redirigir a la URL de pago
        if payment_method_type in ['PSE', 'NEQUI', 'BANCOLOMBIA_TRANSFER']:
            async_url = transaction['data']['payment_method']['extra']['async_payment_url']
            return redirect(async_url)

        # Si es tarjeta, verificar estado inmediatamente
        status = transaction['data']['status']
        if status == 'APPROVED':
            # Registrar el pago
            monto_decimal = Decimal(amount_in_cents) / 100
            HistorialPago.objects.create(
                credito=credito,
                monto=monto_decimal,
                referencia_pago=reference,
                estado=HistorialPago.EstadoPago.EXITOSO
            )
            credit_services.actualizar_saldo_tras_pago(credito, monto_decimal)
            messages.success(request, f"Pago de ${monto_decimal:,.2f} procesado exitosamente.")
            return redirect('pagador:dashboard')
        elif status == 'DECLINED':
            messages.error(request, "El pago fue rechazado. Por favor intenta con otro método.")
            return redirect('pagador:dashboard')
        else:
            messages.warning(request, "El pago está pendiente de confirmación.")
            return redirect('pagador:dashboard')

    except WompiAPIException as e:
        logger.error(f"Error en WOMPI: {str(e)}")
        messages.error(request, f"Error al procesar el pago: {str(e)}")
        return redirect('pagador:dashboard')
    except Exception as e:
        logger.error(f"Error inesperado: {str(e)}")
        messages.error(request, "Ocurrió un error inesperado. Por favor intenta de nuevo.")
        return redirect('pagador:dashboard')


@require_http_methods(["GET"])
def pago_wompi_callback_view(request):
    """
    Callback después de que el usuario completa el pago en WOMPI (PSE, Nequi, Bancolombia)
    """
    from .services.wompi_client import WompiClient, WompiAPIException

    transaction_id = request.GET.get('id') or request.session.get('wompi_transaction_id')

    if not transaction_id:
        messages.error(request, "No se encontró información de la transacción.")
        return redirect('pagador:dashboard')

    client = WompiClient()

    try:
        wompi_transaction = client.get_transaction(transaction_id)
        status = wompi_transaction['data']['status']

        # Verificar si es un pago masivo CSV
        pagos_csv_pendientes = request.session.get('pagos_csv_pendientes')

        if pagos_csv_pendientes:
            # PAGO MASIVO CSV
            reference = request.session.get('reference', f"CSV-MASIVO-{timezone.now().strftime('%Y%m%d%H%M%S')}")

            if status == 'APPROVED':
                monto_total = Decimal(wompi_transaction['data']['amount_in_cents']) / 100
                pagos_exitosos = 0

                with transaction.atomic():
                    for pago_info in pagos_csv_pendientes:
                        credito = Credito.objects.filter(id=pago_info['credito_id']).first()
                        if credito:
                            monto_pago = Decimal(pago_info['monto'])

                            # Crear registro de pago
                            HistorialPago.objects.create(
                                credito=credito,
                                monto=monto_pago,
                                referencia_pago=f"{reference}-{credito.id}",
                                estado=HistorialPago.EstadoPago.EXITOSO
                            )

                            # Actualizar saldo
                            credit_services.actualizar_saldo_tras_pago(credito, monto_pago)
                            pagos_exitosos += 1

                messages.success(request, f"¡Pago masivo procesado exitosamente! Se aplicaron {pagos_exitosos} pagos por un total de ${monto_total:,.2f}")
            elif status == 'DECLINED':
                messages.error(request, "El pago fue rechazado. No se aplicaron los pagos del CSV.")
            else:
                messages.warning(request, f"El pago está en estado: {status}")

            # Limpiar sesión
            request.session.pop('pagos_csv_pendientes', None)
            request.session.pop('wompi_transaction_id', None)
            request.session.pop('reference', None)

        else:
            # PAGO INDIVIDUAL
            credito_id = request.session.get('credito_id')
            reference = request.session.get('reference')

            if not credito_id:
                messages.error(request, "Sesión expirada. Por favor intenta de nuevo.")
                return redirect('pagador:dashboard')

            credito = get_object_or_404(Credito, id=credito_id)

            if status == 'APPROVED':
                # Registrar el pago
                monto_decimal = Decimal(wompi_transaction['data']['amount_in_cents']) / 100
                HistorialPago.objects.create(
                    credito=credito,
                    monto=monto_decimal,
                    referencia_pago=reference,
                    estado=HistorialPago.EstadoPago.EXITOSO
                )
                credit_services.actualizar_saldo_tras_pago(credito, monto_decimal)
                messages.success(request, f"Pago de ${monto_decimal:,.2f} procesado exitosamente.")
            elif status == 'DECLINED':
                messages.error(request, "El pago fue rechazado.")
            else:
                messages.warning(request, f"El pago está en estado: {status}")

            # Limpiar sesión
            request.session.pop('wompi_transaction_id', None)
            request.session.pop('credito_id', None)
            request.session.pop('reference', None)

    except WompiAPIException as e:
        logger.error(f"Error al consultar transacción: {str(e)}")
        messages.error(request, "Error al verificar el estado del pago.")

    return redirect('pagador:dashboard')


@login_required
@pagador_required
def iniciar_pago_masivo_wompi_view(request):
    """
    Muestra el formulario de selección de método de pago con WOMPI para pagos masivos
    """
    from .services.wompi_client import WompiClient, WompiAPIException

    if request.method != 'POST':
        messages.error(request, "Método no permitido.")
        return redirect('pagador:dashboard')

    # Obtener IDs de créditos seleccionados
    creditos_ids_str = request.POST.get('creditos_ids', '')
    if not creditos_ids_str:
        messages.error(request, "No se seleccionaron créditos para pagar.")
        return redirect('pagador:dashboard')

    try:
        creditos_ids = [int(id.strip()) for id in creditos_ids_str.split(',') if id.strip()]
    except ValueError:
        messages.error(request, "IDs de créditos inválidos.")
        return redirect('pagador:dashboard')

    if not creditos_ids:
        messages.error(request, "No se seleccionaron créditos válidos.")
        return redirect('pagador:dashboard')

    # Obtener créditos
    creditos = Credito.objects.filter(
        id__in=creditos_ids,
        linea=Credito.LineaCredito.LIBRANZA,
        detalle_libranza__empresa=request.empresa,
        estado=Credito.EstadoCredito.ACTIVO
    ).select_related('detalle_libranza')

    if not creditos.exists():
        messages.error(request, "No se encontraron créditos válidos para pagar.")
        return redirect('pagador:dashboard')

    # Calcular monto total
    monto_total = sum(c.valor_cuota for c in creditos if c.valor_cuota)
    if monto_total <= 0:
        messages.error(request, "El monto total a pagar es inválido.")
        return redirect('pagador:dashboard')

    # Obtener acceptance token de WOMPI
    client = WompiClient()
    try:
        acceptance_response = client.get_acceptance_token()
        acceptance_token = acceptance_response['data']['presigned_acceptance']['acceptance_token']

        # Obtener lista de bancos PSE
        bancos_pse = client.get_pse_financial_institutions()
    except WompiAPIException as e:
        logger.error(f"Error al obtener datos de WOMPI: {str(e)}")
        messages.error(request, "Error al conectar con la pasarela de pagos. Por favor intenta más tarde.")
        return redirect('pagador:dashboard')

    # Guardar en sesión
    request.session['creditos_ids_pago_masivo'] = creditos_ids
    request.session['monto_total_pago_masivo'] = str(monto_total)

    context = {
        'creditos': creditos,
        'cantidad_creditos': creditos.count(),
        'monto_total': int(monto_total),
        'monto_total_centavos': int(monto_total * 100),  # Convertir a centavos
        'referencia_pago': f"MASIVO-{timezone.now().strftime('%Y%m%d%H%M%S')}",
        'acceptance_token': acceptance_token,
        'bancos_pse': bancos_pse,
        'customer_email': request.empresa.correo_contacto if hasattr(request.empresa, 'correo_contacto') else request.user.email,
        'customer_name': request.empresa.nombre,
        'customer_phone': request.empresa.telefono if hasattr(request.empresa, 'telefono') else '',
        'wompi_public_key': settings.WOMPI_PUBLIC_KEY,
    }

    return render(request, 'pagador/pago_masivo_wompi.html', context)


@require_http_methods(["GET"])
def get_pse_banks_view(request):
    """
    API endpoint para obtener la lista de bancos PSE
    """
    from .services.wompi_client import WompiClient, WompiAPIException

    client = WompiClient()
    try:
        banks = client.get_pse_financial_institutions()
        return JsonResponse(banks, safe=False)
    except WompiAPIException as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def wompi_webhook_view(request):
    """
    Webhook de WOMPI para recibir notificaciones de eventos de pago.

    Eventos que maneja:
    - transaction.updated: Cuando una transacción cambia de estado

    IMPORTANTE: Este endpoint debe estar accesible públicamente sin autenticación
    para que WOMPI pueda enviar las notificaciones.
    """
    from .services.wompi_client import WompiClient
    import hashlib
    import hmac

    try:
        # Leer el cuerpo de la petición
        import json
        payload = json.loads(request.body.decode('utf-8'))

        # Validar la firma del webhook (integridad del mensaje)
        signature_header = request.headers.get('X-Event-Checksum', '')

        # Verificar la firma usando el EVENTS_SECRET
        expected_signature = hmac.new(
            settings.WOMPI_EVENTS_SECRET.encode('utf-8'),
            request.body,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature_header, expected_signature):
            logger.warning(f"Firma inválida en webhook de WOMPI. Esperada: {expected_signature}, Recibida: {signature_header}")
            return JsonResponse({'error': 'Invalid signature'}, status=401)

        # Procesar el evento
        event_type = payload.get('event')
        data = payload.get('data', {})

        logger.info(f"Webhook WOMPI recibido: {event_type}")

        if event_type == 'transaction.updated':
            transaction_data = data.get('transaction', {})
            transaction_id = transaction_data.get('id')
            status = transaction_data.get('status')
            reference = transaction_data.get('reference')
            amount_in_cents = transaction_data.get('amount_in_cents')

            logger.info(f"Transacción {transaction_id} actualizada a estado: {status}, Referencia: {reference}")

            # Buscar el crédito por la referencia
            # La referencia tiene formato: CUOTA-{credito_id}-{timestamp}
            if reference and reference.startswith('CUOTA-'):
                try:
                    credito_id = reference.split('-')[1]
                    credito = Credito.objects.get(id=credito_id)

                    if status == 'APPROVED':
                        # Registrar el pago
                        monto_decimal = Decimal(amount_in_cents) / 100

                        # Verificar si el pago ya existe (evitar duplicados)
                        pago_existente = HistorialPago.objects.filter(
                            referencia_pago=reference
                        ).first()

                        if not pago_existente:
                            with transaction.atomic():
                                HistorialPago.objects.create(
                                    credito=credito,
                                    monto=monto_decimal,
                                    referencia_pago=reference,
                                    estado=HistorialPago.EstadoPago.EXITOSO
                                )
                                credit_services.actualizar_saldo_tras_pago(credito, monto_decimal)
                                logger.info(f"Pago de ${monto_decimal} registrado exitosamente para crédito {credito_id}")
                        else:
                            logger.info(f"Pago con referencia {reference} ya existe, omitiendo.")

                    elif status == 'DECLINED' or status == 'ERROR':
                        logger.warning(f"Pago rechazado para crédito {credito_id}: {status}")
                        # Opcionalmente registrar el intento fallido

                except (IndexError, Credito.DoesNotExist) as e:
                    logger.error(f"Error al procesar referencia {reference}: {str(e)}")

        return JsonResponse({'status': 'ok'}, status=200)

    except json.JSONDecodeError as e:
        logger.error(f"Error al decodificar JSON del webhook: {str(e)}")
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error inesperado en webhook de WOMPI: {str(e)}")
        return JsonResponse({'error': 'Internal server error'}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def zapsign_webhook_view(request):
    """
    Webhook de ZapSign para eventos de firma de pagarés.
    Este endpoint debe estar accesible públicamente sin autenticación.
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError as e:
        logger.error(f"Error al decodificar JSON del webhook de ZapSign: {str(e)}")
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    doc_token = payload.get('token') or payload.get('doc_token')
    event = payload.get('event') or payload.get('event_type') or ''

    ip_address = request.META.get('REMOTE_ADDR') or '0.0.0.0'
    headers = {
        key: str(value)
        for key, value in request.META.items()
        if key.startswith('HTTP_')
    }

    webhook_log = ZapSignWebhookLog.objects.create(
        doc_token=doc_token or '',
        event=event or '',
        payload=payload,
        headers=headers,
        ip_address=ip_address,
        signature_valid=False,
        processed=False
    )

    secret_expected = getattr(settings, 'ZAPSIGN_WEBHOOK_SECRET', '') or ''
    header_name = getattr(settings, 'ZAPSIGN_WEBHOOK_HEADER', 'X-ZapSign-Secret') or 'X-ZapSign-Secret'
    if secret_expected:
        secret_received = request.headers.get(header_name, '')
        if header_name.lower() == 'authorization' and secret_received.lower().startswith('bearer '):
            secret_received = secret_received[7:]

        if secret_received != secret_expected:
            webhook_log.error_message = "Secret token inv?lido"
            webhook_log.save(update_fields=['error_message'])
            logger.warning(f"Webhook ZapSign rechazado: secret inv?lido desde {ip_address}")
            return JsonResponse({'error': 'Unauthorized'}, status=403)

    webhook_log.signature_valid = True
    webhook_log.save(update_fields=['signature_valid'])


    try:
        with transaction.atomic():
            if event == 'doc_signed':
                if not doc_token:
                    webhook_log.error_message = "Falta token del documento"
                    webhook_log.save(update_fields=['error_message'])
                    return JsonResponse({'error': 'Missing document token'}, status=400)

                pagare = Pagare.objects.select_for_update().get(zapsign_doc_token=doc_token)

                if pagare.estado == Pagare.EstadoPagare.SIGNED:
                    webhook_log.processed = True
                    webhook_log.save(update_fields=['processed'])
                    return JsonResponse({'status': 'already_processed'}, status=200)

                credito = pagare.credito
                if credito.estado != Credito.EstadoCredito.PENDIENTE_FIRMA:
                    webhook_log.error_message = f"Estado inválido del crédito: {credito.estado}"
                    webhook_log.save(update_fields=['error_message'])
                    return JsonResponse({'error': 'Invalid credit state'}, status=400)

                pagare.estado = Pagare.EstadoPagare.SIGNED
                pagare.fecha_firma = timezone.now()
                pagare.zapsign_status = payload.get('status')
                signed_url = payload.get('signed_file_url') or payload.get('signed_file')
                if signed_url:
                    pagare.zapsign_signed_file_url = signed_url
                signers = payload.get('signers') or []
                if signers:
                    ip_firmante = signers[0].get('ip') or signers[0].get('ip_address')
                    if ip_firmante:
                        pagare.ip_firmante = ip_firmante
                pagare.evidencias = payload
                pagare.save()

                credit_services.gestionar_cambio_estado_credito(
                    credito=credito,
                    nuevo_estado=Credito.EstadoCredito.FIRMADO,
                    motivo="Pagaré firmado por ZapSign"
                )
                credit_services.iniciar_proceso_desembolso(credito)

                webhook_log.processed = True
                webhook_log.save(update_fields=['processed'])
                return JsonResponse({'status': 'ok'}, status=200)

            if event == 'doc_refused':
                if not doc_token:
                    webhook_log.error_message = "Falta token del documento"
                    webhook_log.save(update_fields=['error_message'])
                    return JsonResponse({'error': 'Missing document token'}, status=400)

                pagare = Pagare.objects.select_for_update().get(zapsign_doc_token=doc_token)
                pagare.estado = Pagare.EstadoPagare.REFUSED
                pagare.fecha_rechazo = timezone.now()
                pagare.zapsign_status = payload.get('status')
                pagare.evidencias = payload
                pagare.save()

                webhook_log.processed = True
                webhook_log.save(update_fields=['processed'])
                return JsonResponse({'status': 'refused_recorded'}, status=200)

            webhook_log.processed = True
            webhook_log.save(update_fields=['processed'])
            return JsonResponse({'status': 'event_ignored'}, status=200)

    except Pagare.DoesNotExist:
        webhook_log.error_message = f"Documento no encontrado: {doc_token}"
        webhook_log.save(update_fields=['error_message'])
        return JsonResponse({'error': 'Document not found'}, status=404)
    except Exception as e:
        webhook_log.error_message = str(e)
        webhook_log.save(update_fields=['error_message'])
        logger.error(f"Error procesando webhook ZapSign {doc_token}: {str(e)}")
        return JsonResponse({'error': 'Internal server error'}, status=500)


#? ============================================================================
#? VISTAS DE BILLETERA DIGITAL - USUARIO
#? ============================================================================

@login_required
def billetera_digital_view(request):
    """
    Vista principal de la billetera digital del usuario.
    Muestra saldo, estadísticas, movimientos e impacto social.
    """
    context = credit_services.get_billetera_context(request.user)
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
    
    return render(request, 'admin/billetera_dashboard.html', context)


@staff_member_required
@require_POST
def aprobar_consignacion_view(request, movimiento_id):
    """
    Aprueba una consignación pendiente usando el servicio centralizado.
    """
    nota_admin = request.POST.get('nota_admin', 'Consignación aprobada')
    try:
        movimiento = credit_services.gestionar_consignacion_billetera(
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
        movimiento = credit_services.gestionar_consignacion_billetera(
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
            movimiento = credit_services.crear_ajuste_manual_billetera(
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


#? ===================================================================
#? VISTAS DE ABONOS AL CRÉDITO Y REESTRUCTURACIÓN
#? ===================================================================

@login_required
@require_http_methods(["GET"])
def calcular_pago_total_view(request, credito_id):
    """
    API endpoint que calcula el monto total para liquidar completamente el crédito.

    Calcula: Capital Pendiente + Intereses Acumulados

    Returns:
        JSON con:
        - capital_pendiente
        - intereses_acumulados
        - total_pagar
    """
    try:
        credito = get_object_or_404(Credito, id=credito_id, usuario=request.user)

        # Validar que el crédito esté activo
        if credito.estado not in [Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA]:
            return JsonResponse({
                'success': False,
                'error': 'El crédito debe estar activo para calcular el pago total.'
            }, status=400)

        # Calcular el total desde la tabla de amortización pendiente
        cuotas_pendientes = credito.tabla_amortizacion.filter(pagada=False)

        capital_pendiente = sum(
            (cuota.capital_a_pagar for cuota in cuotas_pendientes),
            Decimal('0.00')
        )
        intereses_totales = sum(
            (cuota.interes_a_pagar for cuota in cuotas_pendientes),
            Decimal('0.00')
        )
        total_pagar = sum(
            (cuota.valor_cuota for cuota in cuotas_pendientes),
            Decimal('0.00')
        )

        return JsonResponse({
            'success': True,
            'capital_pendiente': float(capital_pendiente),
            'intereses_acumulados': float(intereses_totales),
            'total_pagar': float(total_pagar)
        })

    except Exception as e:
        logger.error(f"Error al calcular pago total para crédito {credito_id}: {e}")
        return JsonResponse({
            'success': False,
            'error': 'Error al calcular el pago total.'
        }, status=500)


def analizar_abono_credito_view(request, credito_id):
    """
    API endpoint que analiza un abono propuesto y devuelve información
    sobre la reestructuración, ahorro de intereses, etc.

    POST params:
        - tipo_abono: 'CUOTAS' o 'CAPITAL'
        - num_cuotas: número de cuotas a pagar (si tipo='CUOTAS')
        - monto_capital: monto a abonar a capital (si tipo='CAPITAL')

    Returns:
        JSON con análisis del abono
    """
    try:
        content_type = request.headers.get('Content-Type', '')
        is_json = content_type.startswith('application/json')
        data = request.POST
        if is_json:
            try:
                data = json.loads(request.body.decode('utf-8') or '{}')
            except json.JSONDecodeError:
                return JsonResponse({
                    'success': False,
                    'error': 'JSON inválido.'
                }, status=400)

        credito = get_object_or_404(Credito, id=credito_id, usuario=request.user)

        # Validar que el crédito esté activo
        if credito.estado not in [Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA]:
            return JsonResponse({
                'success': False,
                'error': 'El crédito debe estar activo para realizar abonos.'
            }, status=400)

        tipo_abono_ui = data.get('tipo_abono')  # 'CUOTAS' o 'CAPITAL'

        if tipo_abono_ui == 'CUOTAS':
            num_cuotas = int(data.get('num_cuotas') or 1)

            # Validar número de cuotas
            cuotas_restantes = credit_services.calcular_cuotas_restantes(credito)
            if num_cuotas > cuotas_restantes:
                return JsonResponse({
                    'success': False,
                    'error': f'Solo quedan {cuotas_restantes} cuotas pendientes.'
                }, status=400)

            if num_cuotas < 1:
                return JsonResponse({
                    'success': False,
                    'error': 'Debe pagar al menos 1 cuota.'
                }, status=400)

            # Calcular monto total de las cuotas seleccionadas
            monto_abono = credito.valor_cuota * num_cuotas

            # Determinar tipo de abono para el servicio
            if num_cuotas <= 2:
                tipo_abono_servicio = 'NORMAL'
            else:
                tipo_abono_servicio = 'MAYOR'

        elif tipo_abono_ui == 'CAPITAL':
            # REGLA DE ORO: Solo 1 abono a capital por crédito
            from .models import ReestructuracionCredito

            ya_tiene_abono_capital = ReestructuracionCredito.objects.filter(
                credito=credito,
                tipo_abono='CAPITAL'
            ).exists()

            if ya_tiene_abono_capital:
                return JsonResponse({
                    'success': False,
                    'error': 'Ya realizó un abono a capital en este crédito. Solo se permite 1 abono a capital por crédito.'
                }, status=400)

            monto_abono = Decimal(str(data.get('monto_capital') or '0'))

            if monto_abono <= 0:
                return JsonResponse({
                    'success': False,
                    'error': 'El monto debe ser mayor a cero.'
                }, status=400)

            if monto_abono > credito.capital_pendiente:
                return JsonResponse({
                    'success': False,
                    'error': f'El monto no puede ser mayor al capital pendiente (${credito.capital_pendiente:,.0f}).'
                }, status=400)

            tipo_abono_servicio = 'CAPITAL'

        else:
            return JsonResponse({
                'success': False,
                'error': 'Tipo de abono inválido.'
            }, status=400)

        # Analizar el abono
        analisis = credit_services.analizar_abono_credito(credito, monto_abono, tipo_abono_servicio)

        plan_actual = analisis['plan_actual']
        plan_nuevo = analisis['plan_nuevo']
        valor_cuota_actual = float(credito.valor_cuota or 0)
        capital_actual = float(credito.capital_pendiente or plan_actual.get('total_capital', 0))

        capital_nuevo = plan_nuevo.get('total_capital', 0)
        if tipo_abono_servicio == 'CAPITAL':
            capital_nuevo = float(max(Decimal('0.00'), (credito.capital_pendiente or Decimal('0.00')) - monto_abono))

        valor_cuota_nuevo = valor_cuota_actual
        if plan_nuevo.get('cuotas'):
            valor_cuota_nuevo = float(plan_nuevo['cuotas'][0]['cuota'])

        plan_actual_ui = {
            'cuotas_restantes': plan_actual.get('num_cuotas', 0),
            'valor_cuota': valor_cuota_actual,
            'capital_pendiente': capital_actual,
            'total_intereses': float(plan_actual.get('total_intereses', 0))
        }
        plan_nuevo_ui = {
            'cuotas_restantes': plan_nuevo.get('num_cuotas', 0),
            'valor_cuota': valor_cuota_nuevo,
            'capital_pendiente': float(capital_nuevo),
            'total_intereses': float(plan_nuevo.get('total_intereses', 0))
        }

        # Preparar respuesta
        return JsonResponse({
            'success': True,
            'monto_abono': float(monto_abono),
            'tipo_abono': tipo_abono_servicio,
            'requiere_reestructuracion': analisis['requiere_reestructuracion'],
            'ahorro_intereses': analisis['ahorro_intereses'],
            'plazo_actual': analisis['plazo_actual'],
            'nuevo_plazo': analisis['nuevo_plazo'],
            'cuota_actual': analisis['cuota_actual'],
            'nueva_cuota': analisis['nueva_cuota'],
            'advertencia': analisis['advertencia'],
            'plan_actual': plan_actual_ui,
            'plan_nuevo': plan_nuevo_ui
        })

    except ValueError as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en los datos: {str(e)}'
        }, status=400)
    except Exception as e:
        logger.error(f"Error al analizar abono para crédito {credito_id}: {e}")
        return JsonResponse({
            'success': False,
            'error': 'Error al procesar la solicitud.'
        }, status=500)


@login_required
@require_POST
def confirmar_abono_credito_view(request, credito_id):
    """
    Confirma y aplica un abono al crédito después de que el usuario
    ha revisado el análisis y aceptado los términos.

    POST params:
        - tipo_abono: 'CUOTAS' o 'CAPITAL'
        - num_cuotas: número de cuotas (si tipo='CUOTAS')
        - monto_capital: monto (si tipo='CAPITAL')
        - confirmacion: 'true' para confirmar
    """
    try:
        content_type = request.headers.get('Content-Type', '')
        is_json = content_type.startswith('application/json')
        data = request.POST
        if is_json:
            try:
                data = json.loads(request.body.decode('utf-8') or '{}')
            except json.JSONDecodeError:
                return JsonResponse({
                    'success': False,
                    'error': 'JSON inválido.'
                }, status=400)

        credito = get_object_or_404(Credito, id=credito_id, usuario=request.user)

        # Validar confirmación
        if data.get('confirmacion') != 'true':
            if is_json:
                return JsonResponse({
                    'success': False,
                    'error': 'Debe confirmar el abono antes de proceder.'
                }, status=400)
            messages.error(request, 'Debe confirmar el abono antes de proceder.')
            return redirect('usuariocreditos:dashboard_emprendimiento')

        # Validar que el crédito esté activo
        if credito.estado not in [Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA]:
            messages.error(request, 'El crédito debe estar activo para realizar abonos.')
            return redirect('usuariocreditos:dashboard_emprendimiento')

        tipo_abono_ui = data.get('tipo_abono')

        # Calcular monto y tipo de abono
        if tipo_abono_ui == 'CUOTAS':
            num_cuotas = int(data.get('num_cuotas') or 1)
            monto_abono = credito.valor_cuota * num_cuotas
            tipo_abono_servicio = 'NORMAL' if num_cuotas <= 2 else 'MAYOR'
            descripcion = f'{num_cuotas} cuota(s)'
        elif tipo_abono_ui == 'CAPITAL':
            # REGLA DE ORO: Validar que no haya un abono a capital previo
            from .models import ReestructuracionCredito

            ya_tiene_abono_capital = ReestructuracionCredito.objects.filter(
                credito=credito,
                tipo_abono='CAPITAL'
            ).exists()

            if ya_tiene_abono_capital:
                messages.error(request, 'Ya realizó un abono a capital en este crédito. Solo se permite 1 abono a capital por crédito.')
                return redirect('usuariocreditos:dashboard_emprendimiento')

            monto_abono = Decimal(str(data.get('monto_capital') or '0'))
            tipo_abono_servicio = 'CAPITAL'
            descripcion = f'abono a capital'
        else:
            if is_json:
                return JsonResponse({
                    'success': False,
                    'error': 'Tipo de abono inválido.'
                }, status=400)
            messages.error(request, 'Tipo de abono inválido.')
            return redirect('usuariocreditos:dashboard_emprendimiento')

        # Generar referencia única
        import uuid
        referencia = f"ABONO-{credito.numero_credito}-{uuid.uuid4().hex[:8].upper()}"

        # Aplicar el abono
        pago, reestructuracion = credit_services.aplicar_abono_credito(
            credito=credito,
            monto_abono=monto_abono,
            tipo_abono=tipo_abono_servicio,
            usuario=request.user,
            referencia_pago=referencia
        )

        # Crear notificación
        if reestructuracion:
            Notificacion.objects.create(
                usuario=request.user,
                tipo=Notificacion.TipoNotificacion.SISTEMA,
                titulo='Abono aplicado con reestructuración',
                mensaje=(
                    f'Se aplicó un abono de ${monto_abono:,.0f} ({descripcion}) a su crédito {credito.numero_credito}. '
                    f'Su plan de pagos ha sido reestructurado. '
                    f'Ahorro en intereses: ${reestructuracion.ahorro_intereses:,.0f}. '
                    f'Nuevo plazo: {reestructuracion.plazo_restante_nuevo} cuotas.'
                ),
                url=f'/emprendimiento/mi-credito/'
            )
            messages.success(
                request,
                f'¡Abono aplicado exitosamente! Ahorrará ${reestructuracion.ahorro_intereses:,.0f} en intereses. '
                f'Su nuevo plan tiene {reestructuracion.plazo_restante_nuevo} cuotas.'
            )
        else:
            Notificacion.objects.create(
                usuario=request.user,
                tipo=Notificacion.TipoNotificacion.PAGO_RECIBIDO,
                titulo='Pago recibido',
                mensaje=(
                    f'Se registró su pago de ${monto_abono:,.0f} ({descripcion}) '
                    f'para el crédito {credito.numero_credito}. '
                    f'Nuevo saldo: ${credito.saldo_pendiente:,.0f}.'
                ),
                url=f'/emprendimiento/mi-credito/'
            )
            messages.success(
                request,
                f'Pago de ${monto_abono:,.0f} aplicado exitosamente. '
                f'Nuevo saldo: ${credito.saldo_pendiente:,.0f}.'
            )

        logger.info(
            f"Abono aplicado por usuario {request.user.username} al crédito {credito.numero_credito}. "
            f"Monto: ${monto_abono}, Tipo: {tipo_abono_servicio}, Referencia: {referencia}"
        )

        if is_json:
            return JsonResponse({
                'success': True,
                'monto_abono': float(monto_abono),
                'tipo_abono': tipo_abono_servicio
            })
        return redirect('usuariocreditos:dashboard_emprendimiento')

    except Exception as e:
        logger.error(f"Error al confirmar abono para crédito {credito_id}: {e}")
        if is_json:
            return JsonResponse({
                'success': False,
                'error': f'Error al procesar el abono: {str(e)}'
            }, status=500)
        messages.error(request, f'Error al procesar el abono: {str(e)}')
        return redirect('usuariocreditos:dashboard_emprendimiento')


@login_required
def historial_reestructuraciones_view(request, credito_id):
    """
    Muestra el historial de reestructuraciones realizadas a un crédito.
    """
    from .models import ReestructuracionCredito

    credito = get_object_or_404(Credito, id=credito_id, usuario=request.user)

    reestructuraciones = ReestructuracionCredito.objects.filter(
        credito=credito
    ).select_related('aprobado_por', 'pago_relacionado').order_by('-fecha_reestructuracion')

    context = {
        'credito': credito,
        'reestructuraciones': reestructuraciones,
    }

    return render(request, 'emprendimiento/historial_reestructuraciones.html', context)
