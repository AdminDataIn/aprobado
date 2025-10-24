# -*- coding: utf-8 -*-
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from decimal import Decimal
from gestion_creditos.models import Credito, CreditoLibranza, CreditoEmprendimiento, Empresa
from gestion_creditos.services import filtrar_creditos, get_billetera_context, procesar_pagos_masivos_csv
import io
from datetime import date

class FiltrarCreditosServiceTest(TestCase):
    """Pruebas para la función de servicio `filtrar_creditos`."""

    @classmethod
    def setUpTestData(cls):
        """Crea los datos iniciales para todas las pruebas de esta clase."""
        cls.user = User.objects.create_user(username='testuser', password='123')
        cls.empresa = Empresa.objects.create(nombre='Empresa Test')

        # Crédito de Libranza para filtrar
        credito_libranza_1 = Credito.objects.create(
            usuario=cls.user,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO
        )
        CreditoLibranza.objects.create(
            credito=credito_libranza_1,
            nombres='Juan',
            apellidos='Perez',
            cedula='12345',
            empresa=cls.empresa,
            valor_credito=1000,
            plazo=12
        )

        # Crédito de Emprendimiento para filtrar
        credito_emprendimiento_1 = Credito.objects.create(
            usuario=cls.user,
            linea=Credito.LineaCredito.EMPRENDIMIENTO,
            estado=Credito.EstadoCredito.EN_REVISION
        )
        CreditoEmprendimiento.objects.create(
            credito=credito_emprendimiento_1,
            nombre='Negocio de Ana',
            numero_cedula='67890',
            valor_credito=2000,
            plazo=24,
            fecha_nac=date(1990, 1, 1),
            celular_wh='3001234567',
            direccion='Calle Falsa 123',
            estado_civil='Soltero/a',
            numero_personas_cargo=0,
            nombre_negocio='Mi Negocio',
            ubicacion_negocio='Centro',
            tiempo_operando='1 año',
            dias_trabajados_sem=5,
            prod_serv_ofrec='Venta de productos',
            ingresos_prom_mes='1000000',
            cli_aten_day=10,
            inventario='si',
            nomb_ref_per1='Ref1',
            cel_ref_per1='3001234568',
            rel_ref_per1='Amigo',
            nomb_ref_cl1='RefC1',
            cel_ref_cl1='3001234569',
            rel_ref_cl1='Cliente',
            ref_conoc_lid_com='no',
            foto_negocio='fotos_negocios/test.pdf',
            desc_fotos_neg='...',
            tipo_cta_mno='Nequi',
            ahorro_tand_alc='si',
            depend_h='no',
            desc_cred_nec='Para capital de trabajo',
            redes_soc='si',
            fotos_prod='si'
        )
        
        cls.factory = RequestFactory()

    def test_filtrar_por_linea_libranza(self):
        """Verifica que el filtro por línea 'LIBRANZA' funcione correctamente."""
        request = self.factory.get('/', {'linea': 'LIBRANZA'})
        queryset = filtrar_creditos(request, Credito.objects.all())
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().linea, Credito.LineaCredito.LIBRANZA)

    def test_filtrar_por_estado_activo(self):
        """Verifica que el filtro por estado 'ACTIVO' funcione correctamente."""
        request = self.factory.get('/', {'estado': 'ACTIVO'})
        queryset = filtrar_creditos(request, Credito.objects.all())
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().estado, Credito.EstadoCredito.ACTIVO)

    def test_filtrar_por_busqueda_nombre(self):
        """Verifica que la búsqueda por nombre de solicitante funcione."""
        request = self.factory.get('/', {'search': 'Juan'})
        queryset = filtrar_creditos(request, Credito.objects.all())
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().detalle.nombres, 'Juan')

    def test_filtrar_por_busqueda_cedula(self):
        """Verifica que la búsqueda por cédula funcione."""
        request = self.factory.get('/', {'search': '12345'})
        queryset = filtrar_creditos(request, Credito.objects.all())
        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().detalle.cedula, '12345')

    def test_sin_filtros(self):
        """Verifica que si no se aplican filtros, se devuelvan todos los créditos."""
        request = self.factory.get('/')
        queryset = filtrar_creditos(request, Credito.objects.all())
        self.assertEqual(queryset.count(), 2)

class BilleteraContextServiceTest(TestCase):
    """Pruebas para la función de servicio `get_billetera_context`."""

    def setUp(self):
        """Configura un usuario para las pruebas de la billetera."""
        self.user = User.objects.create_user(username='billetera_user', password='123')

    def test_contexto_billetera_creacion_cuenta(self):
        """Verifica que se cree una cuenta si el usuario no tiene una y el contexto sea correcto."""
        context = get_billetera_context(self.user)
        self.assertIsNotNone(context.get('cuenta'))
        self.assertEqual(context.get('saldo_disponible'), Decimal('0.00'))
        self.assertEqual(context.get('total_depositado'), Decimal('0.00'))
        self.assertEqual(context.get('progreso_porcentaje'), 0)

class PagosMasivosCSVServiceTest(TestCase):
    """Pruebas para la función de servicio `procesar_pagos_masivos_csv`."""

    @classmethod
    def setUpTestData(cls):
        """Crea datos para las pruebas de procesamiento de CSV."""
        cls.user = User.objects.create_user(username='pagador_user', password='123')
        cls.empresa = Empresa.objects.create(nombre='Empresa Pagadora')

        # Crédito activo para procesar pago
        credito_activo = Credito.objects.create(
            usuario=cls.user,
            linea=Credito.LineaCredito.LIBRANZA,
            estado=Credito.EstadoCredito.ACTIVO
        )
        CreditoLibranza.objects.create(
            credito=credito_activo,
            cedula='112233',
            valor_credito=5000,
            saldo_pendiente=5000,
            capital_original_pendiente=4500, # Simulado
            tasa_interes=Decimal('2.5'),
            empresa=cls.empresa,
            plazo=12
        )

    def test_procesar_csv_exitoso(self):
        """Verifica el procesamiento exitoso de un CSV de pagos masivos."""
        csv_content = 'cedula,monto_a_pagar\n112233,500\n'
        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        
        pagos_exitosos, errores = procesar_pagos_masivos_csv(csv_file, self.empresa)
        
        self.assertEqual(pagos_exitosos, 1)
        self.assertEqual(len(errores), 0)
        
        credito_actualizado = Credito.objects.get(detalle_libranza__cedula='112233')
        self.assertTrue(credito_actualizado.detalle.saldo_pendiente < 5000)

    def test_procesar_csv_con_errores(self):
        """Verifica que se manejen correctamente las filas con errores en el CSV."""
        # Cédula no existente, monto inválido
        csv_content = 'cedula,monto_a_pagar\n999999,100\n112233,monto_invalido\n'
        csv_file = io.BytesIO(csv_content.encode('utf-8'))

        pagos_exitosos, errores = procesar_pagos_masivos_csv(csv_file, self.empresa)

        self.assertEqual(pagos_exitosos, 0)
        self.assertEqual(len(errores), 2)
        self.assertIn("No se encontró un crédito activo para la cédula 999999", errores[0])
        self.assertIn("Monto 'monto_invalido' no es un número válido", errores[1])
