from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone

from contractors.models import (
    ContractorApplication,
    PredecisionPrestadorAudit,
    RevisionManualPrestador,
)
from gestion_creditos.models import (
    AprobacionPagadorLibranza,
    Credito,
    CreditoLibranza,
    Empresa,
)
from usuarios.models import PerfilPagador


class CheckpointEvaluacionStaffTest(TestCase):
    host = 'contratistas.localhost'

    def setUp(self):
        User = get_user_model()
        self.solicitante = User.objects.create_user(
            'prestador-checkpoint', password='test-password'
        )
        self.staff = User.objects.create_user(
            'staff-checkpoint', password='test-password', is_staff=True
        )
        self.staff_lector = User.objects.create_user(
            'lector-checkpoint', password='test-password', is_staff=True
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Checkpoint', convenio_activo=True
        )
        self.solicitud = self._crear_solicitud()
        self._otorgar(
            self.staff,
            'can_view_contractor_review_queue',
            'can_evaluate_contractor_application',
            'can_view_contractor_internal_approval',
        )
        self._otorgar(self.staff_lector, 'can_view_contractor_review_queue')

    def test_evaluacion_pendiente_aparece_en_dashboard_y_otras_etapas_no(self):
        otra = self._crear_solicitud(
            estado=ContractorApplication.Estado.DOCUMENTOS_PENDIENTES,
            documento='900000099',
        )
        self.client.force_login(self.staff)

        response = self.client.get('/gestion/prestadores/', HTTP_HOST=self.host)

        self.assertContains(response, 'Solicitudes pendientes de evaluación')
        self.assertContains(response, f'#{self.solicitud.id}')
        ids_pendientes = {
            solicitud.id
            for solicitud in response.context['pagina_pendientes'].object_list
        }
        self.assertIn(self.solicitud.id, ids_pendientes)
        self.assertNotIn(otra.id, ids_pendientes)
        self.assertContains(response, 'Pendientes de evaluación')

    def test_staff_autorizado_abre_detalle_y_ve_estado_formal(self):
        self.client.force_login(self.staff)

        response = self.client.get(
            f'/gestion/prestadores/{self.solicitud.id}/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Evaluación formal')
        self.assertContains(response, 'Ejecutar evaluación')
        self.assertContains(response, 'Autorización DataCrédito')
        self.assertContains(response, 'Pendiente')

    @patch('contractors.views_admin.evaluar_solicitud_prestador')
    def test_endpoint_solo_post_y_get_no_ejecuta(self, evaluar):
        self.client.force_login(self.staff)

        response = self.client.get(
            f'/gestion/prestadores/solicitudes/{self.solicitud.id}/evaluar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 405)
        evaluar.assert_not_called()

    @patch('contractors.views_admin.evaluar_solicitud_prestador')
    def test_staff_con_permiso_ejecuta_el_servicio_formal(self, evaluar):
        auditoria = self._crear_auditoria(
            PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY
        )
        evaluar.return_value = SimpleNamespace(
            auditoria=auditoria,
            reutilizada=False,
            en_proceso=False,
        )
        self.client.force_login(self.staff)

        response = self.client.post(
            f'/gestion/prestadores/solicitudes/{self.solicitud.id}/evaluar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 302)
        evaluar.assert_called_once_with(
            self.solicitud,
            solicitado_por=self.staff,
        )
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)
        self.assertEqual(AprobacionPagadorLibranza.objects.count(), 0)

    @patch('contractors.views_admin.evaluar_solicitud_prestador')
    def test_staff_sin_permiso_no_ejecuta(self, evaluar):
        self.client.force_login(self.staff_lector)

        response = self.client.post(
            f'/gestion/prestadores/solicitudes/{self.solicitud.id}/evaluar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 403)
        evaluar.assert_not_called()

    @patch('contractors.views_admin.evaluar_solicitud_prestador')
    def test_perfil_pagador_no_ejecuta_aunque_tenga_permiso(self, evaluar):
        pagador = get_user_model().objects.create_user(
            'pagador-checkpoint', password='test-password', is_staff=True
        )
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        self._otorgar(
            pagador,
            'can_view_contractor_review_queue',
            'can_evaluate_contractor_application',
        )
        self.client.force_login(pagador)

        response = self.client.post(
            f'/gestion/prestadores/solicitudes/{self.solicitud.id}/evaluar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 403)
        evaluar.assert_not_called()

    @patch('contractors.views_admin.evaluar_solicitud_prestador')
    def test_doble_post_reutiliza_resultado_del_servicio_sin_nueva_auditoria(self, evaluar):
        auditoria = self._crear_auditoria(
            PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY
        )
        evaluar.return_value = SimpleNamespace(
            auditoria=auditoria,
            reutilizada=True,
            en_proceso=False,
        )
        self.client.force_login(self.staff)

        url = f'/gestion/prestadores/solicitudes/{self.solicitud.id}/evaluar/'
        self.client.post(url, HTTP_HOST=self.host)
        self.client.post(url, HTTP_HOST=self.host)

        self.assertEqual(evaluar.call_count, 2)
        self.assertEqual(self.solicitud.auditorias_predecision.count(), 1)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)

    def test_preaprobado_aparece_en_cola_correspondiente(self):
        self._crear_auditoria(
            PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY
        )
        self.solicitud.estado = ContractorApplication.Estado.EVALUACION_COMPLETADA
        self.solicitud.save(update_fields=['estado', 'updated_at'])
        self.client.force_login(self.staff)

        response = self.client.get('/gestion/prestadores/', HTTP_HOST=self.host)

        self.assertContains(response, 'Preaprobados pendientes de aprobación interna')
        self.assertContains(response, f'#{self.solicitud.id}')

    def test_revision_manual_aparece_en_cola_correspondiente(self):
        auditoria = self._crear_auditoria(
            PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL
        )
        RevisionManualPrestador.objects.create(
            solicitud=self.solicitud,
            auditoria_predecision=auditoria,
            motivo=RevisionManualPrestador.Motivo.OTRA_REVISION_CONTROLADA,
        )
        self.solicitud.estado = ContractorApplication.Estado.EN_REVISION_MANUAL
        self.solicitud.save(update_fields=['estado', 'updated_at'])
        self.client.force_login(self.staff)

        response = self.client.get('/gestion/prestadores/', HTTP_HOST=self.host)

        self.assertContains(response, 'Revisiones manuales')
        self.assertContains(response, f'#{self.solicitud.id}')

    def test_error_controlado_queda_visible_en_detalle(self):
        self._crear_auditoria(
            PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO,
            estado_ejecucion=PredecisionPrestadorAudit.EstadoEjecucion.ERROR_CONTROLADO,
        )
        self.solicitud.estado = ContractorApplication.Estado.EN_REVISION_MANUAL
        self.solicitud.save(update_fields=['estado', 'updated_at'])
        self.client.force_login(self.staff)

        response = self.client.get(
            f'/gestion/prestadores/{self.solicitud.id}/',
            HTTP_HOST=self.host,
        )

        self.assertContains(response, 'Error controlado')
        self.assertContains(response, 'La última evaluación terminó con un error controlado')

    def _crear_solicitud(self, *, estado=None, documento='900000005'):
        return ContractorApplication.objects.create(
            usuario=self.solicitante,
            empresa=self.empresa,
            tipo_documento='CC',
            numero_documento=documento,
            nombres='Persona',
            apellidos='Checkpoint',
            celular='3000000000',
            correo='checkpoint@example.com',
            direccion='Dirección de prueba',
            cargo='Servicios profesionales',
            monto_solicitado='2000000',
            plazo_meses=6,
            acepta_terminos=True,
            acepta_politica_privacidad=True,
            autoriza_consulta_centrales=True,
            estado_analisis_contractual=(
                ContractorApplication.EstadoAnalisisContractual.COMPLETADO
            ),
            estado=estado or ContractorApplication.Estado.EVALUACION_PENDIENTE,
        )

    def _crear_auditoria(self, resultado, *, estado_ejecucion=None):
        numero = self.solicitud.auditorias_predecision.count() + 1
        return PredecisionPrestadorAudit.objects.create(
            solicitud=self.solicitud,
            version_datos=f'checkpoint-v{numero}',
            clave_idempotencia=f'checkpoint-{self.solicitud.id}-{numero}',
            estado_ejecucion=(
                estado_ejecucion
                or PredecisionPrestadorAudit.EstadoEjecucion.COMPLETADA
            ),
            resultado=resultado,
            version_politica='checkpoint-politica-v1',
            version_score='checkpoint-score-v1',
            iniciada_en=timezone.now(),
            finalizada_en=timezone.now(),
        )

    @staticmethod
    def _otorgar(usuario, *codenames):
        usuario.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
