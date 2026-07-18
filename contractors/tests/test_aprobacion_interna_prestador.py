from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from contractors.models import (
    AprobacionInternaPrestador,
    ContractorApplication,
    ContractorApplicationDocument,
    PredecisionPrestadorAudit,
    RequerimientoSubsanacionPrestador,
    RevisionManualPrestador,
    TimelinePrestador,
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
from contractors.tests.test_score_prestadores_v2 import crear_politica_score
from gestion_creditos.models import (
    AprobacionPagadorLibranza,
    Credito,
    CreditoLibranza,
    Empresa,
    HistorialPago,
    Pagare,
)
from integrations.models import ConsultaDatacreditoSnapshot
from usuarios.models import PerfilPagador


@override_settings(
    DATACREDITO_AUTHORIZATION_TEXT_VERSION='uat-gate-v1',
    DATACREDITO_AUTHORIZATION_TEXT='Autorizacion controlada para pruebas del gate.',
)
class AprobacionInternaPrestadorTest(TestCase):
    host = 'contratistas.localhost'

    def setUp(self):
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
        )
        self._otorgar(
            self.lector,
            'can_view_contractor_review_queue',
            'can_view_contractor_internal_approval',
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

    def test_expediente_solo_aprobado_es_determinista_y_no_persiste(self):
        gate = self._crear_gate()
        with self.assertRaises(ValidationError):
            construir_expediente_originacion_prestador(gate)
        aprobar_para_originar(gate, actor=self.analista)
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
        self.assertContains(response, 'etapa de formalizaci\u00f3n')
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
