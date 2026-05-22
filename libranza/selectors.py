"""Read-only query helpers for the libranza domain.

Keep selectors side-effect free. They may read ``gestion_creditos`` models while
models remain in the legacy app, but must not write data or trigger workflows.
"""

from gestion_creditos.models import Credito, Empresa, VinculoLaboralEmpresa


ESTADOS_CREDITO_LIBRANZA_VIGENTE = (
    Credito.EstadoCredito.ACTIVO,
    Credito.EstadoCredito.EN_MORA,
    Credito.EstadoCredito.PENDIENTE_FIRMA,
    Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
    Credito.EstadoCredito.APROBADO_PAGADOR,
)


__all__ = [
    'ESTADOS_CREDITO_LIBRANZA_VIGENTE',
    'buscar_empresa_libranza',
    'empresa_tiene_tipo_libranza',
    'empresa_tiene_convenio_activo',
    'obtener_credito_libranza_vigente',
    'obtener_vinculo_laboral_validado',
    'tiene_vinculo_laboral_validado',
]


def buscar_empresa_libranza(*, empresa_id=None, empresa_nombre=''):
    if empresa_id:
        return Empresa.objects.filter(id=empresa_id).first()
    empresa_nombre = str(empresa_nombre or '').strip()
    if empresa_nombre:
        return Empresa.objects.filter(nombre__iexact=empresa_nombre).first()
    return None


def empresa_tiene_convenio_activo(empresa) -> bool:
    return bool(empresa and empresa.convenio_activo)


def empresa_tiene_tipo_libranza(empresa) -> bool:
    return bool(
        empresa
        and empresa.tipo_empresa in {Empresa.TipoEmpresa.CONVENIO, Empresa.TipoEmpresa.MIXTA}
    )


def obtener_vinculo_laboral_validado(*, empresa, document_number):
    if not empresa or not document_number:
        return None
    return (
        VinculoLaboralEmpresa.objects
        .filter(
            empresa=empresa,
            documento_empleado=document_number,
            estado_vinculo=VinculoLaboralEmpresa.EstadoVinculo.ACTIVO,
            validado_por_pagador=True,
        )
        .order_by('-fecha_alta_aprobado', '-creado_en')
        .first()
    )


def tiene_vinculo_laboral_validado(*, empresa, document_number) -> bool:
    return obtener_vinculo_laboral_validado(
        empresa=empresa,
        document_number=document_number,
    ) is not None


def obtener_credito_libranza_vigente(*, cliente_id=None, document_number=''):
    queryset = (
        Credito.objects
        .filter(
            linea=Credito.LineaCredito.LIBRANZA,
            estado__in=ESTADOS_CREDITO_LIBRANZA_VIGENTE,
        )
        .select_related('usuario', 'detalle_libranza')
        .order_by('-fecha_solicitud', '-id')
    )
    if cliente_id:
        queryset = queryset.filter(usuario_id=cliente_id)
    document_number = str(document_number or '').strip()
    if document_number:
        queryset = queryset.filter(detalle_libranza__cedula=document_number)
    if not cliente_id and not document_number:
        return None
    return queryset.first()
