from decimal import Decimal, ConversionSyntax
import logging
import uuid
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from openai import OpenAI
from configuraciones.models import ConfiguracionPeso
from .models import Credito, HistorialEstado, CuentaAhorro, MovimientoAhorro, ConfiguracionTasaInteres, HistorialPago
from django.db.models import Sum, Count, Case, When, F, DecimalField, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from datetime import timedelta
import json
import csv
import io
from django.db import transaction
from django.contrib import messages

logger = logging.getLogger(__name__)

@transaction.atomic
def gestionar_cambio_estado_credito(credito, nuevo_estado, motivo, usuario_modificacion=None, comprobante=None):
    """
    Centraliza todos los cambios de estado de un crédito, registrando el historial.
    Esta es la ÚNICA función que debe usarse para cambiar el estado de un crédito!!!!.
    """
    estado_anterior = credito.estado

    if estado_anterior == nuevo_estado:
        return  #? No hay cambio de estado

    credito.estado = nuevo_estado
    credito.save()

    #? Si el crédito se activa por primera vez, ejecutar la lógica de cálculo financiero.
    if nuevo_estado == Credito.EstadoCredito.ACTIVO and estado_anterior != Credito.EstadoCredito.ACTIVO:
        activar_credito(credito)

    #? Registrar el cambio en el historial
    HistorialEstado.objects.create(
        credito=credito,
        estado_anterior=estado_anterior,
        estado_nuevo=nuevo_estado,
        motivo=motivo,
        comprobante_pago=comprobante,
        usuario_modificacion=usuario_modificacion
    )
    logger.info(f"Crédito {credito.id} cambió de {estado_anterior} a {nuevo_estado}. Motivo: {motivo}")


def actualizar_saldo_tras_pago(credito, monto_pagado):
    """
    Actualiza el saldo de un crédito después de que se ha registrado un pago.
    Delega el cambio de estado a la función centralizada 'gestionar_cambio_estado_credito'.
    """
    detalle = getattr(credito, 'detalle_emprendimiento', None) or getattr(credito, 'detalle_libranza', None)
    if not detalle or detalle.capital_original_pendiente is None or not detalle.tasa_interes:
        logger.warning(f"No se puede actualizar saldo para crédito {credito.id}: faltan datos clave (capital o tasa).")
        return

    monto_pagado = Decimal(monto_pagado)
    tasa_mensual = detalle.tasa_interes / Decimal(100)

    interes_del_periodo = detalle.capital_original_pendiente * tasa_mensual
    abono_a_capital = max(monto_pagado - interes_del_periodo, Decimal(0))

    if detalle.capital_original_pendiente is not None:
        detalle.capital_original_pendiente -= abono_a_capital
        if detalle.capital_original_pendiente < 0:
            detalle.capital_original_pendiente = 0

    if detalle.saldo_pendiente is not None:
        detalle.saldo_pendiente -= monto_pagado
    
    if detalle.saldo_pendiente <= 0:
        detalle.saldo_pendiente = 0
        detalle.capital_original_pendiente = 0
        if credito.estado != Credito.EstadoCredito.PAGADO:
            gestionar_cambio_estado_credito(
                credito=credito,
                nuevo_estado=Credito.EstadoCredito.PAGADO,
                motivo="Crédito saldado automáticamente por pago."
            )
    else:
        if credito.estado == Credito.EstadoCredito.EN_MORA:
            gestionar_cambio_estado_credito(
                credito=credito,
                nuevo_estado=Credito.EstadoCredito.ACTIVO,
                motivo="Crédito actualizado a ACTIVO por pago."
            )
            
    detalle.save()


def evaluar_motivacion_credito(texto: str) -> int:
    """
    Evalúa la justificación de un crédito usando la API de OpenAI (GPT-3.5-turbo).

    Asigna un puntaje de 1 a 5 basado en la calidad y coherencia de la
    justificación proporcionada por el solicitante.

    Args:
        texto (str): La justificación del solicitante para el crédito.

    Returns:
        int: Un puntaje entre 1 y 5. Devuelve 3 si el texto es muy corto o
             si ocurre un error en la API.
    """
    if not texto or len(texto) < 10:
        return 3
    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        prompt = f'''Evalúa esta justificación para un crédito y asigna un puntaje del 1 al 5:
        - 1: Muy pobre
        - 2: Pobre
        - 3: Aceptable
        - 4: Bueno
        - 5: Excelente

        Justificación: "{texto}"

        Responde SOLO con el número del puntaje (1-5).'''
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
        logger.error(f"Error al evaluar con OpenAI: {e}")
        return 3 # Retorna un puntaje neutral en caso de error


def obtener_puntaje_interno(parametros: dict) -> int:
    """
    Calcula un puntaje interno basado en un conjunto de parámetros y sus
    respectivos pesos definidos en el modelo `ConfiguracionPeso`.

    Args:
        parametros (dict): Un diccionario donde las claves son los nombres de los
                           parámetros y los valores son los niveles seleccionados.

    Returns:
        int: La suma de las estimaciones (puntajes) para los parámetros dados.
    """
    suma_estimaciones = 0
    for parametro, nivel in parametros.items():
        if nivel:
            try:
                configuracion = ConfiguracionPeso.objects.get(parametro=parametro, nivel=nivel)
                suma_estimaciones += configuracion.estimacion
            except ConfiguracionPeso.DoesNotExist:
                logger.warning(f"No se encontró configuración para {parametro} con nivel {nivel}")
    return suma_estimaciones


def filtrar_creditos(request, creditos_base):
    """
    Aplica filtros de línea, estado y búsqueda a un queryset de créditos.

    Args:
        request: El objeto HttpRequest que contiene los parámetros GET.
        creditos_base: El QuerySet base de créditos para filtrar.

    Returns:
        QuerySet: El QuerySet de créditos filtrado.
    """
    linea_filter = request.GET.get('linea', '')
    estado_filter = request.GET.get('estado', '')
    search_query = request.GET.get('search', '')

    if linea_filter:
        creditos_base = creditos_base.filter(linea=linea_filter)
    
    if estado_filter:
        creditos_base = creditos_base.filter(estado=estado_filter)
    
    if search_query:
        creditos_base = creditos_base.filter(
            Q(usuario__username__icontains=search_query) |
            Q(usuario__first_name__icontains=search_query) |
            Q(usuario__last_name__icontains=search_query) |
            Q(numero_credito__icontains=search_query) |
            Q(detalle_libranza__nombres__icontains=search_query) |
            Q(detalle_libranza__apellidos__icontains=search_query) |
            Q(detalle_emprendimiento__nombre__icontains=search_query) |
            Q(detalle_libranza__cedula__icontains=search_query) |
            Q(detalle_emprendimiento__numero_cedula__icontains=search_query)
        )
        
    return creditos_base


def get_dashboard_context():
    """
    Prepara el contexto de datos para el dashboard administrativo.

    Esta función encapsula todas las consultas a la base de datos necesarias
    para renderizar el dashboard, incluyendo estadísticas generales,
    distribución de créditos y datos para las gráficas.

    Returns:
        dict: Un diccionario con todos los datos de contexto para la plantilla.
    """
    total_creditos = Credito.objects.count()
    creditos_activos = Credito.objects.filter(estado='ACTIVO').count()
    creditos_en_mora_count = Credito.objects.filter(estado='EN_MORA').count()

    #? Saldos de cartera
    saldo_emprendimiento_cartera = Credito.objects.filter(
        linea='EMPRENDIMIENTO',
        estado__in=['ACTIVO', 'EN_MORA']
    ).aggregate(total=Sum('detalle_emprendimiento__saldo_pendiente'))['total'] or 0

    saldo_libranza_cartera = Credito.objects.filter(
        linea='LIBRANZA',
        estado__in=['ACTIVO', 'EN_MORA']
    ).aggregate(total=Sum('detalle_libranza__saldo_pendiente'))['total'] or 0

    saldo_cartera_total = saldo_emprendimiento_cartera + saldo_libranza_cartera

    #? Montos en mora
    monto_emprendimiento_en_mora = Credito.objects.filter(
        linea='EMPRENDIMIENTO',
        estado='EN_MORA'
    ).aggregate(total=Sum('detalle_emprendimiento__saldo_pendiente'))['total'] or 0

    monto_libranza_en_mora = Credito.objects.filter(
        linea='LIBRANZA',
        estado='EN_MORA'
    ).aggregate(total=Sum('detalle_libranza__saldo_pendiente'))['total'] or 0

    monto_total_en_mora = monto_emprendimiento_en_mora + monto_libranza_en_mora
    
    #? Distribución de créditos
    creditos_por_linea = Credito.objects.values('linea').annotate(count=Count('id'))
    
    creditos_por_estado_list = list(Credito.objects.values('estado').annotate(count=Count('id')))
    for item in creditos_por_estado_list:
        item['porcentaje'] = (item['count'] / total_creditos) * 100 if total_creditos > 0 else 0

    #? Próximos a vencer
    hoy = timezone.now().date()
    fecha_limite = hoy + timedelta(days=2)
    proximos_vencer = Credito.objects.filter(
        Q(estado=Credito.EstadoCredito.ACTIVO) &
        (
            Q(detalle_emprendimiento__fecha_proximo_pago__gte=hoy, detalle_emprendimiento__fecha_proximo_pago__lte=fecha_limite) |
            Q(detalle_libranza__fecha_proximo_pago__gte=hoy, detalle_libranza__fecha_proximo_pago__lte=fecha_limite)
        )
    ).distinct().count()

    #? --- Gráfica de Saldo de Cartera por Mes ---
    six_months_ago = timezone.now().date().replace(day=1) - timedelta(days=150)
    monthly_balance = Credito.objects.filter(
        estado__in=[Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA],
        fecha_actualizacion__gte=six_months_ago
    ).annotate(
        month=TruncMonth('fecha_actualizacion')
    ).values('month').annotate(
        total_emprendimiento=Sum(
            Case(
                When(linea=Credito.LineaCredito.EMPRENDIMIENTO, then=F('detalle_emprendimiento__saldo_pendiente')),
                default=Decimal(0), output_field=DecimalField()
            )
        ),
        total_libranza=Sum(
            Case(
                When(linea=Credito.LineaCredito.LIBRANZA, then=F('detalle_libranza__saldo_pendiente')),
                default=Decimal(0), output_field=DecimalField()
            )
        )
    ).order_by('month')

    data_by_month = {item['month'].strftime('%Y-%m'): item for item in monthly_balance}

    portfolio_labels = []
    emprendimiento_data = []
    libranza_data = []
    total_data = []

    for i in range(6):
        from dateutil.relativedelta import relativedelta
        month_date = (timezone.now() - relativedelta(months=5-i)).replace(day=1)
        month_key = month_date.strftime('%Y-%m')
        portfolio_labels.append(month_date.strftime('%b %Y'))

        emprendimiento_monthly = data_by_month.get(month_key, {}).get('total_emprendimiento')
        libranza_monthly = data_by_month.get(month_key, {}).get('total_libranza')
        
        emprendimiento_data.append(float(emprendimiento_monthly or 0))
        libranza_data.append(float(libranza_monthly or 0))
        total_data.append(float((emprendimiento_monthly or 0) + (libranza_monthly or 0)))

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
        'emprendimiento_data': json.dumps(emprendimiento_data),
        'libranza_data': json.dumps(libranza_data),
        'total_data': json.dumps(total_data),
        'distribution_labels': json.dumps(distribution_labels),
        'distribution_data': json.dumps(distribution_data),
    }
    return context


def activar_credito(credito):
    """
    Activa un crédito, calculando y guardando todos los valores financieros iniciales.

    Esta función se llama cuando un crédito pasa al estado 'ACTIVO'. Realiza
    las siguientes acciones:
    1.  Obtiene el detalle del crédito (emprendimiento o libranza).
    2.  Calcula la comisión y el IVA sobre la misma.
    3.  Calcula el total a pagar (monto aprobado + comisión + IVA).
    4.  Asigna la tasa de interés por defecto si no está definida.
    5.  Calcula el valor de la cuota mensual usando una fórmula de anualidad.
    6.  Inicializa el saldo pendiente y el capital original pendiente.
    7.  Determina la fecha del primer pago.

    Args:
        credito (Credito): La instancia del crédito a activar.
    """
    detalle_credito = getattr(credito, 'detalle_emprendimiento', None) or getattr(credito, 'detalle_libranza', None)
    
    if not detalle_credito or not detalle_credito.valor_credito or not detalle_credito.plazo > 0:
        logger.error(f"No se pudo activar el crédito {credito.id} por falta de datos (detalle, valor o plazo).")
        return

    monto_aprobado = detalle_credito.valor_credito
    plazo = detalle_credito.plazo

    #? Asignar monto aprobado si no existe
    if not detalle_credito.monto_aprobado or detalle_credito.monto_aprobado <= 0:
        detalle_credito.monto_aprobado = monto_aprobado

    #? Lógica de cálculo de Comisión e IVA
    porcentaje_comision = Decimal('0.10')
    porcentaje_iva = Decimal('0.19')
    
    detalle_credito.comision = (monto_aprobado * porcentaje_comision).quantize(Decimal('0.01'))
    detalle_credito.iva_comision = (detalle_credito.comision * porcentaje_iva).quantize(Decimal('0.01'))
    detalle_credito.total_a_pagar = (monto_aprobado + detalle_credito.comision + detalle_credito.iva_comision).quantize(Decimal('0.01'))
    
    #? Asignar tasa de interés por defecto si no existe
    if not detalle_credito.tasa_interes:
        if credito.linea == Credito.LineaCredito.EMPRENDIMIENTO:
            detalle_credito.tasa_interes = Decimal('3.5')
        elif credito.linea == Credito.LineaCredito.LIBRANZA:
            detalle_credito.tasa_interes = Decimal('2.0')

    #? Calcular valor de la cuota con fórmula de anualidad
    r = detalle_credito.tasa_interes / 100
    n = plazo
    P = detalle_credito.total_a_pagar

    if r > 0:
        valor_cuota = (P * r) / (1 - (1 + r)**-n)
    else:
        valor_cuota = P / n
    
    detalle_credito.valor_cuota = valor_cuota.quantize(Decimal('0.01'))
    
    # Inicializar saldos
    detalle_credito.saldo_pendiente = detalle_credito.total_a_pagar
    detalle_credito.capital_original_pendiente = monto_aprobado

    #? Lógica de Próximo Vencimiento
    from dateutil.relativedelta import relativedelta
    fecha_base = credito.fecha_solicitud.date()
    if fecha_base.day <= 15:
        detalle_credito.fecha_proximo_pago = (fecha_base.replace(day=1) + relativedelta(months=1))
    else:
        detalle_credito.fecha_proximo_pago = (fecha_base.replace(day=1) + relativedelta(months=2))
    
    detalle_credito.save()
    logger.info(f"Crédito {credito.id} activado exitosamente con todos los cálculos financieros.")


def get_billetera_context(user):
    """
    Prepara el contexto de datos para la vista de la billetera digital.
    """
    cuenta, created = CuentaAhorro.objects.get_or_create(
        usuario=user,
        defaults={
            'tipo_usuario': CuentaAhorro.TipoUsuario.NATURAL,
            'saldo_disponible': Decimal('0.00'),
            'saldo_objetivo': Decimal('1000000.00')
        }
    )
    
    movimientos_recientes = MovimientoAhorro.objects.filter(
        cuenta=cuenta,
        estado__in=['APROBADO', 'PROCESADO']
    ).order_by('-fecha_creacion')[:10]
    
    total_depositado = MovimientoAhorro.objects.filter(
        cuenta=cuenta,
        tipo__in=['DEPOSITO_ONLINE', 'DEPOSITO_OFFLINE'],
        estado__in=['APROBADO', 'PROCESADO']
    ).aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
    
    dias_ahorrando = (timezone.now().date() - cuenta.fecha_apertura.date()).days if cuenta.fecha_apertura else 0
    
    fecha_hace_un_mes = timezone.now() - timedelta(days=30)
    fecha_hace_dos_meses = timezone.now() - timedelta(days=60)
    
    depositos_ultimo_mes = MovimientoAhorro.objects.filter(
        cuenta=cuenta,
        tipo__in=['DEPOSITO_ONLINE', 'DEPOSITO_OFFLINE'],
        estado__in=['APROBADO', 'PROCESADO'],
        fecha_creacion__gte=fecha_hace_un_mes
    ).aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
    
    depositos_mes_anterior = MovimientoAhorro.objects.filter(
        cuenta=cuenta,
        tipo__in=['DEPOSITO_ONLINE', 'DEPOSITO_OFFLINE'],
        estado__in=['APROBADO', 'PROCESADO'],
        fecha_creacion__gte=fecha_hace_dos_meses,
        fecha_creacion__lt=fecha_hace_un_mes
    ).aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
    
    crecimiento_porcentaje = ((depositos_ultimo_mes - depositos_mes_anterior) / depositos_mes_anterior) * 100 if depositos_mes_anterior > 0 else (100 if depositos_ultimo_mes > 0 else 0)
    
    progreso_porcentaje = min((cuenta.saldo_disponible / cuenta.saldo_objetivo) * 100, 100) if cuenta.saldo_objetivo > 0 else 0
    
    tasa_actual = ConfiguracionTasaInteres.objects.filter(activa=True).order_by('-fecha_vigencia').first()
    
    interes_estimado = (cuenta.saldo_disponible * tasa_actual.tasa_anual_efectiva) / 100 if tasa_actual and cuenta.saldo_disponible > 0 else Decimal('0.00')
    
    #? --- Preparación de datos para el gráfico ---
    from dateutil.relativedelta import relativedelta
    chart_labels = []
    chart_values = []

    for i in range(9, -1, -1):
        month_date = timezone.now().replace(day=1) - relativedelta(months=i)
        fecha_inicio = month_date
        fecha_fin = fecha_inicio + relativedelta(months=1)

        total_mes = MovimientoAhorro.objects.filter(
            cuenta=cuenta,
            tipo__in=['DEPOSITO_ONLINE', 'DEPOSITO_OFFLINE', 'AJUSTE_ADMIN'],
            estado__in=['APROBADO', 'PROCESADO'],
            fecha_creacion__gte=fecha_inicio,
            fecha_creacion__lt=fecha_fin
        ).aggregate(total=Sum('monto'))['total'] or 0
        
        chart_labels.append(month_date.strftime('%b'))
        chart_values.append(float(total_mes))

    chart_data = {
        'labels': chart_labels,
        'data': chart_values
    }

    return {
        'cuenta': cuenta,
        'saldo_disponible': cuenta.saldo_disponible,
        'saldo_objetivo': cuenta.saldo_objetivo,
        'progreso_porcentaje': round(progreso_porcentaje, 1),
        'crecimiento_porcentaje': round(crecimiento_porcentaje, 1),
        'dias_ahorrando': dias_ahorrando,
        'emprendimientos_financiados': cuenta.emprendimientos_financiados,
        'familias_beneficiadas': cuenta.familias_beneficiadas,
        'interes_estimado': interes_estimado,
        'tasa_actual': tasa_actual,
        'movimientos_recientes': movimientos_recientes,
        'chart_data': json.dumps(chart_data),
        'total_depositado': total_depositado,
    }


def procesar_pagos_masivos_csv(csv_file, empresa):
    """
    Procesa un archivo CSV de pagos masivos para los créditos de una empresa.
    """
    pagos_exitosos = 0
    errores = []
    
    try:
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
                except (ValueError, TypeError, ConversionSyntax):
                    errores.append(f"Fila {i} (Cédula {cedula}): Monto '{monto_str}' no es un número válido.")
                    continue

                credito = Credito.objects.filter(
                    linea=Credito.LineaCredito.LIBRANZA,
                    detalle_libranza__empresa=empresa,
                    detalle_libranza__cedula=cedula,
                    estado=Credito.EstadoCredito.ACTIVO
                ).select_related('detalle_libranza').first()

                if not credito:
                    errores.append(f"Fila {i}: No se encontró un crédito activo para la cédula {cedula}.")
                    continue

                HistorialPago.objects.create(
                    credito=credito,
                    monto=monto_a_pagar,
                    referencia_pago=f"MASIVO-{credito.id}-{timezone.now().strftime('%Y%m%d%H%M%S%f')}",
                    estado=HistorialPago.EstadoPago.EXITOSO
                )

                actualizar_saldo_tras_pago(credito, monto_a_pagar)
                
                pagos_exitosos += 1
    
    except Exception as e:
        logger.error(f"Error al procesar pagos masivos: {e}")
        errores.append(f"Error inesperado al procesar el archivo: {e}")

    return pagos_exitosos, errores

def marcar_creditos_en_mora():
    """
    Busca créditos activos cuya fecha de pago ha vencido y los marca como EN_MORA.
    Utiliza el servicio centralizado de cambio de estado.
    Retorna el número de créditos actualizados.
    """
    hoy = timezone.now().date()
    #? Se buscan los créditos que tienen una fecha de próximo pago vencida en cualquiera de sus detalles
    creditos_vencidos = Credito.objects.filter(
        estado=Credito.EstadoCredito.ACTIVO
    ).filter(
        Q(detalle_emprendimiento__fecha_proximo_pago__lt=hoy) |
        Q(detalle_libranza__fecha_proximo_pago__lt=hoy)
    ).distinct()

    creditos_actualizados = 0
    for credito in creditos_vencidos:
        try:
            gestionar_cambio_estado_credito(
                credito=credito,
                nuevo_estado=Credito.EstadoCredito.EN_MORA,
                motivo='El crédito ha entrado en mora por vencimiento de la fecha de pago.',
                usuario_modificacion=None  #? Es un proceso automático
            )
            creditos_actualizados += 1
        except Exception as e:
            logger.error(f"Error al marcar en mora el crédito {credito.numero_credito}: {e}")
            
    return creditos_actualizados

@transaction.atomic
def gestionar_consignacion_billetera(movimiento_id: int, es_aprobado: bool, usuario_admin, nota: str):
    """
    Aprueba o rechaza una consignación de billetera y actualiza el saldo si es necesario.
    """
    movimiento = get_object_or_404(
        MovimientoAhorro, 
        id=movimiento_id,
        estado=MovimientoAhorro.EstadoMovimiento.PENDIENTE
    )

    if es_aprobado:
        movimiento.estado = MovimientoAhorro.EstadoMovimiento.APROBADO
        movimiento.nota_admin = nota or 'Consignación aprobada'
        
        #? Actualizar saldo de la cuenta
        cuenta = movimiento.cuenta
        cuenta.saldo_disponible += movimiento.monto
        cuenta.save()
    else:
        movimiento.estado = MovimientoAhorro.EstadoMovimiento.RECHAZADO
        movimiento.nota_admin = nota or 'Sin motivo especificado'

    movimiento.fecha_procesamiento = timezone.now()
    movimiento.procesado_por = usuario_admin
    movimiento.save()
    
    return movimiento

@transaction.atomic
def crear_ajuste_manual_billetera(admin_user, user_email, monto, nota, comprobante):
    """
    Crea un ajuste manual en la billetera de un usuario.
    """
    try:
        usuario = User.objects.get(email=user_email)
    except User.DoesNotExist:
        raise ValueError(f'No existe un usuario con el email {user_email}')

    cuenta, created = CuentaAhorro.objects.get_or_create(
        usuario=usuario,
        defaults={
            'tipo_usuario': CuentaAhorro.TipoUsuario.NATURAL,
            'saldo_disponible': Decimal('0.00')
        }
    )

    movimiento = MovimientoAhorro.objects.create(
        cuenta=cuenta,
        tipo=MovimientoAhorro.TipoMovimiento.AJUSTE_ADMIN,
        monto=monto,
        estado=MovimientoAhorro.EstadoMovimiento.APROBADO,
        comprobante=comprobante,
        descripcion='Abono manual realizado por administrador',
        nota_admin=nota,
        referencia=f"ADMIN-{uuid.uuid4().hex[:12].upper()}",
        fecha_procesamiento=timezone.now(),
        procesado_por=admin_user
    )

    cuenta.saldo_disponible += movimiento.monto
    cuenta.save()

    return movimiento
