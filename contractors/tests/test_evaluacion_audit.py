from datetime import timedelta
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from contractors.admin import PredecisionPrestadorAuditAdmin, TimelinePrestadorAdmin
from contractors.models import (
    ContractorApplication,
    PredecisionPrestadorAudit,
    TimelinePrestador,
)
from contractors.services.evaluacion_audit import (
    iniciar_evaluacion_prestador,
    invalidar_evaluacion_si_cambiaron_datos,
    marcar_evaluacion_pendiente,
)
from contractors.services.evaluacion_versionado import (
    construir_clave_idempotencia,
    construir_version_datos,
)
from gestion_creditos.models import Credito, CreditoLibranza, Empresa


class EvaluacionAuditPrestadorTest(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username='prestador-audit',
            email='prestador-audit@example.com',
            password='test-password',
        )
        self.staff = get_user_model().objects.create_superuser(
            username='staff-audit',
            email='staff-audit@example.com',
            password='test-password',
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Auditada',
            convenio_activo=True,
        )

    def test_version_y_clave_son_deterministicas_y_no_exponen_documento(self):
        solicitud = self._crear_solicitud()

        version_uno, snapshot_uno = construir_version_datos(solicitud)
        version_dos, snapshot_dos = construir_version_datos(solicitud)
        clave_uno = construir_clave_idempotencia(
            solicitud=solicitud,
            version_datos=version_uno,
        )
        clave_dos = construir_clave_idempotencia(
            solicitud=solicitud,
            version_datos=version_dos,
        )

        self.assertEqual(version_uno, version_dos)
        self.assertEqual(snapshot_uno, snapshot_dos)
        self.assertEqual(clave_uno, clave_dos)
        self.assertNotIn(solicitud.numero_documento, str(snapshot_uno))
        self.assertEqual(snapshot_uno['documento_enmascarado'], '*****6789')

    def test_cambiar_monto_o_hash_contrato_cambia_version(self):
        solicitud = self._crear_solicitud()
        version_inicial, _ = construir_version_datos(solicitud)

        solicitud.monto_solicitado = Decimal('4200000.00')
        solicitud.save(update_fields=['monto_solicitado', 'updated_at'])
        version_monto, _ = construir_version_datos(solicitud)
        solicitud.metadata_analisis_contractual = {'archivo_hash_sha256': 'hash-contrato-v2'}
        solicitud.save(update_fields=['metadata_analisis_contractual', 'updated_at'])
        version_contrato, _ = construir_version_datos(solicitud)

        self.assertNotEqual(version_inicial, version_monto)
        self.assertNotEqual(version_monto, version_contrato)

    def test_intentos_equivalentes_reutilizan_auditoria_y_no_crean_creditos(self):
        solicitud = self._crear_solicitud()
        marcar_evaluacion_pendiente(solicitud, usuario=self.usuario)
        creditos_antes = Credito.objects.count()
        libranzas_antes = CreditoLibranza.objects.count()

        primero = iniciar_evaluacion_prestador(solicitud, usuario=self.usuario)
        segundo = iniciar_evaluacion_prestador(solicitud, usuario=self.usuario)

        self.assertFalse(primero.reutilizada)
        self.assertTrue(segundo.reutilizada)
        self.assertEqual(primero.auditoria.pk, segundo.auditoria.pk)
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 1)
        self.assertEqual(
            primero.auditoria.resultado,
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
        )
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, ContractorApplication.Estado.EN_REVISION_MANUAL)
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), libranzas_antes)

    def test_auditoria_completada_es_inmutable(self):
        solicitud = self._crear_solicitud()
        marcar_evaluacion_pendiente(solicitud, usuario=self.usuario)
        auditoria = iniciar_evaluacion_prestador(solicitud, usuario=self.usuario).auditoria

        auditoria.razones = ['Intento de modificación posterior.']

        with self.assertRaisesMessage(ValidationError, 'inmutable'):
            auditoria.save()

    def test_cambiar_datos_invalida_sin_eliminar_historial(self):
        solicitud = self._crear_solicitud()
        marcar_evaluacion_pendiente(solicitud, usuario=self.usuario)
        auditoria = iniciar_evaluacion_prestador(solicitud, usuario=self.usuario).auditoria
        version_anterior, _ = construir_version_datos(solicitud)

        solicitud.plazo_meses = 18
        solicitud.save(update_fields=['plazo_meses', 'updated_at'])
        cambio = invalidar_evaluacion_si_cambiaron_datos(
            solicitud,
            version_anterior=version_anterior,
            usuario=self.usuario,
            campos=['plazo_meses'],
        )

        solicitud.refresh_from_db()
        self.assertTrue(cambio)
        self.assertEqual(solicitud.estado, ContractorApplication.Estado.EVALUACION_PENDIENTE)
        self.assertTrue(PredecisionPrestadorAudit.objects.filter(pk=auditoria.pk).exists())
        self.assertTrue(
            TimelinePrestador.objects.filter(
                solicitud=solicitud,
                tipo_evento=TimelinePrestador.TipoEvento.DATOS_MODIFICADOS,
            ).exists()
        )

    def test_timeline_registra_ciclo_base_sin_metadata_sensible(self):
        solicitud = self._crear_solicitud()
        marcar_evaluacion_pendiente(solicitud, usuario=self.usuario)

        iniciar_evaluacion_prestador(solicitud, usuario=self.usuario)

        tipos = set(solicitud.timeline_operativo.values_list('tipo_evento', flat=True))
        self.assertTrue({
            TimelinePrestador.TipoEvento.EVALUACION_PENDIENTE,
            TimelinePrestador.TipoEvento.EVALUACION_INICIADA,
            TimelinePrestador.TipoEvento.EVALUACION_COMPLETADA,
            TimelinePrestador.TipoEvento.REVISION_MANUAL_REQUERIDA,
        }.issubset(tipos))
        self.assertNotIn(solicitud.numero_documento, str(list(
            solicitud.timeline_operativo.values_list('metadata', flat=True)
        )))

    def test_admin_auditoria_y_timeline_son_solo_lectura(self):
        request = RequestFactory().get('/admin/contractors/')
        request.user = self.staff
        request_usuario = RequestFactory().get('/admin/contractors/')
        request_usuario.user = self.usuario
        auditoria_admin = admin.site._registry[PredecisionPrestadorAudit]
        timeline_admin = admin.site._registry[TimelinePrestador]

        self.assertIsInstance(auditoria_admin, PredecisionPrestadorAuditAdmin)
        self.assertIsInstance(timeline_admin, TimelinePrestadorAdmin)
        self.assertTrue(auditoria_admin.has_view_permission(request))
        self.assertTrue(timeline_admin.has_view_permission(request))
        self.assertFalse(auditoria_admin.has_add_permission(request))
        self.assertFalse(auditoria_admin.has_change_permission(request))
        self.assertFalse(auditoria_admin.has_delete_permission(request))
        self.assertFalse(timeline_admin.has_add_permission(request))
        self.assertFalse(timeline_admin.has_change_permission(request))
        self.assertFalse(timeline_admin.has_delete_permission(request))
        self.assertFalse(auditoria_admin.has_view_permission(request_usuario))
        self.assertFalse(auditoria_admin.has_change_permission(request_usuario))
        self.assertFalse(timeline_admin.has_view_permission(request_usuario))
        self.assertFalse(timeline_admin.has_change_permission(request_usuario))

    def test_servicios_base_no_importan_integraciones_externas(self):
        from pathlib import Path

        directorio_servicios = Path(__file__).resolve().parents[1] / 'services'
        contenido = (directorio_servicios / 'evaluacion_audit.py').read_text(
            encoding='utf-8'
        ).lower()

        for dependencia in ('integrations', 'datacredito', 'risk.', 'zapsign', 'whatsapp'):
            self.assertNotIn(dependencia, contenido)

    def test_mi_credito_muestra_estados_operativos_sin_score(self):
        solicitud = self._crear_solicitud()
        solicitud.estado = ContractorApplication.Estado.EVALUACION_PENDIENTE
        solicitud.save(update_fields=['estado', 'updated_at'])
        self.client.force_login(self.usuario)

        response = self.client.get('/mi-credito/', HTTP_HOST='contratistas.localhost')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Evaluación pendiente')
        self.assertNotContains(response, 'DataCrédito')
        self.assertNotContains(response, 'Score')

    def _crear_solicitud(self):
        return ContractorApplication.objects.create(
            usuario=self.usuario,
            empresa=self.empresa,
            escenario_credito=ContractorApplication.EscenarioCredito.NUEVO_CREDITO,
            tipo_documento=ContractorApplication.TipoDocumento.CEDULA_CIUDADANIA,
            numero_documento='123456789',
            nombres='Ana María',
            apellidos='Pérez Gómez',
            celular='3001234567',
            correo='ana@example.com',
            direccion='Calle 1 # 2-3',
            cargo='Consultora',
            tipo_contrato=ContractorApplication.TipoContrato.PRESTACION_SERVICIOS,
            fecha_inicio_contrato=timezone.localdate(),
            fecha_fin_contrato=timezone.localdate() + timedelta(days=180),
            valor_total_contrato=Decimal('12000000.00'),
            valor_pagado_contrato=Decimal('2000000.00'),
            valor_pendiente_cobrar=Decimal('10000000.00'),
            monto_solicitado=Decimal('3500000.00'),
            plazo_meses=12,
            acepta_terminos=True,
            acepta_politica_privacidad=True,
            autoriza_analisis_contractual_asistido=True,
            autoriza_consulta_centrales=True,
            estado_analisis_contractual=(
                ContractorApplication.EstadoAnalisisContractual.COMPLETADO
            ),
            metadata_analisis_contractual={'archivo_hash_sha256': 'hash-contrato-v1'},
            estado=ContractorApplication.Estado.DOCUMENTOS_CARGADOS,
        )
