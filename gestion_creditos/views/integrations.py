from .common import *


@csrf_exempt
@require_http_methods(["POST"])
def wompi_webhook_view(request):
    """
    Webhook de WOMPI para recibir notificaciones de eventos de pago.

    Eventos que maneja:
    - transaction.updated: Cuando una transacción cambia de estado

    IMPORTANTE: Este endpoint debe estar accesible públicamente sin autenticaci?n
    para que WOMPI pueda enviar las notificaciones.
    """
    from ..services.wompi_client import WompiClient
    import hashlib
    import hmac

    try:
        # Leer el cuerpo de la petición
        import json
        payload = json.loads(request.body.decode('utf-8'))

        # Validar la firma del webhook (integridad del mensaje)
        # Según documentaci?n de Wompi:
        # checksum = SHA256(properties concatenadas en orden + timestamp + events_secret)
        signature_data = payload.get('signature', {})
        received_checksum = signature_data.get('checksum', '')
        properties = signature_data.get('properties', [])
        timestamp = payload.get('timestamp', '')

        # Construir la cadena concatenando los valores de las propiedades
        transaction_data = payload.get('data', {}).get('transaction', {})
        concat_values = ''
        for prop in properties:
            # prop tiene formato "transaction.id", "transaction.status", etc.
            field_name = prop.replace('transaction.', '')
            value = transaction_data.get(field_name, '')
            concat_values += str(value)

        # Agregar timestamp y events_secret
        events_secret = getattr(settings, 'WOMPI_EVENTS_SECRET', '')
        string_to_hash = f"{concat_values}{timestamp}{events_secret}"

        # Calcular el checksum esperado
        expected_checksum = hashlib.sha256(string_to_hash.encode('utf-8')).hexdigest()

        if not hmac.compare_digest(received_checksum, expected_checksum):
            logger.warning(f"Firma inválida en webhook de WOMPI. Esperada: {expected_checksum}, Recibida: {received_checksum}")
            logger.debug(f"String to hash: {concat_values}{timestamp}[SECRET]")
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

            logger.info(f"Transacci?n {transaction_id} actualizada a estado: {status}, Referencia: {reference}")
            mapped_status = _map_wompi_status_to_intent(status)
            intent_updated = 0
            if transaction_id:
                intent_updated = WompiIntent.objects.filter(wompi_transaction_id=transaction_id).update(
                    status=mapped_status
                )
            if not intent_updated and reference:
                intent = WompiIntent.objects.filter(referencia=reference).order_by('-created_at').first()
                if intent:
                    intent.status = mapped_status
                    intent.save(update_fields=['status', 'updated_at'])

            # Buscar el crédito por la referencia
            # La referencia tiene formato: CUOTA-{credito_id}-{timestamp}
            if reference and reference.startswith('CUOTA-'):
                try:
                    credito_id = reference.split('-')[1]

                    if status == 'APPROVED':
                        # Registrar el pago
                        monto_decimal = Decimal(amount_in_cents) / 100

                        try:
                            with transaction.atomic():
                                credito = Credito.objects.select_for_update().get(id=credito_id)
                                pago, created = credit_services.registrar_pago_credito(
                                    credito=credito,
                                    monto=monto_decimal,
                                    referencia_pago=reference,
                                    metodo_pago=HistorialPago.MetodoPago.WOMPI,
                                    origen_registro=HistorialPago.OrigenRegistro.PASARELA_WOMPI,
                                    empresa=credito.empresa_relacionada,
                                    notas='Pago conciliado desde webhook Wompi.',
                                )

                                if not created:
                                    logger.info(f"Pago con referencia {reference} ya existe, omitiendo.")
                                else:
                                    logger.info(f"Pago de ${monto_decimal} registrado exitosamente para crédito {credito_id}")
                        except IntegrityError as e:
                            # Puede ocurrir por concurrencia - verificar si el pago ya se procesó
                            logger.warning(f"IntegrityError al procesar pago {reference}: {e}. Verificando si ya existe...")
                            if HistorialPago.objects.filter(referencia_pago=reference).exists():
                                logger.info(f"Pago {reference} ya fue procesado por otra instancia.")
                            else:
                                raise

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
    Este endpoint debe estar accesible públicamente sin autenticaci?n.
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError as e:
        logger.error(f"Error al decodificar JSON del webhook de ZapSign: {str(e)}")
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    doc_token = payload.get('token') or payload.get('doc_token')
    event = payload.get('event') or payload.get('event_type') or ''

    def _zapsign_action_from_payload(event_name, data):
        """
        Determina la acci?n real del webhook con base en status/evento y evidencia de firma.
        Retorna: 'signed' | 'refused' | 'ignored'
        """
        event_lower = (event_name or '').strip().lower()
        status_lower = (data.get('status') or '').strip().lower()

        refused_statuses = {'refused', 'recusado', 'rejected', 'cancelled', 'canceled'}
        signed_statuses = {'signed', 'assinado', 'completed', 'concluded', 'concluido'}
        signed_signer_statuses = {'signed', 'assinado'}

        if event_lower in {'doc_refused', 'doc_rejected'}:
            return 'refused'

        if status_lower in refused_statuses:
            return 'refused'

        if status_lower in signed_statuses:
            return 'signed'

        # Para doc_signed sin status confiable, exigir evidencia de firma en signers.
        if event_lower == 'doc_signed':
            signers = data.get('signers') or []
            for signer in signers:
                signer_status = (signer.get('status') or '').strip().lower()
                if signer.get('signed_at') or signer_status in signed_signer_statuses:
                    return 'signed'
            return 'ignored'

        return 'ignored'

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
            webhook_log.error_message = "Secret token inválido"
            webhook_log.save(update_fields=['error_message'])
            logger.warning(f"Webhook ZapSign rechazado: secret inválido desde {ip_address}")
            return JsonResponse({'error': 'Unauthorized'}, status=403)

    webhook_log.signature_valid = True
    webhook_log.save(update_fields=['signature_valid'])


    action = _zapsign_action_from_payload(event, payload)

    try:
        with transaction.atomic():
            if action == 'signed':
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

                # Descargar y guardar el PDF firmado en nuestro servidor
                try:
                    if not pagare.archivo_pdf_firmado:
                        from gestion_creditos.services.zapsign_client import (
                            descargar_pdf_firmado_pagare as zapsign_descargar_pdf_firmado,
                        )
                        signed_bytes = zapsign_descargar_pdf_firmado(pagare)
                        if signed_bytes:
                            filename = f"{pagare.numero_pagare}_firmado.pdf"
                            pagare.archivo_pdf_firmado.save(filename, ContentFile(signed_bytes), save=True)
                except Exception as e:
                    logger.warning(f"No se pudo guardar el PDF firmado para pagar? {pagare.numero_pagare}: {e}")

                credit_services.gestionar_cambio_estado_credito(
                    credito=credito,
                    nuevo_estado=Credito.EstadoCredito.FIRMADO,
                    motivo="Pagaré firmado por ZapSign"
                )
                credit_services.iniciar_proceso_desembolso(credito)

                webhook_log.processed = True
                webhook_log.save(update_fields=['processed'])
                return JsonResponse({'status': 'ok'}, status=200)

            if action == 'refused':
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

            if event == 'doc_signed':
                webhook_log.error_message = "Evento doc_signed sin evidencia de firma valida"
                webhook_log.processed = True
                webhook_log.save(update_fields=['error_message', 'processed'])
                return JsonResponse({'status': 'event_ignored'}, status=200)

            webhook_log.processed = True
            webhook_log.save(update_fields=['processed'])
            return JsonResponse({'status': 'event_ignored'}, status=200)

    except Pagare.DoesNotExist:
        webhook_log.error_message = f"Documento no encontrado: {doc_token}"
        webhook_log.processed = True
        webhook_log.save(update_fields=['error_message', 'processed'])
        return JsonResponse({'status': 'document_not_found_ignored'}, status=200)
    except Exception as e:
        webhook_log.error_message = str(e)
        webhook_log.save(update_fields=['error_message'])
        logger.error(f"Error procesando webhook ZapSign {doc_token}: {str(e)}")
        return JsonResponse({'error': 'Internal server error'}, status=500)
