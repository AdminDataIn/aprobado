from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier, Event
from unittest import skipUnless

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from contractors.forms import SolicitudPrestadorForm
from contractors.models import ContractorApplication
from contractors.tests.test_confirmacion_simulacion import FixtureConfirmacion
from gestion_creditos.forms import CreditoLibranzaForm
from gestion_creditos.models import Credito, CreditoLibranza, Empresa, RestriccionEmpresa
from gestion_creditos.services.restriccion_empresa import (
    EmpresaRestringida, crear_restriccion, levantar_restriccion, restriccion_vigente,
    validar_nueva_solicitud,
)
from usuarios.models import PerfilPagador


class RestriccionFixture:
    def setUp(self):
        super().setUp()
        self.empresa = Empresa.objects.create(nombre='Empresa sintetica', convenio_activo=True)
        self.actor = get_user_model().objects.create_user('gestor-restricciones', is_staff=True)
        self.actor.user_permissions.add(Permission.objects.get(codename='manage_company_restrictions'))
        self.cliente = get_user_model().objects.create_user('solicitante-sintetico')

    def crear(self, **kwargs):
        return crear_restriccion(**{
            'empresa_id': self.empresa.pk, 'producto': 'PRESTADORES', 'actor': self.actor,
            'motivo_interno': 'Motivo administrativo reservado',
            'mensaje_publico': 'Contacta al equipo para continuar.', **kwargs,
        })

    def solicitud(self, **kwargs):
        return ContractorApplication.objects.create(**{
            'usuario': self.cliente, 'empresa': self.empresa, 'nombres': 'Persona',
            'apellidos': 'Sintetica', 'numero_documento': '000000041', **kwargs,
        })


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver', 'contratistas.localhost'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RestriccionEmpresaTest(RestriccionFixture, TestCase):
    def test_alta_auditada_parametro_no_es_automatizacion(self):
        self.assertIsNone(restriccion_vigente(self.empresa.pk, 'PRESTADORES'))
        registro = self.crear(umbral_mora_dias=120)
        self.assertEqual(registro.umbral_mora_dias, 120)
        log = LogEntry.objects.get(object_id=str(registro.pk))
        self.assertEqual(log.user_id, self.actor.pk)
        self.assertIn('CREAR', log.change_message)
        self.assertNotIn('reservado', log.change_message)
        self.assertIsNone(restriccion_vigente(self.empresa.pk, 'LIBRANZA'))
        with self.assertRaises(EmpresaRestringida):
            validar_nueva_solicitud(self.empresa.pk, 'PRESTADORES')

    def test_restriccion_default_90_dias(self):
        self.assertEqual(self.crear().umbral_mora_dias, 90)

    def test_permiso_requerido_incluso_staff(self):
        sin_permiso = get_user_model().objects.create_user('sin-permiso', is_staff=True)
        for actor in (None, self.cliente, sin_permiso):
            with self.subTest(actor=actor and actor.pk), self.assertRaises(PermissionDenied):
                self.crear(actor=actor)
        self.assertFalse(RestriccionEmpresa.objects.exists())

    def test_pagador_no_gestiona_aunque_tenga_permiso(self):
        PerfilPagador.objects.create(usuario=self.actor, empresa=self.empresa)
        with self.assertRaises(PermissionDenied):
            self.crear()

    def test_actor_inactivo_no_gestiona(self):
        self.actor.is_active = False
        with self.assertRaises(PermissionDenied):
            self.crear()

    def test_inicio_futuro_y_vencimiento(self):
        ahora = timezone.now()
        registro = self.crear(inicio=ahora + timedelta(days=1), vence_en=ahora + timedelta(days=2))
        self.assertIsNone(restriccion_vigente(self.empresa.pk, 'PRESTADORES', ahora=ahora))
        self.assertEqual(restriccion_vigente(self.empresa.pk, 'PRESTADORES',
            ahora=ahora + timedelta(days=1)).pk, registro.pk)
        self.assertIsNone(restriccion_vigente(self.empresa.pk, 'PRESTADORES',
            ahora=ahora + timedelta(days=2)))

    def test_fechas_producto_mensajes_y_umbral_invalidos(self):
        ahora = timezone.now()
        for datos in ({'vence_en': ahora, 'inicio': ahora}, {'umbral_mora_dias': 0},
                      {'producto': 'MARKETPLACE'}, {'motivo_interno': ''}, {'mensaje_publico': ''},
                      {'inicio': ahora.replace(tzinfo=None)}):
            with self.subTest(datos=tuple(datos)), self.assertRaises(ValidationError):
                self.crear(**datos)
        self.assertFalse(RestriccionEmpresa.objects.exists())

    def test_no_dos_restricciones_abiertas(self):
        self.crear()
        with self.assertRaises(ValidationError):
            self.crear()
        self.assertEqual(RestriccionEmpresa.objects.count(), 1)

    def test_levantamiento_auditado_idempotente_y_requiere_motivo(self):
        registro = self.crear()
        with self.assertRaises(ValidationError):
            levantar_restriccion(restriccion_id=registro.pk, actor=self.actor, motivo='')
        levantar_restriccion(restriccion_id=registro.pk, actor=self.actor, motivo='Revision autorizada')
        levantar_restriccion(restriccion_id=registro.pk, actor=self.actor, motivo='Reenvio')
        registro.refresh_from_db()
        self.assertEqual(registro.levantada_por_id, self.actor.pk)
        self.assertEqual(registro.motivo_levantamiento, 'Revision autorizada')
        self.assertEqual(LogEntry.objects.count(), 2)
        self.assertIsNone(restriccion_vigente(self.empresa.pk, 'PRESTADORES'))
        self.solicitud()

    def test_no_modificacion_ordinaria_ni_borrado_del_historial(self):
        registro = RestriccionEmpresa.objects.get(pk=self.crear().pk)
        with self.assertRaises(ValidationError):
            registro.save()
        with self.assertRaises(ValidationError):
            registro.delete()

    def test_creacion_directa_orm_y_formulario_no_omiten_control(self):
        self.crear()
        with self.assertRaises(EmpresaRestringida):
            self.solicitud()
        form = SolicitudPrestadorForm(data={'empresa': self.empresa.pk}, usuario=self.cliente)
        self.assertFalse(form.is_valid())
        self.assertIn('empresa', form.errors)
        self.assertIn('data-restriccion=', str(form['empresa']))
        self.assertIn('disabled', str(form['empresa']))
        self.assertFalse(ContractorApplication.objects.exists())

    def test_solicitud_anterior_no_se_bloquea_retroactivamente(self):
        solicitud = self.solicitud()
        self.crear()
        validar_nueva_solicitud(self.empresa.pk, 'PRESTADORES', creada_en=solicitud.created_at)
        solicitud.cargo = 'Actividad actualizada'
        solicitud.save(update_fields=['cargo'])
        form = SolicitudPrestadorForm(instance=solicitud, usuario=self.cliente)
        self.assertNotIn('data-restriccion=', str(form['empresa']))

    def test_cambio_empresa_no_omite_restriccion(self):
        solicitud = self.solicitud()
        otra = Empresa.objects.create(nombre='Otra sintetica', convenio_activo=True)
        self.crear(empresa_id=otra.pk)
        solicitud.empresa = otra
        with self.assertRaises(EmpresaRestringida):
            solicitud.save(update_fields=['empresa_id'])
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.empresa_id, self.empresa.pk)

    def test_libranza_backend_y_api_solo_mensaje_publico(self):
        self.crear(producto='LIBRANZA')
        form = CreditoLibranzaForm(data={'empresa': self.empresa.pk})
        self.assertFalse(form.is_valid())
        self.assertIn('empresa', form.errors)
        response = self.client.get(reverse('libranza:buscar_empresas'), {'q': 'sintetica'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['mensaje_restriccion'], 'Contacta al equipo para continuar.')
        self.assertNotContains(response, 'reservado')
        self.assertNotContains(response, 'umbral_mora')
        credito = Credito.objects.create(usuario=self.cliente, monto_solicitado=1000000,
            plazo_solicitado=3, linea='LIBRANZA')
        with self.assertRaises(EmpresaRestringida):
            CreditoLibranza.objects.create(credito=credito, empresa=self.empresa)
        self.assertFalse(CreditoLibranza.objects.exists())

    def test_credito_existente_conserva_consulta_y_actualizacion(self):
        credito = Credito.objects.create(usuario=self.cliente, monto_solicitado=1000000,
            plazo_solicitado=3, linea='LIBRANZA')
        detalle = CreditoLibranza.objects.create(credito=credito, empresa=self.empresa)
        self.crear(producto='LIBRANZA')
        detalle.telefono = '0000000000'
        detalle.save(update_fields=['telefono'])
        credito.refresh_from_db()
        self.assertEqual(credito.monto_solicitado, 1000000)
        self.assertEqual(CreditoLibranza.objects.get(pk=detalle.pk).telefono, '0000000000')

    def test_cambio_empresa_libranza_no_omite_restriccion(self):
        credito = Credito.objects.create(usuario=self.cliente, monto_solicitado=1000000,
            plazo_solicitado=3, linea='LIBRANZA')
        detalle = CreditoLibranza.objects.create(credito=credito, empresa=self.empresa)
        otra = Empresa.objects.create(nombre='Otra convenio', convenio_activo=True)
        self.crear(empresa_id=otra.pk, producto='LIBRANZA')
        detalle.empresa = otra
        with self.assertRaises(EmpresaRestringida):
            detalle.save(update_fields=['empresa'])
        detalle.refresh_from_db()
        self.assertEqual(detalle.empresa_id, self.empresa.pk)

    def test_endpoint_alternativo_caso_especial_revierte_todos_los_efectos(self):
        from libranza.tests.test_special_case_preproduction import SpecialCasePreproductionFlowTests
        from libranza.services.special_case_originator import originate_special_case_libranza
        fixture = SpecialCasePreproductionFlowTests()
        fixture.empresa, fixture.staff = self.empresa, self.actor
        audit = fixture.create_audit()
        self.crear(producto='LIBRANZA')
        usuarios_antes = get_user_model().objects.count()
        with self.assertRaises(EmpresaRestringida):
            originate_special_case_libranza(audit_id=audit.pk, applicant_data=fixture.applicant_data(),
                files=fixture.files(), originated_by=self.actor)
        audit.refresh_from_db()
        self.assertIsNone(audit.credito_id)
        self.assertFalse(Credito.objects.exists())
        self.assertFalse(CreditoLibranza.objects.exists())
        self.assertEqual(get_user_model().objects.count(), usuarios_antes)

    def test_admin_sin_permiso_y_con_csrf_no_puede_operar(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.actor)
        url = reverse('admin:gestion_creditos_restriccionempresa_add')
        self.assertEqual(cliente.get(url).status_code, 200)
        self.assertEqual(cliente.post(url, {}).status_code, 403)
        self.client.force_login(self.cliente)
        self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_admin_alta_y_levantamiento_sin_edicion_del_original(self):
        self.client.force_login(self.actor)
        response = self.client.post(reverse('admin:gestion_creditos_restriccionempresa_add'), {
            'empresa': self.empresa.pk, 'producto': 'PRESTADORES', 'motivo_interno': 'Reservado',
            'mensaje_publico': 'Consulta con Aprobado.', 'umbral_mora_dias': 90,
        })
        self.assertEqual(response.status_code, 302)
        registro = RestriccionEmpresa.objects.get()
        url = reverse('admin:gestion_creditos_restriccionempresa_levantar', args=[registro.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIsNone(RestriccionEmpresa.objects.get().levantada_en)
        self.assertEqual(self.client.post(url, {'motivo': 'Autorizado'}).status_code, 302)
        self.assertEqual(LogEntry.objects.count(), 2)

    def test_levantamiento_exige_permiso_en_servicio_y_endpoint(self):
        registro = self.crear()
        intruso = get_user_model().objects.create_user('staff-sin-permiso', is_staff=True)
        with self.assertRaises(PermissionDenied):
            levantar_restriccion(restriccion_id=registro.pk, actor=intruso, motivo='No autorizado')
        self.client.force_login(intruso)
        url = reverse('admin:gestion_creditos_restriccionempresa_levantar', args=[registro.pk])
        self.assertEqual(self.client.post(url, {'motivo': 'No autorizado'}).status_code, 403)
        self.assertIsNone(RestriccionEmpresa.objects.get().levantada_en)


class ConfirmacionConRestriccionTest(FixtureConfirmacion, TestCase):
    def test_restriccion_posterior_no_bloquea_solicitud_ni_replay(self):
        actor = get_user_model().objects.create_superuser('gestor', password='synthetic')
        crear_restriccion(empresa_id=self.empresa.pk, producto='PRESTADORES', actor=actor,
            motivo_interno='Restriccion nueva')
        self.assertFalse(self.confirmar().reutilizada)
        self.assertTrue(self.confirmar().reutilizada)
        self.assertEqual(self.solicitud.timeline_operativo.count(), 1)

    def test_confirmacion_directa_revalida_contexto_no_legitimo(self):
        actor = get_user_model().objects.create_superuser('gestor', password='synthetic')
        registro = crear_restriccion(empresa_id=self.empresa.pk, producto='PRESTADORES', actor=actor,
            motivo_interno='Restriccion previa')
        # Simulate a pre-existing imported row that bypassed the ordinary create guard.
        ContractorApplication.objects.filter(pk=self.solicitud.pk).update(
            created_at=registro.creada_en + timedelta(seconds=1))
        with self.assertRaises(EmpresaRestringida):
            self.confirmar()
        self.assertFalse(self.solicitud.timeline_operativo.exists())


@skipUnless(connection.vendor == 'postgresql', 'Requiere locks PostgreSQL reales')
class RestriccionConcurrenciaTest(RestriccionFixture, TransactionTestCase):
    def trabajador(self, accion):
        close_old_connections()
        try:
            return accion()
        finally:
            connections.close_all()

    def test_doble_alta_concurrente_genera_una_restriccion_y_auditoria(self):
        barrera = Barrier(2)
        def alta():
            actor = get_user_model().objects.get(pk=self.actor.pk)
            barrera.wait(timeout=10)
            try:
                self.crear(actor=actor)
                return 'CREADA'
            except ValidationError:
                return 'RECHAZADA'
        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(self.trabajador, [alta, alta]))
        self.assertCountEqual(resultados, ['CREADA', 'RECHAZADA'])
        self.assertEqual(RestriccionEmpresa.objects.count(), 1)
        self.assertEqual(LogEntry.objects.count(), 1)

    def test_restriccion_confirmada_antes_de_alta_bloquea_solicitud_concurrente(self):
        bloqueada, continuar = Event(), Event()
        def restringir():
            with transaction.atomic():
                self.crear(actor=get_user_model().objects.get(pk=self.actor.pk))
                bloqueada.set()
                if not continuar.wait(10):
                    raise AssertionError('Tiempo de coordinacion agotado')
        def solicitar():
            if not bloqueada.wait(10):
                raise AssertionError('No se adquirio lock')
            continuar.set()
            try:
                self.solicitud()
            except EmpresaRestringida:
                return 'BLOQUEADA'
            return 'CREADA'
        with ThreadPoolExecutor(max_workers=2) as pool:
            primera = pool.submit(self.trabajador, restringir)
            segunda = pool.submit(self.trabajador, solicitar)
            primera.result(timeout=20)
            self.assertEqual(segunda.result(timeout=20), 'BLOQUEADA')
        self.assertFalse(ContractorApplication.objects.exists())

    def test_solicitud_confirmada_antes_de_restriccion_conserva_derecho(self):
        bloqueada, continuar = Event(), Event()
        def solicitar():
            with transaction.atomic():
                solicitud = self.solicitud()
                bloqueada.set()
                if not continuar.wait(10):
                    raise AssertionError('Tiempo de coordinacion agotado')
                return solicitud.pk
        def restringir():
            if not bloqueada.wait(10):
                raise AssertionError('No se adquirio lock')
            continuar.set()
            self.crear(actor=get_user_model().objects.get(pk=self.actor.pk))
        with ThreadPoolExecutor(max_workers=2) as pool:
            primera = pool.submit(self.trabajador, solicitar)
            segunda = pool.submit(self.trabajador, restringir)
            solicitud_id = primera.result(timeout=20)
            segunda.result(timeout=20)
        solicitud = ContractorApplication.objects.get(pk=solicitud_id)
        validar_nueva_solicitud(self.empresa.pk, 'PRESTADORES', creada_en=solicitud.created_at)


class RestriccionMigracionTest(TransactionTestCase):
    def test_check_y_modelos_sin_migraciones_pendientes(self):
        salida = StringIO()
        call_command('check', fail_level='WARNING', stdout=salida, stderr=salida)
        self.assertIn('no issues', salida.getvalue())
        salida = StringIO()
        call_command('makemigrations', check=True, dry_run=True, stdout=salida)
        self.assertIn('No changes detected', salida.getvalue())

    def test_upgrade_desde_0050_no_reescribe_empresas_ni_registra_restricciones(self):
        anterior = [('gestion_creditos', '0050_ocr_documental_local')]
        actual = [('gestion_creditos', '0051_restriccion_empresa')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(anterior)
            apps = executor.loader.project_state(anterior).apps
            EmpresaAnterior = apps.get_model('gestion_creditos', 'Empresa')
            registro = EmpresaAnterior.objects.create(nombre='Empresa historica sintetica', convenio_activo=True)
            datos = EmpresaAnterior.objects.filter(pk=registro.pk).values().get()
            executor = MigrationExecutor(connection)
            executor.migrate(actual)
            self.assertEqual(Empresa.objects.filter(pk=registro.pk).values().get(), datos)
            self.assertFalse(RestriccionEmpresa.objects.exists())
        finally:
            MigrationExecutor(connection).migrate(actual)
