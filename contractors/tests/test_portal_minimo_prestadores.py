from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from contractors.models import ContractorApplication, ContractorApplicationDocument
from gestion_creditos.models import Credito, CreditoLibranza, Empresa


class PortalMinimoPrestadoresTest(TestCase):
    host = 'contratistas.localhost'

    def setUp(self):
        self.usuario = get_user_model().objects.create_user(
            username='prestador',
            email='prestador@example.com',
            password='123456',
        )
        self.otro_usuario = get_user_model().objects.create_user(
            username='otro-prestador',
            email='otro@example.com',
            password='123456',
        )
        self.staff = get_user_model().objects.create_user(
            username='staff-prestadores',
            email='staff@example.com',
            password='123456',
            is_staff=True,
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Convenio',
            convenio_activo=True,
        )
        self.otra_empresa = Empresa.objects.create(
            nombre='Otra Empresa Convenio',
            convenio_activo=True,
        )

    def test_raiz_subdominio_redirige_a_solicitar(self):
        response = self.client.get('/', HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/solicitar/')

    def test_solicitar_exige_login(self):
        response = self.client.get('/solicitar/', HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/login/', response['Location'])
        self.assertIn('next=/solicitar/', response['Location'])

    def test_usuario_autenticado_crea_solicitud_basica_con_empresa_existente(self):
        self.client.force_login(self.usuario)
        response = self.client.post(
            '/solicitar/',
            self._payload_solicitud(),
            HTTP_HOST=self.host,
        )

        solicitud = ContractorApplication.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(solicitud.usuario, self.usuario)
        self.assertEqual(solicitud.empresa, self.empresa)
        self.assertEqual(solicitud.monto_solicitado, 3000000)
        self.assertEqual(solicitud.plazo_meses, 12)
        self.assertEqual(response['Location'], f'/solicitud/{solicitud.id}/documentos/')

    def test_empresa_debe_ser_empresa_existente_activa(self):
        empresa_inactiva = Empresa.objects.create(
            nombre='Empresa sin convenio',
            convenio_activo=False,
        )
        self.client.force_login(self.usuario)
        payload = self._payload_solicitud()
        payload['empresa'] = empresa_inactiva.id

        response = self.client.post('/solicitar/', payload, HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Debes elegir una empresa valida de la lista.')
        self.assertEqual(ContractorApplication.objects.count(), 0)

    def test_usuario_ve_solo_sus_solicitudes_en_mi_credito(self):
        propia = self._crear_solicitud(self.usuario)
        self._crear_solicitud(self.otro_usuario, empresa=self.otra_empresa, documento='987654321')
        self.client.force_login(self.usuario)

        response = self.client.get('/mi-credito/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Solicitud</th>')
        self.assertContains(response, propia.empresa.nombre)
        self.assertNotContains(response, self.otra_empresa.nombre)

    def test_usuario_no_puede_ver_documentos_de_solicitud_ajena(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.otro_usuario)

        response = self.client.get(
            f'/solicitud/{solicitud.id}/documentos/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 404)

    def test_carga_documento_queda_asociada_a_solicitud(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)

        archivo = SimpleUploadedFile(
            'contrato.pdf',
            b'%PDF-1.4 contrato',
            content_type='application/pdf',
        )
        response = self.client.post(
            f'/solicitud/{solicitud.id}/documentos/',
            {
                'tipo_documento': ContractorApplicationDocument.TipoDocumento.CONTRATO,
                'archivo': archivo,
            },
            HTTP_HOST=self.host,
        )

        documento = ContractorApplicationDocument.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(documento.solicitud, solicitud)
        self.assertEqual(documento.uploaded_by, self.usuario)

    def test_reemplazo_de_documento_funciona(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)

        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CONTRATO, 'contrato.pdf')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CONTRATO, 'contrato-reemplazo.pdf')

        self.assertEqual(ContractorApplicationDocument.objects.count(), 1)
        documento = ContractorApplicationDocument.objects.get()
        self.assertIn('CONTRATO', documento.archivo.name)
        self.assertEqual(documento.uploaded_by, self.usuario)

    def test_estado_cambia_a_documentos_cargados_con_todos_los_obligatorios(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)

        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CEDULA_FRONTAL, 'frontal.jpg', b'imagen')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CEDULA_TRASERA, 'trasera.jpg', b'imagen')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CONTRATO, 'contrato.pdf')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO, 'certificado.pdf')

        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, ContractorApplication.Estado.DOCUMENTOS_CARGADOS)

    def test_link_seguro_descarga_documento_solo_para_dueno(self):
        solicitud = self._crear_solicitud(self.usuario)
        documento = ContractorApplicationDocument.objects.create(
            solicitud=solicitud,
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO,
            archivo=SimpleUploadedFile('contrato.pdf', b'%PDF-1.4 contrato', content_type='application/pdf'),
            uploaded_by=self.usuario,
        )
        url = f'/solicitud/{solicitud.id}/documentos/{documento.id}/descargar/'

        self.client.force_login(self.usuario)
        response = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)

        self.client.force_login(self.otro_usuario)
        response = self.client.get(url, HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 404)

    def test_simulador_redirige_si_no_hay_solicitud(self):
        self.client.force_login(self.usuario)

        response = self.client.get('/simular/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/solicitar/')

    def test_simulador_muestra_advertencia_si_faltan_documentos(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)

        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Debes completar la carga documental antes de simular.')
        self.assertContains(response, 'Faltan documentos obligatorios para completar la evaluacion.')

    def test_simulador_solo_permite_ver_solicitud_propia(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.otro_usuario)

        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 404)

    def test_simulador_calcula_cuota_preliminar_con_datos_validos(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='12000000',
            valor_pendiente='8000000',
            monto='3000000',
            plazo=12,
        )
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)

        creditos_antes = Credito.objects.count()
        creditos_libranza_antes = CreditoLibranza.objects.count()
        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cuota estimada preliminar')
        self.assertContains(response, 'Porcentaje sobre valor pendiente')
        self.assertContains(response, 'Esta evaluacion no aprueba')
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), creditos_libranza_antes)

    def test_simulador_advierte_monto_mayor_al_valor_pendiente(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='5000000',
            valor_pendiente='2000000',
            monto='3000000',
            plazo=12,
        )
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)

        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'El monto solicitado supera el valor pendiente por cobrar del contrato.')

    def test_simulador_advierte_contrato_vencido(self):
        solicitud = self._crear_solicitud(
            self.usuario,
            valor_total='5000000',
            valor_pendiente='3000000',
            monto='2000000',
            plazo=10,
            fecha_fin=timezone.localdate() - timedelta(days=1),
        )
        self.client.force_login(self.usuario)
        self._cargar_documentos_obligatorios(solicitud)

        response = self.client.get(f'/simular/?solicitud_id={solicitud.id}', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'El contrato registrado se encuentra vencido.')

    def test_usuario_no_autenticado_no_accede_a_bandeja_staff(self):
        response = self.client.get('/gestion/prestadores/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/login/', response['Location'])

    def test_usuario_autenticado_no_staff_no_accede_a_bandeja(self):
        self.client.force_login(self.usuario)

        response = self.client.get('/gestion/prestadores/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 403)

    def test_staff_accede_a_bandeja_y_ve_solicitud(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.staff)

        response = self.client.get('/gestion/prestadores/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{solicitud.id}')
        self.assertContains(response, solicitud.nombre_completo)
        self.assertContains(response, solicitud.empresa.nombre)
        self.assertContains(response, 'Ver detalle')

    def test_staff_accede_al_detalle(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.staff)

        response = self.client.get(f'/gestion/prestadores/{solicitud.id}/', HTTP_HOST=self.host)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Datos de la solicitud')
        self.assertContains(response, 'Datos contractuales')
        self.assertContains(response, 'Simulacion preliminar read-only')

    def test_staff_puede_ver_documentos_cargados(self):
        solicitud = self._crear_solicitud(self.usuario)
        documento = ContractorApplicationDocument.objects.create(
            solicitud=solicitud,
            tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO,
            archivo=SimpleUploadedFile('contrato.pdf', b'%PDF-1.4 contrato', content_type='application/pdf'),
            uploaded_by=self.usuario,
        )
        self.client.force_login(self.staff)

        detalle = self.client.get(f'/gestion/prestadores/{solicitud.id}/', HTTP_HOST=self.host)
        descarga = self.client.get(
            f'/gestion/prestadores/documentos/{documento.id}/descargar/',
            HTTP_HOST=self.host,
        )

        self.assertEqual(detalle.status_code, 200)
        self.assertContains(detalle, 'Abrir documento')
        self.assertEqual(descarga.status_code, 200)

    def test_staff_puede_cambiar_estado_a_en_revision(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.staff)
        creditos_antes = Credito.objects.count()
        creditos_libranza_antes = CreditoLibranza.objects.count()

        response = self.client.post(
            f'/gestion/prestadores/{solicitud.id}/',
            {'estado': ContractorApplication.Estado.EN_REVISION},
            HTTP_HOST=self.host,
        )

        solicitud.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(solicitud.estado, ContractorApplication.Estado.EN_REVISION)
        self.assertEqual(Credito.objects.count(), creditos_antes)
        self.assertEqual(CreditoLibranza.objects.count(), creditos_libranza_antes)

    def test_usuario_normal_no_puede_cambiar_estado(self):
        solicitud = self._crear_solicitud(self.usuario)
        self.client.force_login(self.usuario)

        response = self.client.post(
            f'/gestion/prestadores/{solicitud.id}/',
            {'estado': ContractorApplication.Estado.EN_REVISION},
            HTTP_HOST=self.host,
        )

        solicitud.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(solicitud.estado, ContractorApplication.Estado.DOCUMENTOS_PENDIENTES)

    def _payload_solicitud(self):
        return {
            'escenario_credito': ContractorApplication.EscenarioCredito.NUEVO_CREDITO,
            'tipo_documento': ContractorApplication.TipoDocumento.CEDULA_CIUDADANIA,
            'numero_documento': '123456789',
            'nombres': 'Ana Maria',
            'apellidos': 'Perez Gomez',
            'celular': '3001234567',
            'correo': 'ana@example.com',
            'direccion': 'Calle 1 # 2-3',
            'cargo': 'Consultora',
            'empresa': self.empresa.id,
            'fecha_inicio_contrato': '2026-01-01',
            'fecha_fin_contrato': '2026-12-31',
            'valor_total_contrato': '12000000',
            'valor_pendiente_cobrar': '8000000',
            'monto_solicitado': '3000000',
            'plazo_meses': '12',
        }

    def _crear_solicitud(
        self,
        usuario,
        empresa=None,
        documento='123456789',
        valor_total=None,
        valor_pendiente=None,
        monto='3000000',
        plazo=12,
        fecha_fin=None,
    ):
        return ContractorApplication.objects.create(
            usuario=usuario,
            empresa=empresa or self.empresa,
            escenario_credito=ContractorApplication.EscenarioCredito.NUEVO_CREDITO,
            tipo_documento=ContractorApplication.TipoDocumento.CEDULA_CIUDADANIA,
            numero_documento=documento,
            nombres='Ana Maria',
            apellidos='Perez Gomez',
            celular='3001234567',
            correo='ana@example.com',
            direccion='Calle 1 # 2-3',
            cargo='Consultora',
            fecha_inicio_contrato=timezone.localdate(),
            fecha_fin_contrato=fecha_fin or (timezone.localdate() + timedelta(days=180)),
            valor_total_contrato=valor_total,
            valor_pendiente_cobrar=valor_pendiente,
            monto_solicitado=monto,
            plazo_meses=plazo,
        )

    def _cargar_documento(self, solicitud, tipo_documento, nombre, contenido=b'%PDF-1.4 documento'):
        return self.client.post(
            f'/solicitud/{solicitud.id}/documentos/',
            {
                'tipo_documento': tipo_documento,
                'archivo': SimpleUploadedFile(nombre, contenido),
            },
            HTTP_HOST=self.host,
        )

    def _cargar_documentos_obligatorios(self, solicitud):
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CEDULA_FRONTAL, 'frontal.jpg', b'imagen')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CEDULA_TRASERA, 'trasera.jpg', b'imagen')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CONTRATO, 'contrato.pdf')
        self._cargar_documento(solicitud, ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO, 'certificado.pdf')
