from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from contractors.models import InformacionLaboralSolicitudContratista


class ErrorDatosContractualesContratista(ValueError):
    pass


@dataclass(frozen=True)
class DatosContractualesContratista:
    cargo: str
    tipo_contrato: str
    fecha_inicio_contrato: object
    fecha_fin_contrato: object
    valor_total_contrato: Decimal
    valor_pagado_contrato: Decimal
    valor_pendiente_cobrar: Decimal
    empresa: object | None = None
    empresa_contratante_nombre: str = ''
    pagador_nombre: str = ''
    pagador_email: str = ''
    empresa_contratante_nit: str = ''
    pagador_telefono: str = ''
    observaciones: str = ''


@dataclass(frozen=True)
class ResultadoDatosContractualesContratista:
    informacion_laboral: InformacionLaboralSolicitudContratista

    @property
    def informacion_laboral_id(self):
        return self.informacion_laboral.id

    @property
    def solicitud_id(self):
        return self.informacion_laboral.solicitud_id


def calcular_valor_pendiente_contrato(valor_total_contrato, valor_pagado_contrato):
    total = Decimal(valor_total_contrato or '0.00')
    pagado = Decimal(valor_pagado_contrato or '0.00')
    return total - pagado


def registrar_datos_contractuales_contratista(*, solicitud, datos):
    if solicitud is None:
        raise ErrorDatosContractualesContratista('solicitud_requerida')
    if not isinstance(datos, DatosContractualesContratista):
        raise ErrorDatosContractualesContratista('datos_contractuales_invalidos')
    if not datos.empresa:
        raise ErrorDatosContractualesContratista('empresa_requerida')
    if not getattr(datos.empresa, 'permite_libranza', False):
        raise ErrorDatosContractualesContratista('empresa_no_elegible_libranza')

    informacion_laboral = InformacionLaboralSolicitudContratista(
        solicitud=solicitud,
        empresa=datos.empresa,
        cargo=datos.cargo,
        tipo_contrato=datos.tipo_contrato,
        fecha_inicio_contrato=datos.fecha_inicio_contrato,
        fecha_fin_contrato=datos.fecha_fin_contrato,
        valor_total_contrato=datos.valor_total_contrato,
        valor_pagado_contrato=datos.valor_pagado_contrato,
        valor_pendiente_cobrar=datos.valor_pendiente_cobrar,
        empresa_contratante_nombre=datos.empresa_contratante_nombre or datos.empresa.nombre,
        empresa_contratante_nit=datos.empresa_contratante_nit or getattr(datos.empresa, 'nit', ''),
        pagador_nombre=datos.pagador_nombre,
        pagador_email=datos.pagador_email,
        pagador_telefono=datos.pagador_telefono,
        observaciones=datos.observaciones,
    )

    try:
        informacion_laboral.full_clean()
    except ValidationError:
        raise

    with transaction.atomic():
        informacion_laboral.save()

    return ResultadoDatosContractualesContratista(informacion_laboral=informacion_laboral)
