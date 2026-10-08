from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import close_old_connections, connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from contractors.models import IngresoNetoVerificadoPrestador, PredecisionPrestadorAudit, TimelinePrestador
from contractors.services import evaluacion_formal
from contractors.services.evaluacion_versionado import construir_version_datos
from contractors.services.ingreso_neto import (
    registrar_ingreso_neto, obtener_ingreso_neto_vigente, ingreso_para_oferta_vigente,
)
from contractors.tests.fixtures_ingreso_neto import preparar_evidencia_neta
from usuarios.models import PerfilPagador


class FixtureIngreso:
    def setUp(self):
        super().setUp()
        self.solicitud, _, self.documento = preparar_evidencia_neta(self)
        self.actor = get_user_model().objects.create_user('riesgo-neto', is_staff=True)
        self.actor.user_permissions.add(Permission.objects.get(codename='can_verify_contractor_net_income'))
        self.hoy = timezone.localdate()

    def registrar(self, **kwargs):
        datos = dict(solicitud=self.solicitud, actor=self.actor, monto='3000000', fecha_corte=self.hoy,
                     vigente_hasta=self.hoy + timedelta(days=30), documentos=[self.documento],
                     observacion='Soporte revisado por analista')
        datos.update(kwargs)
        return registrar_ingreso_neto(**datos)

    def iniciar(self):
        self.solicitud.estado = 'EVALUACION_PENDIENTE'
        self.solicitud.save(update_fields=['estado'])
        return evaluacion_formal._iniciar_evaluacion(solicitud=self.solicitud, usuario=self.actor,
            version_politica='politica-test', version_score='score-test', configuracion_financiera=None,
            version_configuracion_financiera='', modo_datacredito=evaluacion_formal.REUTILIZAR_SI_VIGENTE)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', 'contratistas.localhost'])
class IngresoNetoTest(FixtureIngreso, TestCase):
    def test_version_fuente_evidencia_actor_privacidad_e_idempotencia(self):
        primero = self.registrar()
        repetido = self.registrar()
        self.assertEqual(primero.pk, repetido.pk)
        self.assertEqual(primero.version, 1)
        self.assertEqual(primero.fuente, 'MANUAL_VERIFICADA')
        self.assertEqual(primero.verificado_por_id, self.actor.pk)
        self.assertIsNotNone(primero.verificado_en)
        self.assertEqual(set(primero.evidencias[0]), {'documento_id', 'tipo', 'sha256'})
        self.assertEqual(len(primero.evidencias[0]['sha256']), 64)
        self.assertNotIn(self.documento.archivo.name, str(primero.evidencias))
        self.assertEqual(IngresoNetoVerificadoPrestador.objects.count(), 1)
        evento = TimelinePrestador.objects.get(tipo_evento='DATOS_MODIFICADOS')
        self.assertFalse(evento.visible_cliente)
        self.assertNotIn('3000000', str(evento.metadata))

    def test_solicitante_staff_sin_permiso_y_pagador_rechazados(self):
        solicitante = self.solicitud.usuario
        solicitante.is_staff = False
        lector = get_user_model().objects.create_user('lector-neto', is_staff=True)
        for actor in (solicitante, lector, None):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                self.registrar(actor=actor)
        PerfilPagador.objects.create(usuario=self.actor, empresa=self.solicitud.empresa)
        actor = get_user_model().objects.get(pk=self.actor.pk)
        with self.assertRaises(PermissionDenied):
            self.registrar(actor=actor)
        self.assertFalse(IngresoNetoVerificadoPrestador.objects.exists())

    def test_historial_inmutable_cambio_e_invalidacion_sin_fallback(self):
        viejo = self.registrar()
        nuevo = self.registrar(monto='4000000')
        self.assertEqual((viejo.version, nuevo.version), (1, 2))
        viejo.refresh_from_db()
        self.assertEqual(viejo.monto, Decimal('3000000'))
        viejo.monto = Decimal('10')
        with self.assertRaises(ValidationError):
            viejo.save()
        with self.assertRaises(ValidationError):
            viejo.delete()
        invalidado = self.registrar(invalidar=True, observacion='Evidencia deja de estar vigente')
        self.assertEqual((invalidado.version, invalidado.accion), (3, 'INVALIDADO'))
        self.assertIsNone(obtener_ingreso_neto_vigente(self.solicitud))
        self.assertEqual(self.registrar(invalidar=True, observacion='Evidencia deja de estar vigente').pk, invalidado.pk)
        self.assertEqual(self.solicitud.ingresos_netos_verificados.count(), 3)

    def test_no_omite_evidencia_vigencia_observacion_ni_inventa_importes(self):
        casos = ({'documentos': []}, {'documentos': ['invalido']}, {'documentos': [self.documento.pk + 99]},
                 {'monto': 0}, {'monto': 'NaN'}, {'monto': '1e100'}, {'monto': '1.001'},
                 {'fecha_corte': self.hoy + timedelta(days=1)},
                 {'vigente_hasta': self.hoy - timedelta(days=1)}, {'observacion': ''})
        for cambios in casos:
            with self.subTest(cambios=cambios), self.assertRaises(ValidationError):
                self.registrar(**cambios)
        self.assertFalse(IngresoNetoVerificadoPrestador.objects.exists())

    def test_expiracion_corte_inclusive_no_recupera_version_antigua(self):
        self.registrar(vigente_hasta=self.hoy)
        self.assertIsNotNone(obtener_ingreso_neto_vigente(self.solicitud, corte=self.hoy))
        self.assertIsNone(obtener_ingreso_neto_vigente(self.solicitud, corte=self.hoy + timedelta(days=1)))
        self.assertIsNone(obtener_ingreso_neto_vigente(self.solicitud, corte=self.hoy - timedelta(days=1)))

    def test_evidencia_alterada_invalida_fuente(self):
        self.registrar()
        version_anterior, _ = construir_version_datos(self.solicitud)
        self.documento.archivo.save('evidencia.pdf', ContentFile(b'%PDF-1.4\nOtra evidencia\n%%EOF'))
        self.assertIsNone(obtener_ingreso_neto_vigente(self.solicitud))
        self.assertNotEqual(construir_version_datos(self.solicitud)[0], version_anterior)

    def test_aislamiento_de_solicitud_y_dto_forjado(self):
        self.registrar()
        ingreso = obtener_ingreso_neto_vigente(self.solicitud)
        self.assertTrue(ingreso_para_oferta_vigente(ingreso, corte=self.hoy, solicitud_id=self.solicitud.pk))
        self.assertFalse(ingreso_para_oferta_vigente(ingreso, corte=self.hoy, solicitud_id=self.solicitud.pk + 1))
        self.assertFalse(ingreso_para_oferta_vigente(replace(ingreso, monto=Decimal('10000000')),
                                                 corte=self.hoy, solicitud_id=self.solicitud.pk))

    def test_cambio_invalida_evaluacion_sin_reescribir_snapshot_historico(self):
        self.registrar()
        inicio = self.iniciar()
        historica = evaluacion_formal._finalizar_sin_decision(inicio.auditoria, resultado='NO_EVALUABLE',
            razones=('Sin proveedores en test',), error_codigo='', usuario=self.actor).auditoria
        entrada, salida = historica.snapshot_entrada, historica.snapshot_salida
        self.assertEqual(entrada['ingreso_neto_verificado']['version'], 1)
        self.registrar(monto='4000000')
        actual, _ = construir_version_datos(self.solicitud)
        self.assertNotEqual(actual, historica.version_datos)
        historica.refresh_from_db()
        self.assertEqual((historica.snapshot_entrada, historica.snapshot_salida), (entrada, salida))
        self.assertEqual(historica.estado_ejecucion, 'COMPLETADA')
        nueva = self.iniciar().auditoria
        self.assertNotEqual(nueva.pk, historica.pk)
        self.assertEqual(nueva.snapshot_entrada['ingreso_neto_verificado']['version'], 2)

    def test_evaluacion_en_curso_no_puede_finalizar_con_ingreso_anterior(self):
        self.registrar()
        inicio = self.iniciar()
        self.registrar(monto='4000000')
        cierre = evaluacion_formal._finalizar_evaluacion(auditoria=inicio.auditoria,
                                                      predecision=None, usuario=self.actor)
        self.assertEqual(cierre.auditoria.error_codigo, 'datos_modificados_durante_evaluacion')
        self.assertEqual(cierre.auditoria.estado_ejecucion, 'ERROR_CONTROLADO')

    def test_no_cambia_ingreso_despues_de_firma(self):
        self.solicitud.estado = 'FIRMADO'
        self.solicitud.save(update_fields=['estado'])
        with self.assertRaises(ValidationError):
            self.registrar()

    def test_rollback_si_falla_auditoria(self):
        with patch('contractors.services.ingreso_neto.registrar_evento_timeline_prestador', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                self.registrar()
        self.assertFalse(IngresoNetoVerificadoPrestador.objects.exists())

    def test_admin_exige_permiso_y_rechaza_pagador_y_edicion_borrado(self):
        url = reverse('admin:contractors_ingresonetoverificadoprestador_add')
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, dict(solicitud=self.solicitud.pk, accion='VERIFICADO', monto='3000000',
            fecha_corte=self.hoy.isoformat(), vigente_hasta=(self.hoy + timedelta(days=30)).isoformat(),
            documentos=[self.documento.pk], observacion='Verificacion interna'))
        self.assertEqual(response.status_code, 302)
        registro = self.solicitud.ingresos_netos_verificados.get()
        self.assertEqual(self.client.post(reverse('admin:contractors_ingresonetoverificadoprestador_change', args=[registro.pk]), {}).status_code, 403)
        self.assertEqual(self.client.post(reverse('admin:contractors_ingresonetoverificadoprestador_delete', args=[registro.pk]), {}).status_code, 403)
        lector = get_user_model().objects.create_user('lector-admin-neto', is_staff=True)
        self.client.force_login(lector)
        self.assertEqual(self.client.get(url).status_code, 403)
        PerfilPagador.objects.create(usuario=self.actor, empresa=self.solicitud.empresa)
        self.client.force_login(get_user_model().objects.get(pk=self.actor.pk))
        self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_cliente_no_ve_ingreso_evidencia_ni_observacion(self):
        self.registrar(observacion='REFERENCIA_INTERNA_PRIVADA')
        self.client.force_login(self.solicitud.usuario)
        response = self.client.get('/mi-credito/', HTTP_HOST='contratistas.localhost')
        self.assertEqual(response.status_code, 200)
        for texto in ('MANUAL_VERIFICADA', 'REFERENCIA_INTERNA_PRIVADA', self.documento.archivo.name):
            self.assertNotContains(response, texto)
        for nombre in ('estados_publicos', 'estado_publico_principal', 'timeline_publico_principal'):
            self.assertNotIn('ingreso_neto', str(response.context[nombre]))

    def test_admin_invalidacion_crea_version_y_csrf_es_obligatorio(self):
        original = self.registrar()
        url = reverse('admin:contractors_ingresonetoverificadoprestador_add')
        cliente_csrf = Client(enforce_csrf_checks=True)
        cliente_csrf.force_login(self.actor)
        self.assertEqual(cliente_csrf.post(url, {}).status_code, 403)
        self.client.force_login(self.actor)
        respuesta = self.client.post(url, {'solicitud': self.solicitud.pk, 'accion': 'INVALIDADO',
                                          'observacion': 'Evidencia requiere nueva revision'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.solicitud.ingresos_netos_verificados.first().accion, 'INVALIDADO')
        original.refresh_from_db()
        self.assertEqual(original.accion, 'VERIFICADO')


@skipUnless(connection.vendor == 'postgresql', 'Requiere locks PostgreSQL reales')
class IngresoNetoConcurrenciaPostgresTest(FixtureIngreso, TransactionTestCase):
    def concurrir(self, montos):
        barrera = Barrier(2)
        def guardar(monto):
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                return self.registrar(actor=get_user_model().objects.get(pk=self.actor.pk), monto=monto).pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [pool.submit(guardar, monto) for monto in montos]
            return [f.result(timeout=30) for f in futuros]

    def test_reintentos_equivalentes_una_version(self):
        ids = self.concurrir(['3000000', '3000000'])
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.solicitud.ingresos_netos_verificados.count(), 1)

    def test_cambios_simultaneos_versionados_sin_sobrescribir(self):
        ids = self.concurrir(['3000000', '4000000'])
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(list(self.solicitud.ingresos_netos_verificados.values_list('version', flat=True)), [2, 1])
        vigente = obtener_ingreso_neto_vigente(self.solicitud)
        self.assertEqual(vigente.version, 2)
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), 0)
