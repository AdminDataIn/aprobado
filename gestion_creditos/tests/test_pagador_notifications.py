from datetime import date
from decimal import Decimal
import io

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from gestion_creditos.models import Credito, CreditoLibranza, CuotaAmortizacion, Empresa
from gestion_creditos.services.pagador_notifications import (
    enviar_resumenes_pagador,
    preparar_lotes_resumen_pagador,
)
from gestion_creditos.email_service import enviar_alerta_obligacion_pendiente_usuario
from usuarios.models import PerfilPagador


User = get_user_model()


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    CREDIT_INTERNAL_NOTIFICATION_EMAILS=['interno@aprobado.test'],
)
class PagadorNotificationsTest(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(
            nombre='Empresa Pagadora',
            convenio_activo=True,
            correo_contacto='nomina@empresa.test',
            tipo_empresa=Empresa.TipoEmpresa.MIXTA,
        )
        self.pagador_user = User.objects.create_user(
            username='pagador-mail',
            email='pagador@empresa.test',
            password='123456',
        )
        PerfilPagador.objects.create(
            usuario=self.pagador_user,
            empresa=self.empresa,
            es_pagador=True,
        )
        self.empleado = User.objects.create_user(
            username='empleado-mail',
            email='empleado@empresa.test',
            password='123456',
        )
        self.credito = Credito.objects.create(
            usuario=self.empleado,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            numero_credito='CR-MAIL-001',
            monto_solicitado=Decimal('1200000.00'),
            monto_aprobado=Decimal('1200000.00'),
            plazo_solicitado=6,
            plazo=6,
            valor_cuota=Decimal('238915.48'),
            saldo_pendiente=Decimal('238915.48'),
            capital_pendiente=Decimal('238915.48'),
            total_a_pagar=Decimal('1433492.88'),
            comision=Decimal('120000.00'),
            iva_comision=Decimal('22800.00'),
            fecha_proximo_pago=date(2026, 4, 30),
        )
        CreditoLibranza.objects.create(
            credito=self.credito,
            empresa=self.empresa,
            direccion='Calle 1',
            telefono='3001234567',
            correo_electronico='empleado@empresa.test',
            cedula='100000001',
            nombres='Empleado',
            apellidos='Prueba',
        )
        self.cuota = CuotaAmortizacion.objects.create(
            credito=self.credito,
            numero_cuota=1,
            fecha_vencimiento=date(2026, 4, 30),
            capital_a_pagar=Decimal('200000.00'),
            interes_a_pagar=Decimal('38915.48'),
            valor_cuota=Decimal('238915.48'),
            saldo_capital_pendiente=Decimal('0.00'),
            pagada=False,
            monto_pagado=Decimal('0.00'),
        )

    def test_preparar_lotes_fuera_de_ventana_mensual_se_omite(self):
        resultado = preparar_lotes_resumen_pagador(
            fecha_referencia=date(2026, 5, 2),
            exigir_ventana_mensual=True,
        )

        self.assertEqual(resultado['status'], 'skipped')
        self.assertEqual(resultado['reason'], 'not_month_end')

    def test_preparar_lotes_permite_catchup_al_dia_siguiente(self):
        resultado = preparar_lotes_resumen_pagador(
            fecha_referencia=date(2026, 5, 1),
            exigir_ventana_mensual=True,
        )

        self.assertEqual(resultado['status'], 'ready')
        self.assertEqual(resultado['window'], 'catchup_day_after')
        self.assertEqual(resultado['fecha_corte'], date(2026, 4, 30))
        self.assertEqual(len(resultado['batches']), 1)
        self.assertEqual(resultado['batches'][0]['destinatarios'], ['pagador@empresa.test', 'nomina@empresa.test'])

    def test_envio_de_prueba_usa_destinatario_controlado_y_no_marca_cuota(self):
        resultado = enviar_resumenes_pagador(
            fecha_referencia=date(2026, 4, 30),
            exigir_ventana_mensual=True,
            destinatarios_override=['interno@aprobado.test'],
            include_internal_cc=False,
            marcar_enviado=False,
        )

        self.assertEqual(resultado['status'], 'success')
        self.assertEqual(resultado['empresas_notificadas'], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['interno@aprobado.test'])
        self.assertEqual(mail.outbox[0].cc, [])
        self.cuota.refresh_from_db()
        self.assertIsNone(self.cuota.fecha_ultimo_recordatorio_pagador)

    def test_comando_usa_correo_interno_por_defecto(self):
        stdout = io.StringIO()

        call_command(
            'enviar_resumen_pagador_mensual',
            '--fecha-corte', '2026-04-30',
            '--force',
            '--empresa-id', str(self.empresa.id),
            stdout=stdout,
        )

        self.assertIn('Envio de prueba completado', stdout.getvalue())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['interno@aprobado.test'])

    def test_resumen_pagador_renderiza_cta_y_no_incluye_textos_internos(self):
        enviar_resumenes_pagador(
            fecha_referencia=date(2026, 4, 30),
            exigir_ventana_mensual=True,
            destinatarios_override=['interno@aprobado.test'],
            include_internal_cc=False,
            marcar_enviado=False,
        )

        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('Ir al panel del pagador', html)
        self.assertIn('Resumen mensual de obligaciones', html)
        self.assertNotIn('Se envía una sola vez al cierre de mes', html)
        self.assertNotIn('solo al pagador y a los correos internos', html)

    def test_alerta_usuario_menciona_10_dias_despues_del_cierre(self):
        enviado = enviar_alerta_obligacion_pendiente_usuario(
            credito=self.credito,
            cuota=self.cuota,
            dias_atraso=10,
        )

        self.assertTrue(enviado)
        html = mail.outbox[-1].alternatives[0][0]
        self.assertIn('han pasado <strong>10 d&iacute;as</strong>', html)
        self.assertIn('sigue pendiente por regularizar', html)
