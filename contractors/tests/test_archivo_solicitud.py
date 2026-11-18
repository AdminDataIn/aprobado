from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
from io import StringIO
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from contractors.models import (
    ArchivoSolicitudPrestador, AutorizacionConsultaDatacreditoPrestador, ContractorApplication,
    ContractorApplicationDocument, EvaluacionPrestadorJob, PredecisionPrestadorAudit, TimelinePrestador,
)
from contractors.services.archivo_solicitud import SolicitudArchivada, archivar_solicitud_prestador
from contractors.services.continuidad_solicitud import solicitud_en_proceso
from contractors.services.evaluacion_formal import evaluar_solicitud_prestador
from contractors.services.politica_financiera_prestador import PARAMETROS, VERSION
from contractors.tests.test_confirmacion_simulacion import FixtureConfirmacion
from gestion_creditos.models import Credito, Empresa, SesionCapturaDocumental
from gestion_creditos.services.captura_documental import purgar_sesiones_expiradas
from integrations.models import ConsultaDatacreditoSnapshot as Snapshot


class ArchivoFixture:
    def setUp(self):
        super().setUp()
        users = get_user_model().objects
        self.actor = users.create_user('staff-archivo', is_staff=True)
        self.actor.user_permissions.add(*Permission.objects.filter(codename__in=(
            'can_archive_contractor_application', 'view_contractorapplication',
            'can_view_contractor_review_queue', 'can_evaluate_contractor_application',
        )))
        self.titular = users.create_user('titular-sintetico')
        self.empresa = Empresa.objects.create(nombre='PRUEBAS DATAIN')
        self.solicitud = self.crear(1)

    def crear(self, pk, **kwargs):
        return ContractorApplication.objects.create(**dict(
            {'pk': pk, 'usuario': self.titular, 'empresa': self.empresa, 'tipo_documento': 'CC',
             'numero_documento': '00000000351',
             'estado': ContractorApplication.Estado.EVALUACION_PENDIENTE}, **kwargs))

    def archivar(self, **kwargs):
        return archivar_solicitud_prestador(**dict(
            {'solicitud_id': self.solicitud.pk, 'actor': self.actor, 'motivo': 'Cierre de prueba sintetica'}, **kwargs))


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver', 'contratistas.localhost'],
                   ROOT_URLCONF='aprobado_web.urls_contractors')
class ArchivoSolicitudTest(ArchivoFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.defaults['HTTP_HOST'] = 'contratistas.localhost'

    def test_permiso_especifico_y_motivo_obligatorio(self):
        sin_permiso = get_user_model().objects.create_user('staff-sin-archivo', is_staff=True)
        for actor in (None, self.titular, sin_permiso):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                self.archivar(actor=actor)
        with self.assertRaises(ValidationError):
            self.archivar(motivo='  ')
        self.assertFalse(ArchivoSolicitudPrestador.objects.exists())

    def test_archivo_idempotente_y_conserva_registros_originales(self):
        ahora = timezone.now()
        auth = AutorizacionConsultaDatacreditoPrestador.objects.create(solicitud=self.solicitud,
            usuario=self.titular, autorizada=True, version_texto='sintetica', texto_hash='a'*64, aceptada_en=ahora)
        snapshot = Snapshot.objects.create(estado='EXITOSO', servicio='decisor', autorizacion_referencia=str(auth.pk),
                                           consultado_en=ahora, vigente_hasta=ahora)
        auditoria = PredecisionPrestadorAudit.objects.create(solicitud=self.solicitud, estado_ejecucion='COMPLETADA',
            version_datos='sintetica', clave_idempotencia='sintetica', iniciada_en=ahora, snapshot_midecisor=snapshot)
        documento = ContractorApplicationDocument.objects.create(solicitud=self.solicitud, uploaded_by=self.actor,
                                                                 tipo_documento='CONTRATO', archivo='sintetico.pdf')
        timeline = TimelinePrestador.objects.create(solicitud=self.solicitud, tipo_evento='DATACREDITO_REUTILIZADO')
        evidencia = [(type(x), x.pk, type(x).objects.values().get(pk=x.pk))
                     for x in (self.solicitud, auth, snapshot, auditoria, documento, timeline)]
        marca, creado = self.archivar()
        self.assertTrue(creado)
        self.assertEqual(marca.actor, self.actor)
        self.assertGreaterEqual(marca.archivada_en, ahora)
        repetida, creado = self.archivar(motivo='No reemplazar el motivo original')
        self.assertFalse(creado)
        self.assertEqual(repetida.pk, marca.pk)
        self.assertEqual(repetida.motivo, 'Cierre de prueba sintetica')
        for modelo, pk, original in evidencia:
            self.assertEqual(modelo.objects.values().get(pk=pk), original)

    def test_continuidad_mismo_documento_conserva_bloqueo_si_hay_otra_activa(self):
        self.assertEqual(solicitud_en_proceso(self.titular), self.solicitud)
        self.archivar()
        self.assertIsNone(solicitud_en_proceso(self.titular))
        self.client.force_login(self.titular)
        self.assertEqual(self.client.get(reverse('contractors:solicitar')).status_code, 200)
        nueva = self.crear(2)
        self.assertEqual(nueva.numero_documento, self.solicitud.numero_documento)
        self.assertEqual(solicitud_en_proceso(self.titular), nueva)
        self.assertRedirects(self.client.get(reverse('contractors:solicitar')), reverse('contractors:mi_credito'),
                             fetch_redirect_response=False)

    def test_bandejas_y_detalle_admin_solo_lectura(self):
        self.archivar()
        self.client.force_login(self.titular)
        response = self.client.get(reverse('contractors:mi_credito'))
        self.assertIsNone(response.context['solicitud_principal'])
        self.assertEqual(self.client.get(reverse('contractors:documentos', args=[1])).status_code, 404)
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(reverse('contractors:admin_detalle', args=[1])).status_code, 200)
        url = reverse('admin:contractors_contractorapplication_changelist')
        self.assertEqual(list(self.client.get(url).context['cl'].result_list), [])
        self.assertEqual(list(self.client.get(url, {'archivo': 'archivadas'}).context['cl'].result_list),
                         [self.solicitud])
        self.assertEqual(self.client.get(reverse('admin:contractors_contractorapplication_change', args=[1]))
                         .context['has_change_permission'], False)

    def test_accion_admin_requiere_confirmacion(self):
        self.client.force_login(self.actor)
        url = reverse('admin:contractors_contractorapplication_changelist')
        data = {'action': 'archivar', ACTION_CHECKBOX_NAME: '1'}
        self.assertContains(self.client.post(url, data), 'Confirmar archivo operativo')
        self.assertFalse(ArchivoSolicitudPrestador.objects.exists())
        response = self.client.post(url, dict(data, confirmar_archivo='1', motivo='Cierre documentado'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ArchivoSolicitudPrestador.objects.get().actor, self.actor)

    def test_evaluacion_archivada_no_crea_auditoria_ni_consulta(self):
        self.archivar()
        with self.assertRaises(SolicitudArchivada):
            evaluar_solicitud_prestador(self.solicitud, solicitado_por=self.actor)
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())

    def test_archivo_durante_analisis_contractual_descarta_resultado(self):
        datos_previos = ContractorApplication.objects.values().get(pk=self.solicitud.pk)
        self.client.force_login(self.titular)

        def analizar(**kwargs):
            self.archivar()
            return type('AnalisisSintetico', (), {'estado': 'COMPLETADO', 'metadata': {}})()

        with patch('contractors.views.analizar_contrato_seguro', side_effect=analizar) as analizar_mock:
            response = self.client.post(reverse('contractors:analizar_contrato', args=[self.solicitud.pk]), {
                'autoriza_analisis_contractual_asistido': 'true',
                'contrato_actual': SimpleUploadedFile('contrato.pdf', b'%PDF-1.4 sintetico'),
            })
        analizar_mock.assert_called_once()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(ContractorApplication.objects.values().get(pk=self.solicitud.pk), datos_previos)
        self.assertFalse(PredecisionPrestadorAudit.objects.exists())

    def test_credito_y_firma_bloquean(self):
        self.solicitud.estado = ContractorApplication.Estado.PENDIENTE_FIRMA
        self.solicitud.save(update_fields=['estado'])
        with self.assertRaisesMessage(ValidationError, 'REFERENCIA_FINANCIERA_O_FIRMA'):
            self.archivar()
        ContractorApplication.objects.filter(pk=1).update(estado='BORRADOR')
        Credito.objects.create(usuario=self.titular, monto_solicitado=1000000, plazo_solicitado=3, linea='LIBRANZA')
        with self.assertRaisesMessage(ValidationError, 'REFERENCIA_FINANCIERA_O_FIRMA'):
            self.archivar()
        self.assertFalse(ArchivoSolicitudPrestador.objects.exists())

    def test_job_en_curso_bloquea_sin_cancelarlo(self):
        job = EvaluacionPrestadorJob.objects.create(solicitud=self.solicitud, actor=self.actor,
            clave_contexto='sintetica', proxima_publicacion=timezone.now())
        with self.assertRaisesMessage(ValidationError, 'EVALUACION_EN_CURSO'):
            self.archivar()
        job.refresh_from_db()
        self.assertEqual(job.estado, EvaluacionPrestadorJob.Estado.PENDIENTE_PUBLICACION)

    def test_purga_omite_sesion_vinculada_a_archivo(self):
        sesion = SesionCapturaDocumental.objects.create(solicitud=self.solicitud, usuario=self.titular,
            producto='PRESTADORES', token_hash='a'*64, expira_en=timezone.now())
        self.archivar()
        resumen = purgar_sesiones_expiradas(con_resumen=True)
        self.assertEqual(resumen['omitidos'], 1)
        self.assertEqual(resumen['errores'], 0)
        sesion.refresh_from_db()
        self.assertEqual(sesion.estado, 'ABIERTA')

    def test_rollback_revierte_marca_si_falla_despues_de_insertar(self):
        create = ArchivoSolicitudPrestador.objects.create

        def fallo(**kwargs):
            create(**kwargs)
            raise RuntimeError('fallo sintetico')

        with patch.object(ArchivoSolicitudPrestador.objects, 'create', side_effect=fallo):
            with self.assertRaisesMessage(RuntimeError, 'fallo sintetico'):
                self.archivar()
        self.assertFalse(ArchivoSolicitudPrestador.objects.exists())
        self.assertTrue(ContractorApplication.objects.activas().filter(pk=1).exists())

    def test_preflight_exige_diez_ids_misma_empresa_y_no_archiva(self):
        def ejecutar(empresa):
            salida = StringIO()
            call_command('preflight_archivo_pruebas_datain', empresa_id=empresa, actor_id=self.actor.pk, stdout=salida)
            return json.loads(salida.getvalue())

        with self.assertRaisesMessage(CommandError, 'LOTE_INCOMPLETO_O_FUERA_DE_EMPRESA'):
            ejecutar(self.empresa.pk)
        for pk in range(2, 11):
            self.crear(pk)
        with self.assertRaisesMessage(CommandError, 'ACTOR_NO_AUTORIZADO'):
            call_command('preflight_archivo_pruebas_datain', empresa_id=self.empresa.pk,
                         actor_id=self.titular.pk)
        with self.assertRaisesMessage(CommandError, 'LOTE_INCOMPLETO_O_FUERA_DE_EMPRESA'):
            ejecutar(self.empresa.pk + 1)
        datos = ejecutar(self.empresa.pk)
        self.assertEqual(datos['modo'], 'DRY_RUN')
        self.assertEqual([s['id'] for s in datos['solicitudes']], list(range(1, 11)))
        self.assertEqual({s['resultado'] for s in datos['solicitudes']}, {'ELEGIBLE'})
        self.assertFalse(ArchivoSolicitudPrestador.objects.exists())


class ArchivoSimulacionTest(FixtureConfirmacion, TestCase):
    @patch('django.utils.timezone.localdate', return_value=date(2026, 11, 18))
    def test_archivada_no_accede_ni_a_estimacion_informativa(self, _corte):
        self.configuracion.version = VERSION
        for campo, valor in PARAMETROS.items():
            setattr(self.configuracion, campo, valor)
        self.configuracion.save()
        self.solicitud.fecha_inicio_contrato = date(2026, 11, 18)
        self.solicitud.fecha_fin_contrato = date(2027, 11, 17)
        self.solicitud.duracion_contrato_meses = 12
        self.solicitud.evidencia_forma_pago = 'Pagos mensuales'
        self.solicitud.save()

        def calcular():
            return self.client.post('/simular/calcular/', json.dumps(self.payload),
                                    content_type='application/json', HTTP_HOST=self.host)

        previa = calcular()
        self.assertEqual(previa.status_code, 200)
        self.assertTrue(previa.json()['solo_informativa'])
        archivar_solicitud_prestador(
            solicitud_id=self.solicitud.pk,
            actor=get_user_model().objects.create_superuser('gestor-archivo-calendario'),
            motivo='Cierre sintetico',
        )
        estado = self.evidencia()
        self.assertEqual(calcular().status_code, 404)
        self.assertEqual(self.client.post('/simular/', self.payload, HTTP_HOST=self.host).status_code, 404)
        with self.assertRaises(SolicitudArchivada):
            self.confirmar()
        self.assertEqual(self.evidencia(), estado)
        self.assertFalse(self.solicitud.evaluacion_jobs.exists())


@skipUnless(connection.vendor == 'postgresql', 'Requiere locks PostgreSQL reales')
class ArchivoConcurrenteTest(ArchivoFixture, TransactionTestCase):
    def test_doble_archivo_crea_una_marca(self):
        barrera = Barrier(2)

        def archivar(motivo):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrera.wait(timeout=10)
                return self.archivar(actor=actor, motivo=motivo)[1]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(archivar, ['Primero', 'Segundo'])), [True, False])
        self.assertEqual(ArchivoSolicitudPrestador.objects.count(), 1)


@skipUnless(connection.vendor == 'postgresql', 'Requiere upgrade PostgreSQL aislado')
class ArchivoMigracionTest(ArchivoFixture, TransactionTestCase):
    def test_0023_solo_agrega_tabla_y_conserva_historial(self):
        ahora = timezone.now()
        autorizacion = AutorizacionConsultaDatacreditoPrestador.objects.create(
            solicitud=self.solicitud, usuario=self.titular, autorizada=True,
            version_texto='sintetica', texto_hash='a' * 64, aceptada_en=ahora)
        snapshot = Snapshot.objects.create(
            estado='SIN_INFORMACION', servicio='historial', autorizacion_referencia=str(autorizacion.pk),
            consultado_en=ahora, vigente_hasta=ahora)
        PredecisionPrestadorAudit.objects.create(
            solicitud=self.solicitud, estado_ejecucion='COMPLETADA', version_datos='sintetica',
            clave_idempotencia='upgrade-sintetico', iniciada_en=ahora, snapshot_hdcplus=snapshot)
        ContractorApplicationDocument.objects.create(
            solicitud=self.solicitud, uploaded_by=self.actor, tipo_documento='CONTRATO', archivo='sintetico.pdf')
        modelos = (ContractorApplication, AutorizacionConsultaDatacreditoPrestador, Snapshot,
                   PredecisionPrestadorAudit, ContractorApplicationDocument)
        historial = {modelo: list(modelo.objects.order_by('pk').values()) for modelo in modelos}
        executor = MigrationExecutor(connection)
        anterior = [('contractors', '0022_configuracionscoreprestador_politica_ingreso_evaluable')]
        actual = [('contractors', '0023_archivosolicitudprestador')]
        try:
            executor.migrate(anterior)
            tablas_antes = set(connection.introspection.table_names())
            executor = MigrationExecutor(connection)
            self.assertEqual(executor.loader.graph.leaf_nodes('contractors'), actual)
            self.assertEqual([(m.name, reversa) for m, reversa in executor.migration_plan(actual)],
                             [(actual[0][1], False)])
            salida = StringIO()
            call_command('migrate', 'contractors', '0023', plan=True, stdout=salida)
            self.assertIn('contractors.0023_archivosolicitudprestador', salida.getvalue())
            self.assertIn('Create model ArchivoSolicitudPrestador', salida.getvalue())
            executor.migrate(actual)
            self.assertEqual(set(connection.introspection.table_names()) - tablas_antes,
                             {ArchivoSolicitudPrestador._meta.db_table})
            for modelo, filas in historial.items():
                self.assertEqual(list(modelo.objects.order_by('pk').values()), filas)
            self.assertFalse(ArchivoSolicitudPrestador.objects.exists())
        finally:
            MigrationExecutor(connection).migrate(actual)
