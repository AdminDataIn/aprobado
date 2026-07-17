from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import RequestFactory, TestCase
from django.utils import timezone

from contractors.admin import ContractorApplicationAdmin
from contractors.datacredito.dto import (
    ResultadoConsultaDatacreditoPrestador,
    ResultadoNormalizadoDatacreditoPrestador,
)
from contractors.models import (
    ConfiguracionSimuladorPrestador,
    ContractorApplication,
    ContractorApplicationDocument,
    PredecisionPrestadorAudit,
    TimelinePrestador,
)
from contractors.services.evaluacion_formal import evaluar_solicitud_prestador
from contractors.tests.test_score_prestadores_v2 import crear_politica_score
from gestion_creditos.models import AprobacionPagadorLibranza, Credito, CreditoLibranza, Empresa


class EvaluacionFormalPrestadorV2Test(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username='prestador-formal', password='test-password'
        )
        self.staff = get_user_model().objects.create_superuser(
            username='staff-formal',
            email='staff-formal@example.com',
            password='test-password',
        )
        self.empresa = Empresa.objects.create(nombre='Empresa Formal', convenio_activo=True)
        self.solicitud = self._crear_solicitud()
        self.configuracion_financiera = ConfiguracionSimuladorPrestador.objects.create(
            nombre='Simulador formal alineado',
            version='financiera-formal-v1',
            activo=True,
            monto_minimo=Decimal('1000000'),
            monto_maximo=Decimal('10000000'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=8,
            tasa_mensual=Decimal('2.2000'),
        )
        self.solicitud.version_configuracion_financiera_simulacion = (
            self.configuracion_financiera.version
        )
        self.solicitud.version_politica_simulacion = 'politica-v1'
        self.solicitud.monto_simulado = self.solicitud.monto_solicitado
        self.solicitud.plazo_simulado_meses = self.solicitud.plazo_meses
        self.solicitud.tasa_mensual_simulacion = self.configuracion_financiera.tasa_mensual
        self.solicitud.monto_maximo_configuracion_simulacion = (
            self.configuracion_financiera.monto_maximo
        )
        self.solicitud.plazo_maximo_configuracion_simulacion = (
            self.configuracion_financiera.plazo_maximo_meses
        )
        self.solicitud.simulada_en = timezone.now()
        self.solicitud.save()

    @patch('contractors.services.evaluacion_formal.obtener_evaluacion_datacredito_prestador')
    def test_sin_politica_activa_no_consulta_ni_preaprueba(self, consulta_mock):
        resultado = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        self.assertFalse(consulta_mock.called)
        self.assertEqual(resultado.auditoria.resultado, 'NO_EVALUABLE')
        self.assertIsNone(resultado.auditoria.score)

    @patch('contractors.services.predecision.obtener_autorizacion_datacredito_vigente', return_value=object())
    @patch('contractors.score.componentes.obtener_autorizacion_datacredito_vigente', return_value=object())
    @patch('contractors.services.evaluacion_formal.obtener_evaluacion_datacredito_prestador')
    def test_evaluacion_equivalente_reutiliza_auditoria_y_no_crea_creditos(
        self, consulta_mock, autorizacion_score_mock, autorizacion_predecision_mock
    ):
        crear_politica_score()
        consulta_mock.return_value = self._datacredito(950)
        creditos_antes = Credito.objects.count()
        libranzas_antes = CreditoLibranza.objects.count()

        primero = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        segundo = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)

        self.assertEqual(consulta_mock.call_count, 1)
        self.assertFalse(primero.reutilizada)
        self.assertTrue(segundo.reutilizada)
        self.assertEqual(primero.auditoria.pk, segundo.auditoria.pk)
        self.assertEqual(primero.auditoria.resultado, 'PREAPROBADO_READ_ONLY')
        self.assertEqual(
            primero.auditoria.version_configuracion_financiera,
            self.configuracion_financiera.version,
        )
        self.assertEqual(
            primero.auditoria.tasa_mensual_configuracion,
            self.configuracion_financiera.tasa_mensual,
        )
        self.assertEqual(
            primero.auditoria.monto_maximo_configuracion,
            self.configuracion_financiera.monto_maximo,
        )
        self.assertEqual(
            primero.auditoria.plazo_maximo_configuracion,
            self.configuracion_financiera.plazo_maximo_meses,
        )
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), libranzas_antes)

    @patch('contractors.services.predecision.obtener_autorizacion_datacredito_vigente', return_value=object())
    @patch('contractors.score.componentes.obtener_autorizacion_datacredito_vigente', return_value=object())
    @patch('contractors.services.evaluacion_formal.obtener_evaluacion_datacredito_prestador')
    def test_http_ocurre_fuera_de_transaccion_larga(
        self, consulta_mock, autorizacion_score_mock, autorizacion_predecision_mock
    ):
        crear_politica_score()

        def comprobar_atomic(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            return self._datacredito(900)

        consulta_mock.side_effect = comprobar_atomic
        evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        self.assertEqual(consulta_mock.call_count, 1)

    @patch('contractors.services.predecision.obtener_autorizacion_datacredito_vigente', return_value=object())
    @patch('contractors.score.componentes.obtener_autorizacion_datacredito_vigente', return_value=object())
    @patch('contractors.services.evaluacion_formal.obtener_evaluacion_datacredito_prestador')
    def test_cambio_datos_durante_consulta_descarta_resultado(
        self, consulta_mock, autorizacion_score_mock, autorizacion_predecision_mock
    ):
        crear_politica_score()

        def modificar_solicitud(*args, **kwargs):
            ContractorApplication.objects.filter(pk=self.solicitud.pk).update(
                monto_solicitado=Decimal('3500000')
            )
            return self._datacredito(950)

        consulta_mock.side_effect = modificar_solicitud
        resultado = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        self.solicitud.refresh_from_db()
        self.assertEqual(resultado.auditoria.resultado, 'ERROR_CONTROLADO')
        self.assertEqual(self.solicitud.estado, ContractorApplication.Estado.EVALUACION_PENDIENTE)
        self.assertTrue(TimelinePrestador.objects.filter(
            solicitud=self.solicitud,
            tipo_evento=TimelinePrestador.TipoEvento.DATOS_MODIFICADOS,
        ).exists())

    @patch('contractors.services.evaluacion_formal.obtener_evaluacion_datacredito_prestador')
    def test_datacredito_deshabilitado_no_preaprueba(self, consulta_mock):
        crear_politica_score()
        consulta_mock.return_value = ResultadoConsultaDatacreditoPrestador(
            estado='NO_CONFIGURADO',
            error_codigo='datacredito_deshabilitado',
        )
        resultado = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        self.assertEqual(resultado.auditoria.resultado, 'NO_EVALUABLE')

    @patch('contractors.services.predecision.obtener_autorizacion_datacredito_vigente', return_value=object())
    @patch('contractors.services.evaluacion_formal.obtener_evaluacion_datacredito_prestador')
    def test_revision_manual_no_crea_aprobacion_ni_tarea_pagador(
        self, consulta_mock, autorizacion_mock
    ):
        crear_politica_score()
        self.solicitud.estado_contractual_declarado = 'SUSPENDIDO'
        self.solicitud.save(update_fields=['estado_contractual_declarado', 'updated_at'])
        consulta_mock.return_value = self._datacredito(900)

        resultado = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)

        self.assertEqual(resultado.auditoria.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertEqual(AprobacionPagadorLibranza.objects.count(), 0)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertNotIn(
            'aprobacion_pagador_libranza',
            __import__('inspect').getsource(evaluar_solicitud_prestador),
        )

    @patch('contractors.services.evaluacion_formal.obtener_evaluacion_datacredito_prestador')
    def test_timeout_no_preaprueba_y_queda_auditado(self, consulta_mock):
        crear_politica_score()
        consulta_mock.return_value = ResultadoConsultaDatacreditoPrestador(
            estado='ERROR_TRANSITORIO',
            error_codigo='timeout_datacredito',
        )
        resultado = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        self.solicitud.refresh_from_db()
        self.assertEqual(resultado.auditoria.resultado, 'ERROR_CONTROLADO')
        self.assertEqual(
            resultado.auditoria.estado_ejecucion,
            PredecisionPrestadorAudit.EstadoEjecucion.ERROR_CONTROLADO,
        )
        self.assertEqual(self.solicitud.estado, ContractorApplication.Estado.EN_REVISION_MANUAL)
        self.assertTrue(self.solicitud.timeline_operativo.filter(
            tipo_evento=TimelinePrestador.TipoEvento.REVISION_MANUAL_REQUERIDA
        ).exists())

    def test_admin_action_solo_aparece_con_permiso_especifico(self):
        usuario_staff = get_user_model().objects.create_user(
            username='staff-sin-permiso', password='test-password', is_staff=True
        )
        request = RequestFactory().get('/admin/contractors/contractorapplication/')
        request.user = usuario_staff
        model_admin = admin.site._registry[ContractorApplication]
        self.assertIsInstance(model_admin, ContractorApplicationAdmin)
        self.assertNotIn('ejecutar_evaluacion_formal', model_admin.get_actions(request))

        permiso = Permission.objects.get(codename='can_evaluate_contractor_application')
        usuario_staff.user_permissions.add(permiso)
        request.user = get_user_model().objects.get(pk=usuario_staff.pk)
        self.assertIn('ejecutar_evaluacion_formal', model_admin.get_actions(request))

    def test_snapshot_auditoria_no_contiene_documento_raw(self):
        resultado = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        contenido = str(resultado.auditoria.snapshot_entrada) + str(
            resultado.auditoria.snapshot_salida
        )
        self.assertNotIn(self.solicitud.numero_documento, contenido)

    def _datacredito(self, score):
        return ResultadoConsultaDatacreditoPrestador(
            estado='EXITOSO',
            snapshot_id='00000000-0000-0000-0000-000000000001',
            servicio='decisor',
            resultado_normalizado=ResultadoNormalizadoDatacreditoPrestador(
                score_externo=score,
                cuota_mensual_total='0',
                mora_actual=False,
                mora_severa=False,
                obligaciones_vigentes=1,
                servicio_fuente='decisor',
            ),
        )

    def _crear_solicitud(self):
        solicitud = ContractorApplication.objects.create(
            usuario=self.usuario,
            empresa=self.empresa,
            tipo_documento='CC',
            numero_documento='900000002',
            nombres='Persona',
            apellidos='Formal',
            celular='3000000001',
            correo='formal@example.com',
            direccion='Direccion formal',
            cargo='Consultoria',
            fecha_inicio_contrato=timezone.localdate(),
            fecha_fin_contrato=timezone.localdate() + timedelta(days=240),
            valor_total_contrato=Decimal('50000000'),
            valor_pagado_contrato=Decimal('2000000'),
            valor_pendiente_cobrar=Decimal('48000000'),
            monto_solicitado=Decimal('1000000'),
            plazo_meses=6,
            acepta_terminos=True,
            acepta_politica_privacidad=True,
            autoriza_analisis_contractual_asistido=True,
            autoriza_consulta_centrales=True,
            estado_analisis_contractual='COMPLETADO',
            metadata_analisis_contractual={
                'identidad': {'documento_coincide': True},
                'empresa_sugerida': {
                    'empresa_sugerida_id': self.empresa.id,
                    'tipo_coincidencia': 'NIT_EXACTO',
                },
                'bloqueos': [],
            },
            estado=ContractorApplication.Estado.EVALUACION_PENDIENTE,
        )
        for tipo in ContractorApplicationDocument.TipoDocumento.values:
            extension = '.jpg' if tipo.startswith('CEDULA') else '.pdf'
            ContractorApplicationDocument.objects.create(
                solicitud=solicitud,
                tipo_documento=tipo,
                archivo=SimpleUploadedFile(f'{tipo}{extension}', b'data'),
                uploaded_by=self.usuario,
                metadata_captura={'source': 'camera'} if tipo.startswith('CEDULA') else {},
            )
        return solicitud
