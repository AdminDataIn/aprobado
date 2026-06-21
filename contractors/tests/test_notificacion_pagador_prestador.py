from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from contractors.models import NovedadPagadorPrestador
from contractors.services.notificacion_pagador import (
    construir_contexto_novedad_prestador,
    notificar_pagador_credito_prestador_en_revision,
    obtener_destinatarios_pagador_empresa,
)
from contractors.tests.test_originacion_prestadores import OriginacionPrestadoresTests
from gestion_creditos.models import (
    Credito,
    CreditoLibranza,
    CuotaAmortizacion,
    HistorialPago,
    Notificacion,
    Pagare,
)
from usuarios.models import PerfilPagador


User = get_user_model()


class NotificacionPagadorPrestadorTests(OriginacionPrestadoresTests):
    def setUp(self):
        super().setUp()
        self.usuario_staff.user_permissions.add(
            Permission.objects.get(codename='can_notify_contractor_payer'),
        )
        self.pagador_uno = User.objects.create_user(
            username='pagador-uno',
            email='pagador1@example.com',
            password='x',
            first_name='Pagador',
            last_name='Uno',
        )
        self.pagador_dos = User.objects.create_user(
            username='pagador-dos',
            email='pagador2@example.com',
            password='x',
            first_name='Pagador',
            last_name='Dos',
        )
        PerfilPagador.objects.create(usuario=self.pagador_uno, empresa=self.empresa, es_pagador=True)
        PerfilPagador.objects.create(usuario=self.pagador_dos, empresa=self.empresa, es_pagador=True)

    def _originar(self):
        return self.client_originacion()

    def client_originacion(self):
        from contractors.services.originacion import originar_credito_prestador_desde_auditoria

        return originar_credito_prestador_desde_auditoria(self.auditoria, usuario=self.usuario_staff)

    def test_obtiene_todos_los_pagadores_activos(self):
        destinatarios = obtener_destinatarios_pagador_empresa(self.empresa)

        self.assertEqual(len(destinatarios), 2)
        self.assertEqual(
            {destinatario['email'] for destinatario in destinatarios},
            {'pagador1@example.com', 'pagador2@example.com'},
        )

    def test_crea_novedad_segura_y_notificaciones(self):
        resultado_originacion = self._originar()

        resultado = notificar_pagador_credito_prestador_en_revision(
            resultado_originacion.credito,
            resultado_originacion.solicitud,
            usuario=self.usuario_staff,
        )

        self.assertTrue(resultado.creada)
        self.assertEqual(resultado.notificaciones_creadas, 2)
        self.assertEqual(resultado.novedad.estado, NovedadPagadorPrestador.Estado.ENVIADA)
        self.assertEqual(Notificacion.objects.count(), 2)
        metadata = resultado.novedad.metadata
        self.assertEqual(metadata['titulo'], 'Novedad informativa')
        self.assertEqual(metadata['descripcion'], 'Credito de prestador originado en revision')
        self.assertEqual(metadata['mensaje_operativo'], 'No requiere aprobacion del pagador')
        self.assertIn('****', metadata['documento_enmascarado'])
        self.assertNotIn('datacredito', str(metadata).lower())
        self.assertNotIn('score', str(metadata).lower())
        self.assertNotIn('pagare', str(metadata).lower())

    def test_no_falla_si_no_hay_destinatarios(self):
        PerfilPagador.objects.all().delete()
        resultado_originacion = self._originar()

        resultado = notificar_pagador_credito_prestador_en_revision(
            resultado_originacion.credito,
            resultado_originacion.solicitud,
            usuario=self.usuario_staff,
        )

        self.assertTrue(resultado.creada)
        self.assertEqual(resultado.notificaciones_creadas, 0)
        self.assertEqual(resultado.novedad.estado, NovedadPagadorPrestador.Estado.REGISTRADA)
        self.assertEqual(resultado.novedad.destinatarios, [])
        self.assertIn('sin_destinatarios_pagador', resultado.novedad.metadata['advertencias'])

    def test_evita_duplicados(self):
        resultado_originacion = self._originar()

        primero = notificar_pagador_credito_prestador_en_revision(
            resultado_originacion.credito,
            resultado_originacion.solicitud,
            usuario=self.usuario_staff,
        )
        segundo = notificar_pagador_credito_prestador_en_revision(
            resultado_originacion.credito,
            resultado_originacion.solicitud,
            usuario=self.usuario_staff,
        )

        self.assertTrue(primero.creada)
        self.assertFalse(segundo.creada)
        self.assertEqual(NovedadPagadorPrestador.objects.count(), 1)
        self.assertEqual(Notificacion.objects.count(), 2)

    def test_no_cambia_estado_credito_no_crea_pagos_desembolso_pagare_ni_cuotas(self):
        resultado_originacion = self._originar()
        estado_inicial = resultado_originacion.credito.estado

        notificar_pagador_credito_prestador_en_revision(
            resultado_originacion.credito,
            resultado_originacion.solicitud,
            usuario=self.usuario_staff,
        )

        resultado_originacion.credito.refresh_from_db()
        self.assertEqual(resultado_originacion.credito.estado, estado_inicial)
        self.assertEqual(HistorialPago.objects.count(), 0)
        self.assertEqual(Pagare.objects.count(), 0)
        self.assertEqual(CuotaAmortizacion.objects.count(), 0)
        self.assertIsNone(resultado_originacion.credito.fecha_desembolso)
        self.assertEqual(Credito.objects.count(), 1)
        self.assertEqual(CreditoLibranza.objects.count(), 1)

    def test_contexto_no_incluye_score_datacredito_ni_documentos_sensibles(self):
        resultado_originacion = self._originar()

        contexto = construir_contexto_novedad_prestador(
            resultado_originacion.credito,
            resultado_originacion.solicitud,
        )

        texto = str(contexto).lower()
        self.assertNotIn('datacredito', texto)
        self.assertNotIn('score', texto)
        self.assertNotIn('documento_identidad', texto)
        self.assertNotIn('contrato', texto)

    def test_boton_notificar_requiere_permiso(self):
        resultado_originacion = self._originar()
        url = reverse('gestion:prestadores_riesgo_detalle', args=[self.auditoria.id])

        self.client.force_login(self.usuario_staff)
        respuesta_con_permiso = self.client.get(url)
        self.assertContains(respuesta_con_permiso, 'Notificar pagador')

        usuario_solo_bandeja = User.objects.create_user(
            username='solo-bandeja',
            email='solo-bandeja@example.com',
            password='x',
            is_staff=True,
        )
        usuario_solo_bandeja.user_permissions.add(
            Permission.objects.get(codename='can_view_contractor_risk_queue'),
        )
        self.client.force_login(usuario_solo_bandeja)
        respuesta_sin_permiso = self.client.get(url)
        self.assertNotContains(respuesta_sin_permiso, 'Notificar pagador')

        self.assertEqual(resultado_originacion.credito.estado, Credito.EstadoCredito.EN_REVISION)

    def test_post_vista_notifica_pagador(self):
        self._originar()
        self.client.force_login(self.usuario_staff)
        url = reverse('gestion:prestadores_riesgo_detalle', args=[self.auditoria.id])

        respuesta = self.client.post(url, {'accion': 'notificar_pagador'}, follow=True)

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Novedad al pagador registrada')
        self.assertEqual(NovedadPagadorPrestador.objects.count(), 1)
        self.assertEqual(Notificacion.objects.count(), 2)
