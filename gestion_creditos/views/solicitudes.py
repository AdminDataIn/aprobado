from .common import *
from .common import _rate_limit_simple


@login_required(login_url='/libranza/login/')
def solicitud_credito_libranza_view(request):
    current_flow = get_user_flow(request.user)
    if current_flow and current_flow != ProductAccessProfile.ProductFlow.LIBRANZA:
        messages.error(
            request,
            f'Tu cuenta pertenece al flujo de {get_flow_label(current_flow)} y no puede solicitar Libranza.'
        )
        return redirect(get_flow_home_path(current_flow))

    try:
        assign_user_flow(request.user, ProductAccessProfile.ProductFlow.LIBRANZA)
    except ProductFlowConflict as exc:
        current_flow = exc.args[0] if exc.args else None
        messages.error(
            request,
            f'Tu cuenta pertenece al flujo de {get_flow_label(current_flow)} y no puede solicitar Libranza.'
        )
        return redirect(get_flow_home_path(current_flow))

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    vinculo_laboral = obtener_vinculo_laboral_activo(request.user)
    if request.method == 'POST':
        form = CreditoLibranzaForm(request.POST, request.FILES, vinculo_laboral=vinculo_laboral)
        if form.is_valid():
            try:
                with transaction.atomic():
                    credito_principal = Credito.objects.create(
                        usuario=request.user,
                        linea=Credito.LineaCredito.LIBRANZA,
                        estado=Credito.EstadoCredito.EN_REVISION,
                        monto_solicitado=form.cleaned_data['valor_credito'],
                        plazo_solicitado=form.cleaned_data['plazo']
                    )
                    credito_libranza_detalle = form.save(commit=False)
                    credito_libranza_detalle.credito = credito_principal
                    credito_libranza_detalle.save()
                    # El parsing de certificado se ejecuta después del save para reutilizar
                    # el FileField persistido y dejar trazabilidad para una futura fase OCR.
                    procesar_certificado_bancario(credito_libranza_detalle)
            except IntegrityError:
                form.add_error(
                    'cedula',
                    'Ya existe una solicitud registrada con esta cedula. No es posible crear una nueva solicitud.'
                )
                if is_ajax:
                    return JsonResponse({'success': False, 'errors': form.errors}, status=400)
                tasa_libranza = obtener_tasa_credito(Credito.LineaCredito.LIBRANZA)
                tasa_libranza_decimal = tasa_libranza / Decimal('100')
                return render(request, 'gestion_creditos/solicitud_libranza.html', {
                    'form': form,
                    'vinculo_laboral': vinculo_laboral,
                    'libranza_tasa_mensual': tasa_libranza,
                    'libranza_tasa_decimal': tasa_libranza_decimal,
                    'libranza_tasa_decimal_js': format(tasa_libranza_decimal, 'f'),
                })

            try:
                from gestion_creditos.email_service import (
                    enviar_notificacion_cambio_estado,
                    enviar_notificacion_interna_nueva_solicitud,
                )
                enviar_notificacion_cambio_estado(
                    credito_principal,
                    Credito.EstadoCredito.EN_REVISION,
                    'Solicitud de credito recibida y en proceso de revision'
                )
                enviar_notificacion_interna_nueva_solicitud(credito_principal)
            except Exception as e:
                logger.error(f"Error al enviar email de confirmacion para credito {credito_principal.id}: {e}")

            try:
                from gestion_creditos.email_service import enviar_notificacion_solicitud_libranza_empresa
                from gestion_creditos.models import Notificacion

                pagadores = PerfilPagador.objects.filter(
                    empresa=credito_libranza_detalle.empresa,
                    es_pagador=True
                ).select_related('usuario')

                if pagadores:
                    dashboard_url = request.build_absolute_uri(reverse('pagador:dashboard'))
                    login_url = request.build_absolute_uri(reverse('pagador:login'))

                    for perfil in pagadores:
                        if not perfil.usuario.email:
                            continue

                        enviar_notificacion_solicitud_libranza_empresa(
                            destinatario=perfil.usuario.email,
                            empresa=credito_libranza_detalle.empresa,
                            credito=credito_principal,
                            detalle=credito_libranza_detalle,
                            dashboard_url=dashboard_url,
                            login_url=login_url
                        )

                        Notificacion.objects.create(
                            usuario=perfil.usuario,
                            tipo=Notificacion.TipoNotificacion.SISTEMA,
                            titulo='Nueva solicitud de libranza',
                            mensaje=(
                                f"{credito_libranza_detalle.nombre_completo} "
                                f"solicito ${credito_principal.monto_solicitado:,.0f} "
                                f"a {credito_principal.plazo_solicitado} meses."
                            ),
                            url=reverse('pagador:dashboard')
                        )
            except Exception as e:
                logger.error(f"Error al notificar a pagadores para credito {credito_principal.id}: {e}")

            if is_ajax:
                return JsonResponse({'success': True})
            return redirect('usuariocreditos:dashboard_libranza')
        else:
            if is_ajax:
                return JsonResponse({'success': False, 'errors': form.errors}, status=400)
    else:
        form = CreditoLibranzaForm(vinculo_laboral=vinculo_laboral)

    tasa_libranza = obtener_tasa_credito(Credito.LineaCredito.LIBRANZA)
    tasa_libranza_decimal = tasa_libranza / Decimal('100')
    return render(request, 'gestion_creditos/solicitud_libranza.html', {
        'form': form,
        'vinculo_laboral': vinculo_laboral,
        'libranza_tasa_mensual': tasa_libranza,
        'libranza_tasa_decimal': tasa_libranza_decimal,
        'libranza_tasa_decimal_js': format(tasa_libranza_decimal, 'f'),
    })


@require_http_methods(["GET"])
def buscar_empresas_convenio_view(request):
    if not _rate_limit_simple(request, 'buscar-empresas-convenio', limit=20, window=60):
        return JsonResponse({'results': [], 'error': 'Demasiadas consultas. Intenta de nuevo en un minuto.'}, status=429)

    query = (request.GET.get('q') or '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})

    empresas = (
        Empresa.objects
        .filter(convenio_activo=True)
        .exclude(tipo_empresa=Empresa.TipoEmpresa.MARKETPLACE_EXTERNA)
        .filter(
            Q(nombre__icontains=query) |
            Q(razon_social__icontains=query) |
            Q(nit__icontains=query)
        )
        .order_by('nombre')[:8]
    )

    return JsonResponse({
        'results': [
            {
                'id': empresa.id,
                'nombre': empresa.nombre,
                'razon_social': empresa.razon_social,
                'nit': empresa.nit,
            }
            for empresa in empresas
        ]
    })


@require_http_methods(["POST"])
def simular_adelanto_nomina_view(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        payload = request.POST

    simulacion = simular_adelanto_nomina(
        salario=payload.get('salario') or payload.get('salario_base') or '0',
        auxilio_transporte=payload.get('auxilio_transporte') or '0',
        descuentos=payload.get('descuentos') or '0',
        tasa_mensual=getattr(settings, 'ADELANTO_NOMINA_TASA_MENSUAL', '1.9'),
        porcentaje_comision=getattr(settings, 'ADELANTO_NOMINA_COMISION_PERCENT', '10'),
    )
    return JsonResponse(
        {
            key: f"{value:.2f}" if isinstance(value, Decimal) else value
            for key, value in simulacion.items()
        }
    )


@login_required(login_url='/libranza/login/')
@require_http_methods(["GET", "POST"])
def solicitud_adelanto_nomina_view(request):
    current_flow = get_user_flow(request.user)
    if current_flow and current_flow != ProductAccessProfile.ProductFlow.LIBRANZA:
        messages.error(
            request,
            f'Tu cuenta pertenece al flujo de {get_flow_label(current_flow)} y no puede solicitar adelanto de nomina.'
        )
        return redirect(get_flow_home_path(current_flow))

    try:
        assign_user_flow(request.user, ProductAccessProfile.ProductFlow.LIBRANZA)
    except ProductFlowConflict as exc:
        current_flow = exc.args[0] if exc.args else None
        messages.error(
            request,
            f'Tu cuenta pertenece al flujo de {get_flow_label(current_flow)} y no puede solicitar adelanto de nomina.'
        )
        return redirect(get_flow_home_path(current_flow))

    eligibility = evaluar_elegibilidad_adelanto(request.user)
    vinculo = eligibility['vinculo']
    simulacion = eligibility.get('simulation') or simular_adelanto_nomina()
    adelanto_actual = (
        Credito.objects.filter(
            usuario=request.user,
            linea=Credito.LineaCredito.ADELANTO_NOMINA,
        )
        .select_related('detalle_adelanto_nomina__vinculo_laboral__empresa')
        .order_by('-fecha_solicitud')
        .first()
    )

    if request.method == 'POST':
        if not eligibility['eligible']:
            messages.error(request, eligibility['reason'])
            return redirect('libranza:adelanto_nomina')

        form = CreditoAdelantoNominaForm(request.POST, vinculo_laboral=vinculo)
        if form.is_valid():
            monto = form.cleaned_data['monto_solicitado']
            observaciones = form.cleaned_data.get('observaciones', '')
            simulation = simular_adelanto_nomina(
                salario=vinculo.salario_base_mensual or Decimal('0.00'),
                auxilio_transporte=vinculo.auxilio_transporte_mensual,
                descuentos=vinculo.descuentos_fijos_mensuales,
            )
            simulation['monto_solicitado'] = monto
            percentage_commission = Decimal(str(getattr(settings, 'ADELANTO_NOMINA_COMISION_PERCENT', '10')))
            comision = (monto * percentage_commission / Decimal('100')).quantize(Decimal('0.01'))
            iva_comision = (comision * Decimal('0.19')).quantize(Decimal('0.01'))
            interes = (monto * obtener_tasa_credito(Credito.LineaCredito.ADELANTO_NOMINA) / Decimal('100')).quantize(Decimal('0.01'))
            neto_a_recibir = max(Decimal('0.00'), monto - comision - iva_comision)
            total_a_pagar = monto + comision + iva_comision + interes
            with transaction.atomic():
                credito = Credito.objects.create(
                    usuario=request.user,
                    linea=Credito.LineaCredito.ADELANTO_NOMINA,
                    estado=Credito.EstadoCredito.EN_REVISION,
                    monto_solicitado=monto,
                    plazo_solicitado=1,
                    monto_aprobado=monto,
                    plazo=1,
                    tasa_interes=obtener_tasa_credito(Credito.LineaCredito.ADELANTO_NOMINA),
                    comision=comision,
                    iva_comision=iva_comision,
                    total_a_pagar=total_a_pagar,
                    saldo_pendiente=total_a_pagar,
                    capital_pendiente=monto,
                    valor_cuota=total_a_pagar,
                )
                CreditoAdelantoNomina.objects.create(
                    credito=credito,
                    vinculo_laboral=vinculo,
                    monto_solicitado=monto,
                    monto_maximo_calculado=simulation['monto_bruto_adelanto'],
                    salario_base_usado=vinculo.salario_base_mensual,
                    dias_adelanto=5,
                    motivo_bloqueo=observaciones[:255] if observaciones else '',
                )
                HistorialEstado.objects.create(
                    credito=credito,
                    estado_anterior='',
                    estado_nuevo=Credito.EstadoCredito.EN_REVISION,
                    motivo='Solicitud de adelanto de nomina creada por cliente.',
                    usuario_modificacion=request.user,
                )
            messages.success(request, 'Tu solicitud de adelanto de nomina fue enviada al pagador para revision.')
            return redirect('libranza:adelanto_nomina')
    else:
        form = CreditoAdelantoNominaForm(vinculo_laboral=vinculo, initial={
            'monto_solicitado': simulacion['monto_bruto_adelanto'],
        })

    return render(request, 'gestion_creditos/solicitud_adelanto_nomina.html', {
        'form': form,
        'eligibility': eligibility,
        'vinculo': vinculo,
        'simulacion': simulacion,
        'adelanto_actual': adelanto_actual,
        'monto_maximo': simulacion['monto_bruto_adelanto'],
    })


@login_required(login_url='/emprendimiento/login/')
def solicitud_credito_emprendimiento_view(request):
    current_flow = get_user_flow(request.user)
    if current_flow and current_flow != ProductAccessProfile.ProductFlow.EMPRENDIMIENTO:
        messages.error(
            request,
            f'Tu cuenta pertenece al flujo de {get_flow_label(current_flow)} y no puede solicitar Emprendimiento.'
        )
        return redirect(get_flow_home_path(current_flow))

    try:
        assign_user_flow(request.user, ProductAccessProfile.ProductFlow.EMPRENDIMIENTO)
    except ProductFlowConflict as exc:
        current_flow = exc.args[0] if exc.args else None
        messages.error(
            request,
            f'Tu cuenta pertenece al flujo de {get_flow_label(current_flow)} y no puede solicitar Emprendimiento.'
        )
        return redirect(get_flow_home_path(current_flow))

    # Vista para procesar solicitudes de crédito de emprendimiento.
    # Integra scoring de imágenes con IA y evaluación de motivación.
    if request.method == 'POST':
        # Validar autenticaci?n para solicitudes AJAX
        if not request.user.is_authenticated:
            return JsonResponse({'success': False, 'error': 'Authentication required'}, status=403)

        # Detectar si es solicitud AJAX o viene del formulario HTML con imágenes m?ltiples
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        # Si viene del formulario con imágenes m?ltiples (del trabajo del compañero)
        if 'fotos_neg' in request.FILES:
            try:
                # Capturar imágenes m?ltiples
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

                # Validar formato y tama?o de imágenes
                for imagen in imagenes_negocio:
                    if not imagen.content_type.startswith('image/'):
                        return JsonResponse({
                            'success': False,
                            'error': f'El archivo {imagen.name} no es una imagen válida'
                        }, status=400)
                    if imagen.size > 10 * 1024 * 1024:  # 10MB
                        return JsonResponse({
                            'success': False,
                            'error': f'La imagen {imagen.name} excede el tama?o máximo de 10MB'
                        }, status=400)

                # SCORING DE IM?GENES CON IA
                from ..scoring_client import scoring_client

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
                from ..models import CreditoEmprendimiento
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
                from ..models import ImagenNegocio
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
                    from ..email_service import enviar_notificacion_cambio_estado, enviar_notificacion_interna_nueva_solicitud
                    enviar_notificacion_cambio_estado(
                        credito_principal,
                        Credito.EstadoCredito.EN_REVISION,
                        "Solicitud de crédito recibida y en proceso de revisión"
                    )
                    enviar_notificacion_interna_nueva_solicitud(credito_principal)
                except Exception as e:
                    logger.error(f"Error al enviar email de confirmación: {e}")

                return JsonResponse({
                    'success': True,
                    'suma_estimaciones': puntaje_total,
                    'puntaje_imagenes': puntaje_imagenes,
                    'imagenes_guardadas': len(imagenes_negocio)
                })

            except Exception as e:
                logger.error(f"Error en solicitud con imágenes m?ltiples: {e}")
                return JsonResponse({'success': False, 'error': str(e)}, status=500)

        # Si viene del formulario normal (sin imágenes m?ltiples)
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
                        from ..email_service import enviar_notificacion_cambio_estado, enviar_notificacion_interna_nueva_solicitud
                        enviar_notificacion_cambio_estado(
                            credito_principal,
                            Credito.EstadoCredito.EN_REVISION,
                            "Solicitud de crédito recibida y en proceso de revisión"
                        )
                        enviar_notificacion_interna_nueva_solicitud(credito_principal)
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


@login_required
def iniciar_pago_wompi_emprendimiento_view(request, credito_id):
    """
    Muestra el formulario de pago con WOMPI para clientes de emprendimiento.
    """
    from ..services.wompi_client import WompiClient, WompiAPIException

    if not settings.WOMPI_PUBLIC_KEY or not settings.WOMPI_PRIVATE_KEY:
        messages.error(request, "Configuraci?n WOMPI incompleta. Verifica las llaves en el entorno.")
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

    referencia_pago = f"ABONO-{credito.id}-{timezone.now().strftime('%Y%m%d%H%M%S')}"
    if tipo_pago == 'CUOTA':
        cuota_pendiente = credito.tabla_amortizacion.filter(pagada=False).order_by('numero_cuota').first()
        if not cuota_pendiente:
            messages.error(request, "Este credito no tiene cuotas pendientes por pagar.")
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)
        referencia_pago = f"CUOTA-{credito.id}-{cuota_pendiente.numero_cuota}"
    elif tipo_pago == 'TOTAL':
        referencia_pago = f"TOTAL-{credito.id}"

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
        'referencia_pago': referencia_pago,
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
    from ..services.wompi_client import WompiClient, WompiAPIException

    intent = None

    try:
        payment_method_type = request.POST.get('payment_method')
        credito_id = request.POST.get('credito_id')
        amount_in_cents_raw = request.POST.get('amount_in_cents')
        reference = request.POST.get('reference')
        customer_email = request.POST.get('customer_email')
        acceptance_token = request.POST.get('acceptance_token')
        tipo_pago = (request.POST.get('tipo_pago') or 'CUOTA').upper()

        if not amount_in_cents_raw or not reference:
            messages.error(request, 'Datos de pago incompletos.')
            return redirect('emprendimiento:mi_credito')

        try:
            amount_in_cents = int(amount_in_cents_raw)
        except (TypeError, ValueError):
            messages.error(request, 'Monto invalido.')
            return redirect('emprendimiento:mi_credito')

        credito = get_object_or_404(
            Credito,
            id=credito_id,
            linea=Credito.LineaCredito.EMPRENDIMIENTO,
            usuario=request.user
        )
        monto_decimal = Decimal(amount_in_cents) / 100
        client_ip = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip() or request.META.get('REMOTE_ADDR')
        user_label = request.user.username if request.user.is_authenticated else 'anonymous'
        user_agent = (request.META.get('HTTP_USER_AGENT') or '')[:255]
        referer = (request.META.get('HTTP_REFERER') or '')[:255]
        request_id = (request.META.get('HTTP_X_REQUEST_ID') or '')[:64]
        logger.info(
            'Wompi intento pago: view=emprendimiento credito=%s user=%s ip=%s ref=%s method=%s amount=%s req=%s',
            credito.id,
            user_label,
            client_ip,
            reference,
            payment_method_type,
            amount_in_cents,
            request_id
        )

        if payment_method_type not in ['CARD', 'PSE', 'NEQUI', 'BANCOLOMBIA_TRANSFER']:
            messages.error(request, 'Metodo de pago no valido.')
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

        if not acceptance_token:
            messages.error(request, 'Falta acceptance token.')
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

        if reference and reference.startswith('CUOTA-'):
            parts = reference.split('-')
            if len(parts) < 3 or parts[1] != str(credito.id):
                messages.error(request, 'Referencia de pago invalida.')
                return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)
            try:
                cuota_num = int(parts[2])
            except (TypeError, ValueError):
                messages.error(request, 'Referencia de pago invalida.')
                return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)
            cuota = credito.tabla_amortizacion.filter(numero_cuota=cuota_num).first()
            if not cuota:
                messages.error(request, 'La cuota indicada no existe.')
                return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)
            if cuota.pagada:
                messages.warning(request, 'Esta cuota ya esta pagada.')
                return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

        rate_limit = getattr(settings, 'WOMPI_RATE_LIMIT_ATTEMPTS', 3)
        rate_window = getattr(settings, 'WOMPI_RATE_LIMIT_WINDOW_SECONDS', 60)
        attempt_key = f'wompi:attempts:empr:{credito.id}:{client_ip}'
        attempts = cache.get(attempt_key, 0)
        if attempts >= rate_limit:
            messages.warning(request, 'Demasiados intentos. Espera un momento y vuelve a intentar.')
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)
        cache.set(attempt_key, attempts + 1, timeout=rate_window)

        cooldown_seconds = getattr(settings, 'WOMPI_DUPLICATE_COOLDOWN_SECONDS', 300)
        window_minutes = getattr(settings, 'WOMPI_DUPLICATE_WINDOW_MINUTES', 10)
        lock_key = f'wompi:lock:empr:{credito.id}:{reference}:{amount_in_cents}'
        if not cache.add(lock_key, True, timeout=cooldown_seconds):
            logger.warning(
                'Pago duplicado bloqueado por lock: credito=%s user=%s ip=%s ref=%s',
                credito.id,
                user_label,
                client_ip,
                reference
            )
            messages.warning(request, 'Ya hay un pago en proceso para este credito. Espera unos minutos y verifica el estado.')
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

        reciente = timezone.now() - timedelta(minutes=window_minutes)
        active_intent = WompiIntent.objects.filter(
            credito=credito,
            referencia=reference,
            status__in=[WompiIntent.Estado.CREATED, WompiIntent.Estado.PENDING]
        ).order_by('-created_at').first()
        if active_intent and active_intent.created_at >= reciente:
            messages.warning(request, 'Ya hay un pago en proceso para esta cuota.')
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)
        if WompiIntent.objects.filter(
            credito=credito,
            referencia=reference,
            status=WompiIntent.Estado.APPROVED
        ).exists():
            messages.warning(request, 'Esta cuota ya fue pagada.')
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

        if HistorialPago.objects.filter(referencia_pago=reference).exists():
            messages.warning(request, 'Ya registramos un pago para esta referencia.')
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

        if not customer_email:
            customer_email = request.user.email

        client = WompiClient()

        redirect_url = request.build_absolute_uri(reverse('emprendimiento:pago_wompi_callback'))

        intent = WompiIntent.objects.create(
            credito=credito,
            referencia=reference,
            amount_in_cents=amount_in_cents,
            payment_method=payment_method_type,
            status=WompiIntent.Estado.CREATED,
            usuario=request.user,
            ip_address=client_ip,
            user_agent=user_agent,
            referer=referer
        )

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
                payment_description=f'Pago cuota {reference}'
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
                payment_description=f'Pago cuota {reference}'
            )
            customer_data = None
        else:
            messages.error(request, 'Metodo de pago no valido.')
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

        transaction_data = transaction.get('data', {})
        transaction_id = transaction_data.get('id')
        transaction_status = transaction_data.get('status')
        if intent:
            intent.status = _map_wompi_status_to_intent(transaction_status)
            if transaction_id:
                intent.wompi_transaction_id = transaction_id
            intent.save(update_fields=['status', 'wompi_transaction_id', 'updated_at'])

        request.session['wompi_transaction_id_empr'] = transaction_data.get('id')
        request.session['wompi_credito_id_empr'] = credito_id
        request.session['wompi_reference_empr'] = reference
        request.session['wompi_tipo_pago_empr'] = tipo_pago

        logger.info(f'Wompi transaction response (emprendimiento): {transaction}')

        if payment_method_type in ['PSE', 'NEQUI', 'BANCOLOMBIA_TRANSFER']:
            payment_method_data = transaction_data.get('payment_method', {})
            extra_data = payment_method_data.get('extra', {})
            async_url = extra_data.get('async_payment_url')

            if not async_url:
                logger.warning(f'No async_payment_url en respuesta de Wompi. Payment method data: {payment_method_data}')
                wait_url = f"{reverse('emprendimiento:pago_wompi_callback')}?wait=1&id={transaction_data.get('id')}"
                return redirect(wait_url)

            return redirect(async_url)

        status = transaction_status
        if status == 'APPROVED':
            if tipo_pago == 'CAPITAL':
                if not HistorialPago.objects.filter(referencia_pago=reference).exists():
                    credit_services.aplicar_abono_credito(
                        credito=credito,
                        monto_abono=monto_decimal,
                        tipo_abono='CAPITAL',
                        usuario=request.user,
                        referencia_pago=reference
                    )
                messages.success(request, f'Abono a capital de ${monto_decimal:,.2f} aplicado exitosamente.')
            else:
                pago, created = credit_services.registrar_pago_credito(
                    credito=credito,
                    monto=monto_decimal,
                    referencia_pago=reference,
                    metodo_pago=HistorialPago.MetodoPago.WOMPI,
                    origen_registro=HistorialPago.OrigenRegistro.PASARELA_WOMPI,
                    usuario=request.user,
                    empresa=credito.empresa_relacionada,
                    notas='Pago de cuota procesado por Wompi.',
                )
                messages.success(request, f'Pago de ${monto_decimal:,.2f} procesado exitosamente.')
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)
        if status == 'DECLINED':
            messages.error(request, 'El pago fue rechazado. Por favor intenta con otro metodo.')
            if intent:
                intent.status = WompiIntent.Estado.DECLINED
                intent.save(update_fields=['status', 'updated_at'])
            return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

        messages.warning(request, 'El pago esta pendiente de confirmacion.')
        return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

    except WompiAPIException as e:
        logger.error(f'Error en WOMPI: {str(e)}')
        if intent:
            intent.status = WompiIntent.Estado.ERROR
            intent.save(update_fields=['status', 'updated_at'])
        messages.error(request, f'Error al procesar el pago: {str(e)}')
        return redirect('emprendimiento:mi_credito')
    except Exception as e:
        logger.error(f'Error inesperado: {str(e)}')
        if intent:
            intent.status = WompiIntent.Estado.ERROR
            intent.save(update_fields=['status', 'updated_at'])
        messages.error(request, 'Ocurrio un error inesperado. Por favor intenta de nuevo.')
        return redirect('emprendimiento:mi_credito')


@login_required
@require_http_methods(["GET"])
def pago_wompi_emprendimiento_callback_view(request):
    """
    Callback de WOMPI para clientes de emprendimiento.
    """
    from ..services.wompi_client import WompiClient, WompiAPIException

    transaction_id = request.GET.get('id') or request.session.get('wompi_transaction_id_empr')

    if not transaction_id:
        messages.error(request, 'No se encontro informacion de la transaccion.')
        return redirect('emprendimiento:mi_credito')

    client = WompiClient()

    try:
        transaction = client.get_transaction(transaction_id)
        transaction_data = transaction.get('data', {})
        status = transaction_data.get('status')

        if transaction_id:
            WompiIntent.objects.filter(wompi_transaction_id=transaction_id).update(
                status=_map_wompi_status_to_intent(status)
            )

        try:
            attempt = int(request.GET.get('attempt', 0))
        except (TypeError, ValueError):
            attempt = 0
        max_attempts = 12

        if status not in ['APPROVED', 'DECLINED']:
            if attempt < max_attempts:
                refresh_url = f"{reverse('emprendimiento:pago_wompi_callback')}?wait=1&attempt={attempt + 1}&id={transaction_id}"
                return render(request, 'usuariocreditos/pago_wompi_espera.html', {
                    'refresh_url': refresh_url,
                    'attempts_left': max_attempts - attempt,
                })
            messages.warning(request, f'El pago esta en estado: {status}')
            request.session.pop('wompi_transaction_id_empr', None)
            request.session.pop('wompi_credito_id_empr', None)
            request.session.pop('wompi_reference_empr', None)
            request.session.pop('wompi_tipo_pago_empr', None)
            return redirect('emprendimiento:mi_credito')

        credito_id = request.session.get('wompi_credito_id_empr')
        reference = request.session.get('wompi_reference_empr') or transaction_data.get('reference')
        tipo_pago = (request.session.get('wompi_tipo_pago_empr') or 'CUOTA').upper()

        if not credito_id and reference and '-' in reference:
            parts = reference.split('-')
            if len(parts) >= 2:
                credito_id = parts[1]

        if not credito_id:
            messages.error(request, 'Sesion expirada. Por favor intenta de nuevo.')
            return redirect('emprendimiento:mi_credito')

        credito = get_object_or_404(
            Credito,
            id=credito_id,
            linea=Credito.LineaCredito.EMPRENDIMIENTO,
            usuario=request.user
        )

        if status == 'APPROVED':
            monto_decimal = Decimal(transaction_data.get('amount_in_cents', 0)) / 100
            if tipo_pago == 'CAPITAL':
                if reference and not HistorialPago.objects.filter(referencia_pago=reference).exists():
                    credit_services.aplicar_abono_credito(
                        credito=credito,
                        monto_abono=monto_decimal,
                        tipo_abono='CAPITAL',
                        usuario=request.user,
                        referencia_pago=reference
                    )
                messages.success(request, f'Abono a capital de ${monto_decimal:,.2f} aplicado exitosamente.')
            else:
                pago, created = credit_services.registrar_pago_credito(
                    credito=credito,
                    monto=monto_decimal,
                    referencia_pago=reference,
                    metodo_pago=HistorialPago.MetodoPago.WOMPI,
                    origen_registro=HistorialPago.OrigenRegistro.PASARELA_WOMPI,
                    usuario=request.user,
                    empresa=credito.empresa_relacionada,
                    notas='Pago de cuota procesado por Wompi.',
                )
                messages.success(request, f'Pago de ${monto_decimal:,.2f} procesado exitosamente.')
        elif status == 'DECLINED':
            messages.error(request, 'El pago fue rechazado.')
        else:
            messages.warning(request, f'El pago esta en estado: {status}')

        request.session.pop('wompi_transaction_id_empr', None)
        request.session.pop('wompi_credito_id_empr', None)
        request.session.pop('wompi_reference_empr', None)
        request.session.pop('wompi_tipo_pago_empr', None)

        return redirect('emprendimiento:mi_credito_detalle', credito_id=credito.id)

    except WompiAPIException as e:
        logger.error(f'Error al consultar transaccion: {str(e)}')
        messages.error(request, 'Error al verificar el estado del pago.')
        return redirect('emprendimiento:mi_credito')


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

        # Validar que el crédito está activo
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

        # Validar que el crédito está activo
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
            from ..models import ReestructuracionCredito

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

        # Validar que el crédito está activo
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
            from ..models import ReestructuracionCredito

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
    from ..models import ReestructuracionCredito

    credito = get_object_or_404(Credito, id=credito_id, usuario=request.user)

    reestructuraciones = ReestructuracionCredito.objects.filter(
        credito=credito
    ).select_related('aprobado_por', 'pago_relacionado').order_by('-fecha_reestructuracion')

    context = {
        'credito': credito,
        'reestructuraciones': reestructuraciones,
    }

    return render(request, 'emprendimiento/historial_reestructuraciones.html', context)
