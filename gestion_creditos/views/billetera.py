from .common import *


@login_required
def billetera_digital_view(request):
    """
    Vista principal de la billetera digital del usuario.
    Muestra saldo, estadísticas, movimientos e impacto social.
    """
    context = credit_services.get_billetera_context(request.user, request=request)
    return render(request, 'Billetera/billetera_digital.html', context)


@login_required
@require_POST
def marcar_notificaciones_leidas_view(request):
    Notificacion.objects.filter(
        usuario=request.user,
        leida=False,
    ).update(
        leida=True,
        fecha_leida=timezone.now(),
    )
    return JsonResponse({'success': True})


@login_required
@require_POST
def consignacion_offline_view(request):
    """
    Procesa una consignaci?n offline (con comprobante).
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
                movimiento.descripcion = 'Consignaci?n offline pendiente de aprobaci?n'
            
            movimiento.save()
            
            messages.success(request, '¡Comprobante enviado! Tu consignaci?n será revisada pronto.')
            
            return JsonResponse({
                'success': True,
                'mensaje': 'Consignaci?n enviada exitosamente',
                'referencia': movimiento.referencia
            })
    else:
        return JsonResponse({
            'success': False,
            'errors': form.errors
        }, status=400)


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
    Aprueba una consignaci?n pendiente usando el servicio centralizado.
    """
    nota_admin = request.POST.get('nota_admin', 'Consignaci?n aprobada')
    try:
        movimiento = credit_services.gestionar_consignacion_billetera(
            movimiento_id=movimiento_id,
            es_aprobado=True,
            usuario_admin=request.user,
            nota=nota_admin
        )
        messages.success(
            request, 
            f'Consignaci?n de ${movimiento.monto:,.0f} aprobada para {movimiento.cuenta.usuario.get_full_name()}'
        )
        return JsonResponse({
            'success': True,
            'nuevo_saldo': float(movimiento.cuenta.saldo_disponible)
        })
    except Exception as e:
        logger.error(f"Error al aprobar consignaci?n {movimiento_id}: {e}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@staff_member_required
@require_POST
def rechazar_consignacion_view(request, movimiento_id):
    """
    Rechaza una consignaci?n pendiente usando el servicio centralizado.
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
            f'Consignaci?n de {movimiento.cuenta.usuario.get_full_name()} rechazada.'
        )
        return JsonResponse({'success': True})
    except Exception as e:
        logger.error(f"Error al rechazar consignaci?n {movimiento_id}: {e}")
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
