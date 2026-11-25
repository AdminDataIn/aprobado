from hashlib import sha256
from io import BytesIO
import json
from unittest import mock
from zipfile import ZipFile

from django.test import SimpleTestCase, TransactionTestCase
from django.urls import reverse
from openpyxl import load_workbook

from diagnosticos.exportar_facilidat_local import exportar_pendientes
from gestion_creditos.models import CierreCarteraSME
from gestion_creditos.services.facilidat_xlsx import (
    ENCABEZADOS_FACILIDAT, REFERENCIA_FACILIDAT_SHA256, XLSX_CONTENT_TYPE,
    _validar_fila, filas_sinteticas, xlsx_excepciones, xlsx_sintetico_facilidat,
)
from gestion_creditos.tests.test_reporte_sme import CORTE, CierreSMEFixture, fecha
from gestion_creditos.views.reporte_sme import serializar


def leer(data, sheet):
    return load_workbook(BytesIO(data), data_only=False)[sheet]


def evidencia():
    causa = 'MONTO_PAGO_NO_CONCILIA_CON_IMPUTACIONES'
    cierre = {'corte': CORTE.isoformat(), 'filas': [
        {'credito_id_interno': 1, 'numero_obligacion_interno': 'CR-PRUEBA', 'producto': 'LIBRANZA',
         'incidencia_financiera': causa},
    ]}
    diferencias = [
        {'pago_id': 1, 'al_corte': True, 'delta_pago_menos_asientos': '13.52'},
        {'pago_id': 2, 'al_corte': False, 'delta_pago_menos_asientos': '9999.01'},
    ]
    trazas = {'corte': cierre['corte'], 'casos': [
        {'credito_id_interno': 1, 'diferencias_pago': diferencias, 'cierres_con_residuo': []},
    ]}
    cierre_bytes, trazas_bytes = serializar(cierre), serializar(trazas)
    pendientes = {
        'total': 1, 'corte': cierre['corte'], 'huella_cierre': sha256(cierre_bytes).hexdigest(),
        'huella_soportes': sha256(trazas_bytes).hexdigest(), 'filas': [
            {'credito_id_interno': 1, 'control': causa, 'obligacion': 'CR-PRUEBA',
             'diferencias': diferencias, 'cierres_con_residuo': []},
        ],
    }
    return cierre_bytes, pendientes, trazas_bytes


class FacilidatArchivosTest(SimpleTestCase):
    def test_carga_de_36_columnas_y_108_celdas_textuales(self):
        archivo = xlsx_sintetico_facilidat()
        hoja = leer(archivo, 'Carga')
        self.assertEqual(hoja.calculate_dimension(), 'A1:AJ4')
        self.assertEqual(tuple(c.value for c in hoja[1]), ENCABEZADOS_FACILIDAT)
        self.assertEqual(tuple(tuple(c.value for c in fila) for fila in hoja.iter_rows(min_row=2)), filas_sinteticas())
        self.assertTrue(all(c.data_type == 's' and c.number_format == '@' for fila in hoja.rows for c in fila))
        self.assertEqual((hoja['C2'].value, hoja['H2'].value, hoja['P2'].value, hoja['AJ2'].value),
                         ('SINTETICA01', '00', '00000000', '0000000000'))
        self.assertIn('NO REMITIR', leer(archivo, 'Control')['B2'].value)
        with ZipFile(BytesIO(archivo)) as zip_:
            self.assertFalse(any('vba' in n.lower() or 'externalLinks' in n for n in zip_.namelist()))

    def test_validacion_no_coacciona_numeros_fechas_o_codigos(self):
        original = filas_sinteticas()[0]
        for index, valor in ((1, 123), (1, '=1+1'), (5, '20190231'), (12, '17'), (16, 'AA'), (18, '1E3')):
            with self.subTest(index=index, valor=valor), self.assertRaises(ValueError):
                fila = list(original)
                fila[index] = valor
                _validar_fila(fila)
        fila = list(original)
        fila[1] = '001234567890123456789'
        _validar_fila(fila)
        with self.assertRaises(ValueError):
            _validar_fila(fila[:-1])

    def test_excepciones_toma_importe_respaldado_sin_incluir_pagos_posteriores(self):
        cierre_bytes, pendientes, trazas_bytes = evidencia()
        archivo = exportar_pendientes(cierre_bytes, pendientes, trazas_bytes)
        hoja = leer(archivo, 'Excepciones')
        self.assertEqual(hoja.auto_filter.ref, 'A1:H2')
        self.assertEqual(hoja['D2'].value, 13.52)
        self.assertEqual(leer(archivo, 'Control')['B4'].value, pendientes['huella_cierre'])

    def test_sin_importe_no_es_cero_y_texto_no_se_convierte_en_formula(self):
        cierre_bytes, _, _ = evidencia()
        cierre = json.loads(cierre_bytes)
        cierre['filas'][0]['numero_obligacion_interno'] = '=1+1'
        hoja = leer(xlsx_excepciones(cierre, 'sha-cierre'), 'Excepciones')
        self.assertIsNone(hoja['D2'].value)
        self.assertEqual((hoja['A2'].value, hoja['A2'].data_type), ('=1+1', 's'))

    def test_rechaza_trazas_o_importes_manipulados(self):
        cierre_bytes, pendientes, trazas_bytes = evidencia()
        with self.assertRaisesRegex(ValueError, 'HUELLAS'):
            exportar_pendientes(cierre_bytes, pendientes, trazas_bytes + b' ')
        pendientes['filas'][0]['diferencias'][0]['delta_pago_menos_asientos'] = '1000.00'
        with self.assertRaisesRegex(ValueError, 'IMPORTES'):
            exportar_pendientes(cierre_bytes, pendientes, trazas_bytes)


class FacilidatDescargasTest(CierreSMEFixture, TransactionTestCase):
    def setUp(self):
        self.preparar_usuarios()
        self.caso = self.credito(fecha(10), con_traza=False)
        self.client.force_login(self.operador)
        self.client.post(reverse('gestion:cartera_sme'), {'corte': CORTE.isoformat(), 'producto': 'LIBRANZA'})
        self.cierre = CierreCarteraSME.objects.get()

    def url(self, clase):
        return reverse('gestion:cartera_sme_descarga', args=[self.cierre.pk, clase])

    def test_descargas_privadas_comparten_huella_y_no_recalculan_snapshot(self):
        with mock.patch('gestion_creditos.views.reporte_sme.construir_cierre') as recalcular:
            interna = self.client.get(self.url('excepciones-xlsx'))
            demo = self.client.get(self.url('facilidat-sintetico'))
        recalcular.assert_not_called()
        for r in interna, demo:
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r['Content-Type'], XLSX_CONTENT_TYPE)
            self.assertEqual(r['X-Cierre-SHA256'], self.cierre.sha256)
            self.assertEqual(r['X-Reporte-SHA256'], sha256(r.content).hexdigest())
            self.assertIn('no-store', r['Cache-Control'])
            self.assertIn('NO_REMITIR.xlsx', r['Content-Disposition'])
        self.assertEqual(leer(interna.content, 'Excepciones')['A2'].value, self.caso.numero_credito)
        self.assertEqual(leer(demo.content, 'Carga')['B2'].value, 'ZTEST0001')
        self.assertEqual(self.cierre.plantilla_sha256, REFERENCIA_FACILIDAT_SHA256)

    def test_sin_permiso_y_carga_real_siempre_bloqueada(self):
        self.assertEqual(self.client.get(self.url('facilidat-real')).status_code, 409)
        contenido = self.cierre.contenido
        contenido['total']['reportables'] = 1
        CierreCarteraSME.objects.filter(pk=self.cierre.pk).update(
            contenido=contenido, sha256=sha256(serializar(contenido)).hexdigest(),
        )
        self.assertEqual(self.client.get(self.url('facilidat-real')).status_code, 409)
        self.client.logout()
        self.assertEqual(self.client.get(self.url('facilidat-sintetico')).status_code, 302)
        self.client.force_login(self.otro_staff)
        self.assertEqual(self.client.get(self.url('excepciones-xlsx')).status_code, 403)

    def test_xlsx_no_se_entrega_si_huella_del_snapshot_no_coincide(self):
        CierreCarteraSME.objects.filter(pk=self.cierre.pk).update(contenido={})
        self.assertEqual(self.client.get(self.url('facilidat-sintetico')).status_code, 409)
        self.assertEqual(self.client.get(self.url('excepciones-xlsx')).status_code, 409)

    def test_descargas_internas_nuevas_no_requieren_elegibilidad(self):
        for clase, hoja in (('cartera-xlsx', 'Cartera'), ('facilidat-preparacion', 'Carga'),
                            ('primera-positiva', 'Carga'), ('primera-mixta', 'Revision mora')):
            with self.subTest(clase=clase):
                r = self.client.get(self.url(clase))
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r['X-Cierre-SHA256'], self.cierre.sha256)
                self.assertIn('NO_REMITIR.xlsx', r['Content-Disposition'])
                self.assertEqual(leer(r.content, hoja).max_row, 2 if clase == 'cartera-xlsx' else 1)
        contenido = {**self.cierre.contenido, 'corte': '2026-10-30'}
        CierreCarteraSME.objects.filter(pk=self.cierre.pk).update(
            contenido=contenido, sha256=sha256(serializar(contenido)).hexdigest(),
        )
        self.assertEqual(self.client.get(self.url('cartera-xlsx')).status_code, 200)
        self.assertEqual(self.client.get(self.url('facilidat-preparacion')).status_code, 409)
        self.client.force_login(self.otro_staff)
        self.assertEqual(self.client.get(self.url('cartera-xlsx')).status_code, 403)
