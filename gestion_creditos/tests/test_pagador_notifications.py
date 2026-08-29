from datetime import date, datetime
from decimal import Decimal
import io
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from gestion_creditos.models import Credito, CreditoLibranza, CuotaAmortizacion, Empresa
from gestion_creditos.services.pagador_notifications import (
    enviar_resumenes_pagador,
    preparar_lotes_resumen_pagador,
)
from gestion_creditos.services.mora_notifications import (
    SEVERIDAD_ALTA,
    SEVERIDAD_CRITICA,
    SEVERIDAD_IMPORTANTE,
    clasificar_severidad_mora,
    procesar_alertas_mora_colaborador,
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
            fecha_vencimiento=date(2026, 5, 1),
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
        self.assertEqual(resultado['fecha_inicio_vencimiento'], date(2026, 5, 1))
        self.assertEqual(resultado['fecha_fin_vencimiento'], date(2026, 5, 1))
        self.assertEqual(len(resultado['batches']), 1)
        self.assertEqual(resultado['batches'][0]['destinatarios'], ['pagador@empresa.test', 'nomina@empresa.test'])

    def test_preparar_lotes_incluye_cuota_del_dia_siguiente_al_cierre(self):
        self.cuota.fecha_vencimiento = date(2026, 7, 1)
        self.cuota.save(update_fields=['fecha_vencimiento'])

        resultado = preparar_lotes_resumen_pagador(
            fecha_referencia=date(2026, 6, 30),
            exigir_ventana_mensual=True,
        )

        self.assertEqual(resultado['status'], 'ready')
        self.assertEqual(resultado['window'], 'month_end')
        self.assertEqual(resultado['fecha_inicio_vencimiento'], date(2026, 7, 1))
        self.assertEqual(len(resultado['batches']), 1)
        self.assertEqual(resultado['batches'][0]['cuotas'], [self.cuota])

    def test_preparar_lotes_no_incluye_cuota_vencida_el_mismo_cierre(self):
        self.cuota.fecha_vencimiento = date(2026, 6, 30)
        self.cuota.save(update_fields=['fecha_vencimiento'])

        resultado = preparar_lotes_resumen_pagador(
            fecha_referencia=date(2026, 6, 30),
            exigir_ventana_mensual=True,
        )

        self.assertEqual(resultado['status'], 'ready')
        self.assertEqual(resultado['diagnostics']['cuotas_evaluadas'], 0)
        self.assertEqual(len(resultado['batches']), 0)

    def test_preparar_lotes_no_reenvia_en_catchup_si_ya_notifico_cierre(self):
        self.cuota.fecha_vencimiento = date(2026, 7, 1)
        self.cuota.fecha_ultimo_recordatorio_pagador = timezone.make_aware(datetime(2026, 6, 30, 8, 0))
        self.cuota.save(update_fields=['fecha_vencimiento', 'fecha_ultimo_recordatorio_pagador'])

        resultado = preparar_lotes_resumen_pagador(
            fecha_referencia=date(2026, 7, 1),
            exigir_ventana_mensual=True,
        )

        self.assertEqual(resultado['status'], 'ready')
        self.assertEqual(resultado['window'], 'catchup_day_after')
        self.assertEqual(resultado['diagnostics']['cuotas_evaluadas'], 1)
        self.assertEqual(resultado['diagnostics']['cuotas_omitidas_ya_enviadas'], 1)
        self.assertEqual(len(resultado['batches']), 0)

    def test_empresa_sin_destinatarios_no_genera_envio(self):
        empresa_sin_destinatarios = Empresa.objects.create(
            nombre='Empresa sin destinatarios',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.MIXTA,
        )
        usuario = User.objects.create_user(username='sin-destino', email='sin-destino@aprobado.test')
        credito = Credito.objects.create(
            usuario=usuario,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            numero_credito='CR-SIN-DEST',
            monto_solicitado=Decimal('500000.00'),
            monto_aprobado=Decimal('500000.00'),
            plazo_solicitado=2,
            plazo=2,
            valor_cuota=Decimal('250000.00'),
            saldo_pendiente=Decimal('250000.00'),
            capital_pendiente=Decimal('250000.00'),
            total_a_pagar=Decimal('500000.00'),
        )
        CreditoLibranza.objects.create(
            credito=credito,
            empresa=empresa_sin_destinatarios,
            direccion='Calle 2',
            telefono='3000000000',
            correo_electronico='usuario@empresa.test',
            cedula='100000002',
            nombres='Usuario',
            apellidos='Sin destino',
        )
        CuotaAmortizacion.objects.create(
            credito=credito,
            numero_cuota=1,
            fecha_vencimiento=date(2026, 7, 1),
            capital_a_pagar=Decimal('250000.00'),
            interes_a_pagar=Decimal('0.00'),
            valor_cuota=Decimal('250000.00'),
            saldo_capital_pendiente=Decimal('0.00'),
            pagada=False,
            monto_pagado=Decimal('0.00'),
        )

        resultado = preparar_lotes_resumen_pagador(
            fecha_referencia=date(2026, 6, 30),
            exigir_ventana_mensual=True,
        )

        self.assertEqual(resultado['diagnostics']['empresas_con_cuotas'], 1)
        self.assertEqual(resultado['diagnostics']['empresas_con_envio'], 0)
        self.assertEqual(resultado['diagnostics']['empresas_sin_destinatarios'], ['Empresa sin destinatarios'])
        self.assertEqual(resultado['batches'], [])

    def test_respeta_pagada_estado_y_linea_permitida(self):
        self.cuota.fecha_vencimiento = date(2026, 7, 1)
        self.cuota.save(update_fields=['fecha_vencimiento'])
        credito_pagado = Credito.objects.create(
            usuario=self.empleado,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            numero_credito='CR-PAGADA',
            monto_solicitado=Decimal('500000.00'),
            monto_aprobado=Decimal('500000.00'),
            plazo_solicitado=2,
            plazo=2,
            valor_cuota=Decimal('250000.00'),
        )
        CreditoLibranza.objects.create(
            credito=credito_pagado,
            empresa=self.empresa,
            direccion='Calle 3',
            telefono='3000000000',
            correo_electronico='empleado@empresa.test',
            cedula='100000003',
            nombres='Empleado',
            apellidos='Pagado',
        )
        CuotaAmortizacion.objects.create(
            credito=credito_pagado,
            numero_cuota=1,
            fecha_vencimiento=date(2026, 7, 1),
            capital_a_pagar=Decimal('250000.00'),
            interes_a_pagar=Decimal('0.00'),
            valor_cuota=Decimal('250000.00'),
            saldo_capital_pendiente=Decimal('0.00'),
            pagada=True,
            monto_pagado=Decimal('250000.00'),
        )
        self.credito.estado = Credito.EstadoCredito.ACTIVO
        self.credito.save(update_fields=['estado'])

        resultado = preparar_lotes_resumen_pagador(
            fecha_referencia=date(2026, 6, 30),
            exigir_ventana_mensual=True,
        )

        self.assertEqual(resultado['diagnostics']['cuotas_evaluadas'], 1)
        self.assertEqual(resultado['batches'][0]['cuotas'], [self.cuota])

    def test_resumen_mora_excluye_credito_pagado_aunque_tenga_cuota_pendiente(self):
        self.cuota.fecha_vencimiento = date(2026, 7, 1)
        self.cuota.save(update_fields=['fecha_vencimiento'])
        Credito.objects.filter(pk=self.credito.pk).update(
            estado=Credito.EstadoCredito.PAGADO,
        )

        resultado = preparar_lotes_resumen_pagador(
            fecha_referencia=date(2026, 6, 30),
            exigir_ventana_mensual=True,
        )

        self.assertEqual(resultado['diagnostics']['cuotas_evaluadas'], 0)
        self.assertEqual(resultado['batches'], [])

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
        self.assertIn('Obligaciones próximas a vencer', html)
        self.assertIn('cuotas próximas a vencer', html)
        self.assertNotIn('Se envía una sola vez al cierre de mes', html)
        self.assertNotIn('solo al pagador y a los correos internos', html)

    def test_alerta_usuario_muestra_dias_reales_y_contexto_pagador(self):
        enviado = enviar_alerta_obligacion_pendiente_usuario(
            credito=self.credito,
            cuota=self.cuota,
            dias_atraso=10,
        )

        self.assertTrue(enviado)
        html = mail.outbox[-1].alternatives[0][0]
        self.assertIn('<strong>10 d&iacute;as de mora</strong>', html)
        self.assertIn('empresa o pagadur&iacute;a', html)
        self.assertIn('Nivel Importante', html)
        self.assertEqual(
            mail.outbox[-1].subject,
            'Importante: tu crédito continúa pendiente de normalización - CR-MAIL-001',
        )

    def test_severidad_empieza_en_dia_10_y_progresa_en_limites(self):
        casos = {
            9: None,
            10: SEVERIDAD_IMPORTANTE,
            11: SEVERIDAD_IMPORTANTE,
            14: SEVERIDAD_IMPORTANTE,
            15: SEVERIDAD_ALTA,
            29: SEVERIDAD_ALTA,
            30: SEVERIDAD_CRITICA,
            45: SEVERIDAD_CRITICA,
        }

        for dias, esperado in casos.items():
            with self.subTest(dias=dias):
                self.assertEqual(clasificar_severidad_mora(dias), esperado)

    def test_envio_directo_antes_del_dia_10_es_rechazado(self):
        enviado = enviar_alerta_obligacion_pendiente_usuario(
            credito=self.credito,
            cuota=self.cuota,
            dias_atraso=9,
        )

        self.assertFalse(enviado)
        self.assertEqual(len(mail.outbox), 0)

    def test_dia_11_y_dia_45_generan_copy_y_asunto_dinamicos(self):
        for dias, asunto, severidad in [
            (11, 'Importante: tu crédito continúa pendiente de normalización', 'Importante'),
            (45, 'Alerta crítica: tu crédito continúa en mora por 45 días', 'Crítica'),
        ]:
            with self.subTest(dias=dias):
                enviado = enviar_alerta_obligacion_pendiente_usuario(
                    credito=self.credito,
                    cuota=self.cuota,
                    dias_atraso=dias,
                )
                self.assertTrue(enviado)
                correo = mail.outbox[-1]
                html = correo.alternatives[0][0]
                self.assertIn(f'{dias} d&iacute;as de mora', html)
                self.assertIn(f'Nivel {severidad}', html)
                self.assertEqual(correo.subject, f'{asunto} - CR-MAIL-001')

    def test_template_no_contiene_diez_dias_hardcodeado(self):
        template = (
            Path(__file__).resolve().parents[2]
            / 'templates'
            / 'emails'
            / 'usuarios'
            / 'usuario_alerta_obligacion_pendiente.html'
        ).read_text(encoding='utf-8')

        self.assertNotIn('10 d&iacute;as', template)
        self.assertIn('{{ dias_mora }} d&iacute;as de mora', template)

    def test_proceso_diario_no_envia_en_dia_9_y_envia_en_dia_10(self):
        self.cuota.fecha_vencimiento = date(2026, 8, 20)
        self.cuota.fecha_ultimo_recordatorio_pagador = timezone.make_aware(
            datetime(2026, 8, 1, 8, 0)
        )
        self.cuota.save(update_fields=['fecha_vencimiento', 'fecha_ultimo_recordatorio_pagador'])

        dia_9 = procesar_alertas_mora_colaborador(
            instante_referencia=timezone.make_aware(datetime(2026, 8, 29, 9, 0))
        )
        dia_10 = procesar_alertas_mora_colaborador(
            instante_referencia=timezone.make_aware(datetime(2026, 8, 30, 9, 0))
        )

        self.assertEqual(dia_9['alertas_enviadas'], 0)
        self.assertEqual(dia_10['alertas_enviadas'], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('10 d&iacute;as de mora', mail.outbox[0].alternatives[0][0])

    def test_proceso_diario_no_duplica_por_credito_en_retry_mismo_dia(self):
        self.cuota.fecha_vencimiento = date(2026, 8, 19)
        self.cuota.fecha_ultimo_recordatorio_pagador = timezone.make_aware(
            datetime(2026, 8, 1, 8, 0)
        )
        self.cuota.save(update_fields=['fecha_vencimiento', 'fecha_ultimo_recordatorio_pagador'])
        instante = timezone.make_aware(datetime(2026, 8, 29, 9, 0))

        primero = procesar_alertas_mora_colaborador(instante_referencia=instante)
        retry = procesar_alertas_mora_colaborador(instante_referencia=instante)

        self.assertEqual(primero['alertas_enviadas'], 1)
        self.assertEqual(retry['alertas_enviadas'], 0)
        self.assertEqual(retry['omitidos_duplicado'], 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_varias_cuotas_vencidas_generan_un_aviso_por_credito_y_dia(self):
        instante_recordatorio = timezone.make_aware(datetime(2026, 8, 1, 8, 0))
        self.cuota.fecha_vencimiento = date(2026, 8, 18)
        self.cuota.fecha_ultimo_recordatorio_pagador = instante_recordatorio
        self.cuota.save(update_fields=['fecha_vencimiento', 'fecha_ultimo_recordatorio_pagador'])
        segunda = CuotaAmortizacion.objects.create(
            credito=self.credito,
            numero_cuota=2,
            fecha_vencimiento=date(2026, 8, 19),
            capital_a_pagar=Decimal('200000.00'),
            interes_a_pagar=Decimal('38915.48'),
            valor_cuota=Decimal('238915.48'),
            saldo_capital_pendiente=Decimal('0.00'),
            pagada=False,
            monto_pagado=Decimal('0.00'),
            fecha_ultimo_recordatorio_pagador=instante_recordatorio,
        )

        resultado = procesar_alertas_mora_colaborador(
            instante_referencia=timezone.make_aware(datetime(2026, 8, 29, 9, 0))
        )

        self.assertEqual(resultado['alertas_enviadas'], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.cuota.refresh_from_db()
        segunda.refresh_from_db()
        self.assertIsNotNone(self.cuota.fecha_ultimo_aviso_usuario_mora)
        self.assertIsNone(segunda.fecha_ultimo_aviso_usuario_mora)

    def test_dias_mora_usan_la_cuota_impaga_mas_antigua(self):
        instante_recordatorio = timezone.make_aware(datetime(2026, 8, 1, 8, 0))
        instante_proceso = timezone.make_aware(datetime(2026, 8, 29, 9, 0))
        self.cuota.fecha_vencimiento = date(2026, 7, 28)  # 32 dias
        self.cuota.fecha_ultimo_recordatorio_pagador = instante_recordatorio
        self.cuota.save(update_fields=['fecha_vencimiento', 'fecha_ultimo_recordatorio_pagador'])

        cuotas_adicionales = []
        for numero, fecha_vencimiento, pagada in [
            (2, date(2026, 8, 14), False),  # 15 dias
            (3, date(2026, 8, 27), False),  # 2 dias
            (4, date(2026, 6, 30), True),   # mas antigua, pero pagada
        ]:
            cuotas_adicionales.append(CuotaAmortizacion.objects.create(
                credito=self.credito,
                numero_cuota=numero,
                fecha_vencimiento=fecha_vencimiento,
                capital_a_pagar=Decimal('200000.00'),
                interes_a_pagar=Decimal('38915.48'),
                valor_cuota=Decimal('238915.48'),
                saldo_capital_pendiente=Decimal('0.00'),
                pagada=pagada,
                monto_pagado=Decimal('238915.48') if pagada else Decimal('0.00'),
                fecha_pago=instante_recordatorio if pagada else None,
                fecha_ultimo_recordatorio_pagador=instante_recordatorio,
            ))

        primero = procesar_alertas_mora_colaborador(instante_referencia=instante_proceso)
        retry = procesar_alertas_mora_colaborador(instante_referencia=instante_proceso)

        self.assertEqual(primero['alertas_enviadas'], 1)
        self.assertEqual(retry['alertas_enviadas'], 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('32 d&iacute;as de mora', mail.outbox[0].alternatives[0][0])
        self.assertIn('Nivel Crítica', mail.outbox[0].alternatives[0][0])
        self.assertEqual(
            mail.outbox[0].subject,
            'Alerta crítica: tu crédito continúa en mora por 32 días - CR-MAIL-001',
        )
        self.cuota.refresh_from_db()
        self.assertIsNotNone(self.cuota.fecha_ultimo_aviso_usuario_mora)
        for cuota in cuotas_adicionales:
            cuota.refresh_from_db()
            self.assertIsNone(cuota.fecha_ultimo_aviso_usuario_mora)

    def test_credito_normalizado_no_genera_aviso(self):
        self.cuota.fecha_vencimiento = date(2026, 8, 1)
        self.cuota.fecha_ultimo_recordatorio_pagador = timezone.make_aware(
            datetime(2026, 8, 1, 8, 0)
        )
        self.cuota.pagada = True
        self.cuota.save(update_fields=[
            'fecha_vencimiento',
            'fecha_ultimo_recordatorio_pagador',
            'pagada',
        ])

        resultado = procesar_alertas_mora_colaborador(
            instante_referencia=timezone.make_aware(datetime(2026, 8, 29, 9, 0))
        )

        self.assertEqual(resultado['alertas_enviadas'], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_reingreso_en_mora_usa_fecha_de_nueva_cuota(self):
        aviso_anterior = timezone.make_aware(datetime(2026, 7, 20, 9, 0))
        self.cuota.pagada = True
        self.cuota.fecha_ultimo_aviso_usuario_mora = aviso_anterior
        self.cuota.save(update_fields=['pagada', 'fecha_ultimo_aviso_usuario_mora'])
        nueva_cuota = CuotaAmortizacion.objects.create(
            credito=self.credito,
            numero_cuota=2,
            fecha_vencimiento=date(2026, 8, 19),
            capital_a_pagar=Decimal('200000.00'),
            interes_a_pagar=Decimal('38915.48'),
            valor_cuota=Decimal('238915.48'),
            saldo_capital_pendiente=Decimal('0.00'),
            pagada=False,
            monto_pagado=Decimal('0.00'),
            fecha_ultimo_recordatorio_pagador=timezone.make_aware(
                datetime(2026, 8, 1, 8, 0)
            ),
        )

        resultado = procesar_alertas_mora_colaborador(
            instante_referencia=timezone.make_aware(datetime(2026, 8, 29, 9, 0))
        )

        self.assertEqual(resultado['alertas_enviadas'], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('10 d&iacute;as de mora', mail.outbox[0].alternatives[0][0])
        nueva_cuota.refresh_from_db()
        self.assertIsNotNone(nueva_cuota.fecha_ultimo_aviso_usuario_mora)

    def test_frecuencia_diaria_actualiza_dias_reales(self):
        self.cuota.fecha_vencimiento = date(2026, 8, 19)
        self.cuota.fecha_ultimo_recordatorio_pagador = timezone.make_aware(
            datetime(2026, 8, 1, 8, 0)
        )
        self.cuota.save(update_fields=['fecha_vencimiento', 'fecha_ultimo_recordatorio_pagador'])

        procesar_alertas_mora_colaborador(
            instante_referencia=timezone.make_aware(datetime(2026, 8, 29, 9, 0))
        )
        procesar_alertas_mora_colaborador(
            instante_referencia=timezone.make_aware(datetime(2026, 8, 30, 9, 0))
        )

        self.assertEqual(len(mail.outbox), 2)
        self.assertIn('10 d&iacute;as de mora', mail.outbox[0].alternatives[0][0])
        self.assertIn('11 d&iacute;as de mora', mail.outbox[1].alternatives[0][0])
