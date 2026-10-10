from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from gestion_creditos.credit_services import activar_credito
from gestion_creditos.models import Credito, CuotaAmortizacion
from gestion_creditos.services.costo_originacion_libranza import (
    FECHA_INICIO_V2, requiere_snapshot_originacion_libranza,
)


class RegresionActivacionLibranzaTradicionalTest(TestCase):
    def test_activar_sin_componentes_conserva_formula_tradicional(self):
        usuario = get_user_model().objects.create_user('libranza-tradicional')
        credito = Credito.objects.create(
            usuario=usuario,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            monto_solicitado=Decimal('1000000.00'),
            plazo_solicitado=12,
            monto_aprobado=Decimal('1000000.00'),
            plazo=12,
            tasa_interes=Decimal('1.90'),
        )
        # This regression covers the historical 10% fallback, not a new origination.
        fecha_historica = timezone.make_aware(datetime.combine(
            FECHA_INICIO_V2 - timedelta(days=1), time(hour=12),
        ))
        Credito.objects.filter(pk=credito.pk).update(fecha_solicitud=fecha_historica)
        credito.refresh_from_db()
        self.assertLess(timezone.localdate(credito.fecha_solicitud), FECHA_INICIO_V2)
        self.assertFalse(requiere_snapshot_originacion_libranza(credito))
        capital = Decimal('1119000.00')
        tasa = Decimal('0.019')
        factor = (tasa * (Decimal('1') + tasa) ** 12) / (
            ((Decimal('1') + tasa) ** 12) - Decimal('1')
        )
        cuota_esperada = (capital * factor).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP,
        )

        activar_credito(credito)
        credito.refresh_from_db()

        self.assertEqual(credito.comision, Decimal('100000.00'))
        self.assertEqual(credito.iva_comision, Decimal('19000.00'))
        self.assertEqual(credito.saldo_pendiente, capital)
        self.assertEqual(credito.capital_pendiente, Decimal('1000000.00'))
        self.assertEqual(credito.valor_cuota, cuota_esperada)
        self.assertEqual(CuotaAmortizacion.objects.filter(credito=credito).count(), 12)
