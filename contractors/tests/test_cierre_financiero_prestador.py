import tempfile
from decimal import Decimal
from pathlib import Path
from queue import Queue
from threading import Barrier, Thread
from unittest import skipIf
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from contractors.models import FormalizacionCreditoPrestador
from contractors.services.capacidad_contractual import (
    simular_credito_prestador_informativo,
)
from contractors.services.postfirma import (
    confirmar_desembolso_credito_prestador,
    preparar_transferencia_credito_prestador,
)
from gestion_creditos.models import (
    Credito,
    CreditoLibranza,
    CuotaAmortizacion,
    Empresa,
    HistorialEstado,
    OrigenCreditoPrestador,
    Pagare,
)
from gestion_creditos.credit_services import activar_credito
from gestion_creditos.services.condiciones_financieras import (
    calcular_componentes_financieros,
)
from usuarios.models import PerfilPagador


class ConfiguracionFinancieraPrueba:
    version = 'financiera-v1'
    monto_minimo = Decimal('1000000.00')
    monto_maximo = Decimal('10000000.00')
    plazo_minimo_meses = 3
    plazo_maximo_meses = 8
    tasa_mensual = Decimal('2.2000')
    porcentaje_originacion = Decimal('10.0000')
    porcentaje_iva_originacion = Decimal('19.0000')
    porcentaje_seguro_vida_primera_cuota = Decimal('0.3711')
    porcentaje_fondo_garantia = Decimal('2.0000')


class CalculoFinancieroPrestadorTest(TestCase):
    def test_ejemplo_financiero_exacto_y_paridad_simulador(self):
        componentes = calcular_componentes_financieros(
            monto_base='10000000',
            porcentaje_comision='10',
            porcentaje_iva='19',
            porcentaje_seguro='0.3711',
            porcentaje_fondo='2',
            tasa_mensual='2.2',
            plazo=8,
            version_configuracion='financiera-v1',
            version_score='score-v2',
            version_politica='politica-v2',
        )
        simulacion = simular_credito_prestador_informativo(
            monto='10000000',
            plazo_meses=8,
            configuracion=ConfiguracionFinancieraPrueba(),
        )

        self.assertEqual(componentes.comision, Decimal('1000000.00'))
        self.assertEqual(componentes.iva, Decimal('190000.00'))
        self.assertEqual(componentes.seguro_vida, Decimal('37110.00'))
        self.assertEqual(componentes.fondo_garantia, Decimal('200000.00'))
        self.assertEqual(componentes.capital_total_financiado, Decimal('11427110.00'))
        self.assertEqual(componentes.cuota_aprobada, Decimal('1573387.58'))
        self.assertEqual(componentes.total_intereses, Decimal('1159990.64'))
        self.assertEqual(componentes.total_a_pagar, Decimal('12587100.64'))
        self.assertEqual(simulacion.capital_total_financiado, componentes.capital_total_financiado)
        self.assertEqual(simulacion.cuota_mensual, componentes.cuota_aprobada)
        self.assertEqual(simulacion.intereses_estimados, componentes.total_intereses)
        self.assertEqual(simulacion.total_a_pagar, componentes.total_a_pagar)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CierreFinancieroPrestadorTest(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override_media = override_settings(MEDIA_ROOT=self.media.name)
        self.override_media.enable()
        self.addCleanup(self.override_media.disable)
        User = get_user_model()
        self.titular = User.objects.create_user('titular-cierre', password='x')
        self.staff = User.objects.create_user(
            'finanzas-cierre', password='x', is_staff=True,
        )
        self.empresa = Empresa.objects.create(
            nombre='Empresa cierre financiero', convenio_activo=True,
        )
        self._otorgar(
            self.staff,
            'can_prepare_contractor_transfer',
            'can_confirm_contractor_disbursement',
        )
        self.componentes = calcular_componentes_financieros(
            monto_base='10000000',
            porcentaje_comision='10',
            porcentaje_iva='19',
            porcentaje_seguro='0.3711',
            porcentaje_fondo='2',
            tasa_mensual='2.2',
            plazo=8,
            version_configuracion='financiera-v1',
            version_score='score-v2',
            version_politica='politica-v2',
        )
        self.credito = Credito.objects.create(
            usuario=self.titular,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.FIRMADO,
            monto_solicitado=Decimal('10000000.00'),
            plazo_solicitado=8,
            monto_aprobado=self.componentes.monto_base,
            plazo=self.componentes.plazo,
            tasa_interes=self.componentes.tasa_mensual,
            comision=self.componentes.comision,
            iva_comision=self.componentes.iva,
            valor_cuota=self.componentes.cuota_aprobada,
            total_a_pagar=self.componentes.total_a_pagar,
        )
        self.detalle = CreditoLibranza.objects.create(
            credito=self.credito,
            nombres='Persona',
            apellidos='Prueba',
            cedula='1000000000',
            direccion='Direccion prueba',
            telefono='3000000000',
            correo_electronico='persona@example.test',
            empresa=self.empresa,
            es_prestador_servicios=True,
            cedula_frontal='credito_libranza/cedulas/frontal.jpg',
            cedula_trasera='credito_libranza/cedulas/trasera.jpg',
            certificado_bancario='credito_libranza/certificados_bancarios/cert.pdf',
        )
        self.origen = OrigenCreditoPrestador.objects.create(
            gate_id=9001,
            gate_version='datos-v1',
            clave_idempotencia='prestador:9001:datos-v1',
            credito=self.credito,
            credito_libranza=self.detalle,
            estado=OrigenCreditoPrestador.Estado.EN_PROCESO,
            created_by=self.staff,
        )
        self._completar_origen()
        self.pagare = Pagare.objects.create(
            credito=self.credito,
            numero_pagare='PAG-CIERRE-1',
            estado=Pagare.EstadoPagare.SIGNED,
            archivo_pdf=SimpleUploadedFile('pagare.pdf', b'pdf'),
        )
        self.formalizacion = FormalizacionCreditoPrestador.objects.create(
            origen_credito_prestador=self.origen,
            credito=self.credito,
            credito_libranza=self.detalle,
            pagare=self.pagare,
            estado=FormalizacionCreditoPrestador.Estado.FIRMADO,
            clave_idempotencia='formalizacion-cierre-v1',
            version_origen='datos-v1',
            estado_identidad=FormalizacionCreditoPrestador.EstadoIdentidad.VALIDADA,
            identidad_usuario=self.titular,
            identidad_selfie_validada=True,
            identidad_documento_validada=True,
            identidad_firmante_coincide=True,
            identidad_evidencia_hash='a' * 64,
            firmada_en=timezone.now(),
        )

    def test_snapshot_es_inmutable_y_valida_hash(self):
        self.assertEqual(
            self.origen.componentes_financieros().calcular_hash(),
            self.origen.snapshot_hash,
        )
        self.origen.comision = Decimal('1.00')
        with self.assertRaises(ValidationError):
            self.origen.save()

    def test_origen_completado_no_puede_degradar_estado(self):
        self.origen.estado = OrigenCreditoPrestador.Estado.EN_PROCESO
        with self.assertRaisesMessage(ValidationError, 'no puede volver'):
            self.origen.save(update_fields=['estado'])

        self.origen.refresh_from_db()
        self.assertEqual(self.origen.estado, OrigenCreditoPrestador.Estado.COMPLETADO)

    def test_origen_historico_sin_snapshot_es_consultable_pero_bloquea_postfirma(self):
        OrigenCreditoPrestador.objects.filter(pk=self.origen.pk).update(
            monto_base=None,
            snapshot_hash='',
        )
        self.origen.refresh_from_db()

        self.assertEqual(self.origen.estado, OrigenCreditoPrestador.Estado.COMPLETADO)
        self.assertFalse(self.origen.snapshot_financiero_completo)
        self.origen.gate_version = 'datos-historicos-v1'
        self.origen.save(update_fields=['gate_version', 'updated_at'])
        with self.assertRaisesMessage(ValidationError, 'historico'):
            preparar_transferencia_credito_prestador(self.credito, actor=self.staff)

        self.credito.refresh_from_db()
        self.assertEqual(self.credito.estado, Credito.EstadoCredito.FIRMADO)
        self.assertFalse(
            HistorialEstado.objects.filter(
                clave_idempotencia=f'prestador:{self.credito.pk}:pendiente-transferencia:v1'
            ).exists()
        )

    def test_activar_directamente_rechaza_firmado(self):
        with self.assertRaisesMessage(ValidationError, 'pendiente de transferencia'):
            activar_credito(self.credito, componentes_financieros=self.componentes)
        self.assertFalse(CuotaAmortizacion.objects.filter(credito=self.credito).exists())

    def test_activar_directamente_rechaza_anulado(self):
        self.credito.estado = Credito.EstadoCredito.ANULADO
        self.credito.save(update_fields=['estado'])

        with self.assertRaisesMessage(ValidationError, 'pendiente de transferencia'):
            activar_credito(self.credito, componentes_financieros=self.componentes)
        self.assertFalse(CuotaAmortizacion.objects.filter(credito=self.credito).exists())

    def test_activar_directamente_permite_pendiente_transferencia(self):
        self.credito.estado = Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA
        self.credito.save(update_fields=['estado'])

        activar_credito(self.credito, componentes_financieros=self.componentes)

        self.credito.refresh_from_db()
        self.assertEqual(
            self.credito.estado,
            Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
        )
        self.assertEqual(CuotaAmortizacion.objects.filter(credito=self.credito).count(), 8)

    def test_firmado_pasa_a_transferencia_y_desembolso_activa(self):
        preparado = preparar_transferencia_credito_prestador(
            self.credito, actor=self.staff,
        )
        self.assertFalse(preparado.reutilizado)
        self.assertEqual(
            preparado.credito.estado,
            Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
        )
        comprobante = SimpleUploadedFile('comprobante.pdf', b'comprobante')
        activado = confirmar_desembolso_credito_prestador(
            preparado.credito, comprobante=comprobante, actor=self.staff,
        )
        self.assertFalse(activado.reutilizado)
        self.assertEqual(activado.credito.estado, Credito.EstadoCredito.ACTIVO)
        self.assertEqual(activado.credito.saldo_pendiente, Decimal('11427110.00'))
        self.assertEqual(activado.credito.capital_pendiente, Decimal('10000000.00'))
        self.assertEqual(activado.credito.valor_cuota, Decimal('1573387.58'))
        self.assertEqual(CuotaAmortizacion.objects.filter(credito=self.credito).count(), 8)
        self.assertEqual(
            HistorialEstado.objects.filter(credito=self.credito).count(), 2,
        )
        cuotas = CuotaAmortizacion.objects.filter(credito=self.credito)
        self.assertEqual(
            sum((cuota.capital_a_pagar for cuota in cuotas), Decimal('0.00')),
            self.componentes.capital_total_financiado,
        )
        self.assertEqual(
            sum((cuota.interes_a_pagar for cuota in cuotas), Decimal('0.00')),
            self.componentes.total_intereses,
        )
        self.assertEqual(
            sum((cuota.valor_cuota for cuota in cuotas), Decimal('0.00')),
            self.componentes.total_a_pagar,
        )

        reutilizado = confirmar_desembolso_credito_prestador(
            activado.credito,
            comprobante=SimpleUploadedFile('otro.pdf', b'otro'),
            actor=self.staff,
        )
        self.assertTrue(reutilizado.reutilizado)
        self.assertEqual(CuotaAmortizacion.objects.filter(credito=self.credito).count(), 8)

    def test_desembolso_exige_comprobante_y_no_activa_desde_firmado(self):
        with self.assertRaises(ValidationError):
            confirmar_desembolso_credito_prestador(
                self.credito, comprobante=None, actor=self.staff,
            )
        with self.assertRaises(ValidationError):
            confirmar_desembolso_credito_prestador(
                self.credito,
                comprobante=SimpleUploadedFile('c.pdf', b'c'),
                actor=self.staff,
            )
        self.credito.refresh_from_db()
        self.assertEqual(self.credito.estado, Credito.EstadoCredito.FIRMADO)

    def test_hash_alterado_hace_rollback(self):
        preparar_transferencia_credito_prestador(self.credito, actor=self.staff)
        OrigenCreditoPrestador.objects.filter(pk=self.origen.pk).update(
            snapshot_hash='0' * 64,
        )
        self.credito.refresh_from_db()
        with self.assertRaises(ValidationError):
            confirmar_desembolso_credito_prestador(
                self.credito,
                comprobante=SimpleUploadedFile('c.pdf', b'c'),
                actor=self.staff,
            )
        self.credito.refresh_from_db()
        self.assertEqual(
            self.credito.estado, Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
        )
        self.assertFalse(CuotaAmortizacion.objects.filter(credito=self.credito).exists())

    def test_falla_de_amortizacion_hace_rollback_completo(self):
        preparar_transferencia_credito_prestador(self.credito, actor=self.staff)
        self.credito.refresh_from_db()
        with patch.object(
            CuotaAmortizacion.objects,
            'bulk_create',
            side_effect=RuntimeError('fallo controlado'),
        ):
            with self.assertRaises(RuntimeError):
                confirmar_desembolso_credito_prestador(
                    self.credito,
                    comprobante=SimpleUploadedFile('c.pdf', b'c'),
                    actor=self.staff,
                )
        self.credito.refresh_from_db()
        self.assertEqual(
            self.credito.estado, Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
        )
        self.assertIsNone(self.credito.fecha_desembolso)
        self.assertFalse(
            HistorialEstado.objects.filter(
                clave_idempotencia=f'prestador:{self.credito.pk}:desembolso-confirmado:v1'
            ).exists()
        )

    def test_anulado_y_perfil_pagador_estan_bloqueados(self):
        self.credito.estado = Credito.EstadoCredito.ANULADO
        self.credito.save(update_fields=['estado'])
        with self.assertRaises(ValidationError):
            preparar_transferencia_credito_prestador(self.credito, actor=self.staff)

        pagador = get_user_model().objects.create_user(
            'pagador-cierre', password='x', is_staff=True,
        )
        self._otorgar(
            pagador,
            'can_prepare_contractor_transfer',
            'can_confirm_contractor_disbursement',
        )
        PerfilPagador.objects.create(usuario=pagador, empresa=self.empresa)
        with self.assertRaises(PermissionDenied):
            preparar_transferencia_credito_prestador(self.credito, actor=pagador)

    def test_credito_prestador_sin_snapshot_no_se_activa(self):
        credito = Credito.objects.create(
            usuario=self.titular,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO,
            monto_solicitado=Decimal('1000000.00'),
            plazo_solicitado=3,
            monto_aprobado=Decimal('1000000.00'),
            plazo=3,
            tasa_interes=Decimal('2.20'),
        )
        OrigenCreditoPrestador.objects.create(
            gate_id=9002,
            gate_version='datos-v2',
            clave_idempotencia='prestador:9002:datos-v2',
            credito=credito,
            estado=OrigenCreditoPrestador.Estado.EN_PROCESO,
            created_by=self.staff,
        )
        with self.assertRaises(ValidationError):
            activar_credito(credito)
        self.assertFalse(CuotaAmortizacion.objects.filter(credito=credito).exists())

    def _completar_origen(self):
        componentes = self.componentes
        for campo in (
            'monto_base', 'porcentaje_comision', 'comision', 'porcentaje_iva',
            'iva', 'porcentaje_seguro', 'seguro_vida', 'porcentaje_fondo',
            'fondo_garantia', 'otros_costos_total', 'capital_total_financiado',
            'tasa_mensual', 'plazo', 'cuota_aprobada', 'total_intereses',
            'total_a_pagar', 'version_formula', 'version_configuracion',
            'version_score', 'version_politica',
        ):
            setattr(self.origen, campo, getattr(componentes, campo))
        self.origen.otros_componentes = {}
        self.origen.calculado_en = timezone.now()
        self.origen.snapshot_hash = componentes.calcular_hash()
        self.origen.estado = OrigenCreditoPrestador.Estado.COMPLETADO
        self.origen.save()

    @staticmethod
    def _otorgar(usuario, *codenames):
        usuario.user_permissions.add(*Permission.objects.filter(codename__in=codenames))


@skipIf(connection.vendor != 'postgresql', 'Requiere PostgreSQL real.')
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class CierreFinancieroPrestadorConcurrenciaPostgresTest(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override_media = override_settings(MEDIA_ROOT=self.media.name)
        self.override_media.enable()
        self.addCleanup(self.override_media.disable)

        User = get_user_model()
        self.titular = User.objects.create_user('titular-concurrencia', password='x')
        self.staff = User.objects.create_user(
            'finanzas-concurrencia', password='x', is_staff=True,
        )
        self.staff.user_permissions.add(*Permission.objects.filter(codename__in=(
            'can_prepare_contractor_transfer',
            'can_confirm_contractor_disbursement',
        )))
        empresa = Empresa.objects.create(
            nombre='Empresa concurrencia PostgreSQL', convenio_activo=True,
        )
        self.componentes = calcular_componentes_financieros(
            monto_base='10000000',
            porcentaje_comision='10',
            porcentaje_iva='19',
            porcentaje_seguro='0.3711',
            porcentaje_fondo='2',
            tasa_mensual='2.2',
            plazo=8,
            version_configuracion='financiera-postgres-v1',
            version_score='score-postgres-v1',
            version_politica='politica-postgres-v1',
        )
        self.credito = Credito.objects.create(
            usuario=self.titular,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.FIRMADO,
            monto_solicitado=self.componentes.monto_base,
            plazo_solicitado=self.componentes.plazo,
            monto_aprobado=self.componentes.monto_base,
            plazo=self.componentes.plazo,
            tasa_interes=self.componentes.tasa_mensual,
            comision=self.componentes.comision,
            iva_comision=self.componentes.iva,
            valor_cuota=self.componentes.cuota_aprobada,
            total_a_pagar=self.componentes.total_a_pagar,
        )
        detalle = CreditoLibranza.objects.create(
            credito=self.credito,
            nombres='Persona',
            apellidos='Concurrente',
            cedula='1000000001',
            direccion='Direccion prueba',
            telefono='3000000001',
            correo_electronico='concurrencia@example.test',
            empresa=empresa,
            es_prestador_servicios=True,
            cedula_frontal='credito_libranza/cedulas/frontal.jpg',
            cedula_trasera='credito_libranza/cedulas/trasera.jpg',
            certificado_bancario='credito_libranza/certificados_bancarios/cert.pdf',
        )
        self.origen = OrigenCreditoPrestador.objects.create(
            gate_id=9101,
            gate_version='datos-postgres-v1',
            clave_idempotencia='prestador:9101:datos-postgres-v1',
            credito=self.credito,
            credito_libranza=detalle,
            estado=OrigenCreditoPrestador.Estado.EN_PROCESO,
            created_by=self.staff,
        )
        for campo in (
            'monto_base', 'porcentaje_comision', 'comision', 'porcentaje_iva',
            'iva', 'porcentaje_seguro', 'seguro_vida', 'porcentaje_fondo',
            'fondo_garantia', 'otros_costos_total', 'capital_total_financiado',
            'tasa_mensual', 'plazo', 'cuota_aprobada', 'total_intereses',
            'total_a_pagar', 'version_formula', 'version_configuracion',
            'version_score', 'version_politica',
        ):
            setattr(self.origen, campo, getattr(self.componentes, campo))
        self.origen.otros_componentes = {}
        self.origen.calculado_en = timezone.now()
        self.origen.snapshot_hash = self.componentes.calcular_hash()
        self.origen.estado = OrigenCreditoPrestador.Estado.COMPLETADO
        self.origen.save()

        pagare = Pagare.objects.create(
            credito=self.credito,
            numero_pagare='PAG-CONCURRENCIA-1',
            estado=Pagare.EstadoPagare.SIGNED,
            archivo_pdf=SimpleUploadedFile('pagare.pdf', b'pdf'),
        )
        FormalizacionCreditoPrestador.objects.create(
            origen_credito_prestador=self.origen,
            credito=self.credito,
            credito_libranza=detalle,
            pagare=pagare,
            estado=FormalizacionCreditoPrestador.Estado.FIRMADO,
            clave_idempotencia='formalizacion-concurrencia-v1',
            version_origen='datos-postgres-v1',
            estado_identidad=FormalizacionCreditoPrestador.EstadoIdentidad.VALIDADA,
            identidad_usuario=self.titular,
            identidad_selfie_validada=True,
            identidad_documento_validada=True,
            identidad_firmante_coincide=True,
            identidad_evidencia_hash='b' * 64,
            firmada_en=timezone.now(),
        )

    def test_transiciones_postfirma_son_idempotentes_bajo_concurrencia(self):
        resultados_preparacion, errores_preparacion = self._ejecutar_concurrente(
            lambda credito, actor, indice: preparar_transferencia_credito_prestador(
                credito, actor=actor,
            )
        )
        self.assertEqual(errores_preparacion, [])
        self.assertCountEqual(
            [resultado.reutilizado for resultado in resultados_preparacion],
            [False, True],
        )
        clave_preparacion = f'prestador:{self.credito.pk}:pendiente-transferencia:v1'
        self.assertEqual(
            HistorialEstado.objects.filter(clave_idempotencia=clave_preparacion).count(),
            1,
        )
        self.credito.refresh_from_db()
        self.assertEqual(
            self.credito.estado, Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
        )

        resultados_desembolso, errores_desembolso = self._ejecutar_concurrente(
            lambda credito, actor, indice: confirmar_desembolso_credito_prestador(
                credito,
                comprobante=SimpleUploadedFile(
                    f'comprobante-concurrente-{indice}.pdf', b'comprobante',
                ),
                actor=actor,
            )
        )
        self.assertEqual(errores_desembolso, [])
        self.assertCountEqual(
            [resultado.reutilizado for resultado in resultados_desembolso],
            [False, True],
        )
        clave_desembolso = f'prestador:{self.credito.pk}:desembolso-confirmado:v1'
        historial = HistorialEstado.objects.get(clave_idempotencia=clave_desembolso)
        self.assertTrue(historial.comprobante_pago.name)
        self.credito.refresh_from_db()
        self.assertEqual(self.credito.estado, Credito.EstadoCredito.ACTIVO)
        self.assertEqual(CuotaAmortizacion.objects.filter(credito=self.credito).count(), 8)
        self.assertEqual(
            HistorialEstado.objects.filter(clave_idempotencia=clave_desembolso).count(),
            1,
        )
        comprobantes = [
            ruta for ruta in Path(settings.MEDIA_ROOT).rglob('*')
            if ruta.is_file() and 'comprobante-concurrente-' in ruta.name
        ]
        self.assertEqual(len(comprobantes), 1)

    def _ejecutar_concurrente(self, operacion):
        barrera = Barrier(2)
        resultados = Queue()
        errores = Queue()

        def ejecutar(indice):
            close_old_connections()
            try:
                credito = Credito.objects.get(pk=self.credito.pk)
                actor = get_user_model().objects.get(pk=self.staff.pk)
                barrera.wait(timeout=10)
                resultados.put(operacion(credito, actor, indice))
            except Exception as exc:
                errores.put(exc)
            finally:
                close_old_connections()

        hilos = [Thread(target=ejecutar, args=(indice,)) for indice in (1, 2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)
        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        return list(resultados.queue), list(errores.queue)
