from datetime import date

from django.test import SimpleTestCase

from diagnosticos.matriz_facilidat import enriquecer_diccionario
from gestion_creditos.services.facilidat_homologacion import (
    PERFIL, campos_preparacion, escenarios_primera_entrega, fecha_corte_facilidat,
    novedad_preparacion, ventana_operativa_mora,
)
from gestion_creditos.services.facilidat_xlsx import (
    ENCABEZADOS_FACILIDAT, xlsx_cartera_interna, xlsx_preparacion_facilidat,
)
from gestion_creditos.tests.test_facilidat_xlsx import leer


CORTE = date(2026, 10, 31)


def fila(numero, categoria, **datos):
    return {
        'credito_id_interno': numero, 'numero_obligacion_interno': f'CR{numero}',
        'producto': 'LIBRANZA', 'categoria': categoria, 'desembolso_acreditado': True,
        'saldo_pendiente': '800.00', 'capital_pendiente': '700.00', 'capital_financiado': '1000.00',
        'monto_aprobado': '890.00', 'cuota_pactada': '125.00', 'saldo_vencido': '0.00',
        'cuotas_pactadas': 10, 'cuotas_pagadas': 1, 'cuotas_vencidas': 0,
        'fecha_desembolso_registrada': '2026-06-30', 'fecha_vencimiento_final': '2027-04-29',
        **datos,
    }


class FacultadesFacilidatTest(SimpleTestCase):
    def test_no_mezcla_importes_y_no_inventa_ausencia_de_pago(self):
        r = fila(1, 'AL_DIA', fecha_cuota_mes_corte='2026-10-29')
        datos = campos_preparacion(r, CORTE)
        self.assertEqual([datos[k] for k in ('S', 'T', 'U', 'V', 'W', 'AC', 'AD')],
                         [None, None, '0', '125.00', '0.00', '20261029', None])
        datos = campos_preparacion(dict(r, capital_original_desembolsado_acreditado='845.00',
            saldo_capital_original_acreditado='620.25', ultima_aplicacion=None), CORTE)
        self.assertEqual((datos['S'], datos['T'], datos['AD']), ('845.00', '620.25', '0'))
        self.assertIsNone(campos_preparacion(dict(r, producto='PRESTADORES'), CORTE)['U'])

    def test_mora_usa_cuota_mas_antigua_sin_inferir_codigo_o_cargos(self):
        r = fila(1, 'VENCIDA', dias_mora=59, saldo_vencido='250.00', cuotas_vencidas=2,
                 ultima_aplicacion='2026-08-17')
        datos = campos_preparacion(r, CORTE)
        self.assertEqual([datos[k] for k in ('J', 'W', 'Z', 'AC', 'AD')],
                         [None, '250.00', '2', '20260902', '20260817'])

    def test_cierre_de_actualizacion_no_se_incluye_en_primera_entrega(self):
        r = fila(1, 'PAGADA', saldo_pendiente='0')
        self.assertIsNone(novedad_preparacion(r, actualizacion=True))
        r['reporte_previo_acreditado'] = True
        self.assertIsNone(novedad_preparacion(r))
        self.assertEqual(novedad_preparacion(r, actualizacion=True), '05')
        self.assertEqual(escenarios_primera_entrega([r]), ([], [], 0))

    def test_ventana_operativa_no_constituye_una_banda_de_mora(self):
        self.assertEqual([ventana_operativa_mora(v) for v in (None, 29, 30, 539, 540)], [
            'EDAD_NO_ACREDITADA', 'MENOS_DE_30_DIAS', 'EDAD_OPERATIVA_30_A_539',
            'EDAD_OPERATIVA_30_A_539', 'FUERA_DE_VENTANA_OPERATIVA_540',
        ])
        self.assertEqual(fecha_corte_facilidat('2028-02-29'), date(2028, 2, 29))
        with self.assertRaisesRegex(ValueError, 'ULTIMO_DIA'):
            fecha_corte_facilidat('2026-10-30')

    def test_archivos_de_escenarios_no_seleccionan_negativos(self):
        cierre = {'corte': CORTE.isoformat(), 'filas': [
            fila(1, 'AL_DIA'), fila(2, 'AL_DIA'), fila(3, 'PAGADA', saldo_pendiente='0'),
            fila(4, 'VENCIDA', dias_mora=30), fila(5, 'VENCIDA', dias_mora=65),
            fila(6, 'NO_CONCILIADA', incidencia_financiera='SIN_TRAZA'),
            fila(7, 'AL_DIA', desembolso_acreditado=False),
        ]}
        for caso in ('positiva', 'mixta'):
            archivo = xlsx_preparacion_facilidat(cierre, 'hash', escenario=caso)
            hoja = leer(archivo, 'Carga')
            self.assertEqual(hoja.calculate_dimension(), 'A1:AJ3')
            self.assertEqual([c.value for c in hoja[1]], list(ENCABEZADOS_FACILIDAT))
            self.assertEqual((hoja['C2'].value, hoja['C3'].value), ('CR1', 'CR2'))
            self.assertEqual((hoja['J2'].value, hoja['J3'].value), ('01', '01'))
            ctl = dict(leer(archivo, 'Control').values)
            self.assertEqual((ctl['Tope teorico de mora 2:1'], ctl['Negativas seleccionadas']), ('1', '0'))
        mora = leer(archivo, 'Revision mora')
        self.assertEqual(tuple(mora['F2':'F3'][i][0].value for i in range(2)),
                         ('NO SELECCIONADA', 'NO SELECCIONADA'))
        hoja = leer(xlsx_preparacion_facilidat(cierre, 'hash'), 'Carga')
        self.assertEqual(hoja.calculate_dimension(), 'A1:AJ6')
        self.assertTrue(all(hoja[f'{col}2'].value is None for col in ('E', 'I', 'M', 'N', 'Q', 'R')))
        self.assertEqual(leer(xlsx_cartera_interna(cierre, 'hash'), 'Cartera').max_row, 8)

    def test_documentos_separan_letras_sme_y_autorrelleno_empresas(self):
        d = enriquecer_diccionario({'campos': [{'columna': c} for c in ('R', 'X', 'T')]})
        self.assertEqual(d['campos'][0]['grupo'], 'AUTOMATICO_EMPRESAS_PENDIENTE_PRUEBA_SME')
        self.assertEqual(d['campos'][1]['grupo'], 'SUMINISTRADO_POR_LA_FUENTE')
        self.assertEqual(d['campos'][2]['fuentes'][1]['paginas'], (1,))
        self.assertIsNone(PERFIL['tipologia_asignada'])
        self.assertEqual(PERFIL['tipologia_propuesta_libranza'], 'LBD')
