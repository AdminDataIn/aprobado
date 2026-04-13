from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from gestion_creditos.models import Empresa, MarketplaceItem, MarketplaceLiquidacionEmpresa, MarketplacePago, MarketplacePedido
from gestion_creditos.services.marketplace_checkout_service import (
    calcular_totales_checkout,
    crear_pedido_marketplace,
    marcar_pago_marketplace_aprobado,
)


class MarketplaceCheckoutServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='comprador',
            email='comprador@example.com',
            password='Temporal123!',
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Test',
            marketplace_fee_percent=Decimal('12.50'),
            pagos_habilitados=True,
        )
        self.item = MarketplaceItem.objects.create(
            empresa=self.empresa,
            titulo='Curso premium',
            descripcion='Acceso completo',
            beneficio='10% de descuento',
            tipo=MarketplaceItem.TipoItem.SERVICIO,
            precio='$400.000',
            estado=MarketplaceItem.EstadoItem.APROBADO,
        )

    def test_calcula_totales_checkout_y_comision(self):
        totals = calcular_totales_checkout(self.item, cantidad=2)
        self.assertEqual(totals['precio_unitario'], Decimal('400000'))
        self.assertEqual(totals['subtotal'], Decimal('800000.00'))
        self.assertEqual(totals['marketplace_fee_amount'], Decimal('100000.00'))
        self.assertEqual(totals['valor_neto_empresa'], Decimal('700000.00'))
        self.assertEqual(totals['total'], Decimal('800000.00'))

    def test_crea_pedido_pago_y_liquidacion(self):
        pedido, pago = crear_pedido_marketplace(
            self.item,
            {
                'comprador_nombre': 'Comprador Test',
                'comprador_email': 'comprador@example.com',
                'comprador_telefono': '3001234567',
                'cantidad': 1,
                'nombre_contacto': 'Comprador Test',
                'telefono_contacto': '3001234567',
                'direccion_linea_1': 'Calle 1 # 2-3',
                'direccion_linea_2': '',
                'ciudad': 'Bogota',
                'departamento': 'Cundinamarca',
                'referencia': 'Porteria',
                'instrucciones': 'Entregar en recepcion',
                'notas': 'Pedido MVP',
            },
            comprador=self.user,
        )

        self.assertEqual(pedido.estado, MarketplacePedido.EstadoPedido.PENDIENTE_PAGO)
        self.assertEqual(pago.estado, MarketplacePago.EstadoPago.CREADO)
        self.assertEqual(pedido.items.count(), 1)
        self.assertTrue(hasattr(pedido, 'direccion_entrega'))
        self.assertTrue(hasattr(pedido, 'liquidacion_empresa'))
        self.assertEqual(pedido.subtotal, Decimal('400000.00'))
        self.assertEqual(pedido.marketplace_fee_amount, Decimal('50000.00'))
        self.assertEqual(pedido.liquidacion_empresa.valor_neto, Decimal('350000.00'))

        marcar_pago_marketplace_aprobado(pago, provider_payment_id='mp-123')
        pago.refresh_from_db()
        pedido.refresh_from_db()
        liquidacion = MarketplaceLiquidacionEmpresa.objects.get(pedido=pedido)

        self.assertEqual(pago.estado, MarketplacePago.EstadoPago.APROBADO)
        self.assertEqual(pedido.estado, MarketplacePedido.EstadoPedido.PAGADO)
        self.assertEqual(liquidacion.estado, MarketplaceLiquidacionEmpresa.EstadoLiquidacion.PENDIENTE)
        self.assertEqual(liquidacion.external_reference, 'mp-123')
