from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.utils import timezone

from gestion_creditos.models import Credito
from gestion_creditos.services.tasa_service import obtener_tasa_credito


PRODUCTO_LIBRANZA = 'payroll_loan'
PRODUCTO_CREDITO_WHATSAPP = 'whatsapp_credit'
PRODUCTOS_SOPORTADOS = {PRODUCTO_LIBRANZA, PRODUCTO_CREDITO_WHATSAPP}
DOS_DECIMALES = Decimal('0.01')


@dataclass(frozen=True)
class ConfiguracionProductoCredito:
    tipo_producto: str
    nombre: str
    descripcion: str
    flujo_actual: str
    tasa_mensual: Decimal
    tasa_originacion: Decimal
    tasa_iva: Decimal


def cuantizar_dinero(valor):
    return Decimal(valor).quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP)


def decimal_a_texto(valor):
    return format(cuantizar_dinero(valor), 'f')


def obtener_configuracion_producto(tipo_producto):
    if tipo_producto == PRODUCTO_LIBRANZA:
        return ConfiguracionProductoCredito(
            tipo_producto=PRODUCTO_LIBRANZA,
            nombre='Credito de libranza',
            descripcion='Credito de libranza para empleados de empresas con convenio activo.',
            flujo_actual='aprobado.com.co/libranza/',
            tasa_mensual=obtener_tasa_credito(Credito.LineaCredito.LIBRANZA),
            tasa_originacion=Decimal(str(getattr(settings, 'LIBRANZA_ORIGINATION_RATE', '10'))),
            tasa_iva=Decimal(str(getattr(settings, 'LIBRANZA_VAT_RATE', '19'))),
        )
    if tipo_producto == PRODUCTO_CREDITO_WHATSAPP:
        return ConfiguracionProductoCredito(
            tipo_producto=PRODUCTO_CREDITO_WHATSAPP,
            nombre='Credito por WhatsApp',
            descripcion='Nueva linea de credito originada desde el bot de WhatsApp.',
            flujo_actual='whatsapp',
            tasa_mensual=Decimal(str(getattr(settings, 'WHATSAPP_CREDIT_TASA_MENSUAL', '3.5'))),
            tasa_originacion=Decimal(str(getattr(settings, 'WHATSAPP_CREDIT_ORIGINATION_RATE', '10'))),
            tasa_iva=Decimal(str(getattr(settings, 'WHATSAPP_CREDIT_VAT_RATE', '19'))),
        )
    raise ValueError('Producto no soportado.')


def simular_credito(*, tipo_producto, monto, plazo_meses, numero_documento=None):
    """
    Fuente oficial backend para simulaciones usadas por el simulador web y API interna.

    Replica la formula del simulador publico: costo de originacion + IVA se financian
    como capital y la cuota se calcula por anualidad francesa con tasa mensual.
    """
    configuracion = obtener_configuracion_producto(tipo_producto)
    monto = cuantizar_dinero(monto)
    plazo_meses = int(plazo_meses)
    tasa_mensual = configuracion.tasa_mensual / Decimal('100')
    comision_originacion = cuantizar_dinero(monto * configuracion.tasa_originacion / Decimal('100'))
    iva = cuantizar_dinero(comision_originacion * configuracion.tasa_iva / Decimal('100'))
    capital_financiado = cuantizar_dinero(monto + comision_originacion + iva)

    if tasa_mensual > 0:
        factor = (tasa_mensual * (Decimal('1.00') + tasa_mensual) ** plazo_meses) / (
            ((Decimal('1.00') + tasa_mensual) ** plazo_meses) - Decimal('1.00')
        )
        cuota_mensual = cuantizar_dinero(capital_financiado * factor)
    else:
        cuota_mensual = cuantizar_dinero(capital_financiado / Decimal(plazo_meses))

    total_a_pagar = cuantizar_dinero(cuota_mensual * Decimal(plazo_meses))
    intereses = cuantizar_dinero(max(Decimal('0.00'), total_a_pagar - capital_financiado))
    vigente_hasta = timezone.localdate() + timezone.timedelta(
        days=int(getattr(settings, 'WHATSAPP_SIMULATION_VALID_DAYS', 7))
    )

    advertencias = []
    if tipo_producto == PRODUCTO_LIBRANZA:
        advertencias.append('La libranza requiere convenio activo del pagador y validacion laboral.')
    if not numero_documento:
        advertencias.append('Sin numero de documento solo se genera una simulacion anonima.')

    return {
        'amount': decimal_a_texto(monto),
        'term_months': plazo_meses,
        'origination_fee': decimal_a_texto(comision_originacion),
        'vat': decimal_a_texto(iva),
        'interest': decimal_a_texto(intereses),
        'total_to_pay': decimal_a_texto(total_a_pagar),
        'monthly_payment': decimal_a_texto(cuota_mensual),
        'valid_until': vigente_hasta.isoformat(),
        'warnings': advertencias,
    }


# Aliases legacy para compatibilidad con integraciones previas.
PRODUCT_PAYROLL_LOAN = PRODUCTO_LIBRANZA
PRODUCT_WHATSAPP_CREDIT = PRODUCTO_CREDITO_WHATSAPP
SUPPORTED_PRODUCTS = PRODUCTOS_SOPORTADOS
TWOPLACES = DOS_DECIMALES
ProductConfig = ConfiguracionProductoCredito
money = cuantizar_dinero
decimal_to_string = decimal_a_texto
get_product_config = obtener_configuracion_producto


def calculate_credit_simulation(*, product_type, amount, term_months, document_number=None):
    return simular_credito(
        tipo_producto=product_type,
        monto=amount,
        plazo_meses=term_months,
        numero_documento=document_number,
    )
