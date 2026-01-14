from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from decimal import Decimal
from gestion_creditos.models import Credito, CreditoEmprendimiento
import datetime

class DashboardViewTest(TestCase):
    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.user = User.objects.create_user('testuser', 'test@example.com', 'password')
        
        # Create a credit object for the user
        self.credito = Credito.objects.create(
            usuario=self.user,
            linea=Credito.LineaCredito.EMPRENDIMIENTO,
            estado=Credito.EstadoCredito.ACTIVO,
            monto_aprobado=Decimal('5000000.00'),
            plazo=12,
            valor_cuota=Decimal('450000.00'),
            fecha_solicitud=datetime.date.today(),
        )

        self.detalle_emprendimiento = CreditoEmprendimiento.objects.create(
            credito=self.credito,
            nombre_negocio='Test Business',
            numero_cedula='123456789',
            valor_credito=Decimal('5000000.00'),
            plazo=12,
            fecha_nac=datetime.date(1990, 1, 1),
            celular_wh='3001234567',
            direccion='Test Address',
            estado_civil='Soltero/a',
            numero_personas_cargo=0,
            nombre_negocio_detalle='Test Business Detail',
            ubicacion_negocio='Test Location',
            tiempo_operando='1 year',
            dias_trabajados_sem=5,
            prod_serv_ofrec='Test services',
            ingresos_prom_mes='2000000',
            cli_aten_day=10,
            inventario='si',
            nomb_ref_per1='Ref1',
            cel_ref_per1='3001234568',
            rel_ref_per1='Friend',
            nomb_ref_cl1='RefC1',
            cel_ref_cl1='3001234569',
            rel_ref_cl1='Client',
            ref_conoc_lid_com='no',
            desc_cred_nec='Test needs',
            redes_soc='no',
            fotos_prod='no',
        )

    def test_dashboard_emprendimiento_renders_with_decimal_values(self):
        """
        Test that the emprendimiento dashboard renders correctly with decimal values
        for financial fields.
        """
        self.client.login(username='testuser', password='password')
        
        # Set values with decimal parts
        self.credito.valor_cuota = Decimal('451234.56')
        # Simulate capital_pendiente with a property or direct assignment if possible
        # For this test, we assume capital_pendiente is a property that calculates from other fields
        # or we can mock it if it were a method. Since it's a property on the model,
        # we can't directly set it if it's read-only.
        # However, the template accesses it. Let's assume the property works.
        # The key is that the value passed to the template is a Decimal.
        
        # A real `capital_pendiente` would be calculated, but for template rendering test,
        # we can focus on `valor_cuota` which we can set.
        self.credito.save()

        url = reverse('emprendimiento:mi_credito')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'usuariocreditos/dashboard_emprendimiento.html')
        
        # Check if the unlocalized value is present in the script tag
        unlocalized_valor_cuota = str(self.credito.valor_cuota) # e.g., '451234.56'
        self.assertContains(response, f"parseFloat('{unlocalized_valor_cuota}')")

    def test_dashboard_emprendimiento_renders_with_integer_values(self):
        """
        Test that the emprendimiento dashboard renders correctly with integer-like decimal values
        for financial fields.
        """
        self.client.login(username='testuser', password='password')
        
        self.credito.valor_cuota = Decimal('500000')
        self.credito.save()

        url = reverse('emprendimiento:mi_credito')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'usuariocreditos/dashboard_emprendimiento.html')

        unlocalized_valor_cuota = str(self.credito.valor_cuota)
        self.assertContains(response, f"parseFloat('{unlocalized_valor_cuota}')")