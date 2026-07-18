from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    PredecisionPrestadorAudit,
    RequerimientoSubsanacionPrestador,
    RevisionManualPrestador,
    TimelinePrestador,
)
from contractors.forms import AtenderSubsanacionPrestadorForm
from contractors.services.revision_manual import (
    asignar_revision,
    crear_revisiones_para_auditoria,
    resolver_revision,
    reintentar_evaluacion,
    solicitar_subsanacion,
    solicitar_validacion_empresa,
)
from gestion_creditos.models import AprobacionPagadorLibranza, Credito, CreditoLibranza, Empresa
from usuarios.models import PerfilPagador


class RevisionManualPrestadorTest(TestCase):
    host = 'contratistas.localhost'

    def setUp(self):
        User = get_user_model()
        self.solicitante = User.objects.create_user('solicitante-d', password='test')
        self.otro = User.objects.create_user('otro-d', password='test')
        self.analista = User.objects.create_user('analista-d', password='test', is_staff=True)
        self.staff_solo_lectura = User.objects.create_user(
            'lector-d', password='test', is_staff=True
        )
        self.empresa = Empresa.objects.create(nombre='Empresa Commit D', convenio_activo=True)
        self.solicitud = self._crear_solicitud()
        self._otorgar(self.analista, *[
            'can_view_contractor_review_queue',
            'can_assign_contractor_review',
            'can_resolve_contractor_review',
            'can_request_contractor_correction',
            'can_view_contractor_score_details',
        ])
        self._otorgar(self.staff_solo_lectura, 'can_view_contractor_review_queue')

    def test_resultado_revision_crea_revision_activa_idempotente(self):
        auditoria = self._crear_auditoria(
            resultado=PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL,
            alertas=['contrato:suspendido'],
        )

        primera = crear_revisiones_para_auditoria(auditoria, usuario=self.analista)
        segunda = crear_revisiones_para_auditoria(auditoria, usuario=self.analista)

        self.assertEqual(len(primera), 1)
        self.assertEqual(primera[0].motivo, RevisionManualPrestador.Motivo.CONTRATO_SUSPENDIDO)
        self.assertEqual(segunda[0].pk, primera[0].pk)
        self.assertEqual(self.solicitud.revisiones_manuales.count(), 1)

    def test_revision_resuelta_es_inmutable(self):
        revision = self._crear_revision()
        resolver_revision(
            revision,
            resultado=RevisionManualPrestador.Resultado.CONTINUAR_EVALUACION,
            actor=self.analista,
            comentario_interno='Validacion completada.',
        )
        revision.refresh_from_db()
        revision.comentario_interno = 'Alterado'

        with self.assertRaises(ValidationError):
            revision.save()

    def test_permisos_bandeja_y_resolucion_son_independientes(self):
        revision = self._crear_revision()
        usuario_sin_permiso = get_user_model().objects.create_user(
            'staff-sin-permiso', password='test', is_staff=True
        )
        self.client.force_login(usuario_sin_permiso)
        self.assertEqual(
            self.client.get('/gestion/prestadores/', HTTP_HOST=self.host).status_code,
            403,
        )

        self.client.force_login(self.staff_solo_lectura)
        self.assertEqual(
            self.client.get('/gestion/prestadores/', HTTP_HOST=self.host).status_code,
            200,
        )
        response = self.client.post(
            f'/gestion/prestadores/revisiones/{revision.id}/accion/',
            {'accion': 'RESOLVER', 'resultado': 'CONTINUAR_EVALUACION'},
            HTTP_HOST=self.host,
        )
        revision.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(revision.estado, RevisionManualPrestador.Estado.ABIERTA)

    def test_perfil_pagador_no_puede_usar_bandeja(self):
        pagador = get_user_model().objects.create_user(
            'pagador-commit-d', password='test', is_staff=True
        )
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        self._otorgar(pagador, 'can_view_contractor_review_queue')
        self.client.force_login(pagador)

        response = self.client.get('/gestion/prestadores/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 403)

    def test_servicio_rechaza_perfil_pagador_aunque_tenga_todos_los_permisos(self):
        revision = self._crear_revision()
        pagador = get_user_model().objects.create_user(
            'pagador-servicio-commit-d',
            password='test',
            is_staff=True,
        )
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        self._otorgar(pagador, *[
            'can_view_contractor_review_queue',
            'can_assign_contractor_review',
            'can_resolve_contractor_review',
            'can_request_contractor_correction',
            'can_view_contractor_score_details',
        ])

        with self.assertRaises(PermissionDenied):
            asignar_revision(revision, actor=pagador)

        revision.refresh_from_db()
        self.assertEqual(revision.estado, RevisionManualPrestador.Estado.ABIERTA)
        self.assertIsNone(revision.asignado_a)
        self.assertIsNone(revision.asignada_en)
        self.assertFalse(self.solicitud.timeline_operativo.filter(
            tipo_evento=TimelinePrestador.TipoEvento.REVISION_ASIGNADA,
        ).exists())

    def test_asignacion_registra_actor_y_timeline(self):
        revision = self._crear_revision()

        asignar_revision(revision, actor=self.analista)

        revision.refresh_from_db()
        self.assertEqual(revision.asignado_a, self.analista)
        self.assertEqual(revision.estado, RevisionManualPrestador.Estado.ASIGNADA)
        evento = self.solicitud.timeline_operativo.get(
            tipo_evento=TimelinePrestador.TipoEvento.REVISION_ASIGNADA
        )
        self.assertEqual(evento.creado_por, self.analista)
        self.assertEqual(evento.metadata['revision_id'], revision.id)
        self.assertNotIn(self.solicitud.numero_documento, str(evento.metadata))

    def test_validacion_empresa_no_crea_aprobacion_pagador(self):
        revision = self._crear_revision()
        antes = AprobacionPagadorLibranza.objects.count()

        solicitar_validacion_empresa(revision, actor=self.analista)

        revision.refresh_from_db()
        self.assertEqual(
            revision.estado,
            RevisionManualPrestador.Estado.PENDIENTE_VALIDACION_EMPRESA,
        )
        self.assertEqual(AprobacionPagadorLibranza.objects.count(), antes)

    def test_solicitar_y_atender_nuevo_contrato_conserva_auditoria(self):
        auditoria = self._crear_auditoria(
            resultado=PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL
        )
        revision = self._crear_revision(auditoria=auditoria)
        requerimiento = solicitar_subsanacion(
            revision,
            tipo=RequerimientoSubsanacionPrestador.Tipo.NUEVO_CONTRATO,
            actor=self.analista,
        )
        ContractorApplicationDocument.objects.create(
            solicitud=self.solicitud,
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO,
            archivo=SimpleUploadedFile('anterior.pdf', b'%PDF-1.4 anterior'),
            uploaded_by=self.solicitante,
        )
        self.client.force_login(self.solicitante)

        response = self.client.post(
            f'/mi-credito/solicitud/{self.solicitud.id}/subsanacion/{requerimiento.id}/',
            {'archivo': SimpleUploadedFile('nuevo.pdf', b'%PDF-1.4 nuevo')},
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 302)
        self.solicitud.refresh_from_db()
        requerimiento.refresh_from_db()
        self.assertEqual(requerimiento.estado, RequerimientoSubsanacionPrestador.Estado.ATENDIDO)
        self.assertEqual(self.solicitud.estado, ContractorApplication.Estado.EVALUACION_PENDIENTE)
        self.assertEqual(self.solicitud.auditorias_predecision.count(), 1)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)

    def test_solicitante_solo_ve_y_atiende_requerimientos_propios(self):
        revision = self._crear_revision()
        requerimiento = solicitar_subsanacion(
            revision,
            tipo=RequerimientoSubsanacionPrestador.Tipo.INFORMACION_PERSONAL,
            actor=self.analista,
        )
        self.client.force_login(self.otro)

        response = self.client.get(
            f'/mi-credito/solicitud/{self.solicitud.id}/subsanacion/{requerimiento.id}/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 404)

    def test_mi_credito_preaprobado_no_expone_score_ni_proveedor(self):
        self.solicitud.estado = ContractorApplication.Estado.EVALUACION_COMPLETADA
        self.solicitud.save(update_fields=['estado', 'updated_at'])
        self._crear_auditoria(
            resultado=PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY,
            score='910.00',
            razones=['DataCredito score alto'],
        )
        self.client.force_login(self.solicitante)

        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)

        self.assertContains(response, 'Tu evaluación inicial fue favorable.')
        self.assertNotContains(response, '910')
        self.assertNotContains(response, 'DataCredito')
        self.assertNotContains(response, 'Credito aprobado')

    def test_mi_credito_bloqueado_no_expone_motivo_interno(self):
        self.solicitud.estado = ContractorApplication.Estado.EVALUACION_COMPLETADA
        self.solicitud.save(update_fields=['estado', 'updated_at'])
        self._crear_auditoria(
            resultado=PredecisionPrestadorAudit.Resultado.BLOQUEADO_READ_ONLY,
            bloqueos=['datacredito:mora_supera_umbral_bloqueante'],
        )
        self.client.force_login(self.solicitante)

        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)

        self.assertContains(response, 'No podemos continuar automáticamente con esta solicitud.')
        self.assertNotContains(response, 'mora')
        self.assertNotContains(response, 'datacredito')

    def test_detalle_reserva_score_y_senales_para_permiso_especifico(self):
        auditoria = self._crear_auditoria(
            resultado=PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL,
            score='910.00',
            razones=['DataCredito score alto'],
            bloqueos=['datacredito:mora_supera_umbral_bloqueante'],
        )
        self._crear_revision(auditoria=auditoria)

        self.client.force_login(self.staff_solo_lectura)
        response = self.client.get(
            f'/gestion/prestadores/{self.solicitud.id}/',
            HTTP_HOST=self.host,
        )
        self.assertContains(response, 'La solicitud requiere validación interna')
        self.assertNotContains(response, '910')
        self.assertNotContains(response, 'DataCredito')
        self.assertNotContains(response, 'mora_supera')

        self.client.force_login(self.analista)
        response = self.client.get(
            f'/gestion/prestadores/{self.solicitud.id}/',
            HTTP_HOST=self.host,
        )
        self.assertContains(response, '910')
        self.assertContains(response, 'DataCredito score alto')

    def test_error_controlado_publico_no_expone_detalle_tecnico(self):
        self.solicitud.estado = ContractorApplication.Estado.EN_REVISION_MANUAL
        self.solicitud.save(update_fields=['estado', 'updated_at'])
        self._crear_auditoria(
            resultado=PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO,
            razones=['SecretProviderTimeout stack trace'],
        )
        self.client.force_login(self.solicitante)

        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)

        self.assertContains(response, 'No fue posible completar la evaluación en este momento.')
        self.assertNotContains(response, 'SecretProviderTimeout')
        self.assertNotContains(response, 'stack trace')

    def test_requerimiento_publico_no_expone_detalle_interno(self):
        revision = self._crear_revision()
        requerimiento = solicitar_subsanacion(
            revision,
            tipo=RequerimientoSubsanacionPrestador.Tipo.INFORMACION_PERSONAL,
            actor=self.analista,
            detalle_interno='Motivo antifraude interno reservado.',
        )
        self.client.force_login(self.solicitante)

        response = self.client.get(
            f'/mi-credito/solicitud/{self.solicitud.id}/subsanacion/{requerimiento.id}/',
            HTTP_HOST=self.host,
        )

        self.assertContains(response, requerimiento.mensaje_publico)
        self.assertNotContains(response, 'antifraude')

    def test_subsanacion_no_permite_empresa_documento_monto_ni_plazo(self):
        revision = self._crear_revision()
        requerimiento_personal = solicitar_subsanacion(
            revision,
            tipo=RequerimientoSubsanacionPrestador.Tipo.INFORMACION_PERSONAL,
            actor=self.analista,
        )
        form_personal = AtenderSubsanacionPrestadorForm(
            requerimiento=requerimiento_personal,
        )
        self.assertTrue({'nombres', 'apellidos', 'celular', 'correo', 'direccion'}.issubset(
            form_personal.fields
        ))
        self.assertTrue(
            {'empresa', 'numero_documento', 'monto_solicitado', 'plazo_meses'}.isdisjoint(
                form_personal.fields
            )
        )

        requerimiento_personal.estado = RequerimientoSubsanacionPrestador.Estado.CANCELADO
        requerimiento_personal.save(update_fields=['estado'])
        requerimiento_contractual = RequerimientoSubsanacionPrestador.objects.create(
            solicitud=self.solicitud,
            revision=revision,
            tipo=RequerimientoSubsanacionPrestador.Tipo.INFORMACION_CONTRACTUAL,
            mensaje_publico='Actualiza la información contractual.',
            creado_por=self.analista,
        )
        form_contractual = AtenderSubsanacionPrestadorForm(
            requerimiento=requerimiento_contractual,
        )
        self.assertTrue(
            {'empresa', 'numero_documento', 'monto_solicitado', 'plazo_meses'}.isdisjoint(
                form_contractual.fields
            )
        )

    @patch('contractors.services.evaluacion_formal.evaluar_solicitud_prestador')
    def test_reintento_usa_servicio_formal_sin_forzar_datacredito(self, evaluar):
        revision = self._crear_revision()
        auditoria = self._crear_auditoria(
            resultado=PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY,
        )
        evaluar.return_value = SimpleNamespace(auditoria=auditoria)

        reintentar_evaluacion(revision, actor=self.analista)

        evaluar.assert_called_once_with(revision.solicitud, solicitado_por=self.analista)
        revision.refresh_from_db()
        self.assertEqual(revision.estado, RevisionManualPrestador.Estado.RESUELTA)

    def _crear_solicitud(self):
        return ContractorApplication.objects.create(
            usuario=self.solicitante,
            empresa=self.empresa,
            tipo_documento='CC',
            numero_documento='1234567890',
            nombres='Persona',
            apellidos='Prueba',
            celular='3000000000',
            correo='persona@example.com',
            direccion='Direccion de prueba',
            cargo='Servicios profesionales',
            estado=ContractorApplication.Estado.EN_REVISION_MANUAL,
        )

    def _crear_auditoria(self, *, resultado, score=None, razones=None, alertas=None, bloqueos=None):
        numero = self.solicitud.auditorias_predecision.count() + 1
        return PredecisionPrestadorAudit.objects.create(
            solicitud=self.solicitud,
            version_datos=f'v{numero}',
            clave_idempotencia=f'clave-{self.solicitud.id}-{numero}',
            estado_ejecucion=PredecisionPrestadorAudit.EstadoEjecucion.COMPLETADA,
            resultado=resultado,
            score=score,
            version_politica='politica-test',
            razones=razones or [],
            alertas=alertas or [],
            bloqueos=bloqueos or [],
            iniciada_en=timezone.now(),
            finalizada_en=timezone.now(),
        )

    def _crear_revision(self, auditoria=None):
        return RevisionManualPrestador.objects.create(
            solicitud=self.solicitud,
            auditoria_predecision=auditoria,
            motivo=RevisionManualPrestador.Motivo.OTRA_REVISION_CONTROLADA,
        )

    def _otorgar(self, usuario, *codenames):
        usuario.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
