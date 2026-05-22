"""Payer and employment-link validation for libranza.

This service performs read-only validation of company convenio state, company
type, and employee-link validation. It does not create applications, mutate
models, or call external providers.
"""

from dataclasses import dataclass

from libranza.selectors import (
    buscar_empresa_libranza,
    empresa_tiene_convenio_activo,
    empresa_tiene_tipo_libranza,
    obtener_vinculo_laboral_validado,
)


REASON_EMPRESA_NO_ENCONTRADA = 'empresa_no_encontrada'
REASON_EMPRESA_SIN_CONVENIO_ACTIVO = 'empresa_sin_convenio_activo'
REASON_EMPRESA_TIPO_NO_VALIDO = 'empresa_tipo_no_valido'
REASON_VINCULO_LABORAL_NO_VALIDADO = 'vinculo_laboral_no_validado'

__all__ = [
    'PayerValidationResult',
    'PayerValidationService',
    'REASON_EMPRESA_NO_ENCONTRADA',
    'REASON_EMPRESA_SIN_CONVENIO_ACTIVO',
    'REASON_EMPRESA_TIPO_NO_VALIDO',
    'REASON_VINCULO_LABORAL_NO_VALIDADO',
    'build_payroll_validation',
    'validate_payer',
]


@dataclass(frozen=True)
class PayerValidationResult:
    valid: bool
    company_id: int | None = None
    employee_link_id: int | None = None
    reasons: tuple[str, ...] = ()
    empresa_found: bool = False
    empresa_convenio_activo: bool = False
    empresa_tipo_valido: bool = False
    vinculo_laboral_validado: bool = False


class PayerValidationService:
    """Read-only service for convenio and employment-link validation."""

    def validate(self, *, document_number: str, company_id: int | None = None) -> PayerValidationResult:
        return validate_payer(document_number=document_number, empresa_id=company_id)


def validate_payer(*, document_number, empresa_id=None, empresa_nombre='') -> PayerValidationResult:
    empresa, validation = build_payroll_validation(
        empresa_id=empresa_id,
        empresa_nombre=empresa_nombre,
        document_number=document_number,
    )
    vinculo = None
    if validation['vinculo_laboral_validado']:
        vinculo = obtener_vinculo_laboral_validado(
            empresa=empresa,
            document_number=document_number,
        )
    return PayerValidationResult(
        valid=validation['ready_for_existing_flow'],
        company_id=empresa.id if empresa else None,
        employee_link_id=vinculo.id if vinculo else None,
        reasons=tuple(validation['pending_reasons']),
        empresa_found=validation['empresa_found'],
        empresa_convenio_activo=validation['empresa_convenio_activo'],
        empresa_tipo_valido=validation['empresa_tipo_valido'],
        vinculo_laboral_validado=validation['vinculo_laboral_validado'],
    )


def build_payroll_validation(*, empresa_id=None, empresa_nombre='', document_number=''):
    empresa = buscar_empresa_libranza(
        empresa_id=empresa_id,
        empresa_nombre=empresa_nombre,
    )
    validation = {
        'empresa_found': bool(empresa),
        'empresa_convenio_activo': empresa_tiene_convenio_activo(empresa),
        'empresa_tipo_valido': empresa_tiene_tipo_libranza(empresa),
        'vinculo_laboral_validado': False,
        'ready_for_existing_flow': False,
        'pending_reasons': [],
    }

    if not empresa:
        validation['pending_reasons'].append(REASON_EMPRESA_NO_ENCONTRADA)
        return None, validation

    if not validation['empresa_convenio_activo']:
        validation['pending_reasons'].append(REASON_EMPRESA_SIN_CONVENIO_ACTIVO)
    if not validation['empresa_tipo_valido']:
        validation['pending_reasons'].append(REASON_EMPRESA_TIPO_NO_VALIDO)

    if validation['empresa_convenio_activo'] and validation['empresa_tipo_valido']:
        vinculo = obtener_vinculo_laboral_validado(
            empresa=empresa,
            document_number=document_number,
        )
        validation['vinculo_laboral_validado'] = bool(vinculo)
        if not validation['vinculo_laboral_validado']:
            validation['pending_reasons'].append(REASON_VINCULO_LABORAL_NO_VALIDADO)

    validation['ready_for_existing_flow'] = (
        validation['empresa_convenio_activo']
        and validation['empresa_tipo_valido']
        and validation['vinculo_laboral_validado']
    )
    return empresa, validation
