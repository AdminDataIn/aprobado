import hashlib
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from contractors.consentimiento_centrales import (
    TEXTO_CONSENTIMIENTO_CENTRALES, VERSION_CONSENTIMIENTO_CENTRALES,
)
from contractors import consentimiento_centrales
from contractors.models import (
    AprobacionInternaPrestador, AutorizacionConsultaDatacreditoPrestador,
    ContractorApplication, PredecisionPrestadorAudit,
)
from contractors.services.autorizacion_datacredito import (
    crear_confirmacion_consentimiento, obtener_autorizacion_datacredito_vigente,
    obtener_configuracion_autorizacion_datacredito,
    registrar_autorizacion_datacredito_desde_solicitud,
)
from gestion_creditos.models import Credito, CreditoLibranza, Empresa, OrigenCreditoPrestador


class ConsentimientoHTML(HTMLParser):
    def __init__(self, respuesta):
        super().__init__(convert_charrefs=True)
        self.inputs = {}
        self.texto = ''
        self.en_texto = False
        self.feed(respuesta.content.decode('utf-8'))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input':
            self.inputs[attrs.get('name')] = attrs
        if 'data-consentimiento-texto' in attrs:
            self.en_texto = True

    def handle_endtag(self, tag):
        if tag == 'div':
            self.en_texto = False

    def handle_data(self, data):
        if self.en_texto:
            self.texto += data


@override_settings(
    ROOT_URLCONF='aprobado_web.urls_contractors',
    ALLOWED_HOSTS=['testserver', 'localhost', 'contratistas.localhost'],
    DATACREDITO_ENABLED=False, DATACREDITO_REAL_ENABLED=False,
    DATACREDITO_AUTHORIZATION_TEXT=TEXTO_CONSENTIMIENTO_CENTRALES,
    DATACREDITO_AUTHORIZATION_TEXT_VERSION=VERSION_CONSENTIMIENTO_CENTRALES,
)
class ConsentimientoCentralesTest(TestCase):
    host = 'contratistas.localhost'

    def setUp(self):
        self.usuario = get_user_model().objects.create_user(username='titular-consentimiento')
        self.otro = get_user_model().objects.create_user(username='otro-consentimiento')
        self.empresa = Empresa.objects.create(nombre='Empresa de prueba')
        self.solicitud = ContractorApplication.objects.create(
            usuario=self.usuario, empresa=self.empresa, autoriza_consulta_centrales=True,
            numero_documento='123456789', nombres='Titular', apellidos='Prueba',
        )
        self.url = reverse('contractors:consentimiento_centrales', args=[self.solicitud.pk])
        self.client.force_login(self.usuario)

    def pagina(self, client=None):
        response = (client or self.client).get(self.url, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        return ConsentimientoHTML(response)

    def aceptar(self, token=None, client=None, **extra):
        token = token if token is not None else self.pagina(client).inputs['consentimiento_centrales']['value']
        return (client or self.client).post(self.url, {
            'autoriza_consulta_centrales': 'on', 'consentimiento_centrales': token, **extra,
        }, HTTP_HOST=self.host)

    def test_texto_renderizado_es_exactamente_el_hasheado_y_version_persistida(self):
        response = self.client.get(self.url, HTTP_HOST=self.host)
        pagina = ConsentimientoHTML(response)
        self.assertContains(response, VERSION_CONSENTIMIENTO_CENTRALES)
        self.assertEqual(pagina.texto, TEXTO_CONSENTIMIENTO_CENTRALES)
        self.assertNotIn('checked', pagina.inputs['autoriza_consulta_centrales'])
        self.assertEqual(self.aceptar(pagina.inputs['consentimiento_centrales']['value']).status_code, 302)
        evidencia = AutorizacionConsultaDatacreditoPrestador.objects.get()
        self.assertEqual(evidencia.texto_hash, hashlib.sha256(pagina.texto.encode('utf-8')).hexdigest())
        self.assertEqual(evidencia.version_texto, VERSION_CONSENTIMIENTO_CENTRALES)
        self.assertEqual((evidencia.usuario_id, evidencia.solicitud_id), (self.usuario.pk, self.solicitud.pk))
        self.assertIsNotNone(evidencia.aceptada_en)
        self.assertEqual(ContractorApplication.objects.count(), 1)
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(CreditoLibranza.objects.exists())

    def test_pagina_legal_y_formulario_comparten_fuente_y_escape(self):
        texto = TEXTO_CONSENTIMIENTO_CENTRALES + '\n<script>prueba</script>'
        with patch.object(consentimiento_centrales, 'TEXTO_CONSENTIMIENTO_CENTRALES', texto), override_settings(DATACREDITO_AUTHORIZATION_TEXT=texto):
            pagina = self.pagina()
            legal = self.client.get(reverse('contractors:centrales_informacion'), HTTP_HOST=self.host)
            self.assertEqual(ConsentimientoHTML(legal).texto, pagina.texto)
            self.assertNotContains(legal, '<script>prueba</script>')
            solicitud = self.client.get(reverse('contractors:solicitar'), HTTP_HOST=self.host)
            self.assertEqual(ConsentimientoHTML(solicitud).texto, pagina.texto)

    def test_historico_no_se_backfillea_y_puede_aceptar_sin_nueva_solicitud(self):
        self.pagina()
        response = self.client.get(reverse('contractors:mi_credito'), HTTP_HOST=self.host)
        self.assertContains(response, self.url)
        self.assertFalse(AutorizacionConsultaDatacreditoPrestador.objects.exists())
        self.assertEqual(self.aceptar().status_code, 302)
        self.assertEqual(ContractorApplication.objects.count(), 1)
        self.assertIsNotNone(obtener_autorizacion_datacredito_vigente(self.solicitud))

    def test_reintento_es_idempotente_y_no_cambia_timestamp(self):
        token = self.pagina().inputs['consentimiento_centrales']['value']
        self.aceptar(token)
        primera = AutorizacionConsultaDatacreditoPrestador.objects.get()
        self.aceptar(token)
        segunda = AutorizacionConsultaDatacreditoPrestador.objects.get()
        self.assertEqual((primera.pk, primera.aceptada_en), (segunda.pk, segunda.aceptada_en))

    def test_aceptacion_no_retrocede_solicitud_en_firma(self):
        for estado in (ContractorApplication.Estado.PENDIENTE_FIRMA, ContractorApplication.Estado.FIRMADO):
            with self.subTest(estado=estado):
                self.solicitud.estado = estado
                self.solicitud.save(update_fields=['estado'])
                self.assertEqual(self.aceptar().status_code, 302)
                self.solicitud.refresh_from_db()
                self.assertEqual(self.solicitud.estado, estado)

    def test_aceptacion_invalida_evaluacion_previa_no_financiera(self):
        self.solicitud.estado = ContractorApplication.Estado.EVALUACION_COMPLETADA
        self.solicitud.save(update_fields=['estado'])
        self.assertEqual(self.aceptar().status_code, 302)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, ContractorApplication.Estado.EVALUACION_PENDIENTE)

    def test_aceptacion_con_origen_financiero_conserva_solicitud_y_credito(self):
        auditoria = PredecisionPrestadorAudit.objects.create(
            solicitud=self.solicitud, version_datos='datos-test',
            clave_idempotencia='auditoria-consentimiento-test',
            iniciada_en=timezone.now(),
        )
        gate = AprobacionInternaPrestador.objects.create(
            solicitud=self.solicitud, auditoria_predecision=auditoria,
            version_datos='datos-test', version_politica='politica-test',
            version_configuracion_financiera='configuracion-test', tasa_mensual_snapshot='1',
            monto_solicitado_snapshot='1000', monto_maximo_politica_snapshot='1000',
            monto_maximo_contrato_snapshot='1000', monto_maximo_financiero_snapshot='1000',
            monto_maximo_evaluado='1000', monto_autorizado='1000',
            plazo_solicitado_snapshot=1, plazo_maximo_politica_snapshot=1,
            plazo_maximo_contrato_snapshot=1, plazo_maximo_financiero_snapshot=1,
            plazo_maximo_evaluado=1, plazo_autorizado=1,
        )
        credito = Credito.objects.create(
            usuario=self.usuario, monto_solicitado='1000', plazo_solicitado=1,
            estado=Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
        )
        OrigenCreditoPrestador.objects.create(
            gate_id=gate.pk, gate_version='datos-test',
            clave_idempotencia='origen-consentimiento-test', credito=credito,
        )
        self.solicitud.estado = ContractorApplication.Estado.EVALUACION_COMPLETADA
        self.solicitud.save(update_fields=['estado'])
        self.assertEqual(self.aceptar().status_code, 302)
        self.solicitud.refresh_from_db()
        credito.refresh_from_db()
        self.assertEqual(self.solicitud.estado, ContractorApplication.Estado.EVALUACION_COMPLETADA)
        self.assertEqual(credito.estado, Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA)
        self.assertEqual(credito.monto_solicitado, 1000)
        self.assertEqual(Credito.objects.count(), 1)

    def test_sin_texto_o_version_falla_cerrado(self):
        for nombre in ('DATACREDITO_AUTHORIZATION_TEXT', 'DATACREDITO_AUTHORIZATION_TEXT_VERSION'):
            with self.subTest(nombre=nombre), override_settings(**{nombre: ''}):
                self.assertNotIn('consentimiento_centrales', self.pagina().inputs)
                self.assertEqual(self.aceptar('invalido').status_code, 200)
                with self.assertRaises(ValidationError):
                    registrar_autorizacion_datacredito_desde_solicitud(self.solicitud, usuario=self.usuario)
        self.assertFalse(AutorizacionConsultaDatacreditoPrestador.objects.exists())

    def test_sin_overrides_usa_canonico_en_ui_y_evidencia(self):
        with override_settings():
            delattr(settings, 'DATACREDITO_AUTHORIZATION_TEXT')
            delattr(settings, 'DATACREDITO_AUTHORIZATION_TEXT_VERSION')
            self.assertEqual(self.pagina().texto, TEXTO_CONSENTIMIENTO_CENTRALES)
            self.assertEqual(self.aceptar().status_code, 302)
        evidencia = AutorizacionConsultaDatacreditoPrestador.objects.get()
        self.assertEqual(evidencia.version_texto, VERSION_CONSENTIMIENTO_CENTRALES)
        self.assertEqual(evidencia.texto_hash, hashlib.sha256(TEXTO_CONSENTIMIENTO_CENTRALES.encode('utf-8')).hexdigest())

    def test_override_identico_confirma_pero_no_sustituye_el_canonico(self):
        configuracion = obtener_configuracion_autorizacion_datacredito()
        self.assertTrue(configuracion.configurada)
        self.assertTrue(configuracion.texto_override_compatible)
        self.assertTrue(configuracion.version_override_compatible)
        self.assertEqual(configuracion.texto, TEXTO_CONSENTIMIENTO_CENTRALES)
        self.assertEqual(configuracion.version_texto, VERSION_CONSENTIMIENTO_CENTRALES)

    def test_override_distinto_bloquea_ui_aceptacion_y_reutilizacion_sin_reescribir_historico(self):
        token = self.pagina().inputs['consentimiento_centrales']['value']
        self.aceptar(token)
        anterior = AutorizacionConsultaDatacreditoPrestador.objects.get()
        for nombre, valor in (
            ('DATACREDITO_AUTHORIZATION_TEXT', 'test-override-juridico-no-mostrar'),
            ('DATACREDITO_AUTHORIZATION_TEXT_VERSION', 'test-version-no-mostrar'),
            ('DATACREDITO_AUTHORIZATION_TEXT', TEXTO_CONSENTIMIENTO_CENTRALES + '\n'),
            ('DATACREDITO_AUTHORIZATION_TEXT_VERSION', ' ' + VERSION_CONSENTIMIENTO_CENTRALES),
        ):
            with self.subTest(nombre=nombre), override_settings(**{nombre: valor}):
                configuracion = obtener_configuracion_autorizacion_datacredito()
                self.assertFalse(configuracion.configurada)
                self.assertEqual(configuracion.texto, TEXTO_CONSENTIMIENTO_CENTRALES)
                self.assertEqual(configuracion.version_texto, VERSION_CONSENTIMIENTO_CENTRALES)
                self.assertNotIn('consentimiento_centrales', self.pagina().inputs)
                response = self.aceptar(token)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'test-override-juridico-no-mostrar')
                self.assertNotContains(response, 'test-version-no-mostrar')
                self.assertIsNone(obtener_autorizacion_datacredito_vigente(self.solicitud))
                with self.assertRaises(ValidationError):
                    registrar_autorizacion_datacredito_desde_solicitud(self.solicitud, usuario=self.usuario)
        despues = AutorizacionConsultaDatacreditoPrestador.objects.get()
        self.assertEqual((anterior.pk, anterior.texto_hash, anterior.version_texto, anterior.aceptada_en),
                         (despues.pk, despues.texto_hash, despues.version_texto, despues.aceptada_en))

    def test_checkbox_ausente_no_autoriza_aunque_booleano_historico_sea_true(self):
        response = self.client.post(self.url, {
            'consentimiento_centrales': self.pagina().inputs['consentimiento_centrales']['value'],
        }, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AutorizacionConsultaDatacreditoPrestador.objects.exists())

    def test_cambio_de_texto_o_version_requiere_aceptacion_actual_y_conserva_historico(self):
        for nombre, constante, valor in (
            ('DATACREDITO_AUTHORIZATION_TEXT_VERSION', 'VERSION_CONSENTIMIENTO_CENTRALES', 'prestadores-centrales-v2-test'),
            ('DATACREDITO_AUTHORIZATION_TEXT', 'TEXTO_CONSENTIMIENTO_CENTRALES', TEXTO_CONSENTIMIENTO_CENTRALES + '\nRevision de prueba'),
        ):
            with self.subTest(nombre=nombre):
                token = self.pagina().inputs['consentimiento_centrales']['value']
                self.aceptar(token)
                anteriores = AutorizacionConsultaDatacreditoPrestador.objects.count()
                with patch.object(consentimiento_centrales, constante, valor), override_settings(**{nombre: valor}):
                    self.assertIsNone(obtener_autorizacion_datacredito_vigente(self.solicitud))
                    self.assertEqual(self.aceptar(token).status_code, 200)
                    self.assertEqual(AutorizacionConsultaDatacreditoPrestador.objects.count(), anteriores)
                    self.assertEqual(self.aceptar().status_code, 302)
                    self.assertEqual(AutorizacionConsultaDatacreditoPrestador.objects.count(), anteriores + 1)

    def test_token_ausente_manipulado_o_de_otro_titular_no_autoriza(self):
        for token in ('', 'alterado', crear_confirmacion_consentimiento(self.otro)):
            with self.subTest(token=bool(token)):
                self.assertEqual(self.aceptar(token).status_code, 200)
        self.assertFalse(AutorizacionConsultaDatacreditoPrestador.objects.exists())

    def test_owner_y_login_requeridos_y_cambio_titular_invalida_evidencia(self):
        self.aceptar()
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(self.url, HTTP_HOST=self.host).status_code, 404)
        self.assertEqual(self.aceptar(crear_confirmacion_consentimiento(self.otro)).status_code, 404)
        with self.assertRaises(PermissionDenied):
            registrar_autorizacion_datacredito_desde_solicitud(self.solicitud, usuario=self.otro)
        self.solicitud.usuario = self.otro
        self.solicitud.save(update_fields=['usuario'])
        self.assertIsNone(obtener_autorizacion_datacredito_vigente(self.solicitud))
        self.client.logout()
        self.assertEqual(self.client.get(self.url, HTTP_HOST=self.host).status_code, 302)

    def test_csrf_real_rechaza_post_y_permite_aceptacion_con_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.usuario)
        pagina = self.pagina(client)
        token = pagina.inputs['consentimiento_centrales']['value']
        self.assertEqual(self.aceptar(token, client=client).status_code, 403)
        self.assertFalse(AutorizacionConsultaDatacreditoPrestador.objects.exists())
        self.assertEqual(self.aceptar(token, client=client, csrfmiddlewaretoken=pagina.inputs['csrfmiddlewaretoken']['value']).status_code, 302)

    def test_servicio_revalida_propietario_despues_del_lock(self):
        request = RequestFactory().post(self.url, {'consentimiento_centrales': crear_confirmacion_consentimiento(self.usuario)})
        ContractorApplication.objects.filter(pk=self.solicitud.pk).update(usuario=self.otro)
        with self.assertRaises(PermissionDenied):
            registrar_autorizacion_datacredito_desde_solicitud(self.solicitud, usuario=self.usuario, request=request)
        self.assertFalse(AutorizacionConsultaDatacreditoPrestador.objects.exists())


@skipUnless(connection.vendor == 'postgresql', 'Requiere locks reales PostgreSQL')
@override_settings(
    DATACREDITO_AUTHORIZATION_TEXT=TEXTO_CONSENTIMIENTO_CENTRALES,
    DATACREDITO_AUTHORIZATION_TEXT_VERSION=VERSION_CONSENTIMIENTO_CENTRALES,
)
class ConsentimientoCentralesConcurrenciaPostgresTest(TransactionTestCase):
    def test_doble_aceptacion_conserva_unica_evidencia(self):
        usuario = get_user_model().objects.create_user(username='titular-concurrente-consentimiento')
        empresa = Empresa.objects.create(nombre='Empresa de prueba concurrente')
        solicitud = ContractorApplication.objects.create(
            usuario=usuario, empresa=empresa, autoriza_consulta_centrales=True,
        )
        barrera = Barrier(2)

        def aceptar():
            close_old_connections()
            try:
                titular = get_user_model().objects.get(pk=usuario.pk)
                actual = ContractorApplication.objects.get(pk=solicitud.pk)
                request = RequestFactory().post('/solicitar/', {
                    'consentimiento_centrales': crear_confirmacion_consentimiento(titular),
                })
                barrera.wait(timeout=10)
                evidencia = registrar_autorizacion_datacredito_desde_solicitud(actual, usuario=titular, request=request)
                return evidencia.pk, evidencia.aceptada_en
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(lambda _: aceptar(), range(2)))
        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(AutorizacionConsultaDatacreditoPrestador.objects.count(), 1)
