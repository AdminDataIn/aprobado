"""Publicacion/adopcion aisladas con datos sinteticos y proveedores bloqueados."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from io import StringIO
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from contractors.datacredito.dto import ResultadoNormalizadoDatacreditoPrestador
from contractors.models import (
    CambioPoliticaScorePrestadorAudit, ConfiguracionScorePrestador,
    ContractorApplication, ContractorApplicationDocument, EvaluacionPrestadorJob,
    PredecisionPrestadorAudit, TimelinePrestador,
)
from contractors.score.componentes import componente_capacidad_dual
from contractors.score.politica import obtener_politica_score_activa
from contractors.services.aprobacion_interna import crear_o_reutilizar_aprobacion_interna
from contractors.services.autorizacion_datacredito import (
    obtener_autorizacion_datacredito_vigente,
    registrar_autorizacion_datacredito_desde_solicitud,
)
from contractors.services.datacredito_evaluacion import construir_fingerprint_datacredito
from contractors.services.evaluacion_versionado import construir_version_datos
from contractors.services.ingreso_evaluable import (
    exigir_ingreso_documental_para_originacion, resolver_ingreso_evaluable,
)
from contractors.services.ingreso_neto import registrar_ingreso_neto
from contractors.services.politica_ingreso_publicacion import (
    VERSION_INGRESO, adoptar_politica_ingreso, ingreso_automatico_aplica,
    obtener_politica_para_solicitud, publicar_politica_ingreso,
)
from contractors.services.politica_score import activar_politica_score_prestador
from contractors.tests.test_pipeline_financiero_e2e import FixturePipelineFinanciero
from integrations.datacredito.settings import obtener_configuracion_datacredito
from integrations.models import ConsultaDatacreditoSnapshot
from integrations.tests.test_datacredito_snapshot_v2 import CONFIGURACION_DATACREDITO_PRUEBA


@override_settings(
    **CONFIGURACION_DATACREDITO_PRUEBA,
    CONTRACTORS_CONTRACT_AI_ENABLED=False, OPENAI_API_KEY='',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class PoliticaIngresoPublicacionTest(FixturePipelineFinanciero, TestCase):
    ingreso_automatico = False

    def setUp(self):
        for target in (
            'contractors.datacredito.adapter.consultar_midecisor_persona_natural',
            'contractors.datacredito.adapter.consultar_historial_credito',
        ):
            guard = patch(target, side_effect=AssertionError('Proveedor externo prohibido'))
            guard.start()
            self.addCleanup(guard.stop)
        super().setUp()

    def crear_por_portal(self, *, monto='3000000', plazo=6):
        # Mantiene la fixture financiera sin usar endpoints, PDFs ajenos ni HTTP.
        inicio = timezone.localdate().replace(day=1)
        contenido = b'Contrato completamente sintetico para politica de ingreso.'
        solicitud = ContractorApplication.objects.create(
            usuario=self.usuario, empresa=self.empresa, tipo_documento='CC',
            numero_documento='900000099', nombres='PERSONA', apellidos='SINTETICA',
            correo='sintetico@example.invalid', celular='3000000099',
            direccion='Direccion sintetica', cargo='Consultor',
            fecha_inicio_contrato=inicio,
            fecha_fin_contrato=inicio + relativedelta(months=12) - timedelta(days=1),
            duracion_contrato_meses=12, estado_contractual_declarado='VIGENTE',
            valor_total_contrato='120000000', valor_pagado_contrato='0',
            valor_pendiente_cobrar='120000000', valor_mensual_contractual='10000000',
            forma_pago='MENSUAL',
            evidencia_forma_pago='primeros cinco (5) dias habiles del mes siguiente',
            monto_solicitado=monto, plazo_meses=plazo,
            monto_simulado=monto, plazo_simulado_meses=plazo,
            version_configuracion_financiera_simulacion=self.financiera.version,
            version_politica_simulacion=self.politica.version_politica,
            tasa_mensual_simulacion=self.financiera.tasa_mensual,
            monto_maximo_configuracion_simulacion=self.financiera.monto_maximo,
            plazo_maximo_configuracion_simulacion=self.financiera.plazo_maximo_meses,
            simulada_en=timezone.now(), estado='EVALUACION_PENDIENTE',
            acepta_terminos=True, acepta_politica_privacidad=True,
            autoriza_consulta_centrales=True, estado_analisis_contractual='COMPLETADO',
            metadata_analisis_contractual={
                'archivo_hash_sha256': sha256(contenido).hexdigest(),
                'identidad': {'documento_coincide': True},
                'empresa_sugerida': {
                    'empresa_sugerida_id': self.empresa.pk, 'tipo_coincidencia': 'NIT_EXACTO',
                },
            },
        )
        for tipo in ContractorApplicationDocument.TipoDocumento.values:
            ContractorApplicationDocument.objects.create(
                solicitud=solicitud, tipo_documento=tipo, uploaded_by=self.usuario,
                archivo=SimpleUploadedFile(f'{tipo}.pdf', contenido, content_type='application/pdf'),
                metadata_captura={'source': 'capture'},
            )
        registrar_autorizacion_datacredito_desde_solicitud(solicitud, usuario=self.usuario)
        solicitud.refresh_from_db()
        return solicitud

    def publicar(self, **cambios):
        return publicar_politica_ingreso(**{
            'origen_id': self.politica.pk, 'actor': self.staff,
            'motivo': 'Publicacion sintetica', 'vigente_desde': timezone.localdate(),
            **cambios,
        })

    def adoptar(self, politica, **cambios):
        return adoptar_politica_ingreso(**{
            'solicitud_id': self.solicitud.pk, 'politica_id': politica.pk,
            'actor': self.staff, 'motivo': 'Adopcion sintetica', **cambios,
        })

    def invalidar_manual(self):
        registrar_ingreso_neto(
            solicitud=self.solicitud, actor=self.staff, invalidar=True,
            observacion='Invalidacion sintetica para verificar ausencia de ingreso manual',
        )

    def eventos_adopcion(self):
        return TimelinePrestador.objects.filter(
            solicitud=self.solicitud, tipo_evento=TimelinePrestador.TipoEvento.DATOS_MODIFICADOS,
            metadata__tipo='ADOPCION_INGRESO',
        )

    def test_publicacion_clona_parametros_bandas_idempotente_y_congela(self):
        vieja = ConfiguracionScorePrestador.objects.values().get(pk=self.politica.pk)
        version = construir_version_datos(self.solicitud)
        self.assertEqual(self.politica.politica_ingreso_evaluable, {})
        nueva = self.publicar()
        esperados = dict(vieja)
        for campo in ('id', 'created_at', 'updated_at', 'nombre', 'version', 'version_politica',
                      'activa', 'fecha_vigencia_desde', 'politica_ingreso_evaluable'):
            esperados.pop(campo)
        for campo, valor in esperados.items():
            self.assertEqual(getattr(nueva, campo), valor, campo)
        campos_banda = [f.attname for f in nueva.bandas.model._meta.concrete_fields
                        if f.name not in ('id', 'configuracion')]
        self.assertEqual(list(nueva.bandas.order_by('orden').values(*campos_banda)),
                         list(self.politica.bandas.order_by('orden').values(*campos_banda)))
        self.assertFalse(nueva.activa)
        self.assertEqual(nueva.version_politica, VERSION_INGRESO)
        self.assertEqual(nueva.politica_ingreso_evaluable['descuento_contractual_porcentaje'], '25.00')
        self.assertEqual(self.publicar().pk, nueva.pk)
        audit = CambioPoliticaScorePrestadorAudit.objects.get(
            politica_nueva=nueva, snapshot_nuevo__ingreso_publicado=True,
        )
        self.assertEqual(audit.accion, 'SIN_CAMBIO')
        self.assertIn('politica', audit.snapshot_nuevo)
        self.assertEqual(ConfiguracionScorePrestador.objects.values().get(pk=self.politica.pk), vieja)
        self.assertEqual(construir_version_datos(self.solicitud), version)
        self.assertEqual(obtener_politica_score_activa().pk, self.politica.pk)
        for objetivo, campo, valor in (
            (nueva, 'politica_ingreso_evaluable', {
                **nueva.politica_ingreso_evaluable, 'descuento_contractual_porcentaje': '30.00',
            }),
            (nueva.bandas.first(), 'monto_maximo', Decimal('1.00')),
            (nueva, 'fecha_vigencia_desde', timezone.localdate() - timedelta(days=2)),
        ):
            setattr(objetivo, campo, valor)
            with self.assertRaises(ValidationError):
                objetivo.full_clean()
            objetivo.refresh_from_db()
        with self.assertRaises(ValidationError):
            activar_politica_score_prestador(
                politica_id=nueva.pk, actor=self.staff, motivo='No activar globalmente',
            )

    def test_publicacion_y_adopcion_otra_no_reescriben_historia_legacy(self):
        auditoria = self.evaluar()
        historico = PredecisionPrestadorAudit.objects.values().get(pk=auditoria.pk)
        solicitud = ContractorApplication.objects.values().get(pk=self.solicitud.pk)
        huella = construir_version_datos(self.solicitud)
        timeline = list(TimelinePrestador.objects.filter(solicitud=self.solicitud).values())
        otra = self.nueva_solicitud_autorizada()
        nueva = self.publicar()
        self.adoptar(nueva, solicitud_id=otra.pk)
        self.solicitud.refresh_from_db()
        self.assertEqual(PredecisionPrestadorAudit.objects.values().get(pk=auditoria.pk), historico)
        self.assertEqual(ContractorApplication.objects.values().get(pk=self.solicitud.pk), solicitud)
        self.assertEqual(construir_version_datos(self.solicitud), huella)
        self.assertEqual(list(TimelinePrestador.objects.filter(solicitud=self.solicitud).values()), timeline)
        self.assertFalse(ingreso_automatico_aplica(self.solicitud, nueva))

    def test_adopcion_solo_version_simulacion_timeline_unico_y_vigencia(self):
        nueva = self.publicar()
        anterior = ContractorApplication.objects.values().get(pk=self.solicitud.pk)
        self.assertFalse(ingreso_automatico_aplica(self.solicitud, nueva))
        adoptada = self.adoptar(nueva)
        esperado = {**anterior, 'version_politica_simulacion': VERSION_INGRESO}
        self.assertEqual(ContractorApplication.objects.values().get(pk=adoptada.pk), esperado)
        self.assertEqual(self.adoptar(nueva).pk, adoptada.pk)
        self.assertEqual(self.eventos_adopcion().count(), 1)
        evento = self.eventos_adopcion().get()
        self.assertFalse(evento.visible_cliente)
        self.assertEqual(evento.creado_por_id, self.staff.pk)
        self.assertEqual(evento.metadata['version_politica'], VERSION_INGRESO)
        self.assertTrue(ingreso_automatico_aplica(adoptada, nueva))
        self.assertEqual(obtener_politica_para_solicitud(adoptada).pk, nueva.pk)
        with patch('contractors.services.politica_ingreso_publicacion.timezone.localdate',
                   return_value=nueva.fecha_vigencia_desde - timedelta(days=1)):
            with self.assertRaises(ValidationError):
                obtener_politica_para_solicitud(adoptada)
        self.assertEqual(obtener_politica_score_activa().pk, self.politica.pk)

    def test_adopcion_rechaza_auditoria_job_reserva_error_y_sin_consentimiento(self):
        nueva = self.publicar()
        antes = ContractorApplication.objects.values().get(pk=self.solicitud.pk)
        auth = obtener_autorizacion_datacredito_vigente(self.solicitud)
        fingerprint = construir_fingerprint_datacredito(
            solicitud=self.solicitud, servicio='decisor', autorizacion=auth,
            configuracion=obtener_configuracion_datacredito(),
        )
        for bloqueo in ('auditoria', 'job', 'reserva', 'error', 'consentimiento'):
            with self.subTest(bloqueo=bloqueo), transaction.atomic():
                if bloqueo == 'auditoria':
                    PredecisionPrestadorAudit.objects.create(
                        solicitud=self.solicitud, version_datos='a' * 64,
                        clave_idempotencia='b' * 64, version_politica=self.politica.version_politica,
                        iniciada_en=timezone.now(),
                    )
                elif bloqueo == 'job':
                    EvaluacionPrestadorJob.objects.create(
                        solicitud=self.solicitud, actor=self.staff, clave_contexto='c' * 64,
                        proxima_publicacion=timezone.now(),
                    )
                elif bloqueo in ('reserva', 'error'):
                    ConsultaDatacreditoSnapshot.objects.create(
                        ambiente='uat', servicio='decisor', documento_hash='d' * 64,
                        documento_enmascarado='***0099', fingerprint=fingerprint,
                        estado='EN_PROCESO' if bloqueo == 'reserva' else 'ERROR_PERMANENTE',
                        consultado_en=timezone.now(), vigente_hasta=timezone.now() + timedelta(days=1),
                        autorizacion_referencia='autorizacion-incompatible', creado_por=self.staff,
                    )
                else:
                    self.solicitud.autorizaciones_datacredito.all().delete()
                with self.assertRaises(ValidationError):
                    self.adoptar(nueva)
                self.assertEqual(ContractorApplication.objects.values().get(pk=self.solicitud.pk), antes)
                self.assertFalse(self.eventos_adopcion().exists())
                transaction.set_rollback(True)

    def test_permisos_publicacion_y_adopcion_exigen_actor_staff_y_ambos_permisos(self):
        nueva = self.publicar()
        actor = get_user_model().objects.create_user(username='operador-sintetico', is_staff=True)
        for candidato in (None, self.usuario, actor):
            with self.subTest(actor=getattr(candidato, 'pk', None)):
                with self.assertRaises(PermissionDenied):
                    self.publicar(actor=candidato)
                with self.assertRaises(PermissionDenied):
                    self.adoptar(nueva, actor=candidato)
        actor.user_permissions.add(Permission.objects.get(
            content_type__app_label='contractors', codename='can_activate_contractor_score_policy',
        ))
        actor = get_user_model().objects.get(pk=actor.pk)
        self.assertEqual(self.publicar(actor=actor).pk, nueva.pk)
        with self.assertRaises(PermissionDenied):
            self.adoptar(nueva, actor=actor)
        actor.user_permissions.add(Permission.objects.get(
            content_type__app_label='contractors', codename='can_evaluate_contractor_application',
        ))
        actor = get_user_model().objects.get(pk=actor.pk)
        self.assertEqual(self.adoptar(nueva, actor=actor).pk, self.solicitud.pk)

    def test_sin_manual_solo_adoptada_resuelve_25_y_bloquea_gate_originacion(self):
        self.invalidar_manual()
        self.assertIsNone(resolver_ingreso_evaluable(self.solicitud, self.politica))
        nueva = self.publicar()
        self.assertIsNone(resolver_ingreso_evaluable(self.solicitud, nueva))
        self.solicitud = self.adoptar(nueva)
        ingreso = resolver_ingreso_evaluable(self.solicitud, nueva)
        self.assertIsNotNone(ingreso)
        self.assertEqual(ingreso.monto, Decimal('7500000.00'))
        self.assertEqual(ingreso.fuente, 'CONTRATO_ESTIMADO')
        self.assertEqual(ingreso.evidencia['descuento_porcentaje'], '25.00')
        self.politica = nueva
        auditoria = self.evaluar()
        with self.assertRaises(ValidationError) as rechazo:
            crear_o_reutilizar_aprobacion_interna(auditoria, actor=self.staff)
        self.assertEqual(rechazo.exception.code, 'ingreso_documental_requerido')
        with self.assertRaises(ValidationError) as rechazo:
            exigir_ingreso_documental_para_originacion(self.solicitud)
        self.assertEqual(rechazo.exception.code, 'ingreso_documental_requerido')
        self.assertFalse(self.solicitud.aprobaciones_internas.exists())
        self.assert_sin_originacion()

    def test_cli_dry_run_sin_sql_escritura_y_aplicar_explicito_sin_pii(self):
        comando = 'contractors.management.commands.gestionar_politica_ingreso.'
        base = ['--actor-id', str(self.staff.pk), '--motivo', 'Motivo privado sintetico']
        publicar = ['publicar', *base, '--origen-id', str(self.politica.pk),
                    '--vigente-desde', timezone.localdate().isoformat()]
        salida = StringIO()
        with patch(comando + 'publicar_politica_ingreso') as servicio, CaptureQueriesContext(connection) as queries:
            call_command('gestionar_politica_ingreso', *publicar, stdout=salida)
        servicio.assert_not_called()
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
        self.assertIn('estado=DRY_RUN', salida.getvalue())
        call_command('gestionar_politica_ingreso', *publicar, '--aplicar', stdout=salida)
        nueva = ConfiguracionScorePrestador.objects.get(version=VERSION_INGRESO)
        adoptar = ['adoptar', *base, '--solicitud-id', str(self.solicitud.pk),
                   '--politica-id', str(nueva.pk)]
        with patch(comando + 'adoptar_politica_ingreso') as servicio, CaptureQueriesContext(connection) as queries:
            call_command('gestionar_politica_ingreso', *adoptar, stdout=salida)
        servicio.assert_not_called()
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
        self.assertFalse(self.eventos_adopcion().exists())
        call_command('gestionar_politica_ingreso', *adoptar, '--aplicar', stdout=salida)
        self.assertEqual(self.eventos_adopcion().count(), 1)
        self.assertFalse(nueva.activa)
        with self.assertRaises(CommandError):
            call_command('gestionar_politica_ingreso', 'publicar', *base,
                         '--origen-id', str(self.politica.pk), stdout=salida)
        for pii in (self.staff.username, self.staff.email, self.solicitud.numero_documento,
                    self.solicitud.nombres, 'Motivo privado sintetico'):
            self.assertNotIn(pii, salida.getvalue())

    def test_reconfirmacion_identica_y_cambio_legitimo_conservan_adopcion(self):
        from contractors.services.confirmacion_simulacion import confirmar_condiciones_simulacion
        nueva = self.publicar()
        self.solicitud = self.adoptar(nueva)
        anterior = ContractorApplication.objects.values().get(pk=self.solicitud.pk)
        resultado = confirmar_condiciones_simulacion(
            solicitud=self.solicitud, usuario=self.usuario, monto='3000000', plazo_meses=6,
        )
        self.assertTrue(resultado.reutilizada)
        self.assertEqual(ContractorApplication.objects.values().get(pk=self.solicitud.pk), anterior)
        confirmar_condiciones_simulacion(
            solicitud=self.solicitud, usuario=self.usuario, monto='2000000', plazo_meses=5,
        )
        self.solicitud.refresh_from_db()
        self.assertTrue(ingreso_automatico_aplica(self.solicitud, nueva))
        self.assertEqual(self.solicitud.version_politica_simulacion, VERSION_INGRESO)
        self.assertEqual(self.eventos_adopcion().count(), 1)

    def test_capacidad_legacy_completa_preserva_bruto_con_y_sin_manual(self):
        normalizado = ResultadoNormalizadoDatacreditoPrestador(cuota_mensual_total='0')
        original = componente_capacidad_dual(self.solicitud, normalizado, self.politica)
        componente, variables = original
        self.assertTrue(componente.disponible)
        self.assertEqual(variables['ingreso_contractual_estimado'], Decimal('10000000.00'))
        self.assertEqual(variables['ingreso_disponible'], Decimal('10000000.00'))
        self.assertNotIn('ingreso_evaluable', variables)
        # Formula de componente_capacidad_dual anterior, revisada en git show HEAD.
        relacion = (variables['cuota_solicitada'] / Decimal('10000000')).quantize(
            Decimal('.0001'), rounding=ROUND_HALF_UP,
        )
        esperado = max(Decimal('0'), (1 - relacion / self.politica.cuota_ingreso_maxima) * 1000)
        self.assertEqual(componente.score, esperado.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        self.assertEqual(variables['relacion_cuota_ingreso'], relacion)
        self.invalidar_manual()
        self.assertEqual(componente_capacidad_dual(self.solicitud, normalizado, self.politica), original)
        self.assertIsNone(resolver_ingreso_evaluable(self.solicitud, self.politica))
        self.publicar()
        self.assertEqual(componente_capacidad_dual(self.solicitud, normalizado, self.politica), original)
        self.assertIsNone(resolver_ingreso_evaluable(self.solicitud, self.politica))
