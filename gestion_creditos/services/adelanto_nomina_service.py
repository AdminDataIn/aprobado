from decimal import Decimal

from gestion_creditos.models import Credito, VinculoLaboralEmpresa
from gestion_creditos.services.capacidad_descuento_service import simular_adelanto_nomina


def obtener_vinculo_laboral_activo(usuario):
    return (
        VinculoLaboralEmpresa.objects
        .select_related('empresa')
        .filter(usuario=usuario, estado_vinculo=VinculoLaboralEmpresa.EstadoVinculo.ACTIVO)
        .order_by('-validado_por_pagador', '-fecha_alta_aprobado', '-creado_en')
        .first()
    )


def evaluar_elegibilidad_adelanto(usuario):
    if not getattr(usuario, 'is_authenticated', False):
        return {
            'eligible': False,
            'status_code': 'AUTH_REQUIRED',
            'reason': 'Debes iniciar sesion para solicitar el adelanto.',
            'vinculo': None,
            'empresa': None,
            'monto_maximo': Decimal('0.00'),
        }

    vinculo = obtener_vinculo_laboral_activo(usuario)
    if not vinculo:
        return {
            'eligible': False,
            'status_code': 'NO_ACTIVE_LINK',
            'reason': 'No encontramos un vinculo laboral activo con empresa aliada.',
            'vinculo': None,
            'empresa': None,
            'monto_maximo': Decimal('0.00'),
            'simulation': simular_adelanto_nomina(),
        }

    empresa = vinculo.empresa
    if not empresa or not empresa.convenio_activo:
        return {
            'eligible': False,
            'status_code': 'COMPANY_WITHOUT_CONVENIO',
            'reason': 'Tu empresa aun no tiene convenio activo para adelanto de nomina.',
            'vinculo': vinculo,
            'empresa': empresa,
            'monto_maximo': Decimal('0.00'),
            'simulation': simular_adelanto_nomina(
                salario=vinculo.salario_base_mensual or Decimal('0.00'),
                auxilio_transporte=vinculo.auxilio_transporte_mensual,
                descuentos=vinculo.descuentos_fijos_mensuales,
            ),
        }

    if not vinculo.cumple_antiguedad_minima:
        return {
            'eligible': False,
            'status_code': 'MINIMUM_AGE_NOT_MET',
            'reason': 'Debes completar al menos 30 dias desde tu alta en Aprobado con esta empresa.',
            'vinculo': vinculo,
            'empresa': empresa,
            'monto_maximo': Decimal('0.00'),
            'simulation': simular_adelanto_nomina(
                salario=vinculo.salario_base_mensual or Decimal('0.00'),
                auxilio_transporte=vinculo.auxilio_transporte_mensual,
                descuentos=vinculo.descuentos_fijos_mensuales,
            ),
        }

    if not vinculo.salario_base_mensual:
        return {
            'eligible': False,
            'status_code': 'MISSING_BASE_SALARY',
            'reason': 'Tu salario base aun no esta registrado para calcular el adelanto.',
            'vinculo': vinculo,
            'empresa': empresa,
            'monto_maximo': Decimal('0.00'),
            'simulation': simular_adelanto_nomina(
                salario=vinculo.salario_base_mensual or Decimal('0.00'),
                auxilio_transporte=vinculo.auxilio_transporte_mensual,
                descuentos=vinculo.descuentos_fijos_mensuales,
            ),
        }

    simulation = simular_adelanto_nomina(
        salario=vinculo.salario_base_mensual or Decimal('0.00'),
        auxilio_transporte=vinculo.auxilio_transporte_mensual,
        descuentos=vinculo.descuentos_fijos_mensuales,
    )

    bloqueante = Credito.objects.filter(
        usuario=usuario,
        linea=Credito.LineaCredito.ADELANTO_NOMINA,
        estado__in=[
            Credito.EstadoCredito.EN_REVISION,
            Credito.EstadoCredito.APROBADO_PAGADOR,
            Credito.EstadoCredito.PENDIENTE_FIRMA,
            Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
            Credito.EstadoCredito.ACTIVO,
            Credito.EstadoCredito.EN_MORA,
        ],
    ).exists()
    if bloqueante:
        return {
            'eligible': False,
            'status_code': 'BLOCKING_ADVANCE_EXISTS',
            'reason': 'Ya tienes un adelanto de nomina activo o en proceso.',
            'vinculo': vinculo,
            'empresa': empresa,
            'monto_maximo': simulation['monto_bruto_adelanto'],
            'simulation': simulation,
        }

    return {
        'eligible': True,
        'status_code': 'ELIGIBLE',
        'reason': '',
        'vinculo': vinculo,
        'empresa': empresa,
        'monto_maximo': simulation['monto_bruto_adelanto'],
        'simulation': simulation,
    }
