from datetime import timedelta
import hashlib
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from contractors.admin import ContractorApplicationAdmin
from contractors.datacredito.dto import (
    ResultadoConsultaDatacreditoPrestador,
    ResultadoNormalizadoDatacreditoPrestador,
    ResultadoProveedorDatacreditoPrestador,
)
from contractors.models import (
    ConfiguracionSimuladorPrestador,
    ContractorApplication,
    ContractorApplicationDocument,
    PredecisionPrestadorAudit,
    RevisionManualPrestador,
    TimelinePrestador,
)
from contractors.services.evaluacion_formal import evaluar_solicitud_prestador
from contractors.services.autorizacion_datacredito import (
    crear_confirmacion_consentimiento,
    registrar_autorizacion_datacredito_desde_solicitud,
)
from contractors.services.revision_manual import reintentar_evaluacion
from contractors.tests.test_score_prestadores_v2 import crear_politica_score
from gestion_creditos.models import AprobacionPagadorLibranza, Credito, CreditoLibranza, Empresa
from usuarios.models import PerfilPagador
from integrations.models import ConsultaDatacreditoSnapshot
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA


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

    def test_reevaluacion_tras_configurar_politica_conserva_auditoria_no_evaluable(self):
        with patch(
            'contractors.services.evaluacion_formal.obtener_evaluacion_datacredito_prestador'
        ) as consulta_inicial:
            inicial = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        self.assertFalse(consulta_inicial.called)
        auditoria_inicial_id = inicial.auditoria.id
        clave_inicial = inicial.auditoria.clave_idempotencia
        self.assertEqual(inicial.auditoria.version_politica, 'politica_no_configurada')

        self.configuracion_financiera.delete()
        call_command('configurar_politica_prestadores_demo', stdout=StringIO())

        with patch(
            'contractors.services.evaluacion_formal.obtener_evaluacion_datacredito_prestador',
            return_value=self._datacredito(950),
        ), patch(
            'contractors.services.predecision.obtener_autorizacion_datacredito_vigente',
            return_value=object(),
        ), patch(
            'contractors.score.componentes.obtener_autorizacion_datacredito_vigente',
            return_value=object(),
        ):
            nueva = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)

        inicial.auditoria.refresh_from_db()
        self.assertNotEqual(nueva.auditoria.id, auditoria_inicial_id)
        self.assertNotEqual(nueva.auditoria.clave_idempotencia, clave_inicial)
        self.assertEqual(inicial.auditoria.resultado, 'NO_EVALUABLE')
        self.assertEqual(inicial.auditoria.version_politica, 'politica_no_configurada')
        self.assertEqual(self.solicitud.auditorias_predecision.count(), 2)

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
        self.assertEqual(self.solicitud.auditorias_predecision.count(), 1)
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

    @patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP real prohibido'))
    @patch('contractors.services.evaluacion_formal.obtener_evaluacion_datacredito_prestador')
    def test_reintentos_sucesivos_sin_snapshots_preservan_historial(self, consulta, http):
        crear_politica_score()
        consulta.return_value = ResultadoConsultaDatacreditoPrestador(
            estado='NO_CONFIGURADO', error_codigo='datacredito_deshabilitado',
        )
        inicial = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        revision = self._revision_reintento(inicial.auditoria)
        anteriores = []
        clave_raiz = inicial.auditoria.clave_idempotencia
        anterior = inicial.auditoria
        for _ in range(3):
            anteriores.append(PredecisionPrestadorAudit.objects.values().get(pk=anterior.pk))
            nueva = reintentar_evaluacion(revision, actor=self.staff)
            self.assertFalse(nueva.reutilizada)
            self.assertEqual(nueva.auditoria.snapshot_entrada['clave_operacion'], clave_raiz)
            self.assertEqual(nueva.auditoria.clave_idempotencia, hashlib.sha256(
                f'{clave_raiz}:reintento:{anterior.pk}'.encode('ascii')
            ).hexdigest())
            anterior = nueva.auditoria
        for datos in anteriores:
            self.assertEqual(PredecisionPrestadorAudit.objects.values().get(pk=datos['id']), datos)
        self.assertEqual(self.solicitud.auditorias_predecision.count(), 4)
        self.assertEqual(len(set(self.solicitud.auditorias_predecision.values_list(
            'clave_idempotencia', flat=True,
        ))), 4)
        self.assertEqual(consulta.call_count, 4)
        self.assertTrue(all(llamada.kwargs['modo'] == 'REUTILIZAR_SI_VIGENTE'
                            for llamada in consulta.call_args_list))
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())
        normal = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        self.assertTrue(normal.reutilizada)
        self.assertEqual(normal.auditoria.pk, anterior.pk)
        self.assertEqual(consulta.call_count, 4)
        http.assert_not_called()

    @override_settings(**CONFIGURACION_DATACREDITO_PRUEBA)
    @patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP real prohibido'))
    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_reintento_tras_habilitar_configuracion_consulta_sin_forzar(self, proveedor, http):
        self._preparar_centrales_reales(proveedor)
        with override_settings(DATACREDITO_REAL_ENABLED=False):
            inicial = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        self.assertEqual(inicial.auditoria.estado_ejecucion, 'COMPLETADA')
        self.assertEqual(inicial.auditoria.resultado, 'NO_EVALUABLE')
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())
        proveedor.assert_not_called()
        datos = PredecisionPrestadorAudit.objects.values().get(pk=inicial.auditoria.pk)
        nueva = reintentar_evaluacion(self._revision_reintento(inicial.auditoria), actor=self.staff)
        self.assertFalse(nueva.reutilizada)
        self.assertNotEqual(nueva.auditoria.pk, inicial.auditoria.pk)
        self.assertEqual(nueva.auditoria.version_datos, inicial.auditoria.version_datos)
        self.assertEqual(PredecisionPrestadorAudit.objects.values().get(pk=inicial.auditoria.pk), datos)
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.get().estado, 'EXITOSO')
        proveedor.assert_called_once_with(self.solicitud, servicio='decisor')
        self.assertEqual(self.solicitud.auditorias_predecision.count(), 2)
        http.assert_not_called()

    @override_settings(**CONFIGURACION_DATACREDITO_PRUEBA)
    @patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP real prohibido'))
    @patch('contractors.services.datacredito_evaluacion.consultar_proveedor_datacredito_prestador')
    def test_reintento_nueva_auditoria_reutiliza_snapshot_real_vigente(self, proveedor, http):
        self._preparar_centrales_reales(proveedor)
        inicial = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        self.assertEqual(inicial.auditoria.resultado, 'REQUIERE_REVISION_MANUAL')
        datos = PredecisionPrestadorAudit.objects.values().get(pk=inicial.auditoria.pk)
        nueva = reintentar_evaluacion(self._revision_reintento(inicial.auditoria), actor=self.staff)
        self.assertFalse(nueva.reutilizada)
        self.assertNotEqual(nueva.auditoria.pk, inicial.auditoria.pk)
        self.assertEqual(nueva.auditoria.snapshot_salida['datacredito']['snapshot_id'],
                         inicial.auditoria.snapshot_salida['datacredito']['snapshot_id'])
        self.assertTrue(nueva.auditoria.snapshot_salida['datacredito']['reutilizado'])
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 1)
        self.assertEqual(PredecisionPrestadorAudit.objects.values().get(pk=inicial.auditoria.pk), datos)
        self.assertEqual(proveedor.call_count, 1)
        http.assert_not_called()

    def _revision_reintento(self, auditoria):
        return RevisionManualPrestador.objects.get_or_create(
            solicitud=self.solicitud, motivo=RevisionManualPrestador.Motivo.DATACREDITO_ERROR,
            defaults={'auditoria_predecision': auditoria},
        )[0]

    def _preparar_centrales_reales(self, proveedor):
        crear_politica_score()
        self.solicitud.estado_contractual_declarado = 'SUSPENDIDO'
        self.solicitud.save(update_fields=['estado_contractual_declarado'])
        registrar_autorizacion_datacredito_desde_solicitud(
            self.solicitud, usuario=self.usuario,
            request=RequestFactory().post('/solicitar/', {
                'consentimiento_centrales': crear_confirmacion_consentimiento(self.usuario),
            }),
        )
        proveedor.return_value = ResultadoProveedorDatacreditoPrestador(
            estado_snapshot='EXITOSO', codigo_http=200,
            resultado_normalizado=ResultadoNormalizadoDatacreditoPrestador(
                score_externo=950, cuota_mensual_total='0', mora_actual=False,
                mora_severa=False, obligaciones_vigentes=1, servicio_fuente='decisor',
            ),
        )

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
        revision = RevisionManualPrestador.objects.get(solicitud=self.solicitud)
        self.assertEqual(revision.motivo, RevisionManualPrestador.Motivo.CONTRATO_SUSPENDIDO)
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

    def test_simulacion_legacy_sin_version_exige_revision_sin_reescribir_evidencia(self):
        crear_politica_score()
        self.solicitud.version_politica_simulacion = ''
        self.solicitud.save(update_fields=['version_politica_simulacion'])
        campos = ('version_politica_simulacion', 'monto_simulado', 'plazo_simulado_meses', 'simulada_en')
        antes = {campo: getattr(self.solicitud, campo) for campo in campos}
        with patch('contractors.services.evaluacion_formal.obtener_evaluacion_datacredito_prestador',
                return_value=self._datacredito(950)), patch(
                'contractors.services.predecision.obtener_autorizacion_datacredito_vigente', return_value=object()), patch(
                'contractors.score.componentes.obtener_autorizacion_datacredito_vigente', return_value=object()):
            resultado = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        self.solicitud.refresh_from_db()
        self.assertEqual(antes, {campo: getattr(self.solicitud, campo) for campo in campos})
        self.assertNotEqual(resultado.auditoria.resultado, 'PREAPROBADO_READ_ONLY')
        self.assertEqual(self.solicitud.estado, ContractorApplication.Estado.EN_REVISION_MANUAL)

    def test_servicio_bloquea_perfil_pagador_con_permiso_accidental(self):
        pagador = get_user_model().objects.create_user(
            username='pagador-evaluacion-formal',
            password='test-password',
            is_staff=True,
        )
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        pagador.user_permissions.add(
            Permission.objects.get(codename='can_evaluate_contractor_application')
        )

        with self.assertRaises(PermissionDenied):
            evaluar_solicitud_prestador(self.solicitud, solicitado_por=pagador)

        self.solicitud.refresh_from_db()
        self.assertEqual(
            self.solicitud.estado,
            ContractorApplication.Estado.EVALUACION_PENDIENTE,
        )
        self.assertFalse(self.solicitud.auditorias_predecision.exists())

    def _datacredito(self, score):
        ConsultaDatacreditoSnapshot.objects.get_or_create(
            pk='00000000-0000-0000-0000-000000000001',
            defaults=dict(ambiente='uat', servicio='decisor', documento_hash='0'*64,
                documento_enmascarado='****0002', fingerprint='0'*64, estado='EXITOSO',
                consultado_en=timezone.now(), vigente_hasta=timezone.now()+timedelta(days=30),
                autorizacion_referencia='fixture-sintetico'),
        )
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
            forma_pago=ContractorApplication.FormaPago.MENSUAL,
            frecuencia_pago='Mensual',
            valor_mensual_contractual=Decimal('6000000'),
            evidencia_forma_pago='Pago mensual pactado.',
            confianza_forma_pago=Decimal('0.9500'),
            fuente_forma_pago='TEST',
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
