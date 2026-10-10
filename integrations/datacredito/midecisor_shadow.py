"""In-memory diagnostic candidate. Never used by the provider adapter or scoring."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from integrations.datacredito.dto import (
    ESTADO_ERROR_TECNICO,
    ResultadoDatacreditoNormalizado,
)
from integrations.datacredito.normalizadores import (
    _como_dict,
    _decimal,
    _extraer_campos_midecisor_pn,
    _normalizar_ausente,
    _path,
    normalizar_midecisor_pn,
)


@dataclass(frozen=True, slots=True)
class DiagnosticoMiDecisorShadow:
    estado_oficial: str
    motivo: str
    candidato: ResultadoDatacreditoNormalizado | None = field(default=None, repr=False)
    alertas_disponibles: bool = False

    def como_dict_seguro(self):
        candidato = self.candidato
        campos = (
            'score_midecisor', 'score_normalizado_0_1000', 'viabilidad',
            'rating_recaudo', 'monto_sugerido', 'ingreso_estimado',
            'porcentaje_cuota_vs_ingreso', 'saldo_actual', 'saldo_mora',
            'valor_cuota_total', 'creditos_vigentes', 'creditos_cerrados',
            'porcentaje_deuda', 'mora_severa', 'mora_actual', 'cantidad_alertas',
            'alertas_resumen', 'requiere_revision_cumplimiento',
        )
        valores = (
            {nombre: getattr(candidato, nombre) for nombre in campos}
            if candidato is not None else {}
        )
        if candidato is not None and not self.alertas_disponibles:
            valores.update(cantidad_alertas=None, alertas_resumen=None,
                           requiere_revision_cumplimiento=None)
        return {
            'shadow': True,
            'utilizable_para_decision': False,
            'estado_oficial': self.estado_oficial,
            'motivo': self.motivo,
            'validez_contractual_score': 'NO_CONFIRMADA',
            'alertas_disponibles': self.alertas_disponibles,
            'campos_candidatos': valores,
        }


def diagnosticar_midecisor_pn(raw, *, shadow=False):
    if shadow is not True:
        raise ValueError('modo_shadow_explicito_requerido')
    datos = _como_dict(raw)
    oficial = normalizar_midecisor_pn(datos)
    metadata = oficial.metadata_segura
    if (
        oficial.estado != ESTADO_ERROR_TECNICO
        or metadata.get('codigo_hc') != '14'
        or metadata.get('codigo_tx') != '05'
    ):
        return DiagnosticoMiDecisorShadow(oficial.estado, 'combinacion_no_habilitada')

    try:
        if (
            _estado(_path(datos, 'status')) != 'ACCEPTED'
            or _estado(_path(datos, 'content', 'status')) != '202 ACCEPTED'
        ):
            raise ValueError('envelope_incoherente')
        respuesta = _path(datos, 'content', 'respuesta')
        if (
            _path(respuesta, 'validacion', 'conInformacion') is not True
            or _path(respuesta, 'informacionRiesgo', 'conInformacion') is not True
        ):
            raise ValueError('informacion_no_confirmada')
        _validar_flags(respuesta)
        proyeccion = _proyectar_campos(respuesta)
    except ValueError as error:
        return DiagnosticoMiDecisorShadow(oficial.estado, str(error))

    candidato = _extraer_campos_midecisor_pn(
        {'content': {'respuesta': proyeccion}},
        estado=oficial.estado, con_informacion=True, codigo_hc='14', codigo_tx='05',
    )
    candidato = replace(
        candidato, disponible=False, requiere_revision_manual=True, viable=None,
        descripcion_respuesta=None,
        metadata_segura={
            'shadow': True, 'utilizable_para_decision': False,
            'codigo_hc': '14', 'codigo_tx': '05',
            'estado': oficial.estado,
            'error_codigo': metadata.get('error_codigo'),
            'semantica_hc14': 'NO_CONFIRMADA',
        },
    )
    return DiagnosticoMiDecisorShadow(
        oficial.estado, 'hc14_tx05_semantica_no_confirmada', candidato,
        alertas_disponibles=isinstance(_path(respuesta, 'informacionRiesgo', 'alertas'), list),
    )


def _estado(valor):
    return ' '.join(valor.split()).upper() if isinstance(valor, str) else ''


def _tiene_datos(valor):
    if isinstance(valor, Mapping):
        return valor.get('conInformacion') is True or any(
            _tiene_datos(dato) for clave, dato in valor.items()
            if clave not in {'conInformacion', 'msjExcepcion'}
        )
    if isinstance(valor, list):
        return any(_tiene_datos(dato) for dato in valor)
    return _normalizar_ausente(valor) is not None


def _validar_flags(valor):
    if isinstance(valor, Mapping):
        if 'conInformacion' in valor:
            flag = valor['conInformacion']
            if flag is not None and not isinstance(flag, bool):
                raise ValueError('flag_invalido')
            if flag is not True and _tiene_datos(valor):
                raise ValueError('modulo_contradictorio')
        for clave, dato in valor.items():
            if clave != 'msjExcepcion':
                _validar_flags(dato)
    elif isinstance(valor, list):
        for dato in valor:
            _validar_flags(dato)


def _modulo(valor):
    if valor is None:
        return {}
    if not isinstance(valor, Mapping):
        raise ValueError('estructura_invalida')
    if valor.get('conInformacion') is not True:
        if _tiene_datos(valor):
            raise ValueError('informacion_no_confirmada')
        return {}
    return valor


def _numero(valor, *, entero=False):
    if _normalizar_ausente(valor) is None:
        return None
    if isinstance(valor, str) and ',' in valor:
        raise ValueError('numero_ambiguo')
    numero = _decimal(valor)
    if isinstance(valor, bool) or numero is None or not numero.is_finite() or numero < 0:
        raise ValueError('numero_invalido')
    if entero and numero != numero.to_integral_value():
        raise ValueError('entero_invalido')
    return numero


def _enum(valor, permitidos):
    if _normalizar_ausente(valor) is None:
        return None
    normalizado = _estado(valor)
    if normalizado not in permitidos:
        raise ValueError('categoria_invalida')
    return normalizado


def _proyectar_campos(respuesta):
    riesgo = _modulo(respuesta.get('informacionRiesgo'))
    score = _numero(riesgo.get('score'), entero=True)
    if score is None or not 0 <= score <= 1000:
        raise ValueError('score_fuera_de_rango_interno')
    alertas = riesgo.get('alertas')
    if alertas is not None and not isinstance(alertas, list):
        raise ValueError('estructura_invalida')
    deuda = _modulo(respuesta.get('endeudamiento'))
    comportamiento = _modulo(respuesta.get('comportamientoCrediticio'))
    indicadores = _modulo(comportamiento.get('indicadoresValores'))
    pagos = _modulo(comportamiento.get('comportamientoPago'))
    vector = pagos.get('vectorComportamiento')
    if vector is not None and not isinstance(vector, list):
        raise ValueError('estructura_invalida')
    vector_seguro = []
    for item in vector or []:
        if not isinstance(item, Mapping):
            raise ValueError('estructura_invalida')
        codigo = _enum(item.get('comportamiento'), set('N123456CD'))
        if codigo is not None:
            vector_seguro.append({'comportamiento': codigo})
    return {
        'informacionRiesgo': {
            'score': int(score),
            'viabilidad': _enum(riesgo.get('viabilidad'), {'ALTA', 'MEDIA', 'BAJA'}),
            'ratingRecaudos': _enum(riesgo.get('ratingRecaudos'), set('ABCDN')),
            'montoSugerido': _numero(riesgo.get('montoSugerido'), entero=True),
            'alertas': alertas,
        },
        'endeudamiento': {
            clave: _numero(deuda.get(clave))
            for clave in ('ingreso', 'porcentajeCuotaVsIngreso')
        },
        'comportamientoCrediticio': {
            'indicadoresValores': {
                clave: _numero(indicadores.get(clave), entero=clave.startswith('creditos'))
                for clave in ('saldoActual', 'saldoMora', 'valorCuota', 'creditosVigentes',
                              'creditosCerrados', 'porcentajeDeuda')
            },
            'comportamientoPago': {'vectorComportamiento': vector_seguro or None},
        },
    }
