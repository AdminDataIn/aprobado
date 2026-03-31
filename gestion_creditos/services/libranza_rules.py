from calendar import monthrange
from datetime import date, datetime

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.utils import timezone

from gestion_creditos.models import Credito, CreditoLibranza


ESTADOS_BLOQUEO_SOLICITUD_LIBRANZA = (
    Credito.EstadoCredito.ACTIVO,
    Credito.EstadoCredito.EN_MORA,
    Credito.EstadoCredito.PENDIENTE_FIRMA,
    Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
    Credito.EstadoCredito.APROBADO_PAGADOR,
)


def permitir_multiples_creditos_libranza_en_pruebas():
    return bool(getattr(settings, 'ALLOW_MULTIPLE_LIBRANZA_ACTIVE_CREDITS_FOR_TESTING', False))


def calcular_primera_fecha_pago_libranza(fecha_aprobacion=None, fecha_forzada=None):
    if fecha_forzada:
        return _to_date(fecha_forzada)

    fecha_base = _to_date(fecha_aprobacion) if fecha_aprobacion else timezone.localdate()
    if fecha_base.day <= 14:
        return (fecha_base + relativedelta(months=1)).replace(day=1)
    return (fecha_base + relativedelta(months=2)).replace(day=1)


def obtener_creditos_libranza_bloqueantes(cedula):
    if not cedula or permitir_multiples_creditos_libranza_en_pruebas():
        return CreditoLibranza.objects.none()

    return (
        CreditoLibranza.objects
        .select_related('credito')
        .filter(
            cedula=cedula,
            credito__estado__in=ESTADOS_BLOQUEO_SOLICITUD_LIBRANZA,
        )
        .order_by('-credito__fecha_solicitud')
    )


def obtener_plazo_credito_aplicado(credito):
    return int(credito.plazo_forzado or credito.plazo or credito.plazo_solicitado or 0)


def obtener_tasa_credito_aplicada(credito, tasa_default):
    return credito.tasa_forzada if credito.tasa_forzada is not None else (credito.tasa_interes or tasa_default)


def obtener_fecha_primera_cuota_credito(credito, fecha_aprobacion=None):
    return calcular_primera_fecha_pago_libranza(
        fecha_aprobacion=fecha_aprobacion,
        fecha_forzada=credito.fecha_primera_cuota_forzada,
    )


def obtener_dia_ancla_vencimiento(credito, fecha_base=None):
    if fecha_base:
        return _to_date(fecha_base).day

    primera_cuota = credito.tabla_amortizacion.order_by('numero_cuota').values_list('fecha_vencimiento', flat=True).first()
    if primera_cuota:
        return _to_date(primera_cuota).day

    if credito.fecha_primera_cuota_forzada:
        return _to_date(credito.fecha_primera_cuota_forzada).day

    if credito.fecha_proximo_pago:
        return _to_date(credito.fecha_proximo_pago).day

    return 30 if credito.linea == Credito.LineaCredito.LIBRANZA else 1


def sumar_meses_con_dia_ancla(fecha_base, meses=1, dia_ancla=None):
    fecha_cursor = _to_date(fecha_base) + relativedelta(months=meses, day=1)
    dia_objetivo = dia_ancla or _to_date(fecha_base).day
    ultimo_dia = monthrange(fecha_cursor.year, fecha_cursor.month)[1]
    return fecha_cursor.replace(day=min(dia_objetivo, ultimo_dia))


def reprogramar_cuotas_pendientes(credito, fecha_primera_cuota):
    fecha_cursor = _to_date(fecha_primera_cuota)
    dia_ancla = obtener_dia_ancla_vencimiento(credito, fecha_cursor)
    cuotas_pendientes = list(credito.tabla_amortizacion.filter(pagada=False).order_by('numero_cuota'))
    for cuota in cuotas_pendientes:
        cuota.fecha_vencimiento = fecha_cursor
        cuota.save(update_fields=['fecha_vencimiento'])
        if credito.linea == Credito.LineaCredito.LIBRANZA:
            fecha_cursor = sumar_meses_con_dia_ancla(fecha_cursor, 1, dia_ancla)
        else:
            fecha_cursor += relativedelta(months=1)

    credito.fecha_proximo_pago = fecha_primera_cuota
    credito.save(update_fields=['fecha_proximo_pago'])
    return cuotas_pendientes


def _to_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return timezone.localdate()
