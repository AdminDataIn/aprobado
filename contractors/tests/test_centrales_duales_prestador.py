from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from contractors.datacredito.dto import (
    ResultadoCentralesPrestador,
    ResultadoConsultaDatacreditoPrestador,
    ResultadoNormalizadoDatacreditoPrestador,
)
from contractors.models import (
    ConfiguracionScorePrestador,
    ContractorApplication,
    ContractorApplicationDocument,
    PredecisionPrestadorAudit,
)
from contractors.score.motor import evaluar_score_prestador
from contractors.services.centrales_riesgo import (
    ESTADO_COMPLETA,
    ESTADO_REVISION_MANUAL,
    obtener_evaluacion_centrales_prestador,
)
from contractors.services.evaluacion_formal import evaluar_solicitud_prestador
from contractors.services.predecision import evaluar_predecision_formal_prestador
from contractors.views_admin import _construir_detalle_auditoria
from gestion_creditos.models import Credito, CreditoLibranza, Empresa
from integrations.models import ConsultaDatacreditoSnapshot


class CentralesDualesPrestadorTest(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username='dual-prestador', password='test-password'
        )
        self.staff = get_user_model().objects.create_superuser(
            username='dual-staff', email='dual@example.com', password='test-password'
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Dual', convenio_activo=True
        )
        call_command('configurar_politica_prestadores_demo', stdout=StringIO())
        historica = ConfiguracionScorePrestador.objects.get(
            version='prestadores-score-demo-v1'
        )
        historica.activa = False
        historica.save(update_fields=['activa', 'updated_at'])
        call_command(
            'configurar_politica_prestadores_demo_v2',
            stdout=StringIO(),
        )
        self.politica = ConfiguracionScorePrestador.objects.get(
            version='prestadores-score-demo-v2'
        )
        self.assertFalse(self.politica.activa)
        self.politica.activa = True
        self.politica.full_clean()
        self.politica.save(update_fields=['activa', 'updated_at'])
        self.solicitud = self._crear_solicitud()
        self.snapshot_decisor = self._crear_snapshot('decisor', 'a')
        self.snapshot_hdc = self._crear_snapshot('historial', 'b')

    def test_politica_v2_suma_uno_y_v1_conserva_pesos(self):
        total_v2 = sum((
            self.politica.peso_midecisor,
            self.politica.peso_hdcplus,
            self.politica.peso_capacidad,
            self.politica.peso_comportamiento,
            self.politica.peso_riesgo,
            self.politica.peso_referencias,
        ), Decimal('0'))
        historica = ConfiguracionScorePrestador.objects.get(
            version='prestadores-score-demo-v1'
        )
        self.assertEqual(total_v2, Decimal('1.00000'))
        self.assertEqual(self.politica.peso_datacredito, Decimal('0.00000'))
        self.assertEqual(self.politica.peso_midecisor, Decimal('0.45000'))
        self.assertEqual(self.politica.peso_hdcplus, Decimal('0.00000'))
        self.assertEqual(self.politica.peso_capacidad, Decimal('0.30000'))
        self.assertEqual(self.politica.peso_comportamiento, Decimal('0.08000'))
        self.assertEqual(self.politica.peso_riesgo, Decimal('0.12000'))
        self.assertEqual(self.politica.peso_referencias, Decimal('0.05000'))
        self.assertTrue(self.politica.requiere_midecisor)
        self.assertTrue(self.politica.requiere_hdcplus)
        self.assertFalse(self.politica.permite_evaluar_sin_hdc)
        self.assertEqual(historica.peso_datacredito, Decimal('0.45000'))
        self.assertIsNone(historica.peso_hdcplus)
        self.assertFalse(historica.requiere_hdcplus)

    def test_hdc_es_informativo_y_no_genera_score_sintetico(self):
        casos = (
            self._hdc(mora_severa=True),
            self._hdc(mora_actual=True),
            self._hdc(mora_actual=False),
            self._hdc(obligaciones=8),
            self._hdc(consultas=99),
        )
        for consulta in casos:
            with self.subTest(normalizado=consulta.resultado_normalizado):
                score = evaluar_score_prestador(
                    self.solicitud,
                    self.politica,
                    self._centrales(historial=consulta),
                )
                componente = next(
                    item for item in score.componentes if item.nombre == 'hdcplus'
                )
                self.assertTrue(componente.disponible)
                self.assertIsNone(componente.score)
                self.assertEqual(componente.peso_configurado, Decimal('0.00000'))
                self.assertEqual(componente.peso_aplicado, Decimal('0.00000'))

    def test_midecisor_es_score_crediticio_y_referencias_se_redistribuyen(self):
        score = evaluar_score_prestador(
            self.solicitud,
            self.politica,
            self._centrales(),
        )
        componentes = {item.nombre: item for item in score.componentes}
        midecisor = componentes['datacredito_score']
        self.assertEqual(midecisor.score, Decimal('900'))
        self.assertEqual(midecisor.valor_original, 900)
        self.assertEqual(midecisor.peso_configurado, Decimal('0.45000'))
        self.assertEqual(midecisor.fuente, 'midecisor_normalizado')
        self.assertTrue(score.variables_calculadas['redistribucion_aplicada'])
        self.assertEqual(
            score.variables_calculadas['motivo_redistribucion'],
            'referencias_no_verificadas',
        )
        self.assertIn(
            'Peso de referencias redistribuido porque no existen referencias verificadas.',
            score.alertas,
        )
        self.assertAlmostEqual(
            sum(
                item.peso_aplicado
                for item in score.componentes
                if item.score is not None
            ),
            Decimal('1.00000'),
            places=5,
        )
        esperado = sum(
            (
                item.score * item.peso_aplicado
                for item in score.componentes
                if item.score is not None
            ),
            Decimal('0'),
        ).quantize(Decimal('0.01'))
        self.assertEqual(score.score_final, esperado)

    @patch('contractors.services.centrales_riesgo.obtener_evaluacion_datacredito_prestador')
    def test_orquestador_consulta_ambas_fuentes_explicitas(self, consulta):
        consulta.side_effect = [self._decisor(), self._hdc()]

        resultado = obtener_evaluacion_centrales_prestador(
            self.solicitud,
            politica=self.politica,
            solicitado_por=self.staff,
        )

        self.assertTrue(resultado.completa)
        self.assertEqual(resultado.estado_global, ESTADO_COMPLETA)
        self.assertEqual(
            [item.kwargs['servicio'] for item in consulta.call_args_list],
            ['decisor', 'historial'],
        )
        self.assertEqual(
            [item.kwargs['vigencia_dias'] for item in consulta.call_args_list],
            [30, 30],
        )

    @patch('contractors.services.centrales_riesgo.obtener_evaluacion_datacredito_prestador')
    def test_hdc_error_transitorio_requiere_revision_y_no_preaprueba(self, consulta):
        consulta.side_effect = [
            self._decisor(),
            ResultadoConsultaDatacreditoPrestador(
                estado='ERROR_TRANSITORIO',
                servicio='historial',
                error_codigo='timeout_proveedor',
            ),
        ]
        centrales = obtener_evaluacion_centrales_prestador(
            self.solicitud,
            politica=self.politica,
            solicitado_por=self.staff,
        )
        with self._parches_autorizacion():
            predecision = evaluar_predecision_formal_prestador(
                solicitud=self.solicitud,
                politica=self.politica,
                centrales=centrales,
            )
        self.assertEqual(centrales.estado_global, ESTADO_REVISION_MANUAL)
        self.assertEqual(predecision.resultado, 'REQUIERE_REVISION_MANUAL')
        self.assertFalse(predecision.eligible)

    def test_fuente_hdc_obligatoria_ausente_no_redistribuye_pesos(self):
        hdc_sin_resultado = ResultadoConsultaDatacreditoPrestador(
            estado='ERROR_TRANSITORIO',
            servicio='historial',
            error_codigo='timeout',
        )
        score = evaluar_score_prestador(
            self.solicitud,
            self.politica,
            self._centrales(historial=hdc_sin_resultado),
        )
        self.assertIsNone(score.score_final)
        self.assertFalse(score.variables_calculadas['redistribucion_aplicada'])
        self.assertIn('hdcplus', score.variables_calculadas['componentes_faltantes'])
        midecisor = next(
            item for item in score.componentes
            if item.nombre == 'datacredito_score'
        )
        self.assertEqual(
            midecisor.peso_aplicado,
            midecisor.peso_configurado,
        )

    def test_mora_severa_en_cualquiera_de_las_fuentes_no_es_hard_rule(self):
        for fuente in ('decisor', 'historial'):
            with self.subTest(fuente=fuente):
                centrales = self._centrales(
                    decisor=self._decisor(mora_severa=fuente == 'decisor'),
                    historial=self._hdc(mora_severa=fuente == 'historial'),
                )
                with self._parches_autorizacion():
                    resultado = evaluar_predecision_formal_prestador(
                        solicitud=self.solicitud,
                        politica=self.politica,
                        centrales=centrales,
                    )
                self.assertNotIn(
                    'mora_severa',
                    ' '.join(resultado.bloqueos).lower(),
                )

    def test_mora_leve_hdc_no_activa_revision_automatica(self):
        centrales = self._centrales(historial=self._hdc(mora_actual=True))
        with self._parches_autorizacion():
            resultado = evaluar_predecision_formal_prestador(
                solicitud=self.solicitud,
                politica=self.politica,
                centrales=centrales,
            )
        self.assertNotIn('mora_actual', ' '.join(resultado.razones).lower())
        self.assertNotIn('mora_actual', ' '.join(resultado.bloqueos).lower())

    def test_obligaciones_hdc_entran_en_capacidad_sin_bloquear_por_existir(self):
        centrales = self._centrales(
            historial=self._hdc(cuota='300000', obligaciones=4)
        )
        with self._parches_autorizacion():
            score = evaluar_score_prestador(
                self.solicitud,
                self.politica,
                centrales,
            )
        self.assertEqual(
            score.variables_calculadas['obligaciones_mensuales'],
            Decimal('300000'),
        )
        self.assertEqual(
            score.variables_calculadas['cuota_total_con_nueva_solicitud'],
            score.variables_calculadas['cuota_solicitada'] + Decimal('300000'),
        )
        self.assertNotIn('obligaciones', ' '.join(score.bloqueos).lower())

    def test_carga_total_sobre_limite_no_es_preaprobada(self):
        centrales = self._centrales(historial=self._hdc(cuota='1900000'))
        with self._parches_autorizacion():
            resultado = evaluar_predecision_formal_prestador(
                solicitud=self.solicitud,
                politica=self.politica,
                centrales=centrales,
            )
        self.assertNotEqual(resultado.resultado, 'PREAPROBADO_READ_ONLY')

    @patch('contractors.services.evaluacion_formal.obtener_evaluacion_centrales_prestador')
    def test_evaluacion_formal_audita_ambos_snapshots_y_reutiliza_auditoria(self, obtener):
        obtener.return_value = self._centrales()
        creditos = Credito.objects.count()
        libranzas = CreditoLibranza.objects.count()
        with self._parches_autorizacion():
            primero = evaluar_solicitud_prestador(
                self.solicitud,
                solicitado_por=self.staff,
            )
            segundo = evaluar_solicitud_prestador(
                self.solicitud,
                solicitado_por=self.staff,
            )
        auditoria = primero.auditoria
        auditoria.refresh_from_db()
        self.assertEqual(auditoria.snapshot_midecisor_id, self.snapshot_decisor.id)
        self.assertEqual(auditoria.snapshot_hdcplus_id, self.snapshot_hdc.id)
        self.assertEqual(auditoria.estado_midecisor, 'EXITOSO')
        self.assertEqual(auditoria.estado_hdcplus, 'EXITOSO')
        self.assertTrue(auditoria.evaluacion_centrales_completa)
        self.assertIn('centrales', auditoria.snapshot_salida)
        self.assertNotIn('raw', str(auditoria.snapshot_salida).lower())
        score_snapshot = auditoria.snapshot_salida['score_resultado']
        componentes = {
            item['nombre']: item for item in score_snapshot['componentes']
        }
        self.assertEqual(
            componentes['datacredito_score']['peso_configurado'],
            '0.45000',
        )
        self.assertIsNotNone(
            componentes['datacredito_score']['aporte_ponderado']
        )
        self.assertEqual(componentes['hdcplus']['peso_configurado'], '0.00000')
        self.assertIsNone(componentes['hdcplus']['score_normalizado'])
        self.assertTrue(
            score_snapshot['variables_calculadas']['redistribucion_aplicada']
        )
        detalle_staff = _construir_detalle_auditoria(auditoria, True)
        self.assertEqual(detalle_staff['centrales']['decisor']['score_externo'], 900)
        self.assertEqual(
            detalle_staff['centrales']['historial']['obligaciones_vigentes'], 2
        )
        self.assertTrue(segundo.reutilizada)
        self.assertEqual(obtener.call_count, 1)
        self.assertEqual(Credito.objects.count(), creditos)
        self.assertEqual(CreditoLibranza.objects.count(), libranzas)

        self.client.force_login(self.usuario)
        respuesta_publica = self.client.get(
            '/mi-credito/',
            HTTP_HOST='contratistas.localhost',
        )
        self.assertNotContains(respuesta_publica, 'HDCPlus')
        self.assertNotContains(respuesta_publica, 'Score externo')

    def _crear_solicitud(self):
        financiera = self.politica.configuracion_financiera
        solicitud = ContractorApplication.objects.create(
            usuario=self.usuario,
            empresa=self.empresa,
            tipo_documento='CC',
            numero_documento='900000001',
            nombres='Persona',
            apellidos='Demo',
            celular='3000000000',
            correo='persona@example.com',
            direccion='Direccion de prueba',
            cargo='Consultoria',
            fecha_inicio_contrato=timezone.localdate(),
            fecha_fin_contrato=timezone.localdate() + timedelta(days=300),
            valor_total_contrato=Decimal('60000000'),
            valor_pagado_contrato=Decimal('2000000'),
            valor_pendiente_cobrar=Decimal('58000000'),
            forma_pago=ContractorApplication.FormaPago.MENSUAL,
            valor_mensual_contractual=Decimal('6000000'),
            monto_solicitado=Decimal('3000000'),
            plazo_meses=6,
            monto_simulado=Decimal('3000000'),
            plazo_simulado_meses=6,
            version_configuracion_financiera_simulacion=financiera.version,
            version_politica_simulacion=self.politica.version_politica,
            tasa_mensual_simulacion=financiera.tasa_mensual,
            monto_maximo_configuracion_simulacion=financiera.monto_maximo,
            plazo_maximo_configuracion_simulacion=financiera.plazo_maximo_meses,
            simulada_en=timezone.now(),
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
                metadata_captura={'source': 'capture'} if tipo.startswith('CEDULA') else {},
            )
        return solicitud

    def _crear_snapshot(self, servicio, caracter):
        return ConsultaDatacreditoSnapshot.objects.create(
            ambiente='uat',
            servicio=servicio,
            documento_hash=caracter * 64,
            documento_enmascarado='*****0001',
            fingerprint=caracter * 64,
            estado='EXITOSO',
            resultado_normalizado={},
            codigo_http=200,
            codigo_funcional='13',
            consultado_en=timezone.now(),
            vigente_hasta=timezone.now() + timedelta(days=30),
            autorizacion_referencia='autorizacion-test',
        )

    def _decisor(self, *, mora_severa=False):
        return ResultadoConsultaDatacreditoPrestador(
            estado='EXITOSO',
            servicio='decisor',
            snapshot_id=str(self.snapshot_decisor.id),
            consultado_en=timezone.now().isoformat(),
            resultado_normalizado=ResultadoNormalizadoDatacreditoPrestador(
                score_externo=900,
                mora_actual=bool(mora_severa),
                mora_severa=mora_severa,
                servicio_fuente='decisor',
            ),
        )

    def _hdc(
        self, *, mora_severa=False, mora_actual=False, cuota='100000',
        obligaciones=2, consultas=1,
    ):
        return ResultadoConsultaDatacreditoPrestador(
            estado='EXITOSO',
            servicio='historial',
            snapshot_id=str(self.snapshot_hdc.id),
            consultado_en=timezone.now().isoformat(),
            resultado_normalizado=ResultadoNormalizadoDatacreditoPrestador(
                obligaciones_vigentes=obligaciones,
                obligaciones_en_mora=1 if mora_actual or mora_severa else 0,
                saldo_total='5000000',
                saldo_mora='50000' if mora_actual or mora_severa else '0',
                cuota_mensual_total=cuota,
                mora_actual=mora_actual or mora_severa,
                mora_severa=mora_severa,
                mora_maxima_dias=100 if mora_severa else (30 if mora_actual else 0),
                consultas_recientes=consultas,
                servicio_fuente='historial',
            ),
        )

    def _centrales(self, *, decisor=None, historial=None):
        return ResultadoCentralesPrestador(
            decisor=decisor or self._decisor(),
            historial=historial or self._hdc(),
            estado_global=ESTADO_COMPLETA,
            completa=True,
            requiere_revision_manual=False,
            snapshot_ids={
                'decisor': str(self.snapshot_decisor.id),
                'historial': str(self.snapshot_hdc.id),
            },
        )

    @staticmethod
    def _parches_autorizacion():
        class Parches:
            def __enter__(self):
                self.predecision = patch(
                    'contractors.services.predecision.obtener_autorizacion_datacredito_vigente',
                    return_value=object(),
                )
                self.score = patch(
                    'contractors.score.componentes.obtener_autorizacion_datacredito_vigente',
                    return_value=object(),
                )
                self.predecision.start()
                self.score.start()

            def __exit__(self, exc_type, exc_value, traceback):
                self.score.stop()
                self.predecision.stop()

        return Parches()


class PoliticaDemoV2CommandTest(TestCase):
    def setUp(self):
        call_command('configurar_politica_prestadores_demo', stdout=StringIO())
        ConfiguracionScorePrestador.objects.filter(
            version='prestadores-score-demo-v1'
        ).update(activa=False)

    def test_comando_es_idempotente_corrige_v2_no_usada_y_no_la_activa(self):
        call_command('configurar_politica_prestadores_demo_v2', stdout=StringIO())
        ConfiguracionScorePrestador.objects.filter(
            version='prestadores-score-demo-v2'
        ).update(
            peso_midecisor=Decimal('0.30000'),
            peso_hdcplus=Decimal('0.25000'),
            peso_capacidad=Decimal('0.25000'),
            peso_comportamiento=Decimal('0.00000'),
            peso_riesgo=Decimal('0.15000'),
        )

        call_command('configurar_politica_prestadores_demo_v2', stdout=StringIO())

        politica = ConfiguracionScorePrestador.objects.get(
            version='prestadores-score-demo-v2'
        )
        self.assertFalse(politica.activa)
        self.assertEqual(politica.peso_midecisor, Decimal('0.45000'))
        self.assertEqual(politica.peso_hdcplus, Decimal('0.00000'))
        self.assertEqual(politica.peso_capacidad, Decimal('0.30000'))
        self.assertEqual(politica.peso_comportamiento, Decimal('0.08000'))
        self.assertEqual(politica.peso_riesgo, Decimal('0.12000'))
        self.assertEqual(
            ConfiguracionScorePrestador.objects.filter(
                version='prestadores-score-demo-v2'
            ).count(),
            1,
        )

    def test_v2_usada_permanece_inmutable_y_comando_crea_v3(self):
        call_command('configurar_politica_prestadores_demo_v2', stdout=StringIO())
        v2 = ConfiguracionScorePrestador.objects.get(
            version='prestadores-score-demo-v2'
        )
        usuario = get_user_model().objects.create_user(username='audit-v2')
        empresa = Empresa.objects.create(nombre='Empresa Auditada', convenio_activo=True)
        solicitud = ContractorApplication.objects.create(
            usuario=usuario,
            empresa=empresa,
            numero_documento='900000099',
            nombres='Persona',
            apellidos='Auditada',
            celular='3000000099',
            correo='auditada@example.com',
            direccion='Direccion auditada',
            cargo='Consultoria',
        )
        PredecisionPrestadorAudit.objects.create(
            solicitud=solicitud,
            version_datos='a' * 64,
            clave_idempotencia='b' * 64,
            estado_ejecucion=PredecisionPrestadorAudit.EstadoEjecucion.COMPLETADA,
            resultado=PredecisionPrestadorAudit.Resultado.NO_EVALUABLE,
            version_score=v2.version_score,
            version_politica=v2.version_politica,
            iniciada_en=timezone.now(),
            finalizada_en=timezone.now(),
        )

        call_command('configurar_politica_prestadores_demo_v2', stdout=StringIO())

        v2.refresh_from_db()
        v3 = ConfiguracionScorePrestador.objects.get(
            version='prestadores-score-demo-v3'
        )
        self.assertEqual(v2.version_politica, 'politica-prestadores-demo-v2')
        self.assertFalse(v3.activa)
        self.assertEqual(v3.peso_midecisor, Decimal('0.45000'))
        self.assertEqual(v3.peso_hdcplus, Decimal('0.00000'))

    def test_comando_rechaza_activacion_directa(self):
        with self.assertRaisesMessage(CommandError, 'no activa politicas DEMO'):
            call_command(
                'configurar_politica_prestadores_demo_v2',
                activar=True,
                stdout=StringIO(),
            )


class ComandoHDCPrestadorTest(TestCase):
    @override_settings(DATACREDITO_ENVIRONMENT='uat', DATACREDITO_REAL_ENABLED=True)
    @patch(
        'contractors.management.commands.probar_hdc_prestador.'
        'obtener_evaluacion_datacredito_prestador'
    )
    def test_sin_confirmacion_no_consume(self, consulta):
        salida = StringIO()
        call_command(
            'probar_hdc_prestador',
            solicitud_id=999,
            stdout=salida,
        )
        consulta.assert_not_called()
        self.assertIn('Consumo real no ejecutado', salida.getvalue())

    @override_settings(DATACREDITO_ENVIRONMENT='prod', DATACREDITO_REAL_ENABLED=True)
    def test_comando_no_permite_produccion(self):
        with self.assertRaisesMessage(CommandError, 'solo esta permitido en UAT'):
            call_command(
                'probar_hdc_prestador',
                solicitud_id=999,
                confirmar_consumo_real=True,
                stdout=StringIO(),
            )
