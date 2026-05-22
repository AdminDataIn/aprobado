from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from gestion_creditos.models import Empresa, VinculoLaboralEmpresa
from libranza import selectors
from libranza.services.payer_validation import (
    REASON_EMPRESA_NO_ENCONTRADA,
    REASON_EMPRESA_SIN_CONVENIO_ACTIVO,
    REASON_EMPRESA_TIPO_NO_VALIDO,
    REASON_VINCULO_LABORAL_NO_VALIDADO,
    build_payroll_validation,
    validate_payer,
)


User = get_user_model()


class PayerValidationTests(TestCase):
    def setUp(self):
        self.document_number = '1001234567'
        self.user = User.objects.create_user(
            username='empleado-libranza',
            email='empleado@example.com',
        )

    def create_empresa(self, **overrides):
        data = {
            'nombre': f'Empresa {Empresa.objects.count() + 1}',
            'convenio_activo': True,
            'tipo_empresa': Empresa.TipoEmpresa.CONVENIO,
        }
        data.update(overrides)
        return Empresa.objects.create(**data)

    def create_vinculo(self, empresa, **overrides):
        data = {
            'usuario': self.user,
            'empresa': empresa,
            'documento_empleado': self.document_number,
            'tipo_documento': 'CC',
            'nombre_empleado': 'Empleado Libranza',
            'correo_empleado': 'empleado@example.com',
            'telefono_empleado': '3001234567',
            'estado_vinculo': VinculoLaboralEmpresa.EstadoVinculo.ACTIVO,
            'fecha_alta_aprobado': date(2026, 1, 1),
            'salario_base_mensual': Decimal('2000000.00'),
            'validado_por_pagador': True,
        }
        data.update(overrides)
        return VinculoLaboralEmpresa.objects.create(**data)

    def test_selectors_find_company_by_id_or_name_without_writes(self):
        empresa = self.create_empresa(nombre='Empresa Convenio')

        self.assertEqual(selectors.buscar_empresa_libranza(empresa_id=empresa.id), empresa)
        self.assertEqual(selectors.buscar_empresa_libranza(empresa_nombre='empresa convenio'), empresa)
        self.assertIsNone(selectors.buscar_empresa_libranza(empresa_nombre='No existe'))

    def test_selectors_find_only_active_validated_employee_link(self):
        empresa = self.create_empresa()
        vinculo = self.create_vinculo(empresa)

        self.assertEqual(
            selectors.obtener_vinculo_laboral_validado(
                empresa=empresa,
                document_number=self.document_number,
            ),
            vinculo,
        )
        self.assertTrue(
            selectors.tiene_vinculo_laboral_validado(
                empresa=empresa,
                document_number=self.document_number,
            )
        )

    def test_validate_payer_ready_when_company_and_link_are_valid(self):
        empresa = self.create_empresa(tipo_empresa=Empresa.TipoEmpresa.MIXTA)
        vinculo = self.create_vinculo(empresa)

        result = validate_payer(
            document_number=self.document_number,
            empresa_id=empresa.id,
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.company_id, empresa.id)
        self.assertEqual(result.employee_link_id, vinculo.id)
        self.assertEqual(result.reasons, ())
        self.assertTrue(result.empresa_convenio_activo)
        self.assertTrue(result.empresa_tipo_valido)
        self.assertTrue(result.vinculo_laboral_validado)

    def test_build_payroll_validation_matches_existing_payload_shape(self):
        empresa = self.create_empresa()
        self.create_vinculo(empresa)

        resolved_empresa, validation = build_payroll_validation(
            empresa_id=empresa.id,
            document_number=self.document_number,
        )

        self.assertEqual(resolved_empresa, empresa)
        self.assertEqual(
            validation,
            {
                'empresa_found': True,
                'empresa_convenio_activo': True,
                'empresa_tipo_valido': True,
                'vinculo_laboral_validado': True,
                'ready_for_existing_flow': True,
                'pending_reasons': [],
            },
        )

    def test_missing_company_returns_pending_reason(self):
        result = validate_payer(
            document_number=self.document_number,
            empresa_nombre='No existe',
        )

        self.assertFalse(result.valid)
        self.assertFalse(result.empresa_found)
        self.assertEqual(result.reasons, (REASON_EMPRESA_NO_ENCONTRADA,))

    def test_company_without_active_convenio_returns_pending_reason(self):
        empresa = self.create_empresa(convenio_activo=False)

        result = validate_payer(
            document_number=self.document_number,
            empresa_id=empresa.id,
        )

        self.assertFalse(result.valid)
        self.assertEqual(result.reasons, (REASON_EMPRESA_SIN_CONVENIO_ACTIVO,))

    def test_company_with_invalid_type_returns_pending_reason(self):
        empresa = self.create_empresa(tipo_empresa=Empresa.TipoEmpresa.MARKETPLACE_EXTERNA)

        result = validate_payer(
            document_number=self.document_number,
            empresa_id=empresa.id,
        )

        self.assertFalse(result.valid)
        self.assertEqual(result.reasons, (REASON_EMPRESA_TIPO_NO_VALIDO,))

    def test_company_without_validated_employee_link_returns_pending_reason(self):
        empresa = self.create_empresa()
        self.create_vinculo(empresa, validado_por_pagador=False)

        result = validate_payer(
            document_number=self.document_number,
            empresa_id=empresa.id,
        )

        self.assertFalse(result.valid)
        self.assertEqual(result.reasons, (REASON_VINCULO_LABORAL_NO_VALIDADO,))
