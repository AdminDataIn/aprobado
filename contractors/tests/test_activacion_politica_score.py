from io import StringIO
from threading import Barrier, Thread
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from contractors.admin import (
    CambioPoliticaScorePrestadorAuditAdmin,
    ConfiguracionScorePrestadorAdmin,
)
from contractors.models import (
    CambioPoliticaScorePrestadorAudit,
    ConfiguracionScorePrestador,
    PredecisionPrestadorAudit,
)
from contractors.services.politica_score import activar_politica_score_prestador
from gestion_creditos.models import Empresa
from usuarios.models import PerfilPagador


VERSION_V1 = 'prestadores-score-demo-v1'
VERSION_V2 = 'prestadores-score-demo-v2'


def _crear_politicas_demo():
    call_command('configurar_politica_prestadores_demo', stdout=StringIO())
    call_command('configurar_politica_prestadores_demo_v2', stdout=StringIO())
    return (
        ConfiguracionScorePrestador.objects.get(version=VERSION_V1),
        ConfiguracionScorePrestador.objects.get(version=VERSION_V2),
    )


def _crear_actor(username='actor-politica'):
    actor = get_user_model().objects.create_user(
        username=username,
        password='test',
        is_staff=True,
    )
    actor.user_permissions.add(
        Permission.objects.get(
            content_type__app_label='contractors',
            codename='can_activate_contractor_score_policy',
        )
    )
    return actor


class ActivacionPoliticaScorePrestadorTest(TestCase):
    def setUp(self):
        self.v1, self.v2 = _crear_politicas_demo()
        self.actor = _crear_actor()

    def activar_v2(self, motivo='Prueba controlada de activacion V2'):
        return activar_politica_score_prestador(
            politica_id=self.v2.pk,
            actor=self.actor,
            motivo=motivo,
        )

    def test_v2_permanece_inactiva_antes_de_una_activacion_explicita(self):
        self.v1.refresh_from_db()
        self.v2.refresh_from_db()

        self.assertTrue(self.v1.activa)
        self.assertFalse(self.v2.activa)
        self.assertFalse(CambioPoliticaScorePrestadorAudit.objects.exists())

    def test_activar_v2_desactiva_v1_y_registra_auditoria(self):
        pesos_v1 = self._pesos(self.v1)
        pesos_v2 = self._pesos(self.v2)
        predecisiones = PredecisionPrestadorAudit.objects.count()

        resultado = self.activar_v2()

        self.v1.refresh_from_db()
        self.v2.refresh_from_db()
        self.assertFalse(self.v1.activa)
        self.assertTrue(self.v2.activa)
        self.assertEqual(
            ConfiguracionScorePrestador.objects.filter(activa=True).count(),
            1,
        )
        self.assertTrue(resultado.cambio_realizado)
        self.assertEqual(resultado.politica_anterior, self.v1)
        self.assertEqual(resultado.politica_nueva, self.v2)
        self.assertEqual(
            resultado.auditoria.accion,
            CambioPoliticaScorePrestadorAudit.Accion.ACTIVACION,
        )
        self.assertEqual(resultado.auditoria.actor, self.actor)
        self.assertEqual(
            resultado.auditoria.motivo,
            'Prueba controlada de activacion V2',
        )
        self.assertEqual(
            resultado.auditoria.snapshot_anterior['version'],
            VERSION_V1,
        )
        self.assertEqual(resultado.auditoria.snapshot_nuevo['version'], VERSION_V2)
        self.assertEqual(self._pesos(self.v1), pesos_v1)
        self.assertEqual(self._pesos(self.v2), pesos_v2)
        self.assertEqual(PredecisionPrestadorAudit.objects.count(), predecisiones)

    def test_pesos_invalidos_hacen_rollback_y_conservan_v1(self):
        ConfiguracionScorePrestador.objects.filter(pk=self.v2.pk).update(
            peso_midecisor='0.44000',
        )

        with self.assertRaises(ValidationError):
            self.activar_v2()

        self.v1.refresh_from_db()
        self.v2.refresh_from_db()
        self.assertTrue(self.v1.activa)
        self.assertFalse(self.v2.activa)
        self.assertFalse(CambioPoliticaScorePrestadorAudit.objects.exists())

    def test_bandas_incompletas_hacen_rollback_y_conservan_v1(self):
        self.v2.bandas.filter(nombre='REVISION').delete()

        with self.assertRaises(ValidationError):
            self.activar_v2()

        self.v1.refresh_from_db()
        self.v2.refresh_from_db()
        self.assertTrue(self.v1.activa)
        self.assertFalse(self.v2.activa)
        self.assertFalse(CambioPoliticaScorePrestadorAudit.objects.exists())

    def test_configuracion_financiera_invalida_conserva_v1(self):
        financiera = self.v2.configuracion_financiera
        type(financiera).objects.filter(pk=financiera.pk).update(activo=False)

        with self.assertRaises(ValidationError):
            self.activar_v2()

        self.v1.refresh_from_db()
        self.v2.refresh_from_db()
        self.assertTrue(self.v1.activa)
        self.assertFalse(self.v2.activa)
        self.assertFalse(CambioPoliticaScorePrestadorAudit.objects.exists())

    def test_politica_expirada_no_desactiva_v1(self):
        ConfiguracionScorePrestador.objects.filter(pk=self.v2.pk).update(
            fecha_vigencia_hasta=timezone.localdate() - timedelta(days=1),
        )

        with self.assertRaisesMessage(ValidationError, 'expirada'):
            self.activar_v2()

        self.assertTrue(
            ConfiguracionScorePrestador.objects.get(pk=self.v1.pk).activa
        )
        self.assertFalse(
            ConfiguracionScorePrestador.objects.get(pk=self.v2.pk).activa
        )

    def test_fuentes_requeridas_inconsistentes_bloquean_activacion(self):
        ConfiguracionScorePrestador.objects.filter(pk=self.v2.pk).update(
            permite_evaluar_sin_hdc=True,
        )

        with self.assertRaises(ValidationError):
            self.activar_v2()

        self.assertTrue(
            ConfiguracionScorePrestador.objects.get(pk=self.v1.pk).activa
        )

    def test_parametrizacion_v2_distinta_a_la_aprobada_no_activa(self):
        ConfiguracionScorePrestador.objects.filter(pk=self.v2.pk).update(
            peso_datacredito='0.01000',
        )

        with self.assertRaisesMessage(ValidationError, 'parametrizacion aprobada'):
            self.activar_v2()

        self.assertTrue(
            ConfiguracionScorePrestador.objects.get(pk=self.v1.pk).activa
        )

    def test_politica_inexistente_no_modifica_estado(self):
        with self.assertRaises(ValidationError):
            activar_politica_score_prestador(
                politica_id=999999,
                actor=self.actor,
                motivo='Politica inexistente',
            )

        self.assertTrue(
            ConfiguracionScorePrestador.objects.get(pk=self.v1.pk).activa
        )

    def test_usuario_sin_permiso_no_activa(self):
        sin_permiso = get_user_model().objects.create_user(
            username='sin-permiso',
            password='test',
            is_staff=True,
        )

        with self.assertRaises(PermissionDenied):
            activar_politica_score_prestador(
                politica_id=self.v2.pk,
                actor=sin_permiso,
                motivo='Intento sin permiso',
            )

        self.assertTrue(
            ConfiguracionScorePrestador.objects.get(pk=self.v1.pk).activa
        )
        self.assertFalse(
            ConfiguracionScorePrestador.objects.get(pk=self.v2.pk).activa
        )

    def test_perfil_pagador_no_activa_aunque_sea_staff_y_tenga_permiso(self):
        empresa = Empresa.objects.create(nombre='Empresa politica pagador')
        PerfilPagador.objects.create(usuario=self.actor, empresa=empresa)

        with self.assertRaises(PermissionDenied):
            self.activar_v2(motivo='Intento de pagador con permiso accidental')

        self.assertTrue(
            ConfiguracionScorePrestador.objects.get(pk=self.v1.pk).activa
        )
        self.assertFalse(
            ConfiguracionScorePrestador.objects.get(pk=self.v2.pk).activa
        )

    def test_motivo_es_obligatorio(self):
        with self.assertRaises(ValidationError):
            self.activar_v2(motivo='   ')

        self.assertTrue(
            ConfiguracionScorePrestador.objects.get(pk=self.v1.pk).activa
        )

    def test_activacion_repetida_es_idempotente(self):
        self.activar_v2()

        segundo = self.activar_v2()
        cantidad_tras_segundo = CambioPoliticaScorePrestadorAudit.objects.count()
        tercero = self.activar_v2()

        self.assertFalse(segundo.cambio_realizado)
        self.assertEqual(
            segundo.auditoria.accion,
            CambioPoliticaScorePrestadorAudit.Accion.SIN_CAMBIO,
        )
        self.assertEqual(tercero.auditoria.pk, segundo.auditoria.pk)
        self.assertEqual(
            CambioPoliticaScorePrestadorAudit.objects.count(),
            cantidad_tras_segundo,
        )
        self.assertEqual(
            ConfiguracionScorePrestador.objects.filter(activa=True).count(),
            1,
        )

    def test_auditoria_es_inmutable(self):
        auditoria = self.activar_v2().auditoria
        auditoria.motivo = 'Intento de edicion'

        with self.assertRaises(ValidationError):
            auditoria.save()
        with self.assertRaises(ValidationError):
            auditoria.delete()

    def test_snapshot_de_auditoria_tiene_allowlist_de_configuracion(self):
        auditoria = self.activar_v2().auditoria

        self.assertEqual(
            set(auditoria.snapshot_nuevo),
            {
                'id', 'version', 'version_score', 'version_politica', 'activa',
                'vigencia', 'pesos', 'fuentes_requeridas',
                'configuracion_financiera', 'bandas',
            },
        )
        serializado = str(auditoria.snapshot_nuevo).lower()
        for campo_sensible in ('documento', 'correo', 'token', 'password', 'pdf'):
            self.assertNotIn(campo_sensible, serializado)

    def test_reactivar_v1_es_explicito_y_auditado(self):
        self.activar_v2()

        resultado = activar_politica_score_prestador(
            politica_id=self.v1.pk,
            actor=self.actor,
            motivo='Retorno controlado a V1',
        )

        self.v1.refresh_from_db()
        self.v2.refresh_from_db()
        self.assertTrue(self.v1.activa)
        self.assertFalse(self.v2.activa)
        self.assertEqual(
            resultado.auditoria.accion,
            CambioPoliticaScorePrestadorAudit.Accion.REACTIVACION,
        )
        self.assertEqual(resultado.auditoria.politica_anterior, self.v2)
        self.assertEqual(resultado.auditoria.politica_nueva, self.v1)

    @staticmethod
    def _pesos(politica):
        politica.refresh_from_db()
        return (
            politica.peso_datacredito,
            politica.peso_midecisor,
            politica.peso_hdcplus,
            politica.peso_capacidad,
            politica.peso_comportamiento,
            politica.peso_riesgo,
            politica.peso_referencias,
        )


class ActivacionPoliticaScoreAdminCommandTest(TestCase):
    def setUp(self):
        self.v1, self.v2 = _crear_politicas_demo()
        self.actor = _crear_actor('actor-admin')
        self.actor.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='contractors',
            codename__in=[
                'view_configuracionscoreprestador',
                'change_configuracionscoreprestador',
            ],
        ))

    def test_checkbox_activa_es_readonly(self):
        model_admin = ConfiguracionScorePrestadorAdmin(
            ConfiguracionScorePrestador,
            admin.site,
        )

        self.assertIn('activa', model_admin.get_readonly_fields(None))

    def test_admin_de_auditoria_es_solo_lectura(self):
        model_admin = CambioPoliticaScorePrestadorAuditAdmin(
            CambioPoliticaScorePrestadorAudit,
            admin.site,
        )

        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))

    def test_accion_admin_muestra_confirmacion_y_usa_servicio(self):
        self.client.force_login(self.actor)
        url = reverse('admin:contractors_configuracionscoreprestador_changelist')
        seleccion = {
            'action': 'activar_politica_seleccionada',
            '_selected_action': [str(self.v2.pk)],
            'index': '0',
        }

        respuesta = self.client.post(url, seleccion)

        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(
            respuesta,
            'admin/contractors/configuracionscoreprestador/confirmar_activacion.html',
        )
        with patch(
            'contractors.admin.activar_politica_score_prestador',
        ) as servicio:
            servicio.return_value = SimpleNamespace(
                politica_anterior=self.v1,
                politica_nueva=self.v2,
                auditoria=SimpleNamespace(pk=77),
                cambio_realizado=True,
            )
            respuesta = self.client.post(url, {
                **seleccion,
                'confirmar_activacion': '1',
                'motivo': 'Activacion administrativa controlada',
            })

        self.assertEqual(respuesta.status_code, 302)
        servicio.assert_called_once_with(
            politica_id=self.v2.pk,
            actor=self.actor,
            motivo='Activacion administrativa controlada',
        )

    def test_accion_admin_no_aparece_sin_permiso(self):
        usuario = get_user_model().objects.create_user(
            username='admin-sin-activar',
            password='test',
            is_staff=True,
        )
        usuario.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='contractors',
            codename__in=[
                'view_configuracionscoreprestador',
                'change_configuracionscoreprestador',
            ],
        ))
        self.client.force_login(usuario)

        respuesta = self.client.get(
            reverse('admin:contractors_configuracionscoreprestador_changelist')
        )

        self.assertNotContains(respuesta, 'Activar politica seleccionada')

    @patch('contractors.admin.activar_politica_score_prestador')
    def test_accion_admin_exige_exactamente_una_politica(self, servicio):
        self.client.force_login(self.actor)
        respuesta = self.client.post(
            reverse('admin:contractors_configuracionscoreprestador_changelist'),
            {
                'action': 'activar_politica_seleccionada',
                '_selected_action': [str(self.v1.pk), str(self.v2.pk)],
                'index': '0',
            },
            follow=True,
        )

        self.assertEqual(respuesta.status_code, 200)
        servicio.assert_not_called()
        self.assertContains(respuesta, 'Selecciona exactamente una politica')

    @patch(
        'contractors.management.commands.activar_politica_prestadores.'
        'activar_politica_score_prestador'
    )
    def test_command_usa_servicio_y_actor_explicito(self, servicio):
        servicio.return_value = SimpleNamespace(
            politica_anterior=self.v1,
            politica_nueva=self.v2,
            auditoria=SimpleNamespace(pk=88),
            cambio_realizado=True,
        )

        call_command(
            'activar_politica_prestadores',
            version=VERSION_V2,
            motivo='Prueba de comando controlado',
            actor_username=self.actor.username,
            stdout=StringIO(),
        )

        servicio.assert_called_once_with(
            politica_id=self.v2.pk,
            actor=self.actor,
            motivo='Prueba de comando controlado',
        )

    def test_command_exige_actor_y_motivo(self):
        with self.assertRaises(CommandError):
            call_command(
                'activar_politica_prestadores',
                version=VERSION_V2,
                motivo='Motivo valido',
                stdout=StringIO(),
            )
        with self.assertRaises(CommandError):
            call_command(
                'activar_politica_prestadores',
                version=VERSION_V2,
                actor_username=self.actor.username,
                stdout=StringIO(),
            )

    def test_command_rechaza_actor_inexistente(self):
        with self.assertRaisesMessage(CommandError, 'actor administrativo'):
            call_command(
                'activar_politica_prestadores',
                version=VERSION_V2,
                motivo='Prueba con actor inexistente',
                actor_username='usuario-que-no-existe',
                stdout=StringIO(),
            )


@skipUnless(
    connection.features.has_select_for_update,
    'La prueba concurrente requiere un motor con SELECT FOR UPDATE.',
)
class ActivacionPoliticaScoreConcurrenciaTest(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.v1, self.v2 = _crear_politicas_demo()
        self.actor_v1 = _crear_actor('actor-concurrente-v1')
        self.actor_v2 = _crear_actor('actor-concurrente-v2')

    def test_dos_activaciones_concurrentes_conservan_una_politica_activa(self):
        barrera = Barrier(2)
        resultados = []

        def activar(politica_id, actor_id, motivo):
            close_old_connections()
            try:
                barrera.wait(timeout=5)
                actor = get_user_model().objects.get(pk=actor_id)
                resultado = activar_politica_score_prestador(
                    politica_id=politica_id,
                    actor=actor,
                    motivo=motivo,
                )
                resultados.append(('ok', resultado.politica_nueva.pk))
            except Exception as exc:  # La colision controlada tambien es un resultado valido.
                resultados.append(('error', type(exc).__name__))
            finally:
                close_old_connections()

        hilos = [
            Thread(
                target=activar,
                args=(self.v2.pk, self.actor_v2.pk, 'Activacion concurrente V2'),
            ),
            Thread(
                target=activar,
                args=(self.v1.pk, self.actor_v1.pk, 'Reactivacion concurrente V1'),
            ),
        ]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=15)

        self.assertEqual(len(resultados), 2)
        self.assertEqual(
            ConfiguracionScorePrestador.objects.filter(activa=True).count(),
            1,
        )
        self.assertTrue(
            ConfiguracionScorePrestador.objects.filter(activa=True).exists()
        )
