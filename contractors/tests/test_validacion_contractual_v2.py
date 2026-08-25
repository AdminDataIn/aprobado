from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase

from contractors.models import ContractorApplication
from contractors.services.validacion_contractual import (
    SOLICITAR_VALIDACION_EMPRESA,
    validar_contrato_prestador,
)
from gestion_creditos.models import Empresa


class ValidacionContractualPrestadorV2Test(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username='contrato-v2', password='test-password'
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Contrato V2', convenio_activo=True
        )
        self.hoy = date(2026, 7, 17)

    def test_contrato_vencido_bloquea(self):
        solicitud = self._solicitud(fecha_fin_contrato=self.hoy - timedelta(days=1))
        resultado = validar_contrato_prestador(solicitud, fecha_corte=self.hoy)
        self.assertEqual(resultado.estado, 'VENCIDO')
        self.assertIn('contrato:vencido', resultado.bloqueos)

    def test_contrato_terminado_bloquea(self):
        solicitud = self._solicitud(estado_contractual_declarado='TERMINADO')
        resultado = validar_contrato_prestador(solicitud, fecha_corte=self.hoy)
        self.assertEqual(resultado.estado, 'TERMINADO')
        self.assertFalse(resultado.capacidad_automatica)

    def test_contrato_suspendido_requiere_revision(self):
        solicitud = self._solicitud(estado_contractual_declarado='SUSPENDIDO')
        resultado = validar_contrato_prestador(solicitud, fecha_corte=self.hoy)
        self.assertEqual(resultado.estado, 'SUSPENDIDO')
        self.assertTrue(resultado.requiere_revision_manual)
        self.assertFalse(resultado.bloqueos)

    def test_duracion_contractual_calcula_fecha_final_con_meses_calendario(self):
        inicio = date(2026, 1, 31)
        solicitud = self._solicitud(
            fecha_inicio_contrato=inicio,
            fecha_fin_contrato=None,
            duracion_contrato_meses=8,
        )
        resultado = validar_contrato_prestador(solicitud, fecha_corte=date(2026, 2, 1))
        self.assertEqual(resultado.fecha_fin_efectiva, inicio + relativedelta(months=8))
        self.assertEqual(resultado.estado, 'VIGENTE')

    def test_fecha_no_determinable_requiere_revision(self):
        solicitud = self._solicitud(
            fecha_fin_contrato=None,
            duracion_contrato_meses=None,
        )
        resultado = validar_contrato_prestador(solicitud, fecha_corte=self.hoy)
        self.assertEqual(resultado.estado, 'NO_DETERMINABLE')
        self.assertTrue(resultado.requiere_revision_manual)

    def test_menos_de_un_mes_completo_no_genera_capacidad(self):
        solicitud = self._solicitud(fecha_fin_contrato=self.hoy + timedelta(days=29))
        resultado = validar_contrato_prestador(solicitud, fecha_corte=self.hoy)
        self.assertEqual(resultado.meses_financiables, 0)
        self.assertIn('contrato:menos_de_un_mes_financiable', resultado.bloqueos)

    def test_empresa_solo_confirma_hechos_contractuales(self):
        solicitud = self._solicitud(metadata_analisis_contractual={
            'identidad': {'documento_coincide': True},
            'empresa_sugerida': {
                'empresa_sugerida_id': None,
                'tipo_coincidencia': 'APROXIMADO',
            },
        })
        resultado = validar_contrato_prestador(solicitud, fecha_corte=self.hoy)
        self.assertIn(SOLICITAR_VALIDACION_EMPRESA, resultado.alertas)
        self.assertTrue(resultado.requiere_validacion_empresa)

    def _solicitud(self, **cambios):
        valores = {
            'usuario': self.usuario,
            'empresa': self.empresa,
            'tipo_documento': 'CC',
            'numero_documento': '900000010',
            'nombres': 'Persona',
            'apellidos': 'Contrato',
            'celular': '3000000010',
            'correo': 'contrato@example.com',
            'direccion': 'Direccion',
            'cargo': 'Consultoria',
            'fecha_inicio_contrato': self.hoy - relativedelta(months=2),
            'fecha_fin_contrato': self.hoy + relativedelta(months=8),
            'valor_total_contrato': Decimal('50000000'),
            'valor_pagado_contrato': Decimal('10000000'),
            'valor_pendiente_cobrar': Decimal('40000000'),
            'forma_pago': ContractorApplication.FormaPago.MENSUAL,
            'valor_mensual_contractual': Decimal('5000000'),
            'monto_solicitado': Decimal('3000000'),
            'plazo_meses': 6,
            'estado_analisis_contractual': 'COMPLETADO',
            'metadata_analisis_contractual': {
                'identidad': {'documento_coincide': True},
                'empresa_sugerida': {
                    'empresa_sugerida_id': self.empresa.id,
                    'tipo_coincidencia': 'NIT_EXACTO',
                },
            },
        }
        valores.update(cambios)
        return ContractorApplication.objects.create(**valores)
