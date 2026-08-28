import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from queue import Queue
from threading import Barrier, Thread
from unittest import skipIf
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gestion_creditos.credit_services import activar_credito
from gestion_creditos.models import (
    CondicionOriginacionLibranza,
    Credito,
    CreditoReglaEspecialAudit,
    Empresa,
)
from gestion_creditos.services.costo_originacion_libranza import (
    CostoOriginacionLibranzaError,
    POLITICA_ESPECIAL,
    POLITICA_V1,
    POLITICA_V2,
    crear_snapshot_originacion_libranza,
    resolver_costo_originacion_libranza,
)


User = get_user_model()


def fecha_referencia(year, month, day):
    return timezone.make_aware(datetime(year, month, day, 9, 0, 0))


class PoliticaOriginacionLibranzaTests(TestCase):
    def test_frontera_temporal_preserva_v1_y_activa_v2(self):
        anterior = resolver_costo_originacion_libranza(
            fecha_referencia=fecha_referencia(2026, 8, 31),
            monto='1000000',
            plazo=4,
        )
        vigente = resolver_costo_originacion_libranza(
            fecha_referencia=fecha_referencia(2026, 9, 1),
            monto='1000000',
            plazo=4,
        )

        self.assertEqual(anterior.codigo_politica, POLITICA_V1)
        self.assertEqual(anterior.porcentaje_originacion, Decimal('10.0000'))
        self.assertEqual(vigente.codigo_politica, POLITICA_V2)
        self.assertEqual(vigente.porcentaje_originacion, Decimal('11.0000'))

    def test_bandas_v2(self):
        esperados = {
            1: Decimal('10.0000'),
            2: Decimal('10.0000'),
            3: Decimal('11.0000'),
            4: Decimal('11.0000'),
            5: Decimal('12.0000'),
            6: Decimal('12.0000'),
        }
        for plazo, porcentaje in esperados.items():
            with self.subTest(plazo=plazo):
                resultado = resolver_costo_originacion_libranza(
                    fecha_referencia=fecha_referencia(2026, 9, 1),
                    monto='1000000',
                    plazo=plazo,
                )
                self.assertEqual(resultado.porcentaje_originacion, porcentaje)

    def test_redondea_con_round_half_up(self):
        resultado = resolver_costo_originacion_libranza(
            fecha_referencia=fecha_referencia(2026, 9, 1),
            monto='100.05',
            plazo=3,
        )

        self.assertEqual(resultado.valor_originacion, Decimal('11.01'))
        self.assertEqual(resultado.valor_iva, Decimal('2.09'))

    def test_plazo_siete_normal_v2_es_rechazado(self):
        with self.assertRaises(CostoOriginacionLibranzaError) as context:
            resolver_costo_originacion_libranza(
                fecha_referencia=fecha_referencia(2026, 9, 1),
                monto='1000000',
                plazo=7,
            )

        self.assertEqual(context.exception.codigo, 'plazo_normal_fuera_politica')

    def test_regla_especial_prevalece_sobre_tabla_normal(self):
        audit = CreditoReglaEspecialAudit.objects.create(
            amount=Decimal('5000000.00'),
            term_months=12,
            monthly_rate=Decimal('1.9000'),
            commission_rate=None,
            commission_amount=Decimal('345678.90'),
            vat_amount=Decimal('65678.99'),
            estimated_monthly_payment=Decimal('500000.00'),
            estimated_total_payment=Decimal('6000000.00'),
            estimated_interest=Decimal('123456.00'),
            simulation_payload={'vat_rate': '19.00'},
            business_reason='Condicion especial aprobada para prueba.',
        )

        resultado = resolver_costo_originacion_libranza(
            fecha_referencia=fecha_referencia(2026, 9, 1),
            monto='5000000',
            plazo=12,
            es_especial=True,
            regla_especial=audit,
        )

        self.assertEqual(resultado.codigo_politica, POLITICA_ESPECIAL)
        self.assertEqual(resultado.valor_originacion, Decimal('345678.90'))
        self.assertEqual(resultado.valor_iva, Decimal('65678.99'))
        self.assertIsNone(resultado.porcentaje_originacion)


class SnapshotYActivacionLibranzaTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username='snapshot-libranza',
            email='snapshot@aprobado.test',
            password='123456',
        )

    def crear_credito(self, *, fecha, plazo=4, estado=Credito.EstadoCredito.EN_REVISION):
        credito = Credito.objects.create(
            usuario=self.usuario,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=estado,
            monto_solicitado=Decimal('1000000.00'),
            plazo_solicitado=plazo,
            monto_aprobado=Decimal('1000000.00'),
            plazo=plazo,
            tasa_interes=Decimal('1.90'),
        )
        Credito.objects.filter(pk=credito.pk).update(fecha_solicitud=fecha)
        credito.refresh_from_db()
        return credito

    def test_snapshot_es_inmutable(self):
        credito = self.crear_credito(fecha=fecha_referencia(2026, 9, 1))
        snapshot = crear_snapshot_originacion_libranza(credito=credito)
        hash_original = snapshot.snapshot_hash
        snapshot.valor_originacion = Decimal('1.00')

        with self.assertRaises(ValidationError):
            snapshot.save()

        snapshot.refresh_from_db()
        self.assertEqual(snapshot.valor_originacion, Decimal('110000.00'))
        self.assertEqual(snapshot.snapshot_hash, hash_original)
        with self.assertRaises(ValidationError):
            snapshot.delete()

    def test_activacion_usa_snapshot_y_descarta_valor_manipulado(self):
        credito = self.crear_credito(fecha=fecha_referencia(2026, 9, 1))
        snapshot = crear_snapshot_originacion_libranza(credito=credito)
        Credito.objects.filter(pk=credito.pk).update(
            comision=Decimal('1.00'),
            iva_comision=Decimal('0.19'),
        )
        credito.refresh_from_db()

        activar_credito(credito)
        credito.refresh_from_db()

        self.assertEqual(credito.comision, snapshot.valor_originacion)
        self.assertEqual(credito.iva_comision, snapshot.valor_iva)

    def test_libranza_nueva_sin_snapshot_no_activa(self):
        credito = self.crear_credito(fecha=fecha_referencia(2026, 9, 1))

        with self.assertRaisesMessage(ValidationError, 'requiere condiciones de originacion'):
            activar_credito(credito)

    def test_historico_sin_snapshot_conserva_fallback_legado(self):
        credito = self.crear_credito(fecha=fecha_referencia(2026, 8, 31))

        activar_credito(credito)
        credito.refresh_from_db()

        self.assertEqual(credito.comision, Decimal('100000.00'))
        self.assertEqual(credito.iva_comision, Decimal('19000.00'))
        self.assertFalse(CondicionOriginacionLibranza.objects.filter(credito=credito).exists())


@skipIf(connection.vendor != 'postgresql', 'Requiere PostgreSQL real.')
class SnapshotOriginacionLibranzaConcurrenciaPostgresTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        usuario = User.objects.create_user(
            username='snapshot-libranza-concurrente',
            email='snapshot-concurrente@aprobado.test',
            password='123456',
        )
        self.credito = Credito.objects.create(
            usuario=usuario,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.EN_REVISION,
            monto_solicitado=Decimal('1000000.00'),
            plazo_solicitado=3,
        )
        Credito.objects.filter(pk=self.credito.pk).update(
            fecha_solicitud=fecha_referencia(2026, 9, 1),
        )

    def test_dos_transacciones_consolidan_un_unico_snapshot(self):
        barrera = Barrier(2)
        resultados = Queue()
        errores = Queue()

        def consolidar():
            close_old_connections()
            try:
                credito = Credito.objects.get(pk=self.credito.pk)
                barrera.wait(timeout=10)
                snapshot = crear_snapshot_originacion_libranza(credito=credito)
                resultados.put((
                    snapshot.pk,
                    snapshot.codigo_politica,
                    snapshot.version_politica,
                    snapshot.valor_originacion,
                    snapshot.valor_iva,
                    snapshot.snapshot_hash,
                ))
            except Exception as exc:
                errores.put(exc)
            finally:
                close_old_connections()

        hilos = [Thread(target=consolidar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)

        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertEqual(list(errores.queue), [])
        valores = list(resultados.queue)
        self.assertEqual(len(valores), 2)
        self.assertEqual(valores[0], valores[1])
        self.assertEqual(
            CondicionOriginacionLibranza.objects.filter(credito=self.credito).count(),
            1,
        )
        snapshot = CondicionOriginacionLibranza.objects.get(credito=self.credito)
        self.assertEqual(snapshot.codigo_politica, POLITICA_V2)
        self.assertEqual(snapshot.version_politica, '2')
        self.assertEqual(snapshot.valor_originacion, Decimal('110000.00'))
        self.assertEqual(snapshot.valor_iva, Decimal('20900.00'))


class SimuladorYSolicitudLibranzaTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media.cleanup)
        self.usuario = User.objects.create_user(
            username='solicitud-libranza',
            email='solicitud@aprobado.test',
            password='123456',
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Solicitud Libranza',
            convenio_activo=True,
        )

    def test_endpoint_simulador_usa_politica_backend(self):
        with patch('django.utils.timezone.now', return_value=fecha_referencia(2026, 9, 1)):
            response = self.client.get(
                reverse('libranza:simular_originacion'),
                {'monto': '1000000', 'plazo': '5'},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['codigo_politica'], POLITICA_V2)
        self.assertEqual(payload['porcentaje_originacion'], '12.0000')
        self.assertEqual(payload['valor_originacion'], '120000.00')

    def test_frontend_no_calcula_comision_hardcodeada(self):
        for ruta in (
            Path('templates/libranza/simulacion_libranza.html'),
            Path('templates/gestion_creditos/solicitud_libranza.html'),
        ):
            source = ruta.read_text(encoding='utf-8')
            self.assertIn("libranza:simular_originacion", source)
            self.assertNotIn('montoSolicitado * 0.10', source)

    @patch('gestion_creditos.views.solicitudes.procesar_certificado_bancario')
    def test_solicitud_recalcula_y_persiste_snapshot_sin_confiar_en_frontend(self, procesar):
        self.client.force_login(self.usuario)
        archivos = {
            'cedula_frontal': SimpleUploadedFile('frontal.png', b'frontal', content_type='image/png'),
            'cedula_trasera': SimpleUploadedFile('trasera.png', b'trasera', content_type='image/png'),
            'certificado_bancario': SimpleUploadedFile(
                'certificado.pdf',
                b'%PDF-1.4 certificado',
                content_type='application/pdf',
            ),
        }
        datos = {
            'valor_credito': '1000000',
            'ingresos_mensuales': '3000000',
            'plazo': '3',
            'nombres': 'Cliente',
            'apellidos': 'Solicitud',
            'cedula': '1234567890',
            'direccion': 'Calle Principal 123',
            'telefono': '3001234567',
            'correo_electronico': 'cliente.solicitud@example.com',
            'empresa': str(self.empresa.pk),
            'comision': '1.00',
            'iva_comision': '0.19',
            **archivos,
        }

        with patch('django.utils.timezone.now', return_value=fecha_referencia(2026, 9, 1)):
            response = self.client.post(reverse('libranza:solicitar'), datos)

        self.assertEqual(response.status_code, 302)
        credito = Credito.objects.get(usuario=self.usuario)
        snapshot = credito.condicion_originacion_libranza
        self.assertEqual(snapshot.codigo_politica, POLITICA_V2)
        self.assertEqual(snapshot.porcentaje_originacion, Decimal('11.0000'))
        self.assertEqual(credito.comision, Decimal('110000.00'))
        self.assertEqual(credito.iva_comision, Decimal('20900.00'))
        procesar.assert_called_once()
