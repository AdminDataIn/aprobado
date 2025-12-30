from decimal import Decimal, ConversionSyntax
import logging
import uuid
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from openai import OpenAI
from configuraciones.models import ConfiguracionPeso
from .models import Credito, HistorialEstado, CuentaAhorro, MovimientoAhorro, ConfiguracionTasaInteres, HistorialPago, CuotaAmortizacion
from django.db.models import Sum, Count, Case, When, F, DecimalField, Q, Avg
from django.db.models.functions import TruncMonth, Coalesce
from django.utils import timezone
from datetime import timedelta, datetime
from dateutil.relativedelta import relativedelta
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
    También envía notificaciones por email al cliente.
    """
    from .email_service import enviar_notificacion_cambio_estado

    estado_anterior = credito.estado

    if estado_anterior == nuevo_estado:
        return

    credito.estado = nuevo_estado
    credito.save()

    if nuevo_estado == Credito.EstadoCredito.ACTIVO and estado_anterior != Credito.EstadoCredito.ACTIVO:
        activar_credito(credito)

    HistorialEstado.objects.create(
        credito=credito,
        estado_anterior=estado_anterior,
        estado_nuevo=nuevo_estado,
        motivo=motivo,
        comprobante_pago=comprobante,
        usuario_modificacion=usuario_modificacion
    )
    logger.info(f"Crédito {credito.id} cambió de {estado_anterior} a {nuevo_estado}. Motivo: {motivo}")

    # Enviar notificación por email al cliente
    try:
        enviar_notificacion_cambio_estado(credito, nuevo_estado, motivo)
        logger.info(f"Notificación de email enviada para crédito {credito.id} - Estado: {nuevo_estado}")
    except Exception as e:
        logger.error(f"Error al enviar notificación de email para crédito {credito.id}: {e}")

@transaction.atomic
def preparar_documento_para_firma(credito, usuario_modificacion):
    """
    Prepara el crédito para el proceso de firma.
    """
    gestionar_cambio_estado_credito(
        credito=credito,
        nuevo_estado=Credito.EstadoCredito.PENDIENTE_FIRMA,
        motivo="Crédito aprobado, pendiente de firma del pagaré.",
        usuario_modificacion=usuario_modificacion
    )
    
    credito.documento_enviado = True
    credito.save(update_fields=['documento_enviado'])
    
    logger.info(f"El crédito {credito.id} ha sido preparado para la firma.")

@transaction.atomic
def iniciar_proceso_desembolso(credito):
    """
    Inicia el proceso de desembolso.
    """
    logger.info(f"Iniciando proceso de desembolso para el crédito {credito.id}.")

    gestionar_cambio_estado_credito(
        credito=credito,
        nuevo_estado=Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
        motivo="El pagaré ha sido firmado. El crédito está pendiente de transferencia.",
        usuario_modificacion=None
    )

    logger.info(f"Crédito {credito.id} pendiente de transferencia por el equipo de finanzas.")



def actualizar_saldo_tras_pago(credito, monto_pagado):
    """
    Actualiza el saldo del crédito después de recibir un pago.

    Lógica de actualización:
    1. El pago primero cubre los intereses del período
    2. El remanente del pago abona al capital
    3. Actualiza dos campos:
       - saldo_pendiente: Capital financiado total pendiente (monto + comisión + IVA)
       - capital_pendiente: Solo el monto aprobado pendiente (para mostrar al usuario)
    4. Calcula proporcionalmente cuánto del capital_pendiente se ha pagado

    Ejemplo:
    - Monto aprobado: $500,000
    - Capital financiado: $559,500 (incluye comisión + IVA)
    - Al pagar 1 cuota de $284,750:
      * saldo_pendiente: $559,500 - $284,750 = $274,750
      * capital_pendiente: $500,000 * (274,750/559,500) = $245,448 (proporción)

    Args:
        credito: Instancia del modelo Credito
        monto_pagado: Monto del pago realizado (Decimal o convertible)

    Returns:
        None (actualiza el crédito directamente)
    """
    # ✅ Validar que el crédito tenga los campos necesarios
    if not all([credito.tasa_interes, credito.saldo_pendiente is not None, credito.monto_aprobado]):
        logger.warning(f"No se puede actualizar saldo para crédito {credito.numero_credito}: faltan datos clave")
        return

    monto_pagado = Decimal(monto_pagado)
    tasa_mensual = credito.tasa_interes / Decimal(100)

    # Saldo antes del pago (capital financiado total pendiente)
    saldo_antes_pago = credito.saldo_pendiente

    # 1. Calcular el interés generado sobre el saldo pendiente
    interes_del_periodo = saldo_antes_pago * tasa_mensual

    # 2. Determinar abono a interés y capital
    abono_a_interes = min(monto_pagado, interes_del_periodo)
    abono_a_capital = monto_pagado - abono_a_interes

    # 3. ✅ Actualizar saldo_pendiente (capital financiado total)
    credito.saldo_pendiente -= abono_a_capital

    # 4. ✅ Actualizar capital_pendiente PROPORCIONALMENTE
    # Calcular qué porcentaje del capital financiado total se ha pagado
    # y aplicarlo al monto_aprobado original
    if credito.capital_pendiente is not None and credito.total_a_pagar:
        # Calcular capital financiado inicial (si no está guardado, calcularlo)
        capital_financiado_inicial = credito.monto_aprobado + (credito.comision or 0) + (credito.iva_comision or 0)

        if capital_financiado_inicial > 0:
            # Proporción del saldo pendiente respecto al capital financiado inicial
            proporcion_pendiente = credito.saldo_pendiente / capital_financiado_inicial

            # Aplicar esa proporción al monto aprobado original
            credito.capital_pendiente = credito.monto_aprobado * proporcion_pendiente

            # Redondear a 2 decimales para evitar problemas de precisión
            credito.capital_pendiente = credito.capital_pendiente.quantize(Decimal('0.01'))

    # 5. Validar si el crédito está completamente pagado
    if credito.saldo_pendiente <= Decimal('0.01'):
        credito.saldo_pendiente = Decimal('0.00')
        if credito.capital_pendiente is not None:
            credito.capital_pendiente = Decimal('0.00')

        # Marcar como pagado si no lo está ya
        if credito.estado != Credito.EstadoCredito.PAGADO:
            gestionar_cambio_estado_credito(
                credito=credito,
                nuevo_estado=Credito.EstadoCredito.PAGADO,
                motivo="Crédito saldado automáticamente por pago."
            )
    else:
        # 6. Avanzar fecha de próximo pago si pagó cuotas completas
        if credito.valor_cuota and credito.valor_cuota > 0 and credito.fecha_proximo_pago:
            cuotas_pagadas = int(monto_pagado // credito.valor_cuota)
            if cuotas_pagadas > 0:
                credito.fecha_proximo_pago += relativedelta(months=cuotas_pagadas)

        # 7. Si estaba en mora y se puso al día, volver a ACTIVO
        hoy = timezone.now().date()
        if credito.estado == Credito.EstadoCredito.EN_MORA and credito.fecha_proximo_pago and credito.fecha_proximo_pago > hoy:
            gestionar_cambio_estado_credito(
                credito=credito,
                nuevo_estado=Credito.EstadoCredito.ACTIVO,
                motivo="Crédito actualizado a ACTIVO por pago."
            )

    # Asegurar que no queden saldos negativos
    if credito.saldo_pendiente < 0:
        credito.saldo_pendiente = Decimal('0.00')
    if credito.capital_pendiente and credito.capital_pendiente < 0:
        credito.capital_pendiente = Decimal('0.00')

    # ✅ Guardar cambios en el crédito
    credito.save()

    logger.info(
        f"Pago procesado para crédito {credito.numero_credito}: "
        f"Monto: ${monto_pagado:,.2f}, Interés: ${abono_a_interes:,.2f}, "
        f"Capital: ${abono_a_capital:,.2f}, Nuevo saldo: ${credito.saldo_pendiente:,.2f}, "
        f"Capital pendiente: ${credito.capital_pendiente:,.2f if credito.capital_pendiente else 0}"
    )

    # Enviar confirmación de pago por email
    try:
        from .email_service import enviar_confirmacion_pago
        enviar_confirmacion_pago(credito, monto_pagado, credito.saldo_pendiente)
        logger.info(f"Confirmación de pago enviada por email para crédito {credito.numero_credito}")
    except Exception as e:
        logger.error(f"Error al enviar confirmación de pago por email para crédito {credito.numero_credito}: {e}")


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
    ✅ CORRECTO: Esta función ya está bien porque busca en campos que aún existen en los detalles
    """
    queryset = creditos_base

    # Filtro de búsqueda
    search_text = request.GET.get('search', '').strip()
    if search_text:
        queryset = queryset.filter(
            Q(usuario__username__icontains=search_text) |
            Q(usuario__email__icontains=search_text) |
            Q(numero_credito__icontains=search_text) |
            Q(detalle_emprendimiento__nombre__icontains=search_text) |
            Q(detalle_emprendimiento__numero_cedula__icontains=search_text) |
            Q(detalle_libranza__nombres__icontains=search_text) |
            Q(detalle_libranza__apellidos__icontains=search_text) |
            Q(detalle_libranza__cedula__icontains=search_text)
        )

    # Filtro de línea
    linea_filter = request.GET.get('linea', '')
    if linea_filter:
        queryset = queryset.filter(linea=linea_filter)

    # Filtro de estado
    estado_filter = request.GET.get('estado', '')
    if estado_filter:
        queryset = queryset.filter(estado=estado_filter)

    return queryset.distinct()


def get_admin_dashboard_context(user):
    """
    Obtiene todo el contexto necesario para el dashboard del administrador,
    utilizando el modelo de Crédito centralizado y optimizando las consultas.
    """
    from datetime import date
    from django.db.models.functions import TruncMonth

    today = timezone.now().date()
    proximos_15_dias = today + timedelta(days=15)

    # --- Consultas Principales ---
    creditos_activos = Credito.objects.filter(estado__in=[Credito.EstadoCredito.ACTIVO, Credito.EstadoCredito.EN_MORA])
    
    # --- KPIs Principales ---
    kpis = creditos_activos.aggregate(
        saldo_cartera_total=Coalesce(Sum('saldo_pendiente'), Decimal('0.00')),
        monto_total_en_mora=Coalesce(Sum('saldo_pendiente', filter=Q(estado=Credito.EstadoCredito.EN_MORA)), Decimal('0.00'))
    )
    total_creditos = creditos_activos.count()
    proximos_vencer = creditos_activos.filter(fecha_proximo_pago__range=[today, proximos_15_dias]).count()

    # --- Datos para Tablas ---
    # Créditos por línea - solo creditos activos con saldo
    creditos_por_linea_q = list(creditos_activos.values('linea').annotate(
        count=Count('id'),
        saldo_total=Coalesce(Sum('saldo_pendiente'), Decimal('0.00'))
    ).order_by('-saldo_total'))

    # Créditos por estado - todos los créditos
    total_general_creditos = Credito.objects.count()
    creditos_por_estado_q = Credito.objects.values('estado').annotate(
        count=Count('id')
    ).order_by('-count')

    creditos_por_estado = []
    for item in creditos_por_estado_q:
        porcentaje = (item['count'] / total_general_creditos) * 100 if total_general_creditos > 0 else 0
        creditos_por_estado.append({
            'estado': item['estado'],
            'count': item['count'],
            'porcentaje': porcentaje
        })

    # --- Datos para Gráfico de Distribución (Doughnut) ---
    distribution_labels = [item['linea'] for item in creditos_por_linea_q]
    distribution_data = [item['count'] for item in creditos_por_linea_q]

    # --- Datos para Gráfico de Evolución de Cartera (Líneas) ---
    # Generar últimos 12 meses de labels
    portfolio_labels = []
    for i in range(11, -1, -1):
        mes_fecha = today - relativedelta(months=i)
        portfolio_labels.append(mes_fecha.strftime('%b %Y'))

    # Calcular saldo de cartera por mes usando créditos activos
    # Para cada mes, sumamos los saldos de créditos que estaban activos en ese momento
    emprendimiento_data = []
    libranza_data = []

    for label in portfolio_labels:
        # Convertir label a fecha (último día del mes)
        mes_date = datetime.strptime(label, '%b %Y')
        primer_dia_mes = mes_date.replace(day=1).date()
        ultimo_dia_mes = (primer_dia_mes + relativedelta(months=1) - timedelta(days=1))

        # Créditos que estaban activos en ese mes (desembolsados antes del fin del mes)
        creditos_mes_emprendimiento = Credito.objects.filter(
            linea='EMPRENDIMIENTO',
            estado__in=['ACTIVO', 'EN_MORA', 'PAGADO'],
            fecha_desembolso__lte=ultimo_dia_mes
        ).aggregate(saldo=Coalesce(Sum('saldo_pendiente'), Decimal('0.00')))['saldo']

        creditos_mes_libranza = Credito.objects.filter(
            linea='LIBRANZA',
            estado__in=['ACTIVO', 'EN_MORA', 'PAGADO'],
            fecha_desembolso__lte=ultimo_dia_mes
        ).aggregate(saldo=Coalesce(Sum('saldo_pendiente'), Decimal('0.00')))['saldo']

        emprendimiento_data.append(float(creditos_mes_emprendimiento))
        libranza_data.append(float(creditos_mes_libranza))

    total_data = [e + l for e, l in zip(emprendimiento_data, libranza_data)]

    return {
        # KPIs
        'saldo_cartera_total': kpis['saldo_cartera_total'],
        'monto_total_en_mora': kpis['monto_total_en_mora'],
        'total_creditos': total_creditos,
        'proximos_vencer': proximos_vencer,
        
        # Tablas
        'creditos_por_linea': creditos_por_linea_q,
        'creditos_por_estado': creditos_por_estado,
        
        # Gráfico de Distribución
        'distribution_labels': json.dumps(distribution_labels),
        'distribution_data': json.dumps(distribution_data),
        
        # Gráfico de Evolución de Cartera
        'portfolio_labels': json.dumps(portfolio_labels),
        'emprendimiento_data': json.dumps(emprendimiento_data),
        'libranza_data': json.dumps(libranza_data),
        'total_data': json.dumps(total_data),
    }

def activar_credito(credito):
    """
    Activa un crédito generando todos los cálculos financieros y la tabla de amortización.

    Proceso:
    1. Calcula comisión (10% del monto aprobado) e IVA (19% sobre comisión)
    2. Determina el capital financiado total (monto + comisión + IVA) - esto se financia en cuotas
    3. Calcula la cuota mensual usando amortización francesa sobre el capital financiado
    4. Genera la tabla de amortización donde:
       - capital_pendiente: Refleja solo el monto aprobado original (para transparencia al usuario)
       - saldo_pendiente: Refleja el capital financiado total pendiente (incluye comisión + IVA)
    5. Establece la fecha de primer pago y registra el desembolso

    Tasas de interés por línea:
    - Emprendimiento: 3.5% efectiva mensual (51.11% EA, 42.00% NA)
    - Libranza: 2.0% efectiva mensual (26.82% EA, 24.00% NA)

    Args:
        credito: Instancia del modelo Credito a activar

    Raises:
        ValueError: Si faltan campos críticos (monto_aprobado o plazo)
    """
    logger.info(f"Iniciando activación del crédito {credito.numero_credito} (ID: {credito.id})")

    # ✅ Validar campos críticos
    if not credito.monto_aprobado or not credito.plazo:
        error_msg = f"No se puede activar el crédito {credito.numero_credito}: falta monto_aprobado o plazo"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # ✅ Determinar tasa de interés según la línea de crédito
    if credito.tasa_interes:
        # Si ya tiene tasa asignada, usarla
        tasa_interes = credito.tasa_interes
    else:
        # Asignar tasa según la línea
        if credito.linea == Credito.LineaCredito.EMPRENDIMIENTO:
            tasa_interes = Decimal('3.5')  # 3.5% efectiva mensual
        elif credito.linea == Credito.LineaCredito.LIBRANZA:
            tasa_interes = Decimal('2.0')  # 2.0% efectiva mensual
        else:
            tasa_interes = Decimal('2.0')  # Default

    # ✅ Calcular componentes financieros
    comision = credito.comision or (credito.monto_aprobado * Decimal('0.10'))
    iva_comision = credito.iva_comision or (comision * Decimal('0.19'))

    # El capital financiado incluye monto + comisión + IVA (esto se paga en cuotas)
    capital_financiado = credito.monto_aprobado + comision + iva_comision

    # ✅ Calcular cuota mensual sobre el capital financiado total
    tasa_mensual = tasa_interes / Decimal(100)
    if tasa_mensual > 0:
        # Fórmula de amortización francesa: C = P * [i(1+i)^n] / [(1+i)^n - 1]
        factor = (tasa_mensual * (1 + tasa_mensual) ** credito.plazo) / (((1 + tasa_mensual) ** credito.plazo) - 1)
        valor_cuota = capital_financiado * factor
    else:
        # Caso sin interés (división simple del capital financiado)
        valor_cuota = capital_financiado / credito.plazo

    # Total a pagar es la suma de todas las cuotas
    total_a_pagar = valor_cuota * credito.plazo

    # ✅ Actualizar campos financieros en el crédito
    credito.tasa_interes = tasa_interes
    credito.comision = comision
    credito.iva_comision = iva_comision
    credito.total_a_pagar = total_a_pagar
    credito.valor_cuota = valor_cuota

    # saldo_pendiente: Capital financiado total pendiente (incluye comisión + IVA)
    credito.saldo_pendiente = capital_financiado

    # capital_pendiente: Solo el monto aprobado original (para mostrar al usuario cuánto del monto solicitado ha pagado)
    credito.capital_pendiente = credito.monto_aprobado

    # ✅ Calcular fecha de primer pago
    hoy = timezone.now().date()
    if hoy.day <= 15:
        # Si es antes del 15, el pago es el mismo día del próximo mes
        credito.fecha_proximo_pago = hoy + relativedelta(months=1)
    else:
        # Si es después del 15, el pago es el 1ro del mes subsiguiente
        credito.fecha_proximo_pago = (hoy + relativedelta(months=2)).replace(day=1)

    credito.fecha_desembolso = timezone.now()
    credito.save()

    # ✅ Generar tabla de amortización
    # La tabla amortiza el capital_financiado completo (no solo monto_aprobado)
    saldo_capital_restante = capital_financiado  # ✅ CORRECCIÓN: Amortizar el capital financiado total
    fecha_cuota = credito.fecha_proximo_pago

    for i in range(1, credito.plazo + 1):
        # Calcular interés sobre el saldo restante
        interes_a_pagar = saldo_capital_restante * tasa_mensual
        capital_a_pagar = credito.valor_cuota - interes_a_pagar

        # Ajuste para la última cuota para evitar diferencias por redondeo
        if i == credito.plazo:
            capital_a_pagar = saldo_capital_restante
            interes_a_pagar = credito.valor_cuota - capital_a_pagar
            if interes_a_pagar < 0:
                interes_a_pagar = Decimal('0.00')
                capital_a_pagar = credito.valor_cuota

        # Actualizar saldo restante
        saldo_capital_restante -= capital_a_pagar

        # Asegurar que no quede negativo por redondeo
        if saldo_capital_restante < 0:
            saldo_capital_restante = Decimal('0.00')

        # Crear cuota en la tabla de amortización
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=i,
            fecha_vencimiento=fecha_cuota,
            capital_a_pagar=capital_a_pagar,
            interes_a_pagar=interes_a_pagar,
            valor_cuota=credito.valor_cuota,
            saldo_capital_pendiente=saldo_capital_restante
        )

        # Avanzar a la siguiente fecha de cuota
        fecha_cuota += relativedelta(months=1)

    logger.info(
        f"Crédito {credito.numero_credito} activado exitosamente. "
        f"Línea: {credito.get_linea_display()}, Tasa: {tasa_interes}% mensual, "
        f"Cuota: ${valor_cuota:,.2f}, Plazo: {credito.plazo} meses, "
        f"Total a pagar: ${total_a_pagar:,.2f}"
    )

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

    # Determinar tipo de usuario (empleado/libranza vs emprendedor)
    from gestion_creditos.models import Credito
    es_empleado = user.groups.filter(name='Empleados').exists()
    tiene_credito_libranza = Credito.objects.filter(
        usuario=user,
        linea='LIBRANZA'
    ).exists()
    es_libranza = es_empleado or tiene_credito_libranza

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
        'es_empleado': es_empleado,
        'es_libranza': es_libranza,
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
                    monto_limpio = monto_str.replace('.', '').replace(',', '.')
                    monto_a_pagar = Decimal(monto_limpio)
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
        estado=Credito.EstadoCredito.ACTIVO,
        fecha_proximo_pago__lt=hoy
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
