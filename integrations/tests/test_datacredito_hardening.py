from copy import deepcopy

from django.test import SimpleTestCase, override_settings

from contractors.datacredito.adapter import _proyectar_resultado_allowlist
from integrations.datacredito.dto import EntradaHistorialCredito, EntradaMiDecisor
from integrations.datacredito.exceptions import DatacreditoConfigError
from integrations.datacredito.historial_client import _construir_payload_historial
from integrations.datacredito.identificacion import homologar_tipo_identificacion
from integrations.datacredito.normalizadores import normalizar_historial_credito
from integrations.datacredito.settings import obtener_configuracion_datacredito
from integrations.tests import test_hdcplus_normalizacion_dual as hdc_fixtures


@override_settings(**hdc_fixtures.CONFIGURACION_HDC)
class IdentificacionDatacreditoTest(SimpleTestCase):
    def test_tipos_compartidos_y_payloads(self):
        for tipo, codigo in (('CC', '1'), ('CE', '4')):
            with self.subTest(tipo=tipo):
                self.assertEqual(homologar_tipo_identificacion(tipo), codigo)
                decisor = EntradaMiDecisor(tipo_identificacion=tipo,
                    numero_identificacion='123456789', apellido_razon_social='PRUEBA')
                self.assertEqual(decisor.como_payload()['tipoIdentificacion'], codigo)
                historial = EntradaHistorialCredito(tipo_identificacion=tipo,
                    numero_identificacion='123456789', apellido='PRUEBA')
                payload = _construir_payload_historial(historial, obtener_configuracion_datacredito())
                self.assertEqual(payload['identifyingUser']['person']['personId']['personIdType'], int(codigo))

    def test_vacio_y_no_soportado_fallan_antes_del_http(self):
        for tipo in ('', None, 'DESCONOCIDO', '6'):
            with self.subTest(tipo=tipo), self.assertRaises(DatacreditoConfigError):
                homologar_tipo_identificacion(tipo)

    def test_nit_y_pasaporte_conservan_contrato_compartido(self):
        self.assertEqual(homologar_tipo_identificacion('NIT'), '2')
        self.assertEqual(homologar_tipo_identificacion('PP'), '5')


class CalidadHDCTest(SimpleTestCase):
    @staticmethod
    def payload():
        return deepcopy(hdc_fixtures.HDCPlusNormalizacionDualTest._payload_hdc())

    def test_ausencia_no_es_lista_vacia_sin_deuda(self):
        payload = self.payload()
        del payload['ReportHDCplus']['liabilities']
        incompleto = normalizar_historial_credito(payload)
        self.assertIsNone(incompleto.valor_cuota_total)
        self.assertTrue(incompleto.requiere_revision_manual)
        payload['ReportHDCplus']['liabilities'] = []
        sin_deuda = normalizar_historial_credito(payload)
        self.assertEqual(sin_deuda.valor_cuota_total, 0)
        self.assertTrue(sin_deuda.metadata_segura['hdc_resumen']['carga_mensual_completa'])

    def test_importe_ausente_invalido_negativo_o_no_finito_no_es_cero(self):
        for importe in (None, '', 'incorrecto', '-1', 'NaN', 'Infinity'):
            with self.subTest(importe=importe):
                payload = self.payload()
                payload['ReportHDCplus']['liabilities'][0]['values'][0]['valueMonthlyPayment'] = importe
                resultado = normalizar_historial_credito(payload)
                self.assertIsNone(resultado.valor_cuota_total)
                dto = _proyectar_resultado_allowlist(resultado, servicio='historial')
                self.assertFalse(dto.carga_mensual_completa)
                self.assertIsNone(dto.cuota_mensual_total)
                self.assertEqual(dto.obligaciones_incompletas, 1)
                self.assertTrue(dto.version_normalizador)

    def test_cerrada_no_suma_cuota_aunque_conserve_valor_historico(self):
        payload = self.payload()
        payload['ReportHDCplus']['liabilities'][0]['status']['account'] = {
            'businessAccountStatus': '03', 'businessAccountStatusDesc': 'PAGO TOTAL',
        }
        resultado = normalizar_historial_credito(payload)
        self.assertEqual(resultado.valor_cuota_total, 200000)
        self.assertEqual(resultado.creditos_vigentes, 1)
        self.assertEqual(resultado.metadata_segura['hdc_resumen']['liabilities_cerradas'], 1)

    def test_vigentes_suman_y_estado_desconocido_no_se_infiere(self):
        payload = self.payload()
        self.assertEqual(normalizar_historial_credito(payload).valor_cuota_total, 450000)
        payload['ReportHDCplus']['liabilities'][0]['status']['account'] = {}
        resultado = normalizar_historial_credito(payload)
        self.assertIsNone(resultado.valor_cuota_total)
        self.assertEqual(resultado.metadata_segura['hdc_resumen']['liabilities_estado_desconocido'], 1)
        self.assertEqual(resultado.creditos_cerrados, 0)

    def test_estructura_invalida_y_estado_no_reportado_no_asumen_carga_cero(self):
        for cambio in ('registro', 'valores', 'estado'):
            with self.subTest(cambio=cambio):
                payload = self.payload()
                if cambio == 'registro':
                    payload['ReportHDCplus']['liabilities'][0] = 'no-es-obligacion'
                elif cambio == 'valores':
                    payload['ReportHDCplus']['liabilities'][0]['values'] = ['invalido']
                else:
                    payload['ReportHDCplus']['liabilities'][0]['status']['account'] = {
                        'businessAccountStatus': '00', 'businessAccountStatusDesc': 'AL DIA',
                    }
                resultado = normalizar_historial_credito(payload)
                self.assertIsNone(resultado.valor_cuota_total)
                self.assertFalse(resultado.metadata_segura['hdc_resumen']['carga_mensual_completa'])
