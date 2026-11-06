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


@transaction.non_atomic_requests
@csrf_exempt
@require_http_methods(["POST"])
def zapsign_webhook_view(request):
    """Historical Libranza/legacy route, authenticated with its own secret."""
    from gestion_creditos.services.zapsign_webhook import procesar_webhook
    return procesar_webhook(request, receptor='libranza')


@transaction.non_atomic_requests
@csrf_exempt
@require_http_methods(["POST"])
def zapsign_prestadores_webhook_view(request):
    """Prestadores route; no fallback to the historical receiver's credentials."""
    from gestion_creditos.services.zapsign_webhook import procesar_webhook
    return procesar_webhook(request, receptor='prestadores')
