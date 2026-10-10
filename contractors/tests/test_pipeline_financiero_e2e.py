"""Real portal/risk pipeline; only provider responses are replaced."""
import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from weasyprint import HTML

from contractors.models import (
    AprobacionInternaPrestador, AprobacionPagadorPrestador,
    ConfiguracionScorePrestador, ConfiguracionSimuladorPrestador,
    ContractorApplication, ContractorApplicationDocument, PredecisionPrestadorAudit,
)
from contractors.services.aprobacion_interna import (
    aprobar_para_originar, cerrar_sin_originar, crear_o_reutilizar_aprobacion_interna,
    iniciar_analisis_aprobacion_interna,
)
from contractors.services.aprobacion_pagador import (
    CONFIRMACIONES_REQUERIDAS, decidir_aprobacion_pagador_prestador,
    crear_o_reutilizar_aprobacion_pagador,
)
from contractors.services.autorizacion_datacredito import (
    crear_confirmacion_consentimiento, obtener_autorizacion_datacredito_vigente,
    registrar_autorizacion_datacredito_desde_solicitud,
)
from contractors.services.datacredito_evaluacion import (
    obtener_evaluacion_datacredito_prestador,
)
from contractors.services.centrales_riesgo import obtener_evaluacion_centrales_prestador
from contractors.services.evaluacion_formal import evaluar_solicitud_prestador
from contractors.services.expediente_originacion import construir_expediente_originacion_prestador
from contractors.services.ingreso_neto import obtener_ingreso_neto_vigente, registrar_ingreso_neto
from contractors.services.horizonte_simulacion import horizonte_para_solicitud
from contractors.services.originacion import originar_credito_prestador_desde_gate
from contractors.services.oferta_financiera import calcular_oferta_evaluada
from contractors.services.politica_financiera_prestador import PARAMETROS, VERSION, preparar_oferta
from contractors.services.politica_score import activar_politica_score_prestador
from contractors.services.preparacion_score_prod import preparar_politica_score_prod
from contractors.score.motor import evaluar_score_prestador
from gestion_creditos.models import Credito, CreditoLibranza, SesionCapturaDocumental
from gestion_creditos.models import Empresa
from gestion_creditos.tests.captura_fixtures import sesion_finalizada
from gestion_creditos.services.originacion_libranza import (
    construir_clave_idempotencia_prestador, originar_libranza_desde_expediente,
)
from integrations.datacredito.dto import ResultadoHistorialCreditoRawSeguro, ResultadoMiDecisorRawSeguro
from integrations.datacredito.exceptions import DatacreditoTimeoutError
from integrations.models import ConsultaDatacreditoSnapshot
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA
from integrations.tests import test_hdcplus_normalizacion_dual as hdc_fixtures
from integrations.tests.test_midecisor_clasificacion import payload_pn
from usuarios.models import PerfilPagador


class FixturePipelineFinanciero:
    host = 'contratistas.localhost'

    def setUp(self):
        storage = TemporaryDirectory(prefix='prestadores-e2e-')
        self.addCleanup(storage.cleanup)
        override = override_settings(MEDIA_ROOT=storage.name, PRIVATE_DOCUMENTS_ROOT=storage.name)
        override.enable()
        self.addCleanup(override.disable)
        for target in ('requests.sessions.Session.request', 'httpx.Client.send',
                       'httpx.AsyncClient.send', 'socket.socket.connect'):
            guard = patch(target, side_effect=AssertionError('Red externa prohibida en E2E'))
            guard.start()
            self.addCleanup(guard.stop)
        shadow = patch('integrations.datacredito.midecisor_shadow.diagnosticar_midecisor_pn',
                       side_effect=AssertionError('Shadow no debe participar en decisiones'))
        shadow.start()
        self.addCleanup(shadow.stop)
        self.usuario = get_user_model().objects.create_user(
            username='prestador-sintetico-e2e', email='sintetico@example.invalid', password='Test-only-123!',
        )
        self.staff = get_user_model().objects.create_superuser(
            username='staff-sintetico-e2e', email='staff@example.invalid', password='Test-only-123!',
        )
        self.empresa = Empresa.objects.create(
            nombre='EMPRESA SINTETICA', nit='999000099', convenio_activo=True,
        )
        self.financiera = ConfiguracionSimuladorPrestador.objects.create(
            version=VERSION, activo=True, **PARAMETROS,
        )
        preparar_politica_score_prod(
            parametros={'fecha_vigencia_desde': (timezone.localdate() - timedelta(days=1)).isoformat()},
            persistir=True, actor=self.staff, motivo='Fixture aislado E2E, sin cambios de parametros',
        )
        self.politica = ConfiguracionScorePrestador.objects.get(version='prestadores-score-prod-v1')
        activar_politica_score_prestador(
            politica_id=self.politica.pk, actor=self.staff, motivo='Solo base de tests desechable',
        )
        self.politica.refresh_from_db()
        self.client.force_login(self.usuario)
        self.solicitud = self.crear_por_portal()
        self.verificar_ingreso('10000000')
        mail.outbox.clear()

    def verificar_ingreso(self, monto, *, solicitud=None, vigencia=30):
        solicitud = solicitud or self.solicitud
        return registrar_ingreso_neto(
            solicitud=solicitud, actor=self.staff, monto=monto,
            fecha_corte=timezone.localdate(),
            vigente_hasta=timezone.localdate() + timedelta(days=vigencia),
            documentos=[solicitud.documentos.get(tipo_documento='CERTIFICADO_BANCARIO')],
            observacion='Ingreso verificado sobre evidencia sintetica E2E',
        )

    def oferta(self, auditoria):
        return calcular_oferta_evaluada(
            self.solicitud, auditoria, politica=self.politica,
            monto=self.solicitud.monto_solicitado, plazo=self.solicitud.plazo_meses,
        )

    def crear_por_portal(self, *, monto='3000000', plazo=6):
        inicio = timezone.localdate().replace(day=1)
        fin = inicio + relativedelta(months=12) - timedelta(days=1)
        texto = f'''
        CONTRATO DE PRESTACION DE SERVICIOS
        CONTRATANTE: EMPRESA SINTETICA, NIT 999000099.
        CONTRATISTA: PERSONA SINTETICA, identificada con cedula de ciudadania No. 900000099.
        El valor total del contrato es de $120.000.000.
        El contratante cancelara el valor del contrato mediante doce (12) pagos mensuales de $10.000.000.
        Los pagos se realizan dentro de los primeros cinco (5) dias habiles del mes siguiente.
        A la fecha de firma no se han realizado pagos. Valor pagado: $0.
        Fecha de inicio: {inicio:%d/%m/%Y}. Fecha de terminacion: {fin:%d/%m/%Y}.
        La duracion del contrato es de doce (12) meses.
        '''
        pdf = HTML(string='<html><body><p>' + texto + '</p></body></html>').write_pdf()
        analisis = self.client.post('/contrato/analizar/', {
            'numero_documento': '900000099', 'autoriza_analisis_contractual_asistido': '1',
            'contrato_actual': SimpleUploadedFile('sintetico.pdf', pdf, content_type='application/pdf'),
        }, HTTP_HOST=self.host)
        self.assertEqual(analisis.status_code, 200)
        sesion = sesion_finalizada(self.usuario, 'PRESTADORES')
        payload = dict(
            consentimiento_centrales=crear_confirmacion_consentimiento(self.usuario),
            escenario_credito='NUEVO_CREDITO', tipo_documento='CC', numero_documento='900000099',
            nombres='PERSONA', apellidos='SINTETICA', celular='3000000099', correo='sintetico@example.invalid',
            direccion='Direccion sintetica', cargo='Consultor', tipo_contrato='PRESTACION_SERVICIOS',
            empresa=self.empresa.pk, fecha_inicio_contrato=inicio.isoformat(), fecha_fin_contrato=fin.isoformat(),
            valor_total_contrato='120000000', valor_pagado_contrato='0', valor_pendiente_cobrar='120000000',
            forma_pago='MENSUAL', valor_mensual_contractual='10000000', acepta_terminos='on',
            acepta_politica_privacidad='on', autoriza_analisis_contractual_asistido='on',
            autoriza_consulta_centrales='on', sesion_documental_id=str(sesion.pk),
            certificado_bancario=SimpleUploadedFile('banco.pdf', pdf, content_type='application/pdf'),
            contrato_actual=SimpleUploadedFile('sintetico.pdf', pdf, content_type='application/pdf'),
        )
        campos = analisis.json()['campos_extraidos']
        payload.update({k: v['valor'] for k, v in campos.items() if v['editable'] and v['encontrado']})
        respuesta = self.client.post('/solicitar/', payload, HTTP_HOST=self.host)
        self.assertEqual(respuesta.status_code, 302,
                         getattr(respuesta, 'context', None) and respuesta.context['form'].errors)
        solicitud = ContractorApplication.objects.filter(usuario=self.usuario).latest('pk')
        self.assertIsNotNone(obtener_autorizacion_datacredito_vigente(solicitud))
        sesion.refresh_from_db()
        self.assertEqual(sesion.estado, SesionCapturaDocumental.Estado.UTILIZADA)
        self.assertEqual(solicitud.documentos.count(), 4)
        respuesta = self.client.post('/simular/', {
            'solicitud_id': solicitud.pk, 'monto': monto, 'plazo_meses': plazo,
        }, HTTP_HOST=self.host)
        self.assertEqual(respuesta.status_code, 302,
                         getattr(respuesta, 'context', None) and respuesta.context['form'].errors)
        solicitud.refresh_from_db()
        self.assertIsNotNone(solicitud.simulada_en)
        self.assertEqual(solicitud.estado, ContractorApplication.Estado.EVALUACION_PENDIENTE)
        self.assertEqual(solicitud.version_configuracion_financiera_simulacion, VERSION)
        self.assert_sin_originacion()
        return solicitud

    def assert_sin_originacion(self):
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(CreditoLibranza.objects.exists())
        self.assertFalse(mail.outbox)

    def evaluar(self, *, score=900, hc='13', tx='02', hdc_sin_informacion=False,
                deuda=None, mora=0, incompleta=False, timeout=False):
        decisor = payload_pn(hc=hc, tx=tx, informacion=True)
        decisor['content']['respuesta']['informacionRiesgo']['score'] = str(score)
        if incompleta:
            decisor = {'status': 'ACCEPTED', 'content': {}}
        hdc = deepcopy(hdc_fixtures.HDCPlusNormalizacionDualTest._payload_hdc(mora=mora, dias=90 if mora else 0))
        if hdc_sin_informacion:
            hdc = {'ReportHDCplus': {'productResult': {'responseCode': '14'}}}
        if deuda is not None:
            for i, item in enumerate(hdc['ReportHDCplus']['liabilities']):
                item['values'][0]['valueMonthlyPayment'] = str(deuda if i == 0 else 0)
        raw_decisor = ResultadoMiDecisorRawSeguro(
            status_code=200, codigo_funcional=f'HC{hc}_TX{tx}', raw_sanitizado=decisor,
        )
        raw_hdc = ResultadoHistorialCreditoRawSeguro(
            status_code=200, response_code='14' if hdc_sin_informacion else '13', raw_sanitizado=hdc,
        )
        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural',
                   return_value=raw_decisor) as mock_decisor, \
             patch('contractors.datacredito.adapter.consultar_historial_credito',
                   return_value=raw_hdc) as mock_hdc:
            if timeout:
                mock_decisor.side_effect = DatacreditoTimeoutError('timeout sintetico')
            resultado = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
            original = PredecisionPrestadorAudit.objects.values().get(pk=resultado.auditoria.pk)
            repetida = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff)
        self.assertEqual(mock_decisor.call_count, 1)
        self.assertEqual(mock_hdc.call_count, 1)
        auditoria = resultado.auditoria
        self.assertTrue(repetida.reutilizada)
        self.assertEqual(auditoria.pk, repetida.auditoria.pk)
        self.assertEqual(PredecisionPrestadorAudit.objects.filter(solicitud=self.solicitud).count(), 1)
        self.assertEqual(original, PredecisionPrestadorAudit.objects.values().get(pk=auditoria.pk))
        self.assertEqual(ConsultaDatacreditoSnapshot.objects.count(), 2)
        for snapshot in (auditoria.snapshot_midecisor, auditoria.snapshot_hdcplus):
            self.assertEqual(snapshot.creado_por_id, self.staff.pk)
            self.assertEqual(snapshot.autorizacion_referencia,
                             str(obtener_autorizacion_datacredito_vigente(self.solicitud).pk))
            self.assertNotEqual(snapshot.estado, 'EN_PROCESO')
            seguro = json.dumps(snapshot.resultado_normalizado)
            for prohibido in ('900000099', 'PERSONA', 'SINTETICA', 'raw_sanitizado', 'access_token'):
                self.assertNotIn(prohibido, seguro)
        self.assert_sin_originacion()
        return auditoria

    def assert_no_aprueba(self, auditoria):
        self.assertNotEqual(auditoria.resultado, 'PREAPROBADO_READ_ONLY')
        self.assertNotEqual(self.oferta(auditoria).estado, 'OFERTA_CALCULADA')
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)
        self.assertFalse(AprobacionInternaPrestador.objects.exists())
        self.assert_sin_originacion()

    def nueva_solicitud_autorizada(self, *, ingreso='10000000'):
        otra = deepcopy(self.solicitud)
        otra.pk = None
        otra.estado = 'EVALUACION_PENDIENTE'
        otra.save()
        for documento in self.solicitud.documentos.all():
            ContractorApplicationDocument.objects.create(
                solicitud=otra, tipo_documento=documento.tipo_documento,
                archivo=documento.archivo.name, metadata_captura=documento.metadata_captura,
                uploaded_by=self.usuario,
            )
        registrar_autorizacion_datacredito_desde_solicitud(otra, usuario=self.usuario)
        self.verificar_ingreso(ingreso, solicitud=otra)
        return otra

    def evaluar_solo_cache(self, solicitud):
        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural') as decisor, \
             patch('contractors.datacredito.adapter.consultar_historial_credito') as hdc:
            resultado = evaluar_solicitud_prestador(
                solicitud, solicitado_por=self.staff, modo_datacredito='SOLO_CACHE',
            )
        decisor.assert_not_called()
        hdc.assert_not_called()
        return resultado


@override_settings(
    **CONFIGURACION_DATACREDITO_PRUEBA,
    ALLOWED_HOSTS=['testserver', 'localhost', 'contratistas.localhost'],
    SECURE_SSL_REDIRECT=False, CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class PipelineFinancieroPrestadorE2ETest(FixturePipelineFinanciero, TestCase):
    def test_a_fuentes_completas_hasta_expediente_sin_originar(self):
        auditoria = self.evaluar()
        self.assertEqual(auditoria.resultado, 'PREAPROBADO_READ_ONLY', auditoria.snapshot_salida)
        self.assertIsNotNone(auditoria.score)
        self.assertTrue(auditoria.evaluacion_centrales_completa)
        gate, creado = crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)
        self.assertTrue(creado)
        self.assertEqual(crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)[0].pk, gate.pk)
        with self.assertRaises(ValidationError):
            construir_expediente_originacion_prestador(gate)
        with self.assertRaises(ValidationError):
            originar_credito_prestador_desde_gate(gate, actor=self.staff)
        gate = aprobar_para_originar(gate, actor=self.staff, comentario_interno='Revision sintetica E2E')
        with self.assertRaises(ValidationError):
            construir_expediente_originacion_prestador(gate)
        pagador = get_user_model().objects.create_user(username='pagador-sintetico-e2e')
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        pagador.user_permissions.add(Permission.objects.get(
            content_type__app_label='contractors', codename='can_decide_contractor_payer_approval',
        ))
        aprobacion = AprobacionPagadorPrestador.objects.get(aprobacion_interna=gate)
        decidir_aprobacion_pagador_prestador(
            aprobacion, actor=pagador, decision='APROBADO',
            confirmaciones={campo: True for campo in CONFIRMACIONES_REQUERIDAS},
        )
        expediente = construir_expediente_originacion_prestador(gate)
        self.assertEqual(expediente.solicitud_id, self.solicitud.pk)
        self.assertLessEqual(expediente.monto_autorizado, self.financiera.monto_maximo)
        self.assertLessEqual(expediente.plazo_autorizado, self.financiera.plazo_maximo_meses)
        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        publico = response.context['estado_publico_principal']
        self.assertEqual(publico['codigo'], 'EVALUACION_APROBADA')
        self.assertNotIn('score', publico)
        self.assertNotIn('proveedor', publico)
        self.assert_sin_originacion()

    def test_b_hdc_sin_informacion_requiere_revision(self):
        auditoria = self.evaluar(hdc_sin_informacion=True)
        self.assertEqual(auditoria.snapshot_hdcplus.estado, 'SIN_INFORMACION')
        self.assertEqual(auditoria.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertIsNone(auditoria.score)
        self.assert_no_aprueba(auditoria)

    def test_c_hc14_tx05_no_usa_shadow(self):
        auditoria = self.evaluar(score=667, hc='14', tx='05')
        snapshot = auditoria.snapshot_midecisor
        self.assertEqual(auditoria.resultado, 'NO_EVALUABLE')
        self.assertIsNone(auditoria.score)
        self.assertEqual(snapshot.estado, 'ERROR_PERMANENTE')
        self.assertEqual(snapshot.codigo_http, 200)
        self.assertEqual(snapshot.codigo_funcional, 'HC14_TX05')
        self.assertEqual(snapshot.resultado_normalizado, {})
        self.assert_no_aprueba(auditoria)

    def test_d_timeout_revision_sin_reintento_automatico(self):
        auditoria = self.evaluar(timeout=True)
        self.assertEqual(auditoria.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertEqual(auditoria.snapshot_midecisor.estado, 'ERROR_TRANSITORIO')
        self.assertEqual(auditoria.snapshot_midecisor.error_codigo, 'timeout_proveedor')
        self.assert_no_aprueba(auditoria)

    def test_d_error_funcional_no_evaluable(self):
        auditoria = self.evaluar(tx='20')
        self.assertEqual(auditoria.resultado, 'NO_EVALUABLE')
        self.assertEqual(auditoria.snapshot_midecisor.resultado_normalizado, {})
        self.assert_no_aprueba(auditoria)

    def test_d_respuesta_incompleta_no_evaluable(self):
        auditoria = self.evaluar(incompleta=True)
        self.assertEqual(auditoria.resultado, 'NO_EVALUABLE')
        self.assertEqual(auditoria.snapshot_midecisor.error_tipo, 'RESPUESTA_INVALIDA')
        self.assert_no_aprueba(auditoria)

    def test_e_score_bajo_revision_no_rechazo_inventado(self):
        auditoria = self.evaluar(score=0)
        self.assertEqual(auditoria.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertIsNotNone(auditoria.score)
        self.assert_no_aprueba(auditoria)

    def test_e_score_medio_decision_del_motor(self):
        auditoria = self.evaluar(score=667)
        self.assertEqual(auditoria.resultado, 'PREAPROBADO_READ_ONLY', auditoria.snapshot_salida)
        self.assertIsNotNone(auditoria.score)

    def test_e_capacidad_insuficiente_revision(self):
        auditoria = self.evaluar(deuda=Decimal('9000000'))
        self.assertEqual(auditoria.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertGreater(auditoria.relacion_carga_ingreso, self.politica.cuota_ingreso_maxima)
        self.assert_no_aprueba(auditoria)

    def test_e_mora_hdc_no_es_rechazo_automatico_en_politica_actual(self):
        auditoria = self.evaluar(mora=100000)
        self.assertTrue(auditoria.snapshot_hdcplus.resultado_normalizado['mora_severa'])
        self.assertIsNotNone(auditoria.score)
        self.assertEqual(auditoria.resultado, 'PREAPROBADO_READ_ONLY', auditoria.snapshot_salida)

    def test_e_limites_producto_rechazan_simulacion_sin_consultar(self):
        anterior = self.solicitud.simulada_en
        for monto, plazo in (('999999', 6), ('10000001', 6), ('3000000', 9)):
            with self.subTest(monto=monto, plazo=plazo):
                response = self.client.post('/simular/', {
                    'solicitud_id': self.solicitud.pk, 'monto': monto, 'plazo_meses': plazo,
                }, HTTP_HOST=self.host)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.simulada_en, anterior)
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())

    def test_autorizacion_invalida_no_consume_ni_decide(self):
        self.solicitud.autoriza_consulta_centrales = False
        self.solicitud.save(update_fields=['autoriza_consulta_centrales'])
        self.assertIsNone(obtener_autorizacion_datacredito_vigente(self.solicitud))
        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural') as proveedor:
            auditoria = evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.staff).auditoria
        proveedor.assert_not_called()
        self.assertEqual(auditoria.resultado, 'NO_EVALUABLE')
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())
        self.assert_no_aprueba(auditoria)

    def test_aislamiento_permisos_cache_e_historicos(self):
        auditoria = self.evaluar()
        historicos = list(ConsultaDatacreditoSnapshot.objects.order_by('pk').values())
        ajeno = get_user_model().objects.create_user(username='ajeno-sintetico-e2e')
        with self.assertRaises(PermissionDenied):
            obtener_evaluacion_datacredito_prestador(self.solicitud, solicitado_por=ajeno)
        otra = deepcopy(self.solicitud)
        otra.pk = None
        otra.save()
        oferta_ajena = calcular_oferta_evaluada(
            otra, auditoria, politica=self.politica,
            monto=otra.monto_solicitado, plazo=otra.plazo_meses,
        )
        self.assertEqual(oferta_ajena.estado, 'NO_EVALUABLE')
        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural') as proveedor:
            resultado = obtener_evaluacion_datacredito_prestador(
                otra, modo='SOLO_CACHE', solicitado_por=self.staff, servicio='decisor',
            )
        proveedor.assert_not_called()
        self.assertEqual(resultado.estado, 'AUTORIZACION_REQUERIDA')
        registrar_autorizacion_datacredito_desde_solicitud(otra, usuario=self.usuario)
        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural') as proveedor:
            reutilizado = obtener_evaluacion_datacredito_prestador(
                otra, modo='SOLO_CACHE', solicitado_por=self.staff, servicio='decisor',
            )
            otra.numero_documento = '900000098'
            otra.save(update_fields=['numero_documento'])
            diferente = obtener_evaluacion_datacredito_prestador(
                otra, modo='SOLO_CACHE', solicitado_por=self.staff, servicio='decisor',
            )
        proveedor.assert_not_called()
        self.assertEqual(str(reutilizado.snapshot_id), str(auditoria.snapshot_midecisor_id))
        self.assertTrue(reutilizado.reutilizado)
        self.assertEqual(diferente.estado, 'SIN_CACHE')
        self.assertEqual(historicos, list(ConsultaDatacreditoSnapshot.objects.order_by('pk').values()))
        self.assert_sin_originacion()

    def test_segregacion_staff_sin_permiso_y_pagador_con_permiso(self):
        sin_permiso = get_user_model().objects.create_user(username='staff-sin-permiso-e2e', is_staff=True)
        pagador = get_user_model().objects.create_superuser(username='pagador-con-permisos-e2e')
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        for actor in (self.usuario, sin_permiso, pagador):
            with self.subTest(actor=actor.pk), self.assertRaises(PermissionDenied):
                evaluar_solicitud_prestador(self.solicitud, solicitado_por=actor)
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())
        self.assert_sin_originacion()

    def test_documento_faltante_bloquea_avance(self):
        self.solicitud.documentos.filter(tipo_documento='CONTRATO').delete()
        response = self.client.post('/simular/', {
            'solicitud_id': self.solicitud.pk, 'monto': '3000000', 'plazo_meses': 6,
        }, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].non_field_errors())
        self.assertFalse(ConsultaDatacreditoSnapshot.objects.exists())
        self.assert_sin_originacion()

    def test_cache_entre_solicitudes_traza_consentimiento_propio_y_conserva_historicos(self):
        inicial = self.evaluar()
        historicos = list(ConsultaDatacreditoSnapshot.objects.order_by('pk').values())
        audit_inicial = PredecisionPrestadorAudit.objects.values().get(pk=inicial.pk)
        otra = self.nueva_solicitud_autorizada()
        autorizacion = obtener_autorizacion_datacredito_vigente(otra)
        nueva = self.evaluar_solo_cache(otra).auditoria
        repetida = self.evaluar_solo_cache(otra)
        self.assertTrue(repetida.reutilizada)
        self.assertEqual(repetida.auditoria.pk, nueva.pk)
        self.assertEqual(nueva.resultado, 'PREAPROBADO_READ_ONLY', nueva.snapshot_salida)
        self.assertEqual(nueva.snapshot_midecisor_id, inicial.snapshot_midecisor_id)
        self.assertNotEqual(nueva.snapshot_midecisor.autorizacion_referencia, str(autorizacion.pk))
        for servicio, uso in nueva.snapshot_salida['usos_snapshots'].items():
            self.assertEqual(uso['solicitud_id'], otra.pk)
            self.assertEqual(uso['autorizacion_id'], autorizacion.pk)
            self.assertEqual(uso['autorizacion_origen_id'], int(nueva.snapshot_midecisor.autorizacion_referencia))
            self.assertEqual(uso['servicio'], servicio)
        gate, creado = crear_o_reutilizar_aprobacion_interna(nueva, actor=self.staff)
        self.assertTrue(creado)
        self.assertEqual(crear_o_reutilizar_aprobacion_interna(nueva, actor=self.staff)[0].pk, gate.pk)
        self.assertEqual(otra.auditorias_predecision.count(), 1)
        self.assertEqual(audit_inicial, PredecisionPrestadorAudit.objects.values().get(pk=inicial.pk))
        self.assertEqual(historicos, list(ConsultaDatacreditoSnapshot.objects.order_by('pk').values()))
        self.assert_sin_originacion()

    def test_reutilizacion_autorizada_no_omite_oferta_sin_capacidad(self):
        self.evaluar()
        otra = self.nueva_solicitud_autorizada(ingreso='100000')
        nueva = self.evaluar_solo_cache(otra).auditoria
        self.assertEqual(nueva.resultado, 'PREAPROBADO_READ_ONLY')
        self.assertEqual(len(nueva.snapshot_salida['usos_snapshots']), 2)
        with self.assertRaises(ValidationError) as error:
            crear_o_reutilizar_aprobacion_interna(nueva, actor=self.staff)
        self.assertEqual(error.exception.code, 'sin_oferta')
        self.assertFalse(AprobacionInternaPrestador.objects.exists())
        self.assert_sin_originacion()

    def test_reutilizacion_no_autorizada_no_preaprueba_ni_crea_gate(self):
        self.evaluar()
        otra = self.nueva_solicitud_autorizada()
        self.solicitud.autoriza_consulta_centrales = False
        self.solicitud.save(update_fields=['autoriza_consulta_centrales'])
        nueva = self.evaluar_solo_cache(otra).auditoria
        self.assertEqual(nueva.resultado, 'NO_EVALUABLE')
        self.assertIn('reutilizacion_no_autorizada', nueva.error_codigo)
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(nueva, actor=self.staff)
        self.assertFalse(AprobacionInternaPrestador.objects.exists())
        self.assert_sin_originacion()

    def test_gate_revalida_origen_y_consentimiento_destino_despues_de_reutilizar(self):
        inicial = self.evaluar()
        otra = self.nueva_solicitud_autorizada()
        nueva = self.evaluar_solo_cache(otra).auditoria
        gate, _ = crear_o_reutilizar_aprobacion_interna(nueva, actor=self.staff)
        for solicitud in (self.solicitud, otra):
            with self.subTest(solicitud=solicitud.pk):
                solicitud.autoriza_consulta_centrales = False
                solicitud.save(update_fields=['autoriza_consulta_centrales'])
                with self.assertRaises(ValidationError):
                    iniciar_analisis_aprobacion_interna(gate, actor=self.staff)
                solicitud.autoriza_consulta_centrales = True
                solicitud.save(update_fields=['autoriza_consulta_centrales'])
        self.assertEqual(inicial.resultado, 'PREAPROBADO_READ_ONLY')
        self.assert_sin_originacion()

    def test_auditoria_historica_sin_evidencia_no_habilita_reutilizacion_cruzada(self):
        self.evaluar()
        otra = self.nueva_solicitud_autorizada()
        nueva = self.evaluar_solo_cache(otra).auditoria
        # Inspect the legacy contract in memory, without rewriting completed audits.
        nueva.snapshot_salida = {k: v for k, v in nueva.snapshot_salida.items() if k != 'usos_snapshots'}
        from contractors.services.aprobacion_interna import _obtener_snapshots_datacredito
        self.assertIsNone(_obtener_snapshots_datacredito(
            nueva, obtener_autorizacion_datacredito_vigente(otra), self.politica,
        ))

    def test_cierre_operativo_manual_no_origina(self):
        auditoria = self.evaluar()
        gate, _ = crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)
        gate = cerrar_sin_originar(
            gate, actor=self.staff, motivo=AprobacionInternaPrestador.Motivo.CIERRE_OPERATIVO,
            comentario_interno='Cierre sintetico decidido por staff, no rechazo automatico por score',
        )
        self.assertEqual(gate.estado, 'CERRADA_SIN_ORIGINAR')
        with self.assertRaises(ValidationError):
            originar_credito_prestador_desde_gate(gate, actor=self.staff)
        self.assert_sin_originacion()

    def test_ingreso_neto_insuficiente_bloquea_gate_aunque_score_alto(self):
        self.verificar_ingreso('100000')
        auditoria = self.evaluar()
        # Existing formal capacity uses contractual income, not the verified net source.
        self.assertEqual(auditoria.resultado, 'PREAPROBADO_READ_ONLY')
        self.assertGreater(auditoria.ingreso_contractual_mensual, Decimal('100000'))
        with self.assertRaises(ValidationError) as error:
            crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)
        self.assertEqual(error.exception.code, 'sin_oferta')
        self.assertFalse(AprobacionInternaPrestador.objects.exists())
        self.assertFalse(AprobacionPagadorPrestador.objects.exists())
        with patch('contractors.datacredito.adapter.consultar_midecisor_persona_natural') as decisor, \
             patch('contractors.datacredito.adapter.consultar_historial_credito') as hdc:
            centrales = obtener_evaluacion_centrales_prestador(
                self.solicitud, politica=self.politica, solicitado_por=self.staff,
            )
            score = evaluar_score_prestador(self.solicitud, self.politica, centrales)
            oferta = preparar_oferta(
                score=score, politica=self.politica,
                ingreso_neto=obtener_ingreso_neto_vigente(self.solicitud),
                obligaciones_mensuales=auditoria.cuota_mensual_hdc,
                monto_solicitado=self.solicitud.monto_solicitado, plazo_solicitado=self.solicitud.plazo_meses,
                horizonte=horizonte_para_solicitud(self.solicitud, self.financiera),
                configuracion=self.financiera, corte=timezone.localdate(), solicitud_id=self.solicitud.pk,
            )
        decisor.assert_not_called()
        hdc.assert_not_called()
        self.assertEqual(score.score_final, auditoria.score)
        self.assertEqual(oferta.estado, 'SIN_OFERTA')
        self.assertEqual(oferta.cuota_maxima, Decimal('0'))
        self.assert_sin_originacion()

    def test_sin_ingreso_verificado_no_inventa_oferta(self):
        registrar_ingreso_neto(solicitud=self.solicitud, actor=self.staff, invalidar=True,
                              observacion='Invalidacion sintetica de evidencia neta')
        auditoria = self.evaluar()
        self.assertEqual(self.oferta(auditoria).estado, 'NO_EVALUABLE')
        with self.assertRaises(ValidationError) as error:
            crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)
        self.assertEqual(error.exception.code, 'oferta_no_evaluable')
        self.assertFalse(AprobacionInternaPrestador.objects.exists())

    def test_gate_vigente_deja_de_ser_viable_al_cambiar_ingreso(self):
        auditoria = self.evaluar()
        historico = PredecisionPrestadorAudit.objects.values().get(pk=auditoria.pk)
        gate, _ = crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)
        self.verificar_ingreso('100000')
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)
        with self.assertRaises(ValidationError):
            iniciar_analisis_aprobacion_interna(gate, actor=self.staff)
        decidido = aprobar_para_originar(gate, actor=self.staff)
        self.assertEqual(decidido.estado, 'DEVUELTA_A_REVISION')
        self.assertFalse(AprobacionPagadorPrestador.objects.exists())
        self.assertEqual(historico, PredecisionPrestadorAudit.objects.values().get(pk=auditoria.pk))
        self.assert_sin_originacion()

    def test_plazo_reducido_revalida_cuota_real_y_no_aprueba(self):
        self.verificar_ingreso('2000000')
        auditoria = self.evaluar()
        gate, _ = crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)
        self.assertLess(gate.monto_maximo_evaluado, self.solicitud.monto_solicitado)
        with self.assertRaises(ValidationError):
            aprobar_para_originar(gate, actor=self.staff, plazo_autorizado=1)
        gate.refresh_from_db()
        self.assertEqual(gate.estado, 'PENDIENTE')
        self.assertFalse(AprobacionPagadorPrestador.objects.exists())
        self.assert_sin_originacion()

    def test_error_tecnico_oferta_es_controlado_y_no_rechazo(self):
        auditoria = self.evaluar()
        with patch('contractors.services.oferta_financiera.preparar_oferta',
                   side_effect=RuntimeError('detalle sensible que no debe exponerse')):
            with self.assertRaises(ValidationError) as error:
                crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)
        self.assertEqual(error.exception.code, 'oferta_error_controlado')
        self.assertNotIn('sensible', str(error.exception))
        self.solicitud.refresh_from_db()
        self.assertNotEqual(self.solicitud.estado, 'NO_APROBADO')
        self.assertFalse(AprobacionInternaPrestador.objects.exists())

    def test_pagador_y_expediente_directos_no_omiten_oferta_vencida(self):
        self.verificar_ingreso('10000000', vigencia=0)
        auditoria = self.evaluar()
        gate, _ = crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)
        gate = aprobar_para_originar(gate, actor=self.staff)
        pagador = get_user_model().objects.create_user(username='pagador-oferta-vencida')
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        pagador.user_permissions.add(Permission.objects.get(
            content_type__app_label='contractors', codename='can_decide_contractor_payer_approval',
        ))
        aprobacion = AprobacionPagadorPrestador.objects.get(aprobacion_interna=gate)
        decidir_aprobacion_pagador_prestador(
            aprobacion, actor=pagador, decision='APROBADO',
            confirmaciones={campo: True for campo in CONFIRMACIONES_REQUERIDAS},
        )
        expediente = construir_expediente_originacion_prestador(gate)
        manana = timezone.localdate() + timedelta(days=1)
        with patch('django.utils.timezone.localdate', return_value=manana):
            with self.assertRaises(ValidationError):
                crear_o_reutilizar_aprobacion_pagador(gate, actor=self.staff)
            with self.assertRaises(ValidationError):
                decidir_aprobacion_pagador_prestador(
                    aprobacion, actor=pagador, decision='APROBADO',
                    confirmaciones={campo: True for campo in CONFIRMACIONES_REQUERIDAS},
                )
            with self.assertRaises(ValidationError):
                construir_expediente_originacion_prestador(gate)
            with self.assertRaises(ValidationError):
                originar_credito_prestador_desde_gate(gate, actor=self.staff)
            with self.assertRaises(ValidationError):
                originar_libranza_desde_expediente(
                    expediente, construir_clave_idempotencia_prestador(expediente), self.staff,
                )
        from gestion_creditos.models import OrigenCreditoPrestador
        self.assertFalse(OrigenCreditoPrestador.objects.exists())
        self.assert_sin_originacion()
