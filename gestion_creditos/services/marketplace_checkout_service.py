from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from gestion_creditos.forms_marketplace import parse_marketplace_price
from gestion_creditos.models import (
    MarketplaceDireccionEntrega,
    MarketplaceItem,
    MarketplaceLiquidacionEmpresa,
    MarketplacePago,
    MarketplacePedido,
    MarketplacePedidoItem,
)


def _round_money(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def validar_item_para_checkout(item):
    if item.estado != MarketplaceItem.EstadoItem.APROBADO:
        raise ValueError('Solo las publicaciones aprobadas pueden generar pedidos.')
    return parse_marketplace_price(item.precio)


def existe_pedido_pendiente_repetido(item, comprador):
    if not comprador:
        return False
    return MarketplacePedido.objects.filter(
        empresa=item.empresa,
        comprador=comprador,
        estado__in=[
            MarketplacePedido.EstadoPedido.BORRADOR,
            MarketplacePedido.EstadoPedido.PENDIENTE_PAGO,
        ],
        items__item=item,
    ).exists()


def calcular_totales_checkout(item, cantidad=1):
    precio_unitario = validar_item_para_checkout(item)
    cantidad = int(cantidad or 1)
    if cantidad < 1:
        raise ValueError('La cantidad debe ser al menos 1.')

    subtotal = _round_money(precio_unitario * cantidad)
    fee_percent = item.empresa.marketplace_fee_percent or Decimal('0.00')
    marketplace_fee_amount = _round_money(subtotal * fee_percent / Decimal('100'))
    valor_neto_empresa = _round_money(subtotal - marketplace_fee_amount)

    return {
        'precio_unitario': precio_unitario,
        'cantidad': cantidad,
        'subtotal': subtotal,
        'marketplace_fee_percent': fee_percent,
        'marketplace_fee_amount': marketplace_fee_amount,
        'total': subtotal,
        'valor_neto_empresa': valor_neto_empresa,
    }


@transaction.atomic
def crear_pedido_marketplace(item, form_data, comprador=None):
    totals = calcular_totales_checkout(item, cantidad=form_data['cantidad'])

    pedido = MarketplacePedido.objects.create(
        empresa=item.empresa,
        comprador=comprador,
        comprador_nombre=form_data['comprador_nombre'],
        comprador_email=form_data['comprador_email'],
        comprador_telefono=form_data['comprador_telefono'],
        estado=MarketplacePedido.EstadoPedido.PENDIENTE_PAGO,
        subtotal=totals['subtotal'],
        marketplace_fee_amount=totals['marketplace_fee_amount'],
        total=totals['total'],
        external_reference=f"{item.empresa.slug}-{timezone.now().strftime('%Y%m%d%H%M%S')}",
        notas=form_data.get('notas', ''),
    )

    MarketplacePedidoItem.objects.create(
        pedido=pedido,
        item=item,
        titulo_snapshot=item.titulo,
        tipo_snapshot=item.tipo,
        cantidad=totals['cantidad'],
        precio_unitario=totals['precio_unitario'],
        total_linea=totals['subtotal'],
    )

    MarketplaceDireccionEntrega.objects.create(
        pedido=pedido,
        nombre_contacto=form_data['nombre_contacto'],
        telefono_contacto=form_data['telefono_contacto'],
        direccion_linea_1=form_data['direccion_linea_1'],
        direccion_linea_2=form_data.get('direccion_linea_2', ''),
        ciudad=form_data['ciudad'],
        departamento=form_data.get('departamento', ''),
        referencia=form_data.get('referencia', ''),
        instrucciones=form_data.get('instrucciones', ''),
    )

    pago = MarketplacePago.objects.create(
        pedido=pedido,
        proveedor=MarketplacePago.ProveedorPago.MERCADO_PAGO,
        estado=MarketplacePago.EstadoPago.CREADO,
        external_reference=pedido.external_reference,
        amount_gross=totals['subtotal'],
        marketplace_fee_amount=totals['marketplace_fee_amount'],
        amount_net=totals['valor_neto_empresa'],
    )

    MarketplaceLiquidacionEmpresa.objects.create(
        pedido=pedido,
        empresa=item.empresa,
        estado=MarketplaceLiquidacionEmpresa.EstadoLiquidacion.PENDIENTE,
        valor_bruto=totals['subtotal'],
        marketplace_fee_amount=totals['marketplace_fee_amount'],
        valor_neto=totals['valor_neto_empresa'],
    )

    return pedido, pago


@transaction.atomic
def marcar_pago_marketplace_aprobado(
    pago,
    provider_payment_id='',
    provider_preference_id='',
    payload=None,
    paid_at=None,
):
    paid_at = paid_at or timezone.now()
    payload = payload or {}

    pago.estado = MarketplacePago.EstadoPago.APROBADO
    pago.provider_payment_id = provider_payment_id or pago.provider_payment_id
    pago.provider_preference_id = provider_preference_id or pago.provider_preference_id
    pago.payload = payload
    pago.paid_at = paid_at
    pago.save(
        update_fields=[
            'estado',
            'provider_payment_id',
            'provider_preference_id',
            'payload',
            'paid_at',
            'updated_at',
        ]
    )

    pedido = pago.pedido
    pedido.estado = MarketplacePedido.EstadoPedido.PAGADO
    pedido.save(update_fields=['estado', 'updated_at'])

    liquidacion = getattr(pedido, 'liquidacion_empresa', None)
    if liquidacion:
        liquidacion.estado = MarketplaceLiquidacionEmpresa.EstadoLiquidacion.PENDIENTE
        liquidacion.external_reference = provider_payment_id or liquidacion.external_reference
        liquidacion.save(update_fields=['estado', 'external_reference', 'updated_at'])

    return pago


def enviar_notificaciones_pedido_marketplace(pedido):
    items = list(pedido.items.all())
    direccion = getattr(pedido, 'direccion_entrega', None)
    direccion_texto = ''
    if direccion:
        direccion_texto = (
            f"{direccion.direccion_linea_1} "
            f"{direccion.direccion_linea_2 or ''}, "
            f"{direccion.ciudad} "
            f"{direccion.departamento or ''}"
        ).strip()

    context = {
        'pedido': pedido,
        'empresa': pedido.empresa,
        'items': items,
        'direccion': direccion,
        'direccion_texto': direccion_texto,
        'estado_pago': pedido.pago.get_estado_display() if getattr(pedido, 'pago', None) else 'Pendiente',
    }

    if pedido.empresa.correo_contacto:
        body = render_to_string('emails/marketplace_order_company.txt', context)
        html_body = render_to_string('emails/marketplace_order_company.html', context)
        email = EmailMultiAlternatives(
            subject=f"Nuevo pedido marketplace {pedido.numero_pedido}",
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[pedido.empresa.correo_contacto],
        )
        email.attach_alternative(html_body, 'text/html')
        email.send(fail_silently=True)

    if pedido.comprador_email:
        body = render_to_string('emails/marketplace_order_customer.txt', context)
        html_body = render_to_string('emails/marketplace_order_customer.html', context)
        email = EmailMultiAlternatives(
            subject=f"Confirmacion de pedido {pedido.numero_pedido}",
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[pedido.comprador_email],
        )
        email.attach_alternative(html_body, 'text/html')
        email.send(fail_silently=True)
