from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from gestion_creditos.forms import CreditoLibranzaForm
from gestion_creditos.models import (
    AprobacionPagadorLibranza,
    Credito,
    CreditoLibranza,
    Empresa,
    HistorialEstado,
    Pagare,
)
from gestion_creditos.services.anulacion_credito import (
    MOTIVO_ANULACION_ERROR_DATOS,
    anular_credito_por_error_datos,
)
from gestion_creditos.services.libranza_rules import obtener_creditos_libranza_bloqueantes
from gestion_creditos.services.zapsign_client import enviar_pagare_a_zapsign


User = get_user_model()


class AnulacionCreditoPorErrorDatosTest(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username='cliente-anulacion',
            email='correo-inicial@aprobado.test',
            password='test1234',
        )
        self.actor = User.objects.create_user(
            username='staff-anulacion',
            email='staff-anulacion@aprobado.test',
            password='test1234',
            is_staff=True,
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa Anulacion',
            convenio_activo=True,
        )
        self._secuencia = 0

    def _crear_credito(self, estado=Credito.EstadoCredito.PENDIENTE_FIRMA, cedula=None):
        self._secuencia += 1
        cedula = cedula or f'100000{self._secuencia:04d}'
        credito = Credito.objects.create(
            usuario=self.usuario,
            numero_credito=f'CR-ANULACION-{self._secuencia:04d}',
            linea=Credito.LineaCredito.LIBRANZA,
            estado=estado,
            monto_solicitado=Decimal('1000000.00'),
            plazo_solicitado=6,
        )
        CreditoLibranza.objects.create(
            credito=credito,
            nombres='Cliente',
            apellidos='Prueba',
            cedula=cedula,
            direccion='Calle 1',
            telefono='3000000000',
            correo_electronico='cliente@aprobado.test',
            empresa=self.empresa,
            ingresos_mensuales=Decimal('2500000.00'),
            cedula_frontal='credito_libranza/cedulas/frontal.png',
            cedula_trasera='credito_libranza/cedulas/trasera.png',
            certificado_bancario='credito_libranza/certificados_bancarios/certificado.pdf',
        )
        return credito

    def _crear_pagare(self, credito, estado=Pagare.EstadoPagare.SENT):
        return Pagare.objects.create(
            credito=credito,
            numero_pagare=f'PAG-TEST-{credito.pk}',
            estado=estado,
            archivo_pdf='pagares/pagare-prueba.pdf',
            zapsign_doc_token=f'token-trazabilidad-{credito.pk}',
            zapsign_sign_url=f'https://firma.test/{credito.pk}',
            evidencias={'evento_previo': {'estado': 'enviado'}},
            creado_por=self.actor,
        )

    def test_servicio_anula_credito_pendiente_firma(self):
        credito = self._crear_credito()

        resultado = anular_credito_por_error_datos(
            credito=credito,
            actor=self.actor,
            motivo=MOTIVO_ANULACION_ERROR_DATOS,
        )

        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.ANULADO)
        self.assertEqual(resultado.estado_anterior, Credito.EstadoCredito.PENDIENTE_FIRMA)
        self.assertEqual(resultado.estado_nuevo, Credito.EstadoCredito.ANULADO)

    def test_servicio_cancela_pagare_sent_y_conserva_trazabilidad(self):
        credito = self._crear_credito()
        pagare = self._crear_pagare(credito)
        token = pagare.zapsign_doc_token
        url = pagare.zapsign_sign_url

        anular_credito_por_error_datos(
            credito=credito,
            actor=self.actor,
            motivo=MOTIVO_ANULACION_ERROR_DATOS,
        )

        pagare.refresh_from_db()
        self.assertEqual(pagare.estado, Pagare.EstadoPagare.CANCELLED)
        self.assertEqual(pagare.zapsign_doc_token, token)
        self.assertEqual(pagare.zapsign_sign_url, url)
        self.assertEqual(pagare.evidencias['evento_previo'], {'estado': 'enviado'})
        self.assertEqual(
            pagare.evidencias['anulacion_administrativa_credito']['estado_pagare_anterior'],
            Pagare.EstadoPagare.SENT,
        )

    @patch('gestion_creditos.services.zapsign_client.ZapSignClient')
    def test_envio_zapsign_rechaza_credito_anulado_antes_de_consumir_api(self, cliente_zapsign):
        credito = self._crear_credito(estado=Credito.EstadoCredito.ANULADO)
        pagare = self._crear_pagare(credito, estado=Pagare.EstadoPagare.CREATED)

        with self.assertRaisesMessage(ValueError, 'crédito ANULADO'):
            enviar_pagare_a_zapsign(
                pagare,
                'https://archivos.test/pagare.pdf',
            )

        cliente_zapsign.assert_not_called()
        pagare.refresh_from_db()
        self.assertEqual(pagare.estado, Pagare.EstadoPagare.CREATED)

    @patch('gestion_creditos.services.zapsign_client.ZapSignClient')
    def test_envio_zapsign_conserva_flujo_permitido(self, cliente_zapsign):
        credito = self._crear_credito(estado=Credito.EstadoCredito.PENDIENTE_FIRMA)
        pagare = self._crear_pagare(credito, estado=Pagare.EstadoPagare.CREATED)
        cliente_zapsign.return_value.crear_documento.return_value = {
            'token': 'token-nuevo',
            'signers': [{'sign_url': 'https://firma.test/nueva'}],
        }

        resultado = enviar_pagare_a_zapsign(
            pagare,
            'https://archivos.test/pagare.pdf',
        )

        resultado.refresh_from_db()
        self.assertEqual(resultado.estado, Pagare.EstadoPagare.SENT)
        self.assertEqual(resultado.zapsign_doc_token, 'token-nuevo')
        self.assertEqual(resultado.zapsign_sign_url, 'https://firma.test/nueva')
        cliente_zapsign.return_value.crear_documento.assert_called_once()

    def test_servicio_conserva_aprobacion_pagador(self):
        credito = self._crear_credito()
        aprobacion = AprobacionPagadorLibranza.objects.create(
            credito=credito,
            empresa=self.empresa,
            usuario=self.actor,
            nivel=AprobacionPagadorLibranza.Nivel.FINAL,
            decision=AprobacionPagadorLibranza.Decision.APROBADO,
        )

        anular_credito_por_error_datos(
            credito=credito,
            actor=self.actor,
            motivo=MOTIVO_ANULACION_ERROR_DATOS,
        )

        aprobacion.refresh_from_db()
        self.assertEqual(aprobacion.decision, AprobacionPagadorLibranza.Decision.APROBADO)
        self.assertEqual(credito.aprobaciones_pagador_libranza.count(), 1)

    def test_servicio_crea_historial_estado(self):
        credito = self._crear_credito()

        anular_credito_por_error_datos(
            credito=credito,
            actor=self.actor,
            motivo=MOTIVO_ANULACION_ERROR_DATOS,
        )

        historial = HistorialEstado.objects.get(credito=credito)
        self.assertEqual(historial.estado_anterior, Credito.EstadoCredito.PENDIENTE_FIRMA)
        self.assertEqual(historial.estado_nuevo, Credito.EstadoCredito.ANULADO)
        self.assertEqual(historial.usuario_modificacion, self.actor)
        self.assertEqual(historial.motivo, MOTIVO_ANULACION_ERROR_DATOS)

    def test_servicio_bloquea_estados_financieros_y_postfirma(self):
        estados_bloqueados = (
            Credito.EstadoCredito.FIRMADO,
            Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
            Credito.EstadoCredito.ACTIVO,
            Credito.EstadoCredito.EN_MORA,
            Credito.EstadoCredito.PAGADO,
        )
        for estado in estados_bloqueados:
            with self.subTest(estado=estado):
                credito = self._crear_credito(estado=estado)
                with self.assertRaises(ValidationError):
                    anular_credito_por_error_datos(
                        credito=credito,
                        actor=self.actor,
                        motivo=MOTIVO_ANULACION_ERROR_DATOS,
                    )
                credito.refresh_from_db()
                self.assertEqual(credito.estado, estado)

    def test_servicio_es_atomico_si_falla_el_historial(self):
        credito = self._crear_credito()
        pagare = self._crear_pagare(credito)

        with patch(
            'gestion_creditos.services.anulacion_credito.HistorialEstado.objects.create',
            side_effect=RuntimeError('fallo controlado'),
        ):
            with self.assertRaises(RuntimeError):
                anular_credito_por_error_datos(
                    credito=credito,
                    actor=self.actor,
                    motivo=MOTIVO_ANULACION_ERROR_DATOS,
                )

        credito.refresh_from_db()
        pagare.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.PENDIENTE_FIRMA)
        self.assertEqual(pagare.estado, Pagare.EstadoPagare.SENT)

    def test_estado_anulado_es_terminal(self):
        credito = self._crear_credito(estado=Credito.EstadoCredito.ANULADO)
        credito.estado = Credito.EstadoCredito.EN_REVISION

        with self.assertRaisesMessage(ValidationError, 'no puede cambiar de estado'):
            credito.save(update_fields=['estado'])

        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.ANULADO)

    def test_regla_duplicidad_excluye_anulado(self):
        credito = self._crear_credito(
            estado=Credito.EstadoCredito.ANULADO,
            cedula='70000032',
        )

        bloqueantes = obtener_creditos_libranza_bloqueantes('70000032')

        self.assertFalse(bloqueantes.filter(credito=credito).exists())

    def test_clean_cedula_permite_credito_anterior_anulado(self):
        self._crear_credito(estado=Credito.EstadoCredito.ANULADO, cedula='70000032')
        form = CreditoLibranzaForm()
        form.cleaned_data = {'cedula': '70000032'}

        self.assertEqual(form.clean_cedula(), '70000032')

    def test_clean_cedula_bloquea_credito_vivo(self):
        for estado in (
            Credito.EstadoCredito.PENDIENTE_FIRMA,
            Credito.EstadoCredito.ACTIVO,
        ):
            with self.subTest(estado=estado):
                cedula = f'90000{self._secuencia + 1:05d}'
                self._crear_credito(estado=estado, cedula=cedula)
                form = CreditoLibranzaForm()
                form.cleaned_data = {'cedula': cedula}

                with self.assertRaisesMessage(ValidationError, 'Ya existe un credito'):
                    form.clean_cedula()

    def test_comando_dry_run_no_modifica_credito(self):
        credito = self._crear_credito()
        pagare = self._crear_pagare(credito)
        stdout = StringIO()

        call_command(
            'anular_credito_por_error_datos',
            '--numero-credito', credito.numero_credito,
            '--motivo', 'Correo mal digitado. Se requiere nueva solicitud.',
            stdout=stdout,
        )

        credito.refresh_from_db()
        pagare.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.PENDIENTE_FIRMA)
        self.assertEqual(pagare.estado, Pagare.EstadoPagare.SENT)
        self.assertIn('Dry-run', stdout.getvalue())

    def test_comando_apply_anula_credito(self):
        credito = self._crear_credito()
        pagare = self._crear_pagare(credito)
        stdout = StringIO()

        call_command(
            'anular_credito_por_error_datos',
            '--numero-credito', credito.numero_credito,
            '--motivo', 'Correo mal digitado. Se requiere nueva solicitud.',
            '--apply',
            stdout=stdout,
        )

        credito.refresh_from_db()
        pagare.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.ANULADO)
        self.assertEqual(pagare.estado, Pagare.EstadoPagare.CANCELLED)
        self.assertIn('PENDIENTE_FIRMA -> ANULADO', stdout.getvalue())

    def test_comando_apply_es_idempotente_si_ya_esta_anulado(self):
        credito = self._crear_credito(estado=Credito.EstadoCredito.ANULADO)
        stdout = StringIO()

        call_command(
            'anular_credito_por_error_datos',
            '--numero-credito', credito.numero_credito,
            '--motivo', 'Reintento operativo controlado.',
            '--apply',
            stdout=stdout,
        )

        credito.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.ANULADO)
        self.assertFalse(HistorialEstado.objects.filter(credito=credito).exists())
        self.assertIn('ya esta ANULADO', stdout.getvalue())

    def test_accion_admin_anula_con_permiso_change_credito(self):
        credito = self._crear_credito()
        pagare = self._crear_pagare(credito)
        self.actor.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='gestion_creditos',
                codename='change_credito',
            )
        )
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse('admin:gestion_creditos_credito_changelist'),
            {
                'action': 'anular_por_error_datos',
                '_selected_action': [credito.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        credito.refresh_from_db()
        pagare.refresh_from_db()
        self.assertEqual(credito.estado, Credito.EstadoCredito.ANULADO)
        self.assertEqual(pagare.estado, Pagare.EstadoPagare.CANCELLED)
