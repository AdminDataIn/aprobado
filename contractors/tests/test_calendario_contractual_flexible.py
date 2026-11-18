import json
import os
import subprocess
from datetime import date, timedelta
from decimal import Decimal
from html import escape
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from weasyprint import HTML

from contractors.services.horizonte_simulacion import horizonte_para_solicitud, validar_plazo_contractual
from contractors.services.politica_financiera_prestador import PARAMETROS, VERSION
from contractors.tests.test_confirmacion_simulacion import FixtureConfirmacion
from contractors.views import CLAVE_SESION_ANALISIS_CONTRATO, _aplicar_evidencia_analisis
from gestion_creditos.services.horizonte_contractual import (
    calcular_horizonte_contractual, evidencia_calendario_en_texto,
    marcador_periodos_documentados, periodos_documentados_en_texto,
)


CORTE = date(2026, 11, 18)
ANIVERSARIO = 'Calendario: periodos mensuales por aniversario contractual.'
AJUSTE = 'Ajuste de aniversario: ultimo dia del mes sin cambiar el dia ancla.'
PAGO = 'Pago: al cierre de cada periodo.'
ANIVERSARIOS_NATURALES = (
    'Los per\u00edodos mensuales se contar\u00e1n por aniversario desde la fecha de inicio.',
    'Cada mensualidad corresponde al per\u00edodo comprendido entre aniversarios contractuales.',
)
PAGOS_CIERRE_NATURALES = (
    'El pago se realizar\u00e1 al cierre de cada periodo mensual.',
    'Los honorarios se pagar\u00e1n el \u00faltimo d\u00eda de cada per\u00edodo.',
    'Se pagar\u00e1 a per\u00edodo vencido, el \u00faltimo d\u00eda de cada per\u00edodo mensual.',
)
PAGO_DIAS_NATURAL = 'Se pagar\u00e1 cinco (5) d\u00edas calendario despu\u00e9s de terminado cada per\u00edodo.'
PAGO_CINCO_DIAS = 'Pago: 5 dias calendario despues del cierre de cada periodo.'
DETALLE = (
    'Calendario de pagos documentado:\n'
    'Periodo documentado: 2026-11-18 a 2026-11-30; vence 2026-12-05; valor COP 7800000.00.\n'
    'Periodo documentado: 2026-12-01 a 2026-12-31; vence 2027-01-05; valor COP 18000000.00.\n'
)


def horizonte(inicio='2026-11-18', fin='2027-11-17', clausulas=f'{ANIVERSARIO} {PAGO}', **kwargs):
    return calcular_horizonte_contractual(
        inicio=date.fromisoformat(inicio), fin=date.fromisoformat(fin),
        periodicidad='MENSUAL', valor_periodico=Decimal('18000000'),
        evidencia_calendario=evidencia_calendario_en_texto(clausulas), corte=CORTE, **kwargs,
    )


class CalendarioFlexibleTest(SimpleTestCase):
    def test_aniversario_18_doce_flujos_y_sin_inferir_pagado(self):
        h = horizonte(duracion_meses=12, valor_pagado_actual_declarado=Decimal('0'))
        self.assertTrue(h.disponible, h.motivo)
        self.assertEqual(len(h.flujos), 12)
        self.assertEqual((h.flujos[0].inicio, h.flujos[0].fin, h.flujos[0].fecha_pago),
                         (CORTE, date(2026, 12, 17), date(2026, 12, 17)))
        self.assertEqual(h.flujos[-1].fecha_pago, date(2027, 11, 17))
        self.assertEqual(sum(f.valor for f in h.flujos), Decimal('216000000'))
        self.assertEqual((h.periodos_causados, h.periodos_exigibles), (0, 0))
        self.assertIsNone(h.valor_pagado_actual_verificado)

    def test_variantes_generan_los_mismos_doce_flujos(self):
        original_cierre = horizonte(duracion_meses=12)
        original_dias = horizonte(clausulas=f'{ANIVERSARIO} {PAGO_CINCO_DIAS}', duracion_meses=12)
        for aniversario in ANIVERSARIOS_NATURALES:
            for pago, esperado in (
                *((pago, original_cierre) for pago in PAGOS_CIERRE_NATURALES),
                (PAGO_DIAS_NATURAL, original_dias),
                ('EL PAGO SE EFECTUARA 5 DIAS CALENDARIO A PARTIR DEL CIERRE DE CADA PERIODO MENSUAL;',
                 original_dias),
                ('Se pagara, cinco( 5 ) dias calendario despues de finalizado cada periodo!',
                 original_dias),
            ):
                with self.subTest(aniversario=aniversario, pago=pago):
                    resultado = horizonte(clausulas=f'{aniversario}\n {pago}', duracion_meses=12)
                    self.assertEqual(resultado, esperado)
                    self.assertEqual(resultado.periodos_totales, 12)

        evidencia = evidencia_calendario_en_texto(f'{ANIVERSARIO} {PAGO} {PAGOS_CIERRE_NATURALES[0]}')
        self.assertEqual(evidencia, evidencia_calendario_en_texto(f'{ANIVERSARIO} {PAGO}'))
        for dias, escrito in ((1, 'un'), (15, 'quince'), (22, 'veintid\u00f3s'), (30, 'treinta'), (35, 'treinta y cinco')):
            with self.subTest(dias=dias):
                actual = horizonte(clausulas=f'{ANIVERSARIOS_NATURALES[0]} '
                                   f'Se pagara {escrito} ({dias}) dias calendario despues de terminado cada periodo.')
                esperado = horizonte(clausulas=f'{ANIVERSARIO} '
                                     f'Pago: {dias} dias calendario despues del cierre de cada periodo.')
                self.assertTrue(actual.disponible)
                self.assertEqual(actual, esperado)

    def test_variantes_no_resuelven_ambiguedad_negacion_o_contradiccion(self):
        aniversario = ANIVERSARIOS_NATURALES[0]
        cierre = PAGOS_CIERRE_NATURALES[1]
        for texto in (
            f'{aniversario} Se pagara a periodo vencido.',
            f'{aniversario} El pago se realizara al cierre de cada periodo?',
            f'{aniversario} Se pagara dentro de cinco dias calendario despues del cierre de cada periodo.',
            f'{aniversario} Se pagara cinco dias calendario despues de aprobado el informe.',
            f'{aniversario} Se pagara al cierre de cada periodo si se acepta el informe.',
            f'{aniversario} {cierre[:-1]}; previa aceptacion del informe.',
            f'{aniversario} Los honorarios no se pagaran el ultimo dia de cada periodo.',
            f'No se usaran aniversarios contractuales. {aniversario} {cierre}',
            f'{aniversario} {cierre} No se pagara el ultimo dia de cada periodo.',
            f'{aniversario} {cierre} El aniversario contractual sera el dia 19.',
            f'{aniversario} {cierre} El dia 19 sera el aniversario contractual.',
            f'{aniversario} {cierre} {PAGO_DIAS_NATURAL}',
            f'{aniversario} Se pagara cinco (6) dias calendario despues de terminado cada periodo.',
        ):
            with self.subTest(texto=texto):
                h = horizonte(clausulas=texto, duracion_meses=12)
                self.assertFalse(h.disponible)
                self.assertEqual(h.flujos, ())
        for fin, cantidad in (('2027-11-16', 12), ('2027-11-17', 13)):
            self.assertFalse(horizonte(fin=fin, clausulas=f'{aniversario} {cierre}',
                                       duracion_meses=cantidad).disponible)

    def test_ancla_29_30_31_no_acumula_recorte(self):
        for inicio, fin, esperado in (
            ('2026-01-29', '2026-04-28', [29, 28, 29]),
            ('2028-01-30', '2028-04-29', [30, 29, 30]),
            ('2026-01-31', '2026-04-29', [31, 28, 31]),
        ):
            with self.subTest(inicio=inicio):
                h = horizonte(inicio, fin, f'{ANIVERSARIO} {AJUSTE} {PAGO}', duracion_meses=3)
                self.assertTrue(h.disponible, h.motivo)
                self.assertEqual([f.inicio.day for f in h.flujos], esperado)
                self.assertEqual(h.flujos[-1].fin, date.fromisoformat(fin))
                for actual, siguiente in zip(h.flujos, h.flujos[1:]):
                    self.assertEqual(actual.fin + timedelta(days=1), siguiente.inicio)
                variante = f'{ANIVERSARIOS_NATURALES[0]} {PAGOS_CIERRE_NATURALES[0]}'
                self.assertFalse(horizonte(inicio, fin, variante, duracion_meses=3).disponible)
                self.assertEqual(horizonte(inicio, fin, f'{variante} {AJUSTE}', duracion_meses=3), h)

    def test_bisiesto_al_no_bisiesto_y_diferir_pago_no_es_causacion(self):
        h = horizonte('2028-02-29', '2029-02-27',
                       f'{ANIVERSARIO} {AJUSTE} Pago: 5 dias calendario despues del cierre de cada periodo.',
                       duracion_meses=12)
        self.assertTrue(h.disponible, h.motivo)
        self.assertEqual(h.periodos_totales, 12)
        self.assertEqual(h.flujos[-1].fecha_pago, date(2029, 3, 4))
        t = calcular_horizonte_contractual(
            inicio=date(2026, 11, 18), fin=date(2027, 11, 17), periodicidad='MENSUAL',
            valor_periodico=Decimal('18000000'),
            evidencia_calendario=f'{ANIVERSARIO} Pago: 5 dias calendario despues del cierre de cada periodo.',
            corte=date(2026, 12, 18),
        )
        self.assertEqual((t.periodos_causados, t.periodos_exigibles), (1, 0))
        self.assertIsNone(t.valor_pagado_actual_declarado)

    def test_guarda_fin_irregular_y_evidencia_faltante_o_ambigua(self):
        for inicio, fin, texto, cantidad in (
            ('2026-11-18', '2027-11-17', 'Solo mensual', 12),
            ('2026-11-18', '2027-11-17', ANIVERSARIO, 12),
            ('2026-11-18', '2027-11-17', f'{ANIVERSARIO} pago el ultimo dia del mes', 12),
            ('2026-11-18', '2027-11-17',
             f'{ANIVERSARIO} {PAGO} Pago: 5 dias calendario despues del cierre de cada periodo.', 12),
            ('2026-11-18', '2027-11-17', f'{ANIVERSARIO} No {PAGO}', 12),
            ('2026-11-18', '2027-11-16', f'{ANIVERSARIO} {PAGO}', 12),
            ('2026-11-18', '2027-11-17', f'{ANIVERSARIO} {PAGO}', 13),
            ('2026-01-31', '2026-04-29', f'{ANIVERSARIO} {PAGO}', 3),
        ):
            with self.subTest(inicio=inicio, fin=fin, regla=texto, cantidad=cantidad):
                h = horizonte(inicio, fin, texto, duracion_meses=cantidad)
                self.assertFalse(h.disponible)
                self.assertEqual(h.flujos, ())

    def test_meses_calendario_y_dia_fijo_del_mes_siguiente(self):
        h = horizonte('2026-12-01', '2027-11-30', 'Pago el dia cinco (5) del mes siguiente',
                       duracion_meses=12)
        self.assertTrue(h.disponible, h.motivo)
        self.assertEqual(h.flujos[0].fecha_pago, date(2027, 1, 5))
        self.assertEqual(h.flujos[-1].fecha_pago, date(2027, 12, 5))

    def test_parciales_requieren_cobertura_total_fechas_importes_y_orden(self):
        filas = periodos_documentados_en_texto(DETALLE)
        marcador = marcador_periodos_documentados(filas)
        def calcular(f, evidencia=None, total='25800000'):
            return calcular_horizonte_contractual(
                inicio=CORTE, fin=date(2026, 12, 31), periodicidad='MENSUAL',
                valor_periodico=None, valor_total_contractual=total, corte=CORTE,
                evidencia_calendario=evidencia or marcador_periodos_documentados(f),
                periodos_documentados=f,
            )
        h = calcular(filas)
        self.assertTrue(h.disponible, h.motivo)
        self.assertEqual([f.valor for f in h.flujos], [Decimal('7800000'), Decimal('18000000')])
        for invalido in ([], filas[:1], filas + filas, [['2026-11-18', '2026-12-31', '2027-01-05', '25800000']],
                        [filas[0], ['2026-12-02', *filas[1][1:]]],
                        [filas[0], [*filas[1][:2], '2026-12-21', filas[1][3]]],
                        [filas[0], [*filas[1][:3], '0']]):
            self.assertFalse(calcular(invalido).disponible)
        self.assertFalse(calcular(filas, total='25800001').disponible)
        self.assertFalse(calcular(filas, evidencia=marcador + 'x').disponible)
        self.assertEqual(periodos_documentados_en_texto(DETALLE + 'Periodo documentado: incompleto'), [])


@patch('django.utils.timezone.localdate', return_value=CORTE)
class CalendarioFlexiblePrestadorTest(FixtureConfirmacion, TestCase):
    def setUp(self):
        super().setUp()
        self.configuracion.version = VERSION
        for campo, valor in PARAMETROS.items():
            setattr(self.configuracion, campo, valor)
        self.configuracion.save()
        self.solicitud.fecha_inicio_contrato = CORTE
        self.solicitud.fecha_fin_contrato = date(2027, 11, 17)
        self.solicitud.duracion_contrato_meses = 12
        self.solicitud.valor_mensual_contractual = Decimal('18000000')
        self.solicitud.valor_total_contrato = self.solicitud.valor_pendiente_cobrar = Decimal('216000000')
        self.solicitud.save()

    def calcular(self, **datos):
        return self.client.post('/simular/calcular/', json.dumps(dict(self.payload, **datos)),
                                content_type='application/json', HTTP_HOST=self.host)

    def pagina(self):
        return self.client.get('/simular/', {'solicitud_id': self.solicitud.pk}, HTTP_HOST=self.host)

    def analizar(self, clausulas, *, fin='17/11/2027'):
        texto = (
            'CONTRATO SINTETICO DE PRESTACION DE SERVICIOS. SIN VALIDEZ REAL.\n'
            'Documento: 000000017.\n'
            f'Fecha de inicio: 18/11/2026. Fecha de terminacion: {fin}.\n'
            f'Honorarios mensuales: COP $18.000.000.\n{clausulas}'
        )
        contenido = HTML(string='<pre style="font: 8pt monospace">' + escape(texto) + '</pre>').write_pdf()
        contrato = self.solicitud.documentos.get(tipo_documento='CONTRATO')
        contrato.archivo.save('contrato-sintetico.pdf', SimpleUploadedFile('contrato.pdf', contenido))
        response = self.client.post(f'/solicitud/{self.solicitud.pk}/contrato/analizar/', {
            'autoriza_analisis_contractual_asistido': 'true', 'tipo_documento': 'CC',
        }, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()['estado'], 'BLOQUEADO')
        self.solicitud.refresh_from_db()
        return self.client.session[CLAVE_SESION_ANALISIS_CONTRATO], contrato

    def confirmar_evidencia(self, evidencia):
        _aplicar_evidencia_analisis(self.solicitud, evidencia)
        self.solicitud.save()

    def test_pendiente_calcula_solo_estimacion_y_no_registra(self, _fecha):
        antes = self.evidencia()
        self.assertContains(self.pagina(), 'ESTIMACI&Oacute;N INFORMATIVA')
        respuesta = self.calcular(solo_informativa=False)
        self.assertTrue(respuesta.json()['solo_informativa'])
        self.assertEqual(self.calcular(plazo_meses='12').status_code, 400)
        self.assertEqual(self.client.post('/simular/', self.payload, HTTP_HOST=self.host).status_code, 200)
        self.assertEqual(self.evidencia(), antes)
        self.assertFalse(self.solicitud.evaluacion_jobs.exists())
        with self.assertRaises(ValidationError):
            validar_plazo_contractual(self.solicitud, self.configuracion, 4, corte=CORTE)

    def test_pdf_aniversario_requiere_confirmacion_y_hash_vigente(self, _fecha):
        evidencia, contrato = self.analizar(f'{ANIVERSARIO}\n{PAGO}')
        self.assertTrue(self.calcular().json()['solo_informativa'])
        self.confirmar_evidencia(evidencia)
        valido = horizonte_para_solicitud(self.solicitud, self.configuracion, corte=CORTE)
        self.assertTrue(valido.disponible, valido.motivo)
        self.assertEqual(valido.periodos_totales, 12)
        self.assertFalse(self.calcular().json()['solo_informativa'])
        self.assertEqual(self.client.post('/simular/', self.payload, HTTP_HOST=self.host).status_code, 302)
        contrato.archivo.save('contrato-reemplazado.pdf', SimpleUploadedFile('contrato.pdf', b'%PDF-1.4 otro'))
        with self.assertRaises(ValidationError):
            validar_plazo_contractual(self.solicitud, self.configuracion, 4, corte=CORTE)

    def test_pdf_periodos_parciales_no_acepta_evidencia_editada(self, _fecha):
        self.solicitud.fecha_fin_contrato = date(2026, 12, 31)
        self.solicitud.duracion_contrato_meses = None
        self.solicitud.valor_total_contrato = Decimal('25800000')
        self.solicitud.save()
        evidencia, _ = self.analizar(DETALLE, fin='31/12/2026')
        self.confirmar_evidencia(evidencia)
        h = horizonte_para_solicitud(self.solicitud, self.configuracion, corte=CORTE)
        self.assertTrue(h.disponible, h.motivo)
        self.assertEqual(h.periodos_totales, 2)
        self.solicitud.metadata_analisis_contractual['forma_pago_contractual']['periodos_documentados_v1'][0][3] = '7800001'
        self.assertFalse(horizonte_para_solicitud(self.solicitud, self.configuracion, corte=CORTE).disponible)

    def test_pdf_variantes_confirma_condiciones_una_vez(self, _fecha):
        evidencia, _ = self.analizar(f'{ANIVERSARIOS_NATURALES[1]}\n{PAGO_DIAS_NATURAL}')
        self.assertTrue(self.calcular().json()['solo_informativa'])
        self.confirmar_evidencia(evidencia)
        h = horizonte_para_solicitud(self.solicitud, self.configuracion, corte=CORTE)
        self.assertEqual(h.flujos, horizonte(clausulas=f'{ANIVERSARIO} {PAGO_CINCO_DIAS}').flujos)
        self.assertEqual(h.periodos_totales, 12)
        self.assertFalse(self.calcular().json()['solo_informativa'])
        self.assertEqual(self.client.post('/simular/', self.payload, HTTP_HOST=self.host).status_code, 302)
        confirmada = self.evidencia()
        self.assertEqual(self.client.post('/simular/', self.payload, HTTP_HOST=self.host).status_code, 302)
        self.assertEqual(self.evidencia(), confirmada)

    @skipUnless(os.environ.get('PRESTADORES_CALENDARIO_BROWSER'), 'Requiere Playwright local')
    def test_browser_distingue_estimacion_y_envio(self, _fecha):
        pendiente = self.pagina().content.decode()
        estimacion = self.calcular().json()
        evidencia, _ = self.analizar(f'{ANIVERSARIOS_NATURALES[0]}\n{PAGOS_CIERRE_NATURALES[0]}')
        self.confirmar_evidencia(evidencia)
        confirmado = self.pagina().content.decode()
        calculo = self.calcular().json()
        subprocess.run(
            ['node', 'contractors/tests/browser/test_calendario_contractual_flexible.cjs'],
            input=json.dumps(dict(pendiente=pendiente, estimacion=estimacion,
                                  confirmado=confirmado, calculo=calculo)),
            cwd=settings.BASE_DIR, text=True, check=True,
        )
