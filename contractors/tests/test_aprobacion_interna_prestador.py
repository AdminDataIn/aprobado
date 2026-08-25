from datetime import timedelta
from decimal import Decimal
import json
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection
from django.test import TestCase, override_settings
from django.utils import timezone

from contractors.models import (
    AprobacionInternaPrestador,
    AprobacionPagadorPrestador,
    ContractorApplication,
    ContractorApplicationDocument,
    FormalizacionCreditoPrestador,
    NovedadOperativaPrestador,
    PredecisionPrestadorAudit,
    RequerimientoSubsanacionPrestador,
    RevisionManualPrestador,
    TimelinePrestador,
)
from contractors.services.aprobacion_pagador import (
    decidir_aprobacion_pagador_prestador,
)
from contractors.services.aprobacion_interna import (
    aprobar_para_originar,
    cerrar_sin_originar,
    crear_o_reutilizar_aprobacion_interna,
    devolver_a_revision,
)
from contractors.services.autorizacion_datacredito import (
    registrar_autorizacion_datacredito_desde_solicitud,
)
from contractors.services.evaluacion_versionado import construir_version_datos
from contractors.services.expediente_originacion import (
    construir_expediente_originacion_prestador,
)
from contractors.services.originacion import originar_credito_prestador_desde_gate
from contractors.services.formalizacion import (
    enviar_formalizacion_prestador_a_firma,
    preparar_formalizacion_credito_prestador,
    procesar_callback_formalizacion_prestador,
    registrar_resultado_validacion_identidad_prestador,
)
from contractors.services.novedad_operativa import (
    confirmar_recepcion_novedad_operativa_prestador,
    construir_dto_novedad_operativa_prestador,
    crear_o_reutilizar_novedad_operativa_prestador,
    enviar_novedad_operativa_prestador,
    marcar_novedad_operativa_prestador_gestionada,
    obtener_destinatarios_novedad_operativa_prestador,
)
from contractors.tests.test_score_prestadores_v2 import crear_politica_score
from gestion_creditos.models import (
    AprobacionPagadorLibranza,
    Credito,
    CreditoLibranza,
    CuotaAmortizacion,
    Empresa,
    HistorialPago,
    OrigenCreditoPrestador,
    Pagare,
    ZapSignWebhookLog,
)
from gestion_creditos.services.originacion_libranza import (
    construir_clave_idempotencia_prestador,
    originar_libranza_desde_expediente,
)
from integrations.models import ConsultaDatacreditoSnapshot
from usuarios.models import PerfilPagador


class ClienteFirmaPrueba:
    def __init__(self, *, token='documento-remoto-1', error=None):
        from django.db import connection

        self.token = token
        self.error = error
        self.llamadas = []
        self.profundidad_transaccional_inicial = len(connection.atomic_blocks)

    def crear_documento(self, **kwargs):
        from django.db import connection

        self.llamadas.append({
            'profundidad_transaccional': len(connection.atomic_blocks),
            'external_id': kwargs.get('external_id'),
            'requiere_validacion_identidad': kwargs.get(
                'require_identity_validation'
            ),
            'requiere_validacion_documento': kwargs.get(
                'require_document_validation'
            ),
        })
        if self.error:
            raise self.error
        return {
            'token': self.token,
            'signers': [{'sign_url': 'https://firma.example.test/secreto'}],
        }


class ClienteNovedadPrueba:
    def __init__(self, *, error=None):
        self.error = error
        self.llamadas = []
        self.profundidad_transaccional_inicial = len(connection.atomic_blocks)

    def __call__(self, *, dto, destinatarios):
        self.llamadas.append({
            'dto': dto,
            'destinatarios': list(destinatarios),
            'profundidad_transaccional': len(connection.atomic_blocks),
        })
        if self.error:
            raise self.error
        return True


@override_settings(
    DATACREDITO_AUTHORIZATION_TEXT_VERSION='uat-gate-v1',
    DATACREDITO_AUTHORIZATION_TEXT='Autorizacion controlada para pruebas del gate.',
)
class AprobacionInternaPrestadorTest(TestCase):
    host = 'contratistas.localhost'

    def setUp(self):
        self.directorio_media = tempfile.TemporaryDirectory()
        self.addCleanup(self.directorio_media.cleanup)
        self.override_media = override_settings(MEDIA_ROOT=self.directorio_media.name)
        self.override_media.enable()
        self.addCleanup(self.override_media.disable)
        User = get_user_model()
        self.solicitante = User.objects.create_user('prestador-gate', password='test')
        self.analista = User.objects.create_user(
            'analista-gate', password='test', is_staff=True
        )
        self.lector = User.objects.create_user('lector-gate', password='test', is_staff=True)
        self.empresa = Empresa.objects.create(nombre='Empresa Gate', convenio_activo=True)
        self.configuracion = self._crear_configuracion()
        self.politica = crear_politica_score(
            version='gate-v1',
            configuracion_financiera=self.configuracion,
        )
        self.solicitud = self._crear_solicitud()
        self.autorizacion = registrar_autorizacion_datacredito_desde_solicitud(
            self.solicitud,
            usuario=self.solicitante,
        )
        self.snapshot = self._crear_snapshot()
        self.auditoria = self._crear_auditoria()
        self._otorgar(
            self.analista,
            'can_view_contractor_review_queue',
            'can_view_contractor_internal_approval',
            'can_decide_contractor_internal_approval',
            'can_close_contractor_internal_approval',
            'can_originate_contractor_credit',
            'can_prepare_contractor_formalization',
            'can_retry_contractor_signature',
            'can_view_contractor_formalization',
            'can_create_contractor_operational_notice',
            'can_retry_contractor_operational_notice',
            'can_view_contractor_operational_notice',
        )
        self._otorgar(
            self.lector,
            'can_view_contractor_review_queue',
            'can_view_contractor_internal_approval',
            'can_view_contractor_formalization',
        )
        self.pagador = self._crear_pagador(
            'pagador-aprobacion-prestador',
            self.empresa,
            email='',
            permisos=('can_decide_contractor_payer_approval',),
        )

    def test_solo_preaprobado_crea_gate_y_es_idempotente(self):
        gate, creado = crear_o_reutilizar_aprobacion_interna(
            self.auditoria, actor=self.analista
        )
        reutilizado, creado_otra_vez = crear_o_reutilizar_aprobacion_interna(
            self.auditoria, actor=self.analista
        )
        self.assertTrue(creado)
        self.assertFalse(creado_otra_vez)
        self.assertEqual(reutilizado.pk, gate.pk)
        self.assertEqual(AprobacionInternaPrestador.objects.count(), 1)

        self.auditoria.resultado = PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL
        PredecisionPrestadorAudit.objects.filter(pk=self.auditoria.pk).update(
            resultado=self.auditoria.resultado
        )
        AprobacionInternaPrestador.objects.all().delete()
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(self.auditoria, actor=self.analista)

    def test_resultados_no_favorables_no_crean_gate(self):
        for resultado in (
            PredecisionPrestadorAudit.Resultado.REQUIERE_REVISION_MANUAL,
            PredecisionPrestadorAudit.Resultado.BLOQUEADO_READ_ONLY,
            PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            PredecisionPrestadorAudit.Resultado.ERROR_CONTROLADO,
        ):
            with self.subTest(resultado=resultado):
                PredecisionPrestadorAudit.objects.filter(pk=self.auditoria.pk).update(
                    resultado=resultado
                )
                self.auditoria.refresh_from_db()
                with self.assertRaises(ValidationError):
                    crear_o_reutilizar_aprobacion_interna(
                        self.auditoria, actor=self.analista
                    )
        self.assertFalse(AprobacionInternaPrestador.objects.exists())

    def test_revision_activa_y_subsanacion_pendiente_impiden_gate(self):
        revision = RevisionManualPrestador.objects.create(
            solicitud=self.solicitud,
            auditoria_predecision=self.auditoria,
            motivo=RevisionManualPrestador.Motivo.OTRA_REVISION_CONTROLADA,
        )
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(self.auditoria, actor=self.analista)

        revision.estado = RevisionManualPrestador.Estado.RESUELTA
        revision.resultado = RevisionManualPrestador.Resultado.CONTINUAR_EVALUACION
        revision.save(update_fields=['estado', 'resultado', 'updated_at'])
        RequerimientoSubsanacionPrestador.objects.create(
            solicitud=self.solicitud,
            revision=revision,
            tipo=RequerimientoSubsanacionPrestador.Tipo.INFORMACION_PERSONAL,
            mensaje_publico='Actualiza informacion.',
        )
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_aprobacion_interna(self.auditoria, actor=self.analista)

    def test_gate_guarda_topes_versiones_y_tasa_evaluados(self):
        gate = self._crear_gate()
        self.assertEqual(gate.version_datos, self.auditoria.version_datos)
        self.assertEqual(gate.version_politica, self.politica.version_politica)
        self.assertEqual(gate.version_configuracion_financiera, self.configuracion.version)
        self.assertEqual(gate.tasa_mensual_snapshot, Decimal('2.2000'))
        self.assertEqual(gate.monto_maximo_score_snapshot, Decimal('10000000'))
        self.assertEqual(gate.monto_maximo_politica_snapshot, Decimal('10000000'))
        self.assertEqual(gate.monto_maximo_capacidad_snapshot, Decimal('6000000'))
        self.assertEqual(gate.monto_maximo_contrato_snapshot, Decimal('48000000'))
        self.assertEqual(gate.monto_maximo_evaluado, Decimal('3000000'))
        self.assertEqual(gate.plazo_maximo_evaluado, 6)

    def test_aprobar_permite_reducir_pero_no_aumentar_monto_o_plazo(self):
        gate = self._crear_gate()
        with self.assertRaises(ValidationError):
            aprobar_para_originar(
                gate,
                actor=self.analista,
                monto_autorizado=Decimal('3000000.01'),
            )
        gate.refresh_from_db()
        self.assertEqual(gate.estado, AprobacionInternaPrestador.Estado.PENDIENTE)
        with self.assertRaises(ValidationError):
            aprobar_para_originar(
                gate,
                actor=self.analista,
                plazo_autorizado=gate.plazo_maximo_evaluado + 1,
            )

        aprobar_para_originar(
            gate,
            actor=self.analista,
            monto_autorizado=Decimal('2500000'),
            plazo_autorizado=5,
            comentario_interno='Ajuste conservador de monto y plazo.',
        )
        gate.refresh_from_db()
        self.assertEqual(
            gate.estado,
            AprobacionInternaPrestador.Estado.APROBADA_PARA_ORIGINAR,
        )
        self.assertEqual(gate.monto_autorizado, Decimal('2500000'))
        self.assertEqual(gate.plazo_autorizado, 5)
        evento = self.solicitud.timeline_operativo.get(
            tipo_evento=TimelinePrestador.TipoEvento.APROBADA_PARA_ORIGINAR
        )
        self.assertEqual(evento.metadata['monto_autorizado'], '2500000.00')
        self.assertEqual(evento.metadata['plazo_autorizado'], 5)

    def test_cambio_de_datos_contractuales_devuelve_a_revision(self):
        gate = self._crear_gate()
        self.solicitud.cargo = 'Servicio modificado'
        self.solicitud.save(update_fields=['cargo', 'updated_at'])
        resultado = aprobar_para_originar(gate, actor=self.analista)
        resultado.refresh_from_db()
        self.assertEqual(
            resultado.estado,
            AprobacionInternaPrestador.Estado.DEVUELTA_A_REVISION,
        )
        self.assertIsNotNone(resultado.revision_manual_id)

    def test_contrato_vencido_despues_de_evaluar_devuelve_a_revision(self):
        gate = self._crear_gate()
        self.solicitud.fecha_fin_contrato = timezone.localdate() - timedelta(days=1)
        self.solicitud.save(update_fields=['fecha_fin_contrato', 'updated_at'])
        resultado = aprobar_para_originar(gate, actor=self.analista)
        self.assertEqual(
            resultado.estado,
            AprobacionInternaPrestador.Estado.DEVUELTA_A_REVISION,
        )

    def test_empresa_sin_convenio_impide_aprobacion(self):
        gate = self._crear_gate()
        self.empresa.convenio_activo = False
        self.empresa.save(update_fields=['convenio_activo'])
        resultado = aprobar_para_originar(gate, actor=self.analista)
        self.assertEqual(
            resultado.estado,
            AprobacionInternaPrestador.Estado.DEVUELTA_A_REVISION,
        )

    def test_politica_cambiada_impide_aprobacion(self):
        gate = self._crear_gate()
        type(self.politica).objects.filter(pk=self.politica.pk).update(activa=False)
        crear_politica_score(
            version='gate-v2',
            configuracion_financiera=self.configuracion,
        )
        resultado = aprobar_para_originar(gate, actor=self.analista)
        self.assertEqual(
            resultado.estado,
            AprobacionInternaPrestador.Estado.DEVUELTA_A_REVISION,
        )

    def test_snapshot_datacredito_vencido_devuelve_a_revision(self):
        gate = self._crear_gate()
        ConsultaDatacreditoSnapshot.objects.filter(pk=self.snapshot.pk).update(
            vigente_hasta=timezone.now() - timedelta(seconds=1)
        )
        resultado = aprobar_para_originar(gate, actor=self.analista)
        self.assertEqual(
            resultado.estado,
            AprobacionInternaPrestador.Estado.DEVUELTA_A_REVISION,
        )

    def test_perfil_pagador_y_staff_sin_permiso_no_deciden(self):
        gate = self._crear_gate()
        pagador = get_user_model().objects.create_user(
            'pagador-gate', password='test', is_staff=True
        )
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        self._otorgar(
            pagador,
            'can_view_contractor_internal_approval',
            'can_decide_contractor_internal_approval',
            'can_close_contractor_internal_approval',
        )
        with self.assertRaises(PermissionDenied):
            aprobar_para_originar(gate, actor=pagador)
        with self.assertRaises(PermissionDenied):
            aprobar_para_originar(gate, actor=self.lector)
        gate.refresh_from_db()
        self.assertEqual(gate.estado, AprobacionInternaPrestador.Estado.PENDIENTE)

    def test_ver_no_implica_decidir_en_vista(self):
        gate = self._crear_gate()
        self.client.force_login(self.lector)
        detalle = self.client.get(
            f'/gestion/prestadores/{self.solicitud.id}/', HTTP_HOST=self.host
        )
        self.assertEqual(detalle.status_code, 200)
        respuesta = self.client.post(
            f'/gestion/prestadores/aprobaciones/{gate.id}/accion/',
            {'accion': 'APROBAR'},
            HTTP_HOST=self.host,
        )
        self.assertEqual(respuesta.status_code, 302)
        gate.refresh_from_db()
        self.assertEqual(gate.estado, AprobacionInternaPrestador.Estado.PENDIENTE)

    @patch('gestion_creditos.credit_services.activar_credito')
    @patch('gestion_creditos.credit_services.iniciar_proceso_desembolso')
    @patch('gestion_creditos.credit_services.preparar_documento_para_firma')
    def test_aprobar_no_crea_efectos_financieros_ni_pagador(
        self,
        preparar_firma,
        iniciar_desembolso,
        activar_credito,
    ):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)
        self.assertEqual(AprobacionPagadorLibranza.objects.count(), 0)
        self.assertEqual(HistorialPago.objects.count(), 0)
        self.assertEqual(Pagare.objects.count(), 0)
        preparar_firma.assert_not_called()
        iniciar_desembolso.assert_not_called()
        activar_credito.assert_not_called()

    def test_aprobacion_interna_crea_confirmacion_pagador_sin_originar(self):
        gate = self._crear_gate()

        aprobar_para_originar(gate, actor=self.analista)

        aprobacion = AprobacionPagadorPrestador.objects.get(
            aprobacion_interna=gate
        )
        self.solicitud.refresh_from_db()
        self.assertEqual(
            aprobacion.estado,
            AprobacionPagadorPrestador.Estado.PENDIENTE,
        )
        self.assertEqual(
            self.solicitud.estado,
            ContractorApplication.Estado.PENDIENTE_APROBACION_PAGADOR,
        )
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)
        self.assertEqual(Pagare.objects.count(), 0)

    def test_pagador_ajeno_no_puede_decidir_y_el_propio_es_idempotente(self):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        aprobacion = AprobacionPagadorPrestador.objects.get(
            aprobacion_interna=gate
        )
        empresa_ajena = Empresa.objects.create(
            nombre='Empresa ajena gate',
            convenio_activo=True,
        )
        pagador_ajeno = self._crear_pagador(
            'pagador-ajeno-aprobacion',
            empresa_ajena,
            email='',
            permisos=('can_decide_contractor_payer_approval',),
        )
        confirmaciones = {
            'confirma_vinculo': True,
            'confirma_contrato_vigente': True,
            'confirma_forma_pago_mensual': True,
            'confirma_valores_contractuales': True,
            'confirma_capacidad_operativa': True,
            'acepta_gestionar_pago': True,
        }

        with self.assertRaises(PermissionDenied):
            decidir_aprobacion_pagador_prestador(
                aprobacion,
                actor=pagador_ajeno,
                decision=AprobacionPagadorPrestador.Estado.APROBADO,
                confirmaciones=confirmaciones,
            )

        primera = decidir_aprobacion_pagador_prestador(
            aprobacion,
            actor=self.pagador,
            decision=AprobacionPagadorPrestador.Estado.APROBADO,
            confirmaciones=confirmaciones,
        )
        segunda = decidir_aprobacion_pagador_prestador(
            aprobacion,
            actor=self.pagador,
            decision=AprobacionPagadorPrestador.Estado.APROBADO,
            confirmaciones=confirmaciones,
        )
        self.assertFalse(primera.reutilizada)
        self.assertTrue(segunda.reutilizada)
        self.assertEqual(AprobacionPagadorPrestador.objects.count(), 1)

    def test_rechazo_pagador_bloquea_originacion(self):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        aprobacion = AprobacionPagadorPrestador.objects.get(
            aprobacion_interna=gate
        )

        decidir_aprobacion_pagador_prestador(
            aprobacion,
            actor=self.pagador,
            decision=AprobacionPagadorPrestador.Estado.RECHAZADO,
            motivo=AprobacionPagadorPrestador.Motivo.CONTRATO_NO_VIGENTE,
            observacion='El contrato no continúa vigente.',
        )

        with self.assertRaises(ValidationError):
            originar_credito_prestador_desde_gate(gate, actor=self.analista)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(Pagare.objects.count(), 0)

    def test_cambio_critico_invalida_aprobacion_pagador(self):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        aprobacion = AprobacionPagadorPrestador.objects.get(
            aprobacion_interna=gate
        )
        self.solicitud.direccion = 'Dirección modificada después del gate'
        self.solicitud.save(update_fields=['direccion', 'updated_at'])

        with self.assertRaises(ValidationError):
            self._aprobar_pagador(gate)

        aprobacion.refresh_from_db()
        self.solicitud.refresh_from_db()
        self.assertEqual(
            aprobacion.estado,
            AprobacionPagadorPrestador.Estado.INVALIDADA,
        )
        self.assertEqual(
            self.solicitud.estado,
            ContractorApplication.Estado.EVALUACION_PENDIENTE,
        )

    def test_portal_pagador_no_expone_score_ni_fuentes_crudas(self):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        aprobacion = AprobacionPagadorPrestador.objects.get(
            aprobacion_interna=gate
        )
        self.client.force_login(self.pagador)

        listado = self.client.get('/pagador/prestadores/aprobaciones/')
        detalle = self.client.get(
            f'/pagador/prestadores/aprobaciones/{aprobacion.id}/'
        )

        self.assertEqual(listado.status_code, 200)
        self.assertEqual(detalle.status_code, 200)
        for contenido in (listado.content, detalle.content):
            self.assertNotIn(b'score', contenido.lower())
            self.assertNotIn(b'hdc', contenido.lower())
            self.assertNotIn(b'midecisor', contenido.lower())

    def test_expediente_solo_aprobado_es_determinista_y_no_persiste(self):
        gate = self._crear_gate()
        with self.assertRaises(ValidationError):
            construir_expediente_originacion_prestador(gate)
        aprobar_para_originar(gate, actor=self.analista)
        with self.assertRaises(ValidationError):
            construir_expediente_originacion_prestador(gate)
        self._aprobar_pagador(gate)
        primero = construir_expediente_originacion_prestador(gate)
        segundo = construir_expediente_originacion_prestador(gate)
        self.assertEqual(primero, segundo)
        self.assertEqual(primero.monto_autorizado, Decimal('3000000'))
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)

    def test_datos_modificados_invalidan_expediente(self):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        self.solicitud.direccion = 'Direccion modificada'
        self.solicitud.save(update_fields=['direccion', 'updated_at'])
        with self.assertRaises(ValidationError):
            construir_expediente_originacion_prestador(gate)

    def test_originacion_es_idempotente_y_crea_un_solo_par(self):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        self._aprobar_pagador(gate)

        primero = originar_credito_prestador_desde_gate(gate, actor=self.analista)
        segundo = originar_credito_prestador_desde_gate(gate, actor=self.analista)

        self.assertFalse(primero.reutilizado)
        self.assertTrue(segundo.reutilizado)
        self.assertEqual(primero.credito.pk, segundo.credito.pk)
        self.assertEqual(primero.credito_libranza.pk, segundo.credito_libranza.pk)
        self.assertEqual(Credito.objects.count(), 1)
        self.assertEqual(CreditoLibranza.objects.count(), 1)
        self.assertEqual(OrigenCreditoPrestador.objects.count(), 1)
        self.assertEqual(primero.credito.estado, Credito.EstadoCredito.EN_REVISION)
        self.assertEqual(primero.origen.monto_base, gate.monto_autorizado)
        self.assertEqual(
            primero.origen.version_configuracion,
            gate.version_configuracion_financiera,
        )
        self.assertEqual(
            primero.origen.snapshot_hash,
            primero.origen.componentes_financieros().calcular_hash(),
        )
        snapshot_antes = primero.origen.componentes_financieros()
        type(self.configuracion).objects.filter(pk=self.configuracion.pk).update(
            activo=False,
        )
        primero.origen.refresh_from_db()
        self.assertEqual(
            primero.origen.componentes_financieros(),
            snapshot_antes,
        )
        self.assertTrue(self.solicitud.timeline_operativo.filter(
            tipo_evento=TimelinePrestador.TipoEvento.ORIGINACION_COMPLETADA
        ).exists())
        self.assertTrue(self.solicitud.timeline_operativo.filter(
            tipo_evento=TimelinePrestador.TipoEvento.ORIGINACION_REUTILIZADA
        ).exists())

    def test_clave_diferente_no_duplica_el_mismo_gate(self):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        self._aprobar_pagador(gate)
        dto = construir_expediente_originacion_prestador(gate)
        clave = construir_clave_idempotencia_prestador(dto)
        originar_libranza_desde_expediente(dto, clave, self.analista)

        with self.assertRaises(ValidationError):
            originar_libranza_desde_expediente(
                dto,
                f'{clave}:distinta',
                self.analista,
            )
        self.assertEqual(Credito.objects.count(), 1)
        self.assertEqual(CreditoLibranza.objects.count(), 1)

    def test_originacion_mapea_contrato_sin_inventar_certificado_laboral(self):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        self._aprobar_pagador(gate)
        resultado = originar_credito_prestador_desde_gate(gate, actor=self.analista)
        detalle = resultado.credito_libranza

        self.assertTrue(detalle.es_prestador_servicios)
        self.assertEqual(detalle.tipo_documento, self.solicitud.tipo_documento)
        self.assertEqual(detalle.cargo_actividad_contractual, self.solicitud.cargo)
        self.assertEqual(detalle.valor_pendiente_contrato, self.solicitud.valor_pendiente_cobrar)
        self.assertFalse(detalle.certificado_laboral.name)
        self.assertEqual(
            detalle.contrato_prestacion_servicios.name,
            self.solicitud.documentos.get(tipo_documento='CONTRATO').archivo.name,
        )

    @patch('gestion_creditos.credit_services.activar_credito')
    @patch('gestion_creditos.credit_services.iniciar_proceso_desembolso')
    @patch('gestion_creditos.credit_services.preparar_documento_para_firma')
    def test_originacion_no_formaliza_ni_crea_efectos_laterales(
        self, preparar_firma, iniciar_desembolso, activar_credito
    ):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        self._aprobar_pagador(gate)
        resultado = originar_credito_prestador_desde_gate(gate, actor=self.analista)

        self.assertEqual(resultado.credito.estado, Credito.EstadoCredito.EN_REVISION)
        self.assertFalse(resultado.credito.documento_enviado)
        self.assertIsNone(resultado.credito.fecha_desembolso)
        self.assertEqual(Pagare.objects.count(), 0)
        self.assertEqual(CuotaAmortizacion.objects.count(), 0)
        self.assertEqual(HistorialPago.objects.count(), 0)
        self.assertEqual(AprobacionPagadorLibranza.objects.count(), 0)
        preparar_firma.assert_not_called()
        iniciar_desembolso.assert_not_called()
        activar_credito.assert_not_called()

    def test_gate_no_aprobado_datos_modificados_y_permisos_bloquean_originacion(self):
        gate = self._crear_gate()
        with self.assertRaises(ValidationError):
            originar_credito_prestador_desde_gate(gate, actor=self.analista)

        aprobar_para_originar(gate, actor=self.analista)
        self.solicitud.direccion = 'Direccion cambiada antes de originar'
        self.solicitud.save(update_fields=['direccion', 'updated_at'])
        with self.assertRaises(ValidationError):
            originar_credito_prestador_desde_gate(gate, actor=self.analista)

        self.solicitud.direccion = 'Direccion gate'
        self.solicitud.save(update_fields=['direccion', 'updated_at'])
        self._aprobar_pagador(gate)
        sin_permiso = get_user_model().objects.create_user(
            'staff-sin-originar', password='test', is_staff=True
        )
        with self.assertRaises(PermissionDenied):
            originar_credito_prestador_desde_gate(gate, actor=sin_permiso)

        pagador = get_user_model().objects.create_user(
            'pagador-originacion', password='test', is_staff=True
        )
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        self._otorgar(pagador, 'can_originate_contractor_credit')
        with self.assertRaises(PermissionDenied):
            originar_credito_prestador_desde_gate(gate, actor=pagador)
        dto = construir_expediente_originacion_prestador(gate)
        with self.assertRaises(PermissionDenied):
            originar_libranza_desde_expediente(
                dto,
                construir_clave_idempotencia_prestador(dto),
                pagador,
            )
        self.assertEqual(Credito.objects.count(), 0)

    @patch('gestion_creditos.services.originacion_libranza.CreditoLibranza.objects.create')
    def test_error_controlado_revierte_origen_y_credito(self, crear_detalle):
        crear_detalle.side_effect = RuntimeError('fallo controlado de prueba')
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        self._aprobar_pagador(gate)
        with self.assertRaises(RuntimeError):
            originar_credito_prestador_desde_gate(gate, actor=self.analista)

        self.assertEqual(OrigenCreditoPrestador.objects.count(), 0)
        self.assertEqual(Credito.objects.count(), 0)
        self.assertEqual(CreditoLibranza.objects.count(), 0)
        self.assertTrue(self.solicitud.timeline_operativo.filter(
            tipo_evento=TimelinePrestador.TipoEvento.ORIGINACION_ERROR_CONTROLADO
        ).exists())

    def test_vista_originacion_requiere_permiso_y_reintento_reutiliza(self):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        self.client.force_login(self.lector)
        detalle = self.client.get(
            f'/gestion/prestadores/{self.solicitud.id}/', HTTP_HOST=self.host
        )
        self.assertNotContains(detalle, 'Originar credito en revision')
        bloqueado = self.client.post(
            f'/gestion/prestadores/aprobaciones/{gate.id}/originar/',
            HTTP_HOST=self.host,
        )
        self.assertEqual(bloqueado.status_code, 403)

        self._aprobar_pagador(gate)
        self.client.force_login(self.analista)
        detalle = self.client.get(
            f'/gestion/prestadores/{self.solicitud.id}/', HTTP_HOST=self.host
        )
        self.assertContains(detalle, 'Originar credito en revision')
        for _ in range(2):
            respuesta = self.client.post(
                f'/gestion/prestadores/aprobaciones/{gate.id}/originar/',
                HTTP_HOST=self.host,
            )
            self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(Credito.objects.count(), 1)
        self.assertEqual(CreditoLibranza.objects.count(), 1)
        self.assertTrue(self.solicitud.timeline_operativo.filter(
            tipo_evento=TimelinePrestador.TipoEvento.ORIGINACION_REUTILIZADA
        ).exists())

    def test_mi_credito_no_confunde_originacion_con_desembolso(self):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        self._aprobar_pagador(gate)
        originar_credito_prestador_desde_gate(gate, actor=self.analista)
        self.client.force_login(self.solicitante)
        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)
        self.assertContains(
            response,
            'Tu solicitud está avanzando a la etapa de formalización.',
        )
        self.assertNotContains(response, 'Crédito activo')
        self.assertNotContains(response, 'Crédito desembolsado')

    def test_numeracion_segura_conserva_creditos_tradicionales_existentes(self):
        anio = timezone.now().year
        Credito.objects.create(
            usuario=self.solicitante,
            numero_credito=f'CR-{anio}-00009',
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.EN_REVISION,
            monto_solicitado=Decimal('1000000'),
            plazo_solicitado=3,
        )
        siguiente = Credito.objects.create(
            usuario=self.solicitante,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.EN_REVISION,
            monto_solicitado=Decimal('1000000'),
            plazo_solicitado=3,
        )
        self.assertEqual(siguiente.numero_credito, f'CR-{anio}-00010')

    def test_devolver_crea_revision_y_cerrar_conserva_gate(self):
        gate = self._crear_gate()
        devolver_a_revision(
            gate,
            actor=self.analista,
            motivo=AprobacionInternaPrestador.Motivo.OTRA_VALIDACION_CONTROLADA,
            comentario_interno='Se requiere una validacion adicional.',
        )
        gate.refresh_from_db()
        self.assertEqual(gate.estado, AprobacionInternaPrestador.Estado.DEVUELTA_A_REVISION)
        self.assertTrue(self.solicitud.revisiones_manuales.exists())

        revision = gate.revision_manual
        revision.estado = RevisionManualPrestador.Estado.RESUELTA
        revision.resultado = RevisionManualPrestador.Resultado.CONTINUAR_EVALUACION
        revision.save(update_fields=['estado', 'resultado', 'updated_at'])
        nueva_auditoria = self._crear_auditoria(clave='audit-gate-2')
        nuevo_gate, _ = crear_o_reutilizar_aprobacion_interna(
            nueva_auditoria, actor=self.analista
        )
        cerrar_sin_originar(
            nuevo_gate,
            actor=self.analista,
            motivo=AprobacionInternaPrestador.Motivo.CIERRE_OPERATIVO,
            comentario_interno='Cierre controlado sin originacion.',
        )
        self.assertEqual(AprobacionInternaPrestador.objects.count(), 2)
        self.assertTrue(PredecisionPrestadorAudit.objects.filter(pk=nueva_auditoria.pk).exists())

    def test_mi_credito_usa_mensajes_allowlist_y_no_expone_score(self):
        gate = self._crear_gate()
        self.client.force_login(self.solicitante)
        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)
        self.assertContains(response, 'validaciones finales')
        self.assertNotContains(response, 'Puntaje interno')
        self.assertNotContains(response, 'score interno')
        self.assertNotContains(response, 'Crédito aprobado')

        aprobar_para_originar(gate, actor=self.analista)
        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)
        self.assertContains(response, 'confirmaci\u00f3n contractual')
        self.assertNotContains(response, 'desembolsado')

    def test_bandeja_filtra_aprobacion_sin_exponer_documento_completo(self):
        gate = self._crear_gate()
        self.client.force_login(self.analista)
        response = self.client.get(
            '/gestion/prestadores/',
            {
                'estado_aprobacion': gate.estado,
                'empresa': self.empresa.id,
                'resultado': self.auditoria.resultado,
                'monto_desde': '2000000',
                'monto_hasta': '4000000',
            },
            HTTP_HOST=self.host,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{self.solicitud.id}')
        self.assertContains(response, 'Aprobaciones internas')
        self.assertNotContains(response, self.solicitud.numero_documento)

    def test_formalizacion_y_pagare_son_idempotentes_y_usan_snapshot_originado(self):
        origen = self._originar_aprobado()

        primera = preparar_formalizacion_credito_prestador(
            origen, actor=self.analista
        )
        segunda = preparar_formalizacion_credito_prestador(
            origen, actor=self.analista
        )

        self.assertFalse(primera.reutilizada)
        self.assertTrue(segunda.reutilizada)
        self.assertEqual(primera.formalizacion.pk, segunda.formalizacion.pk)
        self.assertEqual(FormalizacionCreditoPrestador.objects.count(), 1)
        self.assertEqual(Pagare.objects.count(), 1)
        pagare = primera.formalizacion.pagare
        self.assertEqual(pagare.version_plantilla, 'prestadores-1.0')
        self.assertGreaterEqual(
            Pagare._meta.get_field('version_plantilla').max_length,
            len(pagare.version_plantilla),
        )
        self.assertEqual(
            pagare.evidencias['version_origen'], origen.gate_version
        )
        self.assertEqual(origen.credito.monto_aprobado, Decimal('3000000'))
        self.assertEqual(origen.credito.plazo, 6)
        self.assertEqual(origen.credito.tasa_interes, Decimal('2.2000'))
        self.assertFalse(origen.credito_libranza.certificado_laboral.name)
        self.assertTrue(origen.credito_libranza.contrato_prestacion_servicios.name)

    def test_error_db_en_generacion_registra_error_despues_del_rollback(self):
        origen = self._originar_aprobado()

        def generar_con_error(*args, **kwargs):
            Pagare.objects.create(
                credito=origen.credito,
                numero_pagare='PAG-ERROR-1',
                archivo_pdf='pagares/error-1.pdf',
            )
            Pagare.objects.create(
                credito=origen.credito,
                numero_pagare='PAG-ERROR-2',
                archivo_pdf='pagares/error-2.pdf',
            )

        with patch(
            'gestion_creditos.services.pagare_service.generar_pagare_prestador_pdf',
            side_effect=generar_con_error,
        ):
            with self.assertRaises(IntegrityError):
                preparar_formalizacion_credito_prestador(
                    origen,
                    actor=self.analista,
                )

        formalizacion = FormalizacionCreditoPrestador.objects.get(
            origen_credito_prestador=origen
        )
        self.assertEqual(
            formalizacion.estado,
            FormalizacionCreditoPrestador.Estado.ERROR_CONTROLADO,
        )
        self.assertEqual(formalizacion.error_codigo, 'IntegrityError')
        self.assertEqual(formalizacion.error_etapa, 'PREPARACION_DOCUMENTO')
        self.assertFalse(Pagare.objects.filter(credito=origen.credito).exists())

    def test_sin_origen_o_gate_aprobado_no_formaliza(self):
        gate = self._crear_gate()
        origen = OrigenCreditoPrestador.objects.create(
            gate_id=gate.id,
            gate_version=gate.version_datos,
            clave_idempotencia='origen-no-completado',
            estado=OrigenCreditoPrestador.Estado.EN_PROCESO,
        )
        with self.assertRaises(ValidationError):
            preparar_formalizacion_credito_prestador(origen, actor=self.analista)
        self.assertFalse(FormalizacionCreditoPrestador.objects.exists())
        self.assertFalse(Pagare.objects.exists())

    def test_identidad_requerida_pertenece_al_titular_y_debe_estar_vigente(self):
        formalizacion = self._preparar_formalizacion()
        cliente = ClienteFirmaPrueba()
        with self.assertRaises(ValidationError):
            enviar_formalizacion_prestador_a_firma(
                formalizacion, actor=self.analista, cliente=cliente
            )
        self.assertEqual(cliente.llamadas, [])

        otro = get_user_model().objects.create_user('otro-firmante', password='test')
        with self.assertRaises(PermissionDenied):
            registrar_resultado_validacion_identidad_prestador(
                formalizacion,
                usuario=otro,
                referencia_proveedor='identidad-otro',
                expira_en=timezone.now() + timedelta(minutes=10),
            )
        with self.assertRaises(ValidationError):
            registrar_resultado_validacion_identidad_prestador(
                formalizacion,
                usuario=self.solicitante,
                referencia_proveedor='identidad-expirada',
                expira_en=timezone.now() - timedelta(seconds=1),
            )
        formalizacion.refresh_from_db()
        self.assertEqual(
            formalizacion.estado_identidad,
            FormalizacionCreditoPrestador.EstadoIdentidad.PENDIENTE,
        )
        registrar_resultado_validacion_identidad_prestador(
            formalizacion,
            usuario=self.solicitante,
            referencia_proveedor='identidad-vigente-luego-expirada',
            expira_en=timezone.now() + timedelta(minutes=10),
        )
        FormalizacionCreditoPrestador.objects.filter(pk=formalizacion.pk).update(
            identidad_expira_en=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaises(ValidationError):
            enviar_formalizacion_prestador_a_firma(
                formalizacion,
                actor=self.analista,
                cliente=cliente,
            )
        formalizacion.refresh_from_db()
        self.assertEqual(
            formalizacion.estado_identidad,
            FormalizacionCreditoPrestador.EstadoIdentidad.EXPIRADA,
        )
        self.assertEqual(cliente.llamadas, [])

    def test_envio_ocurre_fuera_de_transaccion_y_no_persiste_token_o_url(self):
        formalizacion = self._formalizacion_con_identidad()
        cliente = ClienteFirmaPrueba()

        resultado = enviar_formalizacion_prestador_a_firma(
            formalizacion, actor=self.analista, cliente=cliente
        )

        self.assertEqual(len(cliente.llamadas), 1)
        self.assertEqual(
            cliente.llamadas[0]['profundidad_transaccional'],
            cliente.profundidad_transaccional_inicial,
        )
        self.assertEqual(
            cliente.llamadas[0]['external_id'],
            formalizacion.clave_idempotencia,
        )
        self.assertTrue(cliente.llamadas[0]['requiere_validacion_identidad'])
        self.assertTrue(cliente.llamadas[0]['requiere_validacion_documento'])
        resultado.formalizacion.refresh_from_db()
        resultado.formalizacion.pagare.refresh_from_db()
        resultado.formalizacion.credito.refresh_from_db()
        self.assertEqual(
            resultado.formalizacion.estado,
            FormalizacionCreditoPrestador.Estado.PENDIENTE_FIRMA,
        )
        self.assertEqual(
            resultado.formalizacion.credito.estado,
            Credito.EstadoCredito.PENDIENTE_FIRMA,
        )
        self.assertTrue(resultado.formalizacion.proveedor_document_id_hash)
        self.assertFalse(resultado.formalizacion.pagare.zapsign_doc_token)
        self.assertFalse(resultado.formalizacion.pagare.zapsign_sign_url)
        self.assertNotIn('documento-remoto-1', str(resultado.formalizacion.__dict__))

    def test_error_zapsign_es_controlado_y_retry_no_duplica_pagare(self):
        formalizacion = self._formalizacion_con_identidad()
        with self.assertRaises(RuntimeError):
            enviar_formalizacion_prestador_a_firma(
                formalizacion,
                actor=self.analista,
                cliente=ClienteFirmaPrueba(error=RuntimeError('proveedor caido')),
            )
        formalizacion.refresh_from_db()
        self.assertEqual(
            formalizacion.estado,
            FormalizacionCreditoPrestador.Estado.ERROR_CONTROLADO,
        )
        self.assertEqual(Pagare.objects.count(), 1)

        cliente = ClienteFirmaPrueba(token='documento-retry')
        resultado = enviar_formalizacion_prestador_a_firma(
            formalizacion, actor=self.analista, cliente=cliente
        )
        repetido = enviar_formalizacion_prestador_a_firma(
            resultado.formalizacion, actor=self.analista, cliente=cliente
        )
        self.assertTrue(repetido.reutilizada)
        self.assertEqual(len(cliente.llamadas), 1)
        self.assertEqual(Pagare.objects.count(), 1)

    def test_envio_con_resultado_remoto_incierto_no_repite_llamada(self):
        formalizacion = self._formalizacion_con_identidad()
        FormalizacionCreditoPrestador.objects.filter(pk=formalizacion.pk).update(
            estado=FormalizacionCreditoPrestador.Estado.ENVIANDO_A_FIRMA,
            intentos_firma=1,
        )
        formalizacion.refresh_from_db()
        cliente = ClienteFirmaPrueba(token='documento-que-no-debe-crearse')

        with self.assertRaises(ValidationError):
            enviar_formalizacion_prestador_a_firma(
                formalizacion,
                actor=self.analista,
                cliente=cliente,
            )

        self.assertEqual(cliente.llamadas, [])
        self.assertEqual(Pagare.objects.count(), 1)

    @patch('gestion_creditos.credit_services.iniciar_proceso_desembolso')
    @patch('gestion_creditos.credit_services.activar_credito')
    def test_callback_firmado_es_idempotente_y_no_genera_efectos_financieros(
        self, activar_credito, iniciar_desembolso
    ):
        formalizacion = self._formalizacion_con_identidad()
        resultado_envio = enviar_formalizacion_prestador_a_firma(
            formalizacion,
            actor=self.analista,
            cliente=ClienteFirmaPrueba(token='documento-callback'),
        )

        primero = procesar_callback_formalizacion_prestador(
            documento_id='documento-callback',
            accion='signed',
            estado_proveedor='signed',
        )
        segundo = procesar_callback_formalizacion_prestador(
            documento_id='documento-callback',
            accion='signed',
            estado_proveedor='signed',
        )

        self.assertEqual(primero['estado'], 'ok')
        self.assertEqual(segundo['estado'], 'already_processed')
        resultado_envio.formalizacion.refresh_from_db()
        resultado_envio.formalizacion.credito.refresh_from_db()
        self.assertEqual(
            resultado_envio.formalizacion.estado,
            FormalizacionCreditoPrestador.Estado.FIRMADO,
        )
        self.assertEqual(
            resultado_envio.formalizacion.credito.estado,
            Credito.EstadoCredito.FIRMADO,
        )
        self.assertIsNone(resultado_envio.formalizacion.credito.fecha_desembolso)
        self.assertEqual(HistorialPago.objects.count(), 0)
        self.assertEqual(CuotaAmortizacion.objects.count(), 0)
        self.assertEqual(AprobacionPagadorLibranza.objects.count(), 0)
        activar_credito.assert_not_called()
        iniciar_desembolso.assert_not_called()
        eventos = self.solicitud.timeline_operativo.filter(
            tipo_evento=TimelinePrestador.TipoEvento.FIRMA_CONFIRMADA
        )
        self.assertEqual(eventos.count(), 1)
        self.assertNotIn('documento-callback', str(eventos.first().metadata))

    def test_callback_firmado_sin_evidencia_completa_no_marca_firmado(self):
        formalizacion = self._formalizacion_con_identidad()
        enviar_formalizacion_prestador_a_firma(
            formalizacion,
            actor=self.analista,
            cliente=ClienteFirmaPrueba(token='documento-sin-identidad-completa'),
        )
        FormalizacionCreditoPrestador.objects.filter(pk=formalizacion.pk).update(
            identidad_documento_validada=False
        )

        with self.assertRaises(ValidationError):
            procesar_callback_formalizacion_prestador(
                documento_id='documento-sin-identidad-completa',
                accion='signed',
                estado_proveedor='signed',
            )

        formalizacion.refresh_from_db()
        formalizacion.credito.refresh_from_db()
        self.assertEqual(
            formalizacion.estado,
            FormalizacionCreditoPrestador.Estado.PENDIENTE_FIRMA,
        )
        self.assertEqual(
            formalizacion.credito.estado,
            Credito.EstadoCredito.PENDIENTE_FIRMA,
        )

    @override_settings(ZAPSIGN_WEBHOOK_SECRET='secreto-webhook')
    @patch('gestion_creditos.credit_services.iniciar_proceso_desembolso')
    @patch('gestion_creditos.credit_services.activar_credito')
    def test_webhook_prestador_ignora_credito_anulado_sin_efectos_financieros(
        self, activar_credito, iniciar_desembolso
    ):
        formalizacion = self._formalizacion_con_identidad()
        formalizacion = enviar_formalizacion_prestador_a_firma(
            formalizacion,
            actor=self.analista,
            cliente=ClienteFirmaPrueba(token='documento-prestador-anulado'),
        ).formalizacion
        Credito.objects.filter(pk=formalizacion.credito_id).update(
            estado=Credito.EstadoCredito.ANULADO
        )
        Pagare.objects.filter(pk=formalizacion.pagare_id).update(
            estado=Pagare.EstadoPagare.CANCELLED
        )

        respuesta = self.client.post(
            '/api/webhooks/zapsign/',
            data=json.dumps({
                'token': 'documento-prestador-anulado',
                'event': 'doc_signed',
                'status': 'signed',
                'signers': [{'signed_at': timezone.now().isoformat()}],
            }),
            content_type='application/json',
            HTTP_X_ZAPSIGN_SECRET='secreto-webhook',
            HTTP_HOST=self.host,
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            respuesta.json()['status'], 'credit_cancelled_ignored'
        )
        formalizacion.refresh_from_db()
        formalizacion.credito.refresh_from_db()
        formalizacion.pagare.refresh_from_db()
        self.assertEqual(
            formalizacion.estado,
            FormalizacionCreditoPrestador.Estado.PENDIENTE_FIRMA,
        )
        self.assertEqual(
            formalizacion.credito.estado, Credito.EstadoCredito.ANULADO
        )
        self.assertEqual(
            formalizacion.pagare.estado, Pagare.EstadoPagare.CANCELLED
        )
        log = ZapSignWebhookLog.objects.get()
        self.assertTrue(log.processed)
        self.assertIn('ANULADO', log.error_message)
        self.assertEqual(HistorialPago.objects.count(), 0)
        self.assertEqual(CuotaAmortizacion.objects.count(), 0)
        activar_credito.assert_not_called()
        iniciar_desembolso.assert_not_called()

    @override_settings(ZAPSIGN_WEBHOOK_SECRET='secreto-webhook')
    def test_webhook_prestador_guarda_resumen_sanitizado_y_no_token(self):
        formalizacion = self._formalizacion_con_identidad()
        enviar_formalizacion_prestador_a_firma(
            formalizacion,
            actor=self.analista,
            cliente=ClienteFirmaPrueba(token='token-webhook-prestador'),
        )
        respuesta = self.client.post(
            '/api/webhooks/zapsign/',
            data=json.dumps({
                'token': 'token-webhook-prestador',
                'event': 'doc_signed',
                'status': 'signed',
                'signers': [{
                    'name': 'Dato que no debe persistirse',
                    'document': '123456789',
                    'signed_at': timezone.now().isoformat(),
                }],
            }),
            content_type='application/json',
            HTTP_X_ZAPSIGN_SECRET='secreto-webhook',
            HTTP_HOST=self.host,
        )
        self.assertEqual(respuesta.status_code, 200)
        log = ZapSignWebhookLog.objects.get()
        self.assertTrue(log.doc_token.startswith('sha256:'))
        self.assertNotIn('token-webhook-prestador', str(log.__dict__))
        self.assertEqual(set(log.payload), {'event', 'status'})
        self.assertEqual(log.headers, {})

    def test_perfil_pagador_no_puede_preparar_aunque_tenga_permisos(self):
        origen = self._originar_aprobado()
        pagador = get_user_model().objects.create_user(
            'pagador-formalizacion', password='test', is_staff=True
        )
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        self._otorgar(
            pagador,
            'can_prepare_contractor_formalization',
            'can_retry_contractor_signature',
            'can_view_contractor_formalization',
        )
        with self.assertRaises(PermissionDenied):
            preparar_formalizacion_credito_prestador(origen, actor=pagador)
        self.assertFalse(FormalizacionCreditoPrestador.objects.exists())

    def test_vista_staff_prepara_y_mi_credito_expone_solo_estado_publico(self):
        origen = self._originar_aprobado()
        self.client.force_login(self.analista)
        respuesta = self.client.post(
            f'/gestion/prestadores/origenes/{origen.id}/formalizar/',
            HTTP_HOST=self.host,
        )
        self.assertEqual(respuesta.status_code, 302)
        formalizacion = FormalizacionCreditoPrestador.objects.get()

        self.client.force_login(self.solicitante)
        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)
        self.assertContains(response, 'Necesitamos validar tu identidad')
        self.assertNotContains(response, formalizacion.clave_idempotencia)
        self.assertNotContains(response, 'ZapSign')
        self.assertNotContains(response, 'desembolsado')
        self.assertNotContains(response, 'activo')

    def test_novedad_exige_formalizacion_y_credito_firmados(self):
        formalizacion = self._preparar_formalizacion()
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_novedad_operativa_prestador(
                formalizacion,
                actor=self.analista,
            )

        FormalizacionCreditoPrestador.objects.filter(pk=formalizacion.pk).update(
            estado=FormalizacionCreditoPrestador.Estado.FIRMADO,
            firmada_en=timezone.now(),
        )
        formalizacion.refresh_from_db()
        with self.assertRaises(ValidationError):
            crear_o_reutilizar_novedad_operativa_prestador(
                formalizacion,
                actor=self.analista,
            )
        self.assertFalse(NovedadOperativaPrestador.objects.exists())

    @patch('gestion_creditos.credit_services.iniciar_proceso_desembolso')
    @patch('gestion_creditos.credit_services.activar_credito')
    @patch(
        'gestion_creditos.services.aprobacion_pagador_libranza.'
        'decidir_solicitud_libranza_por_pagador'
    )
    def test_novedad_firmada_es_idempotente_y_sin_efectos_financieros(
        self, decidir_pagador, activar_credito, iniciar_desembolso
    ):
        formalizacion = self._formalizacion_firmada()
        credito = formalizacion.credito
        condiciones = (credito.monto_aprobado, credito.plazo, credito.tasa_interes)

        primera = crear_o_reutilizar_novedad_operativa_prestador(
            formalizacion,
            actor=self.analista,
        )
        segunda = crear_o_reutilizar_novedad_operativa_prestador(
            formalizacion,
            actor=self.analista,
        )

        self.assertFalse(primera.reutilizada)
        self.assertTrue(segunda.reutilizada)
        self.assertEqual(primera.novedad.pk, segunda.novedad.pk)
        self.assertEqual(NovedadOperativaPrestador.objects.count(), 1)
        credito.refresh_from_db()
        self.assertEqual(
            (credito.monto_aprobado, credito.plazo, credito.tasa_interes),
            condiciones,
        )
        self.assertEqual(credito.estado, Credito.EstadoCredito.FIRMADO)
        self.assertIsNone(credito.fecha_desembolso)
        self.assertEqual(HistorialPago.objects.count(), 0)
        self.assertEqual(CuotaAmortizacion.objects.count(), 0)
        self.assertEqual(AprobacionPagadorLibranza.objects.count(), 0)
        decidir_pagador.assert_not_called()
        activar_credito.assert_not_called()
        iniciar_desembolso.assert_not_called()

    def test_dto_y_destinatarios_son_sanitizados_y_pertenecen_a_empresa(self):
        self.solicitante.email = 'solicitante-libre@example.test'
        self.solicitante.save(update_fields=['email'])
        formalizacion = self._formalizacion_firmada()
        novedad = crear_o_reutilizar_novedad_operativa_prestador(
            formalizacion,
            actor=self.analista,
        ).novedad
        pagador = self._crear_pagador(
            'pagador-destino',
            self.empresa,
            email='operaciones@empresa.test',
        )
        otra_empresa = Empresa.objects.create(
            nombre='Empresa ajena',
            convenio_activo=True,
        )
        self._crear_pagador(
            'pagador-ajeno-destino',
            otra_empresa,
            email='ajeno@empresa.test',
        )

        dto = construir_dto_novedad_operativa_prestador(novedad)
        destinatarios = obtener_destinatarios_novedad_operativa_prestador(
            self.empresa
        )

        self.assertEqual(destinatarios, [pagador.email])
        self.assertNotIn(self.solicitante.email, destinatarios)
        self.assertNotIn(self.solicitud.numero_documento, str(dto.como_dict()))
        self.assertEqual(
            dto.documento_enmascarado,
            f'****{self.solicitud.numero_documento[-4:]}',
        )
        self.assertNotIn('score', dto.como_dict())
        self.assertNotIn('datacredito', dto.como_dict())

    def test_envio_ocurre_fuera_de_transaccion_y_persiste_solo_destinos_seguros(self):
        formalizacion = self._formalizacion_firmada()
        novedad = crear_o_reutilizar_novedad_operativa_prestador(
            formalizacion,
            actor=self.analista,
        ).novedad
        self._crear_pagador(
            'pagador-envio',
            self.empresa,
            email='operativo@empresa.test',
        )
        cliente = ClienteNovedadPrueba()

        resultado = enviar_novedad_operativa_prestador(
            novedad,
            actor=self.analista,
            cliente=cliente,
        )

        self.assertFalse(resultado.reutilizado)
        self.assertEqual(len(cliente.llamadas), 1)
        self.assertEqual(
            cliente.llamadas[0]['profundidad_transaccional'],
            cliente.profundidad_transaccional_inicial,
        )
        resultado.novedad.refresh_from_db()
        self.assertEqual(
            resultado.novedad.estado,
            NovedadOperativaPrestador.Estado.ENVIADA,
        )
        self.assertEqual(resultado.novedad.intentos_envio, 1)
        self.assertEqual(resultado.novedad.destinatarios_enmascarados, [
            'o***@empresa.test'
        ])
        self.assertNotIn('operativo@empresa.test', str(resultado.novedad.__dict__))
        formalizacion.credito.refresh_from_db()
        self.assertEqual(formalizacion.credito.estado, Credito.EstadoCredito.FIRMADO)

    def test_error_email_y_retry_no_duplican_novedad(self):
        formalizacion = self._formalizacion_firmada()
        novedad = crear_o_reutilizar_novedad_operativa_prestador(
            formalizacion,
            actor=self.analista,
        ).novedad
        self._crear_pagador(
            'pagador-retry',
            self.empresa,
            email='retry@empresa.test',
        )
        with self.assertRaises(RuntimeError):
            enviar_novedad_operativa_prestador(
                novedad,
                actor=self.analista,
                cliente=ClienteNovedadPrueba(error=RuntimeError('correo caido')),
            )
        novedad.refresh_from_db()
        self.assertEqual(
            novedad.estado,
            NovedadOperativaPrestador.Estado.ERROR_CONTROLADO,
        )

        cliente = ClienteNovedadPrueba()
        resultado = enviar_novedad_operativa_prestador(
            novedad,
            actor=self.analista,
            cliente=cliente,
        )
        repetido = enviar_novedad_operativa_prestador(
            resultado.novedad,
            actor=self.analista,
            cliente=cliente,
        )
        self.assertTrue(repetido.reutilizado)
        self.assertEqual(len(cliente.llamadas), 1)
        self.assertEqual(NovedadOperativaPrestador.objects.count(), 1)
        resultado.novedad.refresh_from_db()
        self.assertEqual(resultado.novedad.intentos_envio, 2)

    def test_pagador_solo_gestiona_novedad_de_su_empresa_e_idempotente(self):
        formalizacion = self._formalizacion_firmada()
        novedad = crear_o_reutilizar_novedad_operativa_prestador(
            formalizacion,
            actor=self.analista,
        ).novedad
        pagador = self._crear_pagador(
            'pagador-propio',
            self.empresa,
            email='propio@empresa.test',
            permisos=(
                'can_view_contractor_operational_notice',
                'can_acknowledge_contractor_operational_notice',
            ),
        )
        otra_empresa = Empresa.objects.create(
            nombre='Empresa ownership', convenio_activo=True
        )
        pagador_ajeno = self._crear_pagador(
            'pagador-ownership-ajeno',
            otra_empresa,
            email='ownership-ajeno@empresa.test',
            permisos=(
                'can_view_contractor_operational_notice',
                'can_acknowledge_contractor_operational_notice',
            ),
        )
        enviar_novedad_operativa_prestador(
            novedad,
            actor=self.analista,
            cliente=ClienteNovedadPrueba(),
        )
        novedad.refresh_from_db()

        with self.assertRaises(PermissionDenied):
            confirmar_recepcion_novedad_operativa_prestador(
                novedad,
                actor=pagador_ajeno,
            )
        self.client.force_login(pagador_ajeno)
        respuesta = self.client.get(
            f'/pagador/prestadores/novedades/{novedad.id}/'
        )
        self.assertEqual(respuesta.status_code, 404)

        primera = confirmar_recepcion_novedad_operativa_prestador(
            novedad,
            actor=pagador,
        )
        segunda = confirmar_recepcion_novedad_operativa_prestador(
            primera.novedad,
            actor=pagador,
        )
        self.assertFalse(primera.reutilizada)
        self.assertTrue(segunda.reutilizada)
        gestionada = marcar_novedad_operativa_prestador_gestionada(
            segunda.novedad,
            actor=pagador,
        )
        self.assertEqual(
            gestionada.novedad.estado,
            NovedadOperativaPrestador.Estado.GESTIONADA,
        )
        formalizacion.credito.refresh_from_db()
        self.assertEqual(formalizacion.credito.estado, Credito.EstadoCredito.FIRMADO)

    def test_ui_pagador_solo_expone_acciones_operativas(self):
        formalizacion = self._formalizacion_firmada()
        novedad = crear_o_reutilizar_novedad_operativa_prestador(
            formalizacion,
            actor=self.analista,
        ).novedad
        pagador = self._crear_pagador(
            'pagador-ui-novedad',
            self.empresa,
            email='ui@empresa.test',
            permisos=(
                'can_view_contractor_operational_notice',
                'can_acknowledge_contractor_operational_notice',
            ),
        )
        enviar_novedad_operativa_prestador(
            novedad,
            actor=self.analista,
            cliente=ClienteNovedadPrueba(),
        )
        self.client.force_login(pagador)
        listado = self.client.get('/pagador/prestadores/novedades/')
        detalle = self.client.get(
            f'/pagador/prestadores/novedades/{novedad.id}/'
        )
        self.assertEqual(listado.status_code, 200)
        self.assertEqual(detalle.status_code, 200)
        self.assertContains(detalle, 'Confirmar recepci')
        for texto in ('Aprobar', 'Rechazar', 'DataCr', 'score'):
            self.assertNotContains(detalle, texto)

    def test_perfil_pagador_no_puede_generar_novedad_aunque_sea_staff(self):
        formalizacion = self._formalizacion_firmada()
        pagador = self._crear_pagador(
            'pagador-staff-novedad',
            self.empresa,
            email='staff@empresa.test',
            is_staff=True,
            permisos=(
                'can_create_contractor_operational_notice',
                'can_retry_contractor_operational_notice',
                'can_view_contractor_operational_notice',
                'can_acknowledge_contractor_operational_notice',
            ),
        )
        with self.assertRaises(PermissionDenied):
            crear_o_reutilizar_novedad_operativa_prestador(
                formalizacion,
                actor=pagador,
            )
        self.assertFalse(NovedadOperativaPrestador.objects.exists())

    def test_timeline_novedad_es_sanitizado(self):
        formalizacion = self._formalizacion_firmada()
        novedad = crear_o_reutilizar_novedad_operativa_prestador(
            formalizacion,
            actor=self.analista,
        ).novedad
        self._crear_pagador(
            'pagador-timeline',
            self.empresa,
            email='timeline@empresa.test',
        )
        enviar_novedad_operativa_prestador(
            novedad,
            actor=self.analista,
            cliente=ClienteNovedadPrueba(),
        )
        eventos = self.solicitud.timeline_operativo.filter(
            tipo_evento__in=[
                TimelinePrestador.TipoEvento.NOVEDAD_OPERATIVA_GENERADA,
                TimelinePrestador.TipoEvento.NOVEDAD_OPERATIVA_ENVIO_INICIADO,
                TimelinePrestador.TipoEvento.NOVEDAD_OPERATIVA_ENVIADA,
            ]
        )
        self.assertEqual(eventos.count(), 3)
        texto = str(list(eventos.values_list('metadata', flat=True)))
        self.assertNotIn(self.solicitud.numero_documento, texto)
        self.assertNotIn('timeline@empresa.test', texto)
        self.assertNotIn('score', texto.lower())
        self.assertLessEqual(
            set(eventos.first().metadata),
            {'novedad_id', 'credito_id', 'empresa_id', 'actor_id', 'canal', 'estado'},
        )

    def test_mi_credito_refleja_etapa_operativa_sin_afirmar_transferencia(self):
        formalizacion = self._formalizacion_firmada()
        self.client.force_login(self.solicitante)
        respuesta = self.client.get('/mi-credito/', HTTP_HOST=self.host)
        self.assertContains(respuesta, 'validaciones operativas finales')
        self.assertNotContains(respuesta, 'transferencia realizada')

        novedad = crear_o_reutilizar_novedad_operativa_prestador(
            formalizacion,
            actor=self.analista,
        ).novedad
        self._crear_pagador(
            'pagador-ux',
            self.empresa,
            email='ux@empresa.test',
        )
        enviar_novedad_operativa_prestador(
            novedad,
            actor=self.analista,
            cliente=ClienteNovedadPrueba(),
        )
        respuesta = self.client.get('/mi-credito/', HTTP_HOST=self.host)
        self.assertContains(respuesta, 'etapa final de formalizacion operativa')
        self.assertNotContains(respuesta, 'pagador aprobo')
        self.assertNotContains(respuesta, 'desembolsado')

    def _crear_pagador(
        self,
        username,
        empresa,
        *,
        email,
        permisos=(),
        is_staff=False,
    ):
        usuario = get_user_model().objects.create_user(
            username,
            password='test',
            email=email,
            is_staff=is_staff,
        )
        PerfilPagador.objects.create(usuario=usuario, empresa=empresa, es_pagador=True)
        if permisos:
            self._otorgar(usuario, *permisos)
        return usuario

    def _formalizacion_firmada(self):
        formalizacion = self._formalizacion_con_identidad()
        enviar_formalizacion_prestador_a_firma(
            formalizacion,
            actor=self.analista,
            cliente=ClienteFirmaPrueba(token=f'firma-novedad-{formalizacion.id}'),
        )
        procesar_callback_formalizacion_prestador(
            documento_id=f'firma-novedad-{formalizacion.id}',
            accion='signed',
            estado_proveedor='signed',
        )
        formalizacion.refresh_from_db()
        return formalizacion

    def _originar_aprobado(self):
        gate = self._crear_gate()
        aprobar_para_originar(gate, actor=self.analista)
        self._aprobar_pagador(gate)
        return originar_credito_prestador_desde_gate(
            gate, actor=self.analista
        ).origen

    def _aprobar_pagador(self, gate):
        aprobacion = AprobacionPagadorPrestador.objects.get(
            aprobacion_interna=gate
        )
        return decidir_aprobacion_pagador_prestador(
            aprobacion,
            actor=self.pagador,
            decision=AprobacionPagadorPrestador.Estado.APROBADO,
            confirmaciones={
                'confirma_vinculo': True,
                'confirma_contrato_vigente': True,
                'confirma_forma_pago_mensual': True,
                'confirma_valores_contractuales': True,
                'confirma_capacidad_operativa': True,
                'acepta_gestionar_pago': True,
            },
        ).aprobacion

    def _preparar_formalizacion(self):
        origen = self._originar_aprobado()
        return preparar_formalizacion_credito_prestador(
            origen, actor=self.analista
        ).formalizacion

    def _formalizacion_con_identidad(self):
        formalizacion = self._preparar_formalizacion()
        return registrar_resultado_validacion_identidad_prestador(
            formalizacion,
            usuario=self.solicitante,
            referencia_proveedor=f'identidad-{formalizacion.id}',
            expira_en=timezone.now() + timedelta(minutes=15),
        )

    def _crear_configuracion(self):
        from contractors.models import ConfiguracionSimuladorPrestador

        return ConfiguracionSimuladorPrestador.objects.create(
            nombre='Configuracion gate',
            version='financiera-gate-v1',
            activo=True,
            monto_minimo=Decimal('1000000'),
            monto_maximo=Decimal('10000000'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=8,
            tasa_mensual=Decimal('2.2000'),
        )

    def _crear_solicitud(self):
        solicitud = ContractorApplication.objects.create(
            usuario=self.solicitante,
            empresa=self.empresa,
            tipo_documento='CC',
            numero_documento='900000099',
            nombres='Persona',
            apellidos='Gate',
            celular='3000000099',
            correo='gate@example.com',
            direccion='Direccion gate',
            cargo='Consultoria',
            fecha_inicio_contrato=timezone.localdate(),
            fecha_fin_contrato=timezone.localdate() + timedelta(days=240),
            valor_total_contrato=Decimal('50000000'),
            valor_pagado_contrato=Decimal('2000000'),
            valor_pendiente_cobrar=Decimal('48000000'),
            forma_pago=ContractorApplication.FormaPago.MENSUAL,
            valor_mensual_contractual=Decimal('6000000'),
            monto_solicitado=Decimal('3000000'),
            plazo_meses=6,
            version_configuracion_financiera_simulacion=self.configuracion.version,
            version_politica_simulacion=self.politica.version_politica,
            monto_simulado=Decimal('3000000'),
            plazo_simulado_meses=6,
            tasa_mensual_simulacion=self.configuracion.tasa_mensual,
            monto_maximo_configuracion_simulacion=self.configuracion.monto_maximo,
            plazo_maximo_configuracion_simulacion=self.configuracion.plazo_maximo_meses,
            simulada_en=timezone.now(),
            acepta_terminos=True,
            acepta_politica_privacidad=True,
            autoriza_analisis_contractual_asistido=True,
            autoriza_consulta_centrales=True,
            estado_analisis_contractual=ContractorApplication.EstadoAnalisisContractual.COMPLETADO,
            metadata_analisis_contractual={
                'identidad': {'documento_coincide': True},
                'empresa_sugerida': {
                    'empresa_sugerida_id': self.empresa.id,
                    'tipo_coincidencia': 'NIT_EXACTO',
                },
                'bloqueos': [],
            },
            estado=ContractorApplication.Estado.EVALUACION_COMPLETADA,
        )
        for tipo in ContractorApplicationDocument.TipoDocumento.values:
            extension = '.jpg' if tipo.startswith('CEDULA') else '.pdf'
            ContractorApplicationDocument.objects.create(
                solicitud=solicitud,
                tipo_documento=tipo,
                archivo=SimpleUploadedFile(f'{tipo}{extension}', b'data'),
                uploaded_by=self.solicitante,
                metadata_captura={'source': 'camera'} if tipo.startswith('CEDULA') else {},
            )
        return solicitud

    def _crear_snapshot(self):
        ahora = timezone.now()
        return ConsultaDatacreditoSnapshot.objects.create(
            ambiente='uat',
            servicio=ConsultaDatacreditoSnapshot.Servicio.DECISOR,
            documento_hash='a' * 64,
            documento_enmascarado='*****0099',
            fingerprint='b' * 64,
            estado=ConsultaDatacreditoSnapshot.Estado.EXITOSO,
            resultado_normalizado={'score_externo': 900, 'mora_severa': False},
            consultado_en=ahora,
            vigente_hasta=ahora + timedelta(days=30),
            autorizacion_referencia=str(self.autorizacion.pk),
        )

    def _crear_auditoria(self, clave='audit-gate-1'):
        self.solicitud.refresh_from_db()
        version_datos, snapshot_entrada = construir_version_datos(self.solicitud)
        return PredecisionPrestadorAudit.objects.create(
            solicitud=self.solicitud,
            version_datos=version_datos,
            clave_idempotencia=clave,
            estado_ejecucion=PredecisionPrestadorAudit.EstadoEjecucion.COMPLETADA,
            resultado=PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY,
            score=Decimal('900'),
            version_score=self.politica.version_score,
            version_politica=self.politica.version_politica,
            version_configuracion_financiera=self.configuracion.version,
            tasa_mensual_configuracion=self.configuracion.tasa_mensual,
            monto_maximo_configuracion=self.configuracion.monto_maximo,
            plazo_maximo_configuracion=self.configuracion.plazo_maximo_meses,
            snapshot_entrada=snapshot_entrada,
            snapshot_salida={
                'resultado': PredecisionPrestadorAudit.Resultado.PREAPROBADO_READ_ONLY,
                'eligible': True,
                'requiere_revision_manual': False,
                'monto_maximo_sugerido': '3000000.00',
                'plazo_maximo_sugerido': 6,
                'score_resultado': {
                    'banda': 'PREMIUM',
                    'monto_maximo_sugerido': '3000000.00',
                    'plazo_maximo_sugerido': 6,
                    'variables_calculadas': {
                        'capacidad_monto_teorica': '6000000.00',
                        'meses_restantes_contrato': 7,
                    },
                },
                'datacredito': {
                    'estado': 'EXITOSO',
                    'snapshot_id': str(self.snapshot.pk),
                    'servicio': 'decisor',
                },
            },
            iniciada_en=timezone.now(),
            finalizada_en=timezone.now(),
            creada_por=self.analista,
        )

    def _crear_gate(self):
        return crear_o_reutilizar_aprobacion_interna(
            self.auditoria, actor=self.analista
        )[0]

    def _otorgar(self, usuario, *codenames):
        usuario.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
