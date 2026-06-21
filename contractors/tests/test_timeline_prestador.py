from decimal import Decimal
from types import SimpleNamespace

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from dateutil.relativedelta import relativedelta

from contractors.models import (
    ConfiguracionPortalContratistas,
    ContractorApplication,
    ContractorApplicationDocument,
    InformacionLaboralSolicitudContratista,
    PredecisionPrestadorAudit,
    TimelinePrestador,
)
from contractors.services.documentos import DatosDocumentoSolicitudContratista, registrar_documento_solicitud_contratista
from contractors.services.notificacion_pagador import registrar_novedad_pagador_prestador
from contractors.services.originacion import originar_credito_prestador_desde_auditoria
from contractors.services.predecision_audit import crear_auditoria_predecision_prestador
from contractors.services.solicitudes import DatosSolicitudContratista, crear_solicitud_contratista
from contractors.services.timeline import (
    listar_timeline_por_credito,
    listar_timeline_por_solicitud,
    registrar_evento_timeline_prestador,
)
from gestion_creditos.models import Credito, CuotaAmortizacion, Empresa, HistorialPago, Pagare


User = get_user_model()


def archivo_pdf(nombre='documento.pdf'):
    return SimpleUploadedFile(nombre, b'%PDF-1.4 timeline', content_type='application/pdf')


class ResultadoPredecisionFake:
    def como_dict(self):
        return {
            'eligible': True,
            'decision': 'PREAPROBADO_READ_ONLY',
            'escenario_credito': 'NUEVO_CREDITO',
            'score_status': 'APROBADO',
            'score_resultado': {
                'score_final': '820',
                'banda': {'nombre': 'ALTA'},
                'version_configuracion': 'test',
            },
            'datacredito_status': 'APROBADO',
            'datacredito_resultado': {
                'fuente': 'mock',
                'mora_severa': False,
                'mora_actual': False,
            },
            'capacidad_status': 'APROBADO',
            'riesgo_status': 'APROBADO',
            'monto_maximo_sugerido': '2500000.00',
            'plazo_maximo_sugerido': 10,
            'bloqueos': [],
            'advertencias': [],
            'razones': ['ok'],
        }


class TimelinePrestadorTests(TestCase):
    def setUp(self):
        self.usuario_cliente = User.objects.create_user(
            username='cliente-timeline',
            email='cliente-timeline@example.com',
            password='x',
        )
        self.usuario_staff = User.objects.create_user(
            username='staff-timeline',
            email='staff-timeline@example.com',
            password='x',
            is_staff=True,
        )
        self.usuario_staff.user_permissions.add(
            Permission.objects.get(codename='can_originate_contractor_credit'),
            Permission.objects.get(codename='can_notify_contractor_payer'),
        )
        self.configuracion_portal = ConfiguracionPortalContratistas.objects.create(
            nombre_visible='Portal Timeline',
            host='contratistas.aprobado.com.co',
            slug='contratistas',
            activo=True,
            monto_minimo=Decimal('1000000.00'),
            monto_maximo=Decimal('10000000.00'),
            plazo_minimo_meses=3,
            plazo_maximo_meses=24,
            tasa_mensual=Decimal('2.5000'),
            tasa_comision=Decimal('5.0000'),
            comision_fija=Decimal('0.00'),
            tasa_iva=Decimal('19.0000'),
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Timeline',
            convenio_activo=True,
            tipo_empresa=Empresa.TipoEmpresa.CONVENIO,
        )
        self.solicitud = self._crear_solicitud_base()
        self._crear_datos_contractuales(self.solicitud)
        self._crear_documentos_aprobados(self.solicitud)
        self.auditoria = self._crear_auditoria(self.solicitud)

    def _crear_solicitud_base(self, documento='100200300'):
        return ContractorApplication.objects.create(
            configuracion_portal=self.configuracion_portal,
            usuario=self.usuario_cliente,
            status=ContractorApplication.Estado.EN_REVISION,
            requested_amount=Decimal('2500000.00'),
            term_months=10,
            estimated_monthly_payment=Decimal('280000.00'),
            simulation_payload={'origen': 'timeline'},
            document_type='CC',
            document_number=documento,
            first_name='Laura',
            last_name='Gomez',
            phone='3001112233',
            email=f'{documento}@example.com',
            address='Calle 1 # 2-3',
            accepted_terms=True,
            source_subdomain='contratistas',
        )

    def _crear_datos_contractuales(self, solicitud):
        hoy = timezone.localdate()
        return InformacionLaboralSolicitudContratista.objects.create(
            solicitud=solicitud,
            empresa=self.empresa,
            cargo='Consultora',
            tipo_contrato=InformacionLaboralSolicitudContratista.TipoContrato.PRESTACION_SERVICIOS,
            fecha_inicio_contrato=hoy - relativedelta(months=1),
            fecha_fin_contrato=hoy + relativedelta(months=12),
            valor_total_contrato=Decimal('12000000.00'),
            valor_pagado_contrato=Decimal('3000000.00'),
            valor_pendiente_cobrar=Decimal('9000000.00'),
            empresa_contratante_nombre=self.empresa.nombre,
        )

    def _crear_documentos_aprobados(self, solicitud):
        documentos = {
            ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_FRONTAL: 'cedula-frontal.jpg',
            ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_REVERSO: 'cedula-reverso.jpg',
            ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL: 'contrato.pdf',
            ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO: 'certificado.pdf',
        }
        for tipo, nombre in documentos.items():
            ContractorApplicationDocument.objects.create(
                application=solicitud,
                document_type=tipo,
                file=f'contractors/applications/documents/{nombre}',
                original_filename=nombre,
                content_type='application/pdf' if nombre.endswith('.pdf') else 'image/jpeg',
                file_size=100,
                status=ContractorApplicationDocument.Estado.APROBADO,
            )

    def _crear_auditoria(self, solicitud):
        return PredecisionPrestadorAudit.objects.create(
            solicitud=solicitud,
            usuario=self.usuario_staff,
            escenario_credito=solicitud.escenario_credito,
            decision='PREAPROBADO_READ_ONLY',
            eligible=True,
            requiere_revision_manual=False,
            monto_maximo_sugerido=Decimal('2000000.00'),
            plazo_maximo_sugerido=8,
            score_status='APROBADO',
            score_final=Decimal('820.00'),
            score_banda='ALTA',
            score_version_configuracion='test',
            datacredito_status='APROBADO',
            datacredito_fuente='mock',
            datacredito_mora_severa=False,
            datacredito_mora_actual=False,
            capacidad_status='APROBADO',
            riesgo_status='APROBADO',
            bloqueos=[],
            advertencias=[],
            razones=[],
            resultado_sanitizado={'decision': 'PREAPROBADO_READ_ONLY'},
        )

    def test_crea_evento_de_solicitud_creada(self):
        resultado = crear_solicitud_contratista(
            configuracion_portal=self.configuracion_portal,
            usuario=self.usuario_cliente,
            datos=DatosSolicitudContratista(
                monto_solicitado=Decimal('1500000.00'),
                plazo_meses=6,
                tipo_documento='CC',
                numero_documento='555666777',
                nombres='Carlos',
                apellidos='Perez',
                celular='3009998888',
                correo='carlos@example.com',
                direccion='Carrera 10 # 11-12',
                terminos_aceptados=True,
                subdominio_origen='contratistas',
            ),
        )

        evento = TimelinePrestador.objects.get(solicitud=resultado.solicitud)
        self.assertEqual(evento.tipo_evento, TimelinePrestador.TipoEvento.SOLICITUD_CREADA)
        self.assertEqual(evento.estado_resultante, ContractorApplication.Estado.RECIBIDA)
        self.assertEqual(evento.usuario, self.usuario_cliente)

    def test_crea_evento_documentos_cargados(self):
        resultado = registrar_documento_solicitud_contratista(
            solicitud=self.solicitud,
            datos=DatosDocumentoSolicitudContratista(
                tipo_documento=ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
                archivo=archivo_pdf('contrato-nuevo.pdf'),
                nombre_original='contrato-nuevo.pdf',
                content_type='application/pdf',
                tamano_archivo=len(b'%PDF-1.4 timeline'),
            ),
        )

        evento = TimelinePrestador.objects.filter(
            solicitud=self.solicitud,
            tipo_evento=TimelinePrestador.TipoEvento.DOCUMENTOS_CARGADOS,
        ).latest('created_at', 'id')
        self.assertEqual(evento.estado_resultante, resultado.estado)
        self.assertEqual(evento.metadata['tipo_documento'], ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL)
        self.assertNotIn('file', evento.metadata)

    def test_crea_evento_auditoria_predecision(self):
        auditoria = crear_auditoria_predecision_prestador(
            self.solicitud,
            ResultadoPredecisionFake(),
            usuario=self.usuario_staff,
        )

        evento = TimelinePrestador.objects.filter(
            solicitud=self.solicitud,
            tipo_evento=TimelinePrestador.TipoEvento.AUDITORIA_PREDECISION_CREADA,
        ).latest('created_at', 'id')
        self.assertEqual(evento.metadata['auditoria_id'], auditoria.id)
        self.assertEqual(evento.estado_resultante, 'PREAPROBADO_READ_ONLY')

    def test_crea_evento_originacion_y_no_altera_flujo_financiero(self):
        pagos_antes = HistorialPago.objects.count()
        resultado = originar_credito_prestador_desde_auditoria(self.auditoria, usuario=self.usuario_staff)

        evento = TimelinePrestador.objects.filter(
            solicitud=self.solicitud,
            credito=resultado.credito,
            tipo_evento=TimelinePrestador.TipoEvento.ORIGINADO_EN_REVISION,
        ).get()
        self.assertEqual(evento.estado_resultante, Credito.EstadoCredito.EN_REVISION)
        resultado.credito.refresh_from_db()
        self.assertEqual(resultado.credito.estado, Credito.EstadoCredito.EN_REVISION)
        self.assertIsNone(resultado.credito.fecha_desembolso)
        self.assertEqual(HistorialPago.objects.count(), pagos_antes)
        self.assertEqual(Pagare.objects.count(), 0)
        self.assertEqual(CuotaAmortizacion.objects.count(), 0)

    def test_crea_evento_novedad_pagador(self):
        resultado_originacion = originar_credito_prestador_desde_auditoria(self.auditoria, usuario=self.usuario_staff)

        resultado = registrar_novedad_pagador_prestador(
            resultado_originacion.credito,
            resultado_originacion.solicitud,
            usuario=self.usuario_staff,
        )

        evento = TimelinePrestador.objects.filter(
            solicitud=self.solicitud,
            credito=resultado_originacion.credito,
            tipo_evento=TimelinePrestador.TipoEvento.NOVEDAD_PAGADOR_REGISTRADA,
        ).get()
        self.assertEqual(evento.metadata['novedad_id'], resultado.novedad.id)
        self.assertNotIn('score', str(evento.metadata).lower())
        self.assertNotIn('datacredito', str(evento.metadata).lower())

    def test_listados_por_solicitud_y_credito(self):
        resultado = originar_credito_prestador_desde_auditoria(self.auditoria, usuario=self.usuario_staff)

        por_solicitud = list(listar_timeline_por_solicitud(self.solicitud))
        por_credito = list(listar_timeline_por_credito(resultado.credito))

        self.assertTrue(any(evento.tipo_evento == TimelinePrestador.TipoEvento.ORIGINADO_EN_REVISION for evento in por_solicitud))
        self.assertEqual(len(por_credito), 1)
        self.assertEqual(por_credito[0].credito, resultado.credito)

    def test_metadata_sanitizada(self):
        evento = registrar_evento_timeline_prestador(
            solicitud=self.solicitud,
            tipo_evento=TimelinePrestador.TipoEvento.PREDECISION_EJECUTADA,
            titulo='Evento sensible',
            metadata={
                'token': 'abc',
                'prompt': 'texto completo',
                'archivo_pdf': 'contrato completo',
                'normal': 'ok',
                'nested': {'client_secret': 'secreto', 'visible': 'si'},
            },
            usuario=self.usuario_staff,
        )

        self.assertNotIn('token', evento.metadata)
        self.assertNotIn('prompt', evento.metadata)
        self.assertNotIn('archivo_pdf', evento.metadata)
        self.assertEqual(evento.metadata['normal'], 'ok')
        self.assertNotIn('client_secret', evento.metadata['nested'])

    def test_admin_timeline_solo_lectura(self):
        admin_modelo = admin.site._registry[TimelinePrestador]

        self.assertFalse(admin_modelo.has_add_permission(SimpleNamespace(user=self.usuario_staff)))
        self.assertFalse(admin_modelo.has_change_permission(SimpleNamespace(user=self.usuario_staff)))
        self.assertFalse(admin_modelo.has_delete_permission(SimpleNamespace(user=self.usuario_staff)))
