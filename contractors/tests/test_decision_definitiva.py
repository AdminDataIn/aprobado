"""Final V3 mandate; every identity, provider and exclusion here is synthetic."""
from copy import deepcopy
from datetime import timedelta
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, close_old_connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from contractors.models import AprobacionInternaPrestador, AprobacionPagadorPrestador, TimelinePrestador
from contractors.politica_decision import VERSION_POLITICA, definicion_decision
from contractors.services.decision_definitiva import (
    APROBADO, RECHAZADO, REVISION, TIPO_EVENTO, consultar_decision_vigente, decidir_solicitud_prestador,
)
from contractors.services.politica_ingreso_publicacion import publicar_politica_ingreso, adoptar_politica_ingreso
from contractors.services.aprobacion_pagador import CONFIRMACIONES_REQUERIDAS, decidir_aprobacion_pagador_prestador
from contractors.services.expediente_originacion import construir_expediente_originacion_prestador
from contractors.services.originacion import originar_credito_prestador_desde_gate
from contractors.services.formalizacion import (
    preparar_formalizacion_credito_prestador, registrar_resultado_validacion_identidad_prestador,
    enviar_formalizacion_prestador_a_firma, procesar_callback_formalizacion_prestador,
)
from contractors.services.postfirma import preparar_transferencia_credito_prestador, confirmar_desembolso_credito_prestador
from contractors.tests.test_aprobacion_interna_prestador import ClienteFirmaPrueba
from contractors.tests.test_pipeline_financiero_e2e import FixturePipelineFinanciero
from django.core.files.uploadedfile import SimpleUploadedFile
from gestion_creditos.models import Credito
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA
from usuarios.models import PerfilPagador


class FixtureDecisionV3(FixturePipelineFinanciero):
    exclusiones_test = ()

    def setUp(self):
        super().setUp()
        self.reglas = deepcopy(definicion_decision())
        if self.exclusiones_test:
            self.reglas.update(exclusiones=list(self.exclusiones_test),
                               fuente_normativa='POLITICA_SINTETICA_SOLO_BASE_TEST')
        guard = patch('contractors.politica_decision.definicion_decision', return_value=self.reglas)
        guard.start()
        self.addCleanup(guard.stop)
        from contractors.score.politica import obtener_politica_score_activa
        from contractors.models import ConfiguracionScorePrestador, BandaScorePrestador
        self.historicas = list(ConfiguracionScorePrestador.objects.order_by('pk').values())
        self.bandas_historicas = list(BandaScorePrestador.objects.order_by('pk').values())
        origen = obtener_politica_score_activa()
        self.politica = publicar_politica_ingreso(origen_id=origen.pk, actor=self.staff,
            motivo='Preparacion V3 sintetica, sin activacion global', vigente_desde=timezone.localdate(),
            version_destino=VERSION_POLITICA)
        self.solicitud = adoptar_politica_ingreso(solicitud_id=self.solicitud.pk,
            politica_id=self.politica.pk, actor=self.staff, motivo='Adopcion sintetica V3')

    def decision(self, audit):
        return decidir_solicitud_prestador(audit, actor=self.staff)


@override_settings(**CONFIGURACION_DATACREDITO_PRUEBA,
    ALLOWED_HOSTS=['testserver','localhost','contratistas.localhost'], SECURE_SSL_REDIRECT=False,
    CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DecisionDefinitivaTest(FixtureDecisionV3, TestCase):
    def test_publicacion_inactiva_preserva_v1_v2_y_adopcion_es_explicita(self):
        from contractors.models import ConfiguracionScorePrestador, BandaScorePrestador
        self.assertFalse(self.politica.activa)
        self.assertEqual(self.solicitud.version_politica_simulacion, VERSION_POLITICA)
        self.assertEqual(self.historicas, list(ConfiguracionScorePrestador.objects.filter(
            pk__in=[p['id'] for p in self.historicas]).order_by('pk').values()))
        self.assertEqual(self.bandas_historicas, list(BandaScorePrestador.objects.filter(
            pk__in=[p['id'] for p in self.bandas_historicas]).order_by('pk').values()))

    def test_v3_aprueba_sin_analista_pero_exige_pagador_y_origen_validado(self):
        audit = self.evaluar()
        decision = self.decision(audit)
        self.assertEqual(decision.resultado, APROBADO)
        self.assertTrue(decision.reutilizada)
        gate = AprobacionInternaPrestador.objects.get(pk=decision.gate_id)
        self.assertEqual(gate.estado, 'APROBADA_PARA_ORIGINAR')
        self.assertEqual(gate.decidida_por_id, self.staff.pk)
        self.assertEqual(AprobacionPagadorPrestador.objects.get(aprobacion_interna=gate).estado, 'PENDIENTE')
        with self.assertRaises(ValidationError):
            construir_expediente_originacion_prestador(gate)
        self.assert_sin_originacion()
        self.assertEqual(self.solicitud.timeline_operativo.filter(metadata__tipo=TIPO_EVENTO).count(), 1)
        from contractors.services.presentacion_solicitud import construir_estado_publico_solicitud
        self.solicitud.refresh_from_db()
        self.assertEqual(construir_estado_publico_solicitud(self.solicitud)['codigo'], 'PENDIENTE_CONFIRMACION_EMPRESA')

    def test_catalogo_real_vacio_no_rechaza_banda_baja(self):
        self.assertEqual(self.reglas['exclusiones'], [])
        audit = self.evaluar(score=0)
        self.assertEqual(self.decision(audit).resultado, REVISION)
        self.solicitud.refresh_from_db()
        self.assertNotEqual(self.solicitud.estado, 'NO_APROBADO')

    def test_ingreso_insuficiente_distingue_sin_oferta_y_revision_sin_gate(self):
        self.verificar_ingreso('100000')
        audit = self.evaluar()
        self.assertEqual(self.oferta(audit).estado, 'NO_EVALUABLE')
        from contractors.services.centrales_riesgo import obtener_evaluacion_centrales_prestador
        from contractors.score.motor import evaluar_score_prestador
        from contractors.services.ingreso_neto import obtener_ingreso_neto_vigente
        from contractors.services.politica_financiera_prestador import preparar_oferta
        from contractors.services.horizonte_simulacion import horizonte_para_solicitud
        fuentes = obtener_evaluacion_centrales_prestador(
            self.solicitud, politica=self.politica, solicitado_por=self.staff)
        oferta = preparar_oferta(
            score=evaluar_score_prestador(self.solicitud,self.politica,fuentes), politica=self.politica,
            ingreso_neto=obtener_ingreso_neto_vigente(self.solicitud),
            obligaciones_mensuales=audit.cuota_mensual_hdc,
            monto_solicitado=self.solicitud.monto_solicitado, plazo_solicitado=self.solicitud.plazo_meses,
            horizonte=horizonte_para_solicitud(self.solicitud,self.financiera),
            configuracion=self.financiera, corte=timezone.localdate(),solicitud_id=self.solicitud.pk)
        self.assertEqual(oferta.estado, 'SIN_OFERTA')
        self.assertEqual(self.decision(audit).resultado, REVISION)
        self.assertFalse(AprobacionInternaPrestador.objects.exists())
        self.assertTrue(self.solicitud.revisiones_manuales.exists())

    def test_hc14_y_hdc_sin_informacion_siempre_revision(self):
        audit = self.evaluar(hc='14',tx='05',hdc_sin_informacion=True)
        result = self.decision(audit)
        self.assertEqual(result.resultado, REVISION)
        self.assertIn('FUENTES_OFICIALES_NO_UTILIZABLES', result.causas)
        self.assert_no_aprueba(audit)

    def test_revision_se_reutiliza_y_mandato_modificado_no_la_convierte_en_aprobacion(self):
        audit = self.evaluar(hc='14', tx='05', hdc_sin_informacion=True)
        previo = self.decision(audit)
        self.assertTrue(previo.reutilizada)
        self.reglas['aprobacion_automatica'] = False
        self.assertIsNone(consultar_decision_vigente(audit))
        nueva = self.decision(audit)
        self.assertEqual(nueva.resultado, REVISION)
        self.assertNotEqual(previo.evento_id, nueva.evento_id)
        with patch('django.utils.timezone.now',return_value=timezone.now()+timedelta(days=60)):
            self.assertIsNone(consultar_decision_vigente(audit))
        self.assertFalse(AprobacionInternaPrestador.objects.exists())

    def test_estimacion_contractual_no_habilita_aprobacion(self):
        from contractors.models import IngresoNetoVerificadoPrestador
        # Test-only fixture without any verified income, not a production update.
        IngresoNetoVerificadoPrestador.objects.all().delete()
        audit = self.evaluar()
        self.assertEqual(self.decision(audit).resultado, REVISION)
        self.assertFalse(AprobacionInternaPrestador.objects.exists())

    def test_operador_puede_pedir_evidencia_sin_convertir_hc14_en_aprobacion(self):
        from contractors.services.revision_manual import solicitar_subsanacion, resolver_revision
        from contractors.models import PredecisionPrestadorAudit
        audit = self.evaluar(hc='14', tx='05', hdc_sin_informacion=True)
        previo = PredecisionPrestadorAudit.objects.values().get(pk=audit.pk)
        revision = self.solicitud.revisiones_manuales.first()
        self.client.force_login(self.staff)
        response = self.client.get('/gestion/prestadores/', HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        solicitar_subsanacion(revision, tipo='INFORMACION_CONTRACTUAL', actor=self.staff)
        resolver_revision(revision, resultado='CONTINUAR_EVALUACION', actor=self.staff,
            comentario_interno='Revision sintetica; no acredita fuente utilizable')
        self.assertEqual(self.decision(audit).resultado, REVISION)
        self.assertEqual(previo, PredecisionPrestadorAudit.objects.values().get(pk=audit.pk))
        self.assert_sin_originacion()
        self.assertFalse(AprobacionInternaPrestador.objects.exists())

    def test_invalidacion_por_ingreso_no_reutiliza_aprobacion_y_bloquea_expediente(self):
        audit = self.evaluar()
        approved = self.decision(audit)
        from contractors.services.aprobacion_interna import _devolver_gate_a_revision
        _devolver_gate_a_revision(AprobacionInternaPrestador.objects.get(pk=approved.gate_id),
            actor=self.staff, motivo='DATOS_MODIFICADOS', comentario='Nueva evidencia sintetica')
        self.verificar_ingreso('100000')
        self.assertIsNone(consultar_decision_vigente(audit))
        self.assertEqual(self.decision(audit).resultado, REVISION)
        gate = AprobacionInternaPrestador.objects.get(pk=approved.gate_id)
        self.assertNotEqual(gate.estado, 'APROBADA_PARA_ORIGINAR')
        with self.assertRaises(ValidationError):
            construir_expediente_originacion_prestador(gate)

    def test_caducidad_no_reutiliza_decision(self):
        audit = self.evaluar()
        self.assertEqual(self.decision(audit).resultado, APROBADO)
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(days=60)):
            self.assertIsNone(consultar_decision_vigente(audit))

    def test_fallo_de_auditoria_revierte_gate_y_pagador_sin_reconsultar_fuentes(self):
        from contractors.models import PredecisionPrestadorAudit
        with patch('contractors.services.evaluacion_formal._integrar_decision_final',
                   side_effect=lambda resultado, actor: resultado):
            audit = self.evaluar()
        original = PredecisionPrestadorAudit.objects.values().get(pk=audit.pk)
        with patch('contractors.services.decision_definitiva.registrar_evento_timeline_prestador',
                   side_effect=RuntimeError('Fallo sintetico de persistencia')):
            with self.assertRaises(RuntimeError):
                self.decision(audit)
        self.assertFalse(AprobacionInternaPrestador.objects.exists())
        self.assertFalse(AprobacionPagadorPrestador.objects.exists())
        self.assertFalse(self.solicitud.timeline_operativo.filter(metadata__tipo=TIPO_EVENTO).exists())
        self.assertEqual(original, PredecisionPrestadorAudit.objects.values().get(pk=audit.pk))
        self.assertEqual(self.decision(audit).resultado, APROBADO)

    def test_actor_evaluador_sin_permiso_decidir_deriva_a_revision(self):
        with patch('contractors.services.evaluacion_formal._integrar_decision_final',
                   side_effect=lambda resultado, actor: resultado):
            audit = self.evaluar()
        user = get_user_model().objects.create_user(username='evaluador-no-decisor', is_staff=True)
        user.user_permissions.add(Permission.objects.get(content_type__app_label='contractors',
            codename='can_evaluate_contractor_application'))
        result = decidir_solicitud_prestador(audit, actor=user)
        self.assertEqual(result.resultado, REVISION)
        self.assertIn('ACTOR_SIN_FACULTAD_DE_APROBACION', result.causas)
        self.assertFalse(AprobacionInternaPrestador.objects.exists())

    def test_cambio_mandato_invalida_sin_reescribir_evento_historico(self):
        audit = self.evaluar()
        previous = self.decision(audit)
        snapshot = TimelinePrestador.objects.values().get(pk=previous.evento_id)
        self.reglas['aprobacion_automatica'] = False
        self.assertIsNone(consultar_decision_vigente(audit))
        self.assertEqual(self.decision(audit).resultado, REVISION)
        self.assertEqual(snapshot, TimelinePrestador.objects.values().get(pk=previous.evento_id))

    def test_permisos_y_pagador_no_pueden_ejecutar_decisor(self):
        audit = self.evaluar()
        staff = get_user_model().objects.create_user(username='staff-sin-permiso',is_staff=True)
        with self.assertRaises(PermissionDenied):
            decidir_solicitud_prestador(audit,actor=staff)
        PerfilPagador.objects.create(usuario=self.staff,empresa=self.empresa)
        with self.assertRaises(PermissionDenied):
            decidir_solicitud_prestador(audit,actor=self.staff)

    def test_auto_aprobacion_hasta_credito_activo_sintetico(self):
        audit = self.evaluar()
        gate = AprobacionInternaPrestador.objects.get(pk=self.decision(audit).gate_id)
        payer = get_user_model().objects.create_user(username='pagador-auto-sintetico')
        PerfilPagador.objects.create(usuario=payer,empresa=self.empresa)
        payer.user_permissions.add(Permission.objects.get(content_type__app_label='contractors',codename='can_decide_contractor_payer_approval'))
        decidir_aprobacion_pagador_prestador(AprobacionPagadorPrestador.objects.get(aprobacion_interna=gate),
            actor=payer,decision='APROBADO',confirmaciones={c:True for c in CONFIRMACIONES_REQUERIDAS})
        origin = originar_credito_prestador_desde_gate(gate,actor=self.staff).origen
        formal = preparar_formalizacion_credito_prestador(origin,actor=self.staff).formalizacion
        formal = registrar_resultado_validacion_identidad_prestador(formal,usuario=self.usuario,
            referencia_proveedor='identidad-sintetica-auto',expira_en=timezone.now()+timedelta(minutes=15))
        enviar_formalizacion_prestador_a_firma(formal,actor=self.staff,cliente=ClienteFirmaPrueba(token='firma-auto-sintetica'))
        procesar_callback_formalizacion_prestador(documento_id='firma-auto-sintetica',accion='signed',estado_proveedor='signed')
        preparar_transferencia_credito_prestador(formal.credito,actor=self.staff)
        active = confirmar_desembolso_credito_prestador(formal.credito,actor=self.staff,
            comprobante=SimpleUploadedFile('prueba.pdf',b'%PDF-1.4\nPRUEBA AISLADA'))
        self.assertEqual(active.credito.estado,Credito.EstadoCredito.ACTIVO)
        duplicate = procesar_callback_formalizacion_prestador(
            documento_id='firma-auto-sintetica',accion='signed',estado_proveedor='signed')
        self.assertEqual(duplicate['estado'], 'already_processed')


@override_settings(**CONFIGURACION_DATACREDITO_PRUEBA,
    ALLOWED_HOSTS=['testserver','localhost','contratistas.localhost'], SECURE_SSL_REDIRECT=False,
    CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class RechazoSinteticoTest(FixtureDecisionV3, TestCase):
    exclusiones_test = ('BANDA_REVISION',)

    def test_causal_sintetica_rechaza_cierra_revision_no_origina_y_es_idempotente(self):
        audit = self.evaluar(score=0)
        result = self.decision(audit)
        self.assertEqual(result.resultado,RECHAZADO,
            (result.causas, list(self.solicitud.revisiones_manuales.values_list('motivo',flat=True))))
        self.assertEqual(result.causas,('BANDA_REVISION',))
        self.assertTrue(result.reutilizada)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado,'NO_APROBADO')
        from contractors.services.presentacion_solicitud import construir_estado_publico_solicitud
        self.assertEqual(construir_estado_publico_solicitud(self.solicitud)['codigo'], 'NO_APROBADO')
        self.assertFalse(self.solicitud.revisiones_manuales.exclude(estado='RESUELTA').exists())
        self.assertEqual(self.solicitud.timeline_operativo.filter(metadata__tipo=TIPO_EVENTO).count(),1)
        self.assert_sin_originacion()

    def test_causal_sintetica_no_rechaza_fuentes_incompletas(self):
        audit=self.evaluar(score=0,hc='14',tx='05')
        self.assertEqual(self.decision(audit).resultado,REVISION)
        self.assert_sin_originacion()

    def test_rechazo_caduco_o_mandato_cambiado_no_se_reutiliza(self):
        audit = self.evaluar(score=0)
        previo = self.decision(audit)
        self.assertEqual(previo.resultado, RECHAZADO)
        with patch('django.utils.timezone.now',return_value=timezone.now()+timedelta(days=60)):
            self.assertIsNone(consultar_decision_vigente(audit))
        self.reglas['exclusiones'] = []
        self.assertIsNone(consultar_decision_vigente(audit))
        self.assertEqual(self.decision(audit).resultado, REVISION)
        self.assertEqual(TimelinePrestador.objects.get(pk=previo.evento_id).metadata['resultado'], RECHAZADO)
        self.assert_sin_originacion()


@skipUnless(connection.vendor=='postgresql','Requires PostgreSQL row locks')
@override_settings(**CONFIGURACION_DATACREDITO_PRUEBA,
    ALLOWED_HOSTS=['testserver','localhost','contratistas.localhost'], SECURE_SSL_REDIRECT=False,
    CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DecisionConcurrenciaPostgresTest(FixtureDecisionV3, TransactionTestCase):
    exclusiones_test = ('BANDA_REVISION',)

    def _concurrentes(self, expected, **kwargs):
        with patch('contractors.services.evaluacion_formal._integrar_decision_final',
                   side_effect=lambda resultado, actor: resultado):
            audit=self.evaluar(**kwargs)
        barrier=Barrier(2)
        def execute():
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                user=get_user_model().objects.get(pk=self.staff.pk)
                return decidir_solicitud_prestador(audit,actor=user)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados=list(pool.map(lambda _:execute(),range(2)))
        self.assertEqual(resultados[0].evento_id,resultados[1].evento_id)
        self.assertEqual([r.resultado for r in resultados], [expected, expected])
        self.assertEqual(self.solicitud.timeline_operativo.filter(metadata__tipo=TIPO_EVENTO).count(),1)
        self.assertEqual(AprobacionInternaPrestador.objects.count(), int(expected == APROBADO))

    def test_dos_aprobaciones_un_evento_y_un_gate(self):
        self._concurrentes(APROBADO)

    def test_dos_rechazos_un_evento_y_sin_gate(self):
        self._concurrentes(RECHAZADO, score=0)

    def test_dos_revisiones_un_evento_y_sin_gate(self):
        self._concurrentes(REVISION, hc='14',tx='05',hdc_sin_informacion=True)


class DefinicionDecisionTest(SimpleTestCase):
    def test_catalogo_real_vacio_y_aprobacion_expresamente_versionada(self):
        reglas = definicion_decision()
        self.assertEqual(reglas['exclusiones'], [])
        self.assertTrue(reglas['aprobacion_automatica'])
        self.assertEqual(reglas['version_politica'], VERSION_POLITICA)

    def test_definicion_invalida_falla_cerrado(self):
        for raw in ('null', '{}', '{', json.dumps({**definicion_decision(), 'exclusiones':['NO_AUTORIZADA']})):
            with self.subTest(raw=raw), patch('contractors.politica_decision.ARCHIVO') as archivo:
                archivo.read_text.return_value = raw
                with self.assertRaises(ValidationError):
                    definicion_decision()
