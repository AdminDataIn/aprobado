import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone


CENTAVO = Decimal('0.01')
CUATRO_DECIMALES = Decimal('0.0001')
IVA_ORIGINACION = Decimal('19.0000')
FECHA_INICIO_V2 = date(2026, 9, 1)
POLITICA_V1 = 'LIBRANZA_ORIGINACION_V1'
POLITICA_V2 = 'LIBRANZA_ORIGINACION_V2'
POLITICA_ESPECIAL = 'LIBRANZA_ORIGINACION_ESPECIAL'


class CostoOriginacionLibranzaError(ValidationError):
    def __init__(self, codigo, mensaje):
        self.codigo = codigo
        super().__init__(mensaje, code=codigo)


@dataclass(frozen=True)
class CostoOriginacionLibranza:
    codigo_politica: str
    version_politica: str
    fecha_referencia: datetime
    origen: str
    monto_base: Decimal
    plazo: int
    porcentaje_originacion: Decimal | None
    valor_originacion: Decimal
    porcentaje_iva: Decimal
    valor_iva: Decimal
    regla_especial_id: int | None = None

    def as_dict(self):
        return asdict(self)


def resolver_costo_originacion_libranza(
    *,
    fecha_referencia,
    monto,
    plazo,
    es_especial=False,
    regla_especial=None,
):
    fecha_referencia = _normalizar_fecha_referencia(fecha_referencia)
    monto = _moneda(monto)
    plazo = _plazo(plazo)

    if monto <= Decimal('0.00'):
        raise CostoOriginacionLibranzaError(
            'monto_invalido',
            'El monto base debe ser mayor que cero.',
        )

    if regla_especial is not None:
        return _resolver_regla_especial(
            fecha_referencia=fecha_referencia,
            monto=monto,
            plazo=plazo,
            regla_especial=regla_especial,
        )

    if es_especial:
        raise CostoOriginacionLibranzaError(
            'regla_especial_requerida',
            'El credito especial requiere una regla auditada y vinculada.',
        )

    if _fecha_local(fecha_referencia) < FECHA_INICIO_V2:
        porcentaje = Decimal('10.0000')
        codigo = POLITICA_V1
        version = '1'
    else:
        porcentaje = _porcentaje_v2(plazo)
        codigo = POLITICA_V2
        version = '2'

    valor_originacion = _moneda(monto * porcentaje / Decimal('100'))
    valor_iva = _moneda(valor_originacion * IVA_ORIGINACION / Decimal('100'))
    return CostoOriginacionLibranza(
        codigo_politica=codigo,
        version_politica=version,
        fecha_referencia=fecha_referencia,
        origen='NORMAL',
        monto_base=monto,
        plazo=plazo,
        porcentaje_originacion=porcentaje,
        valor_originacion=valor_originacion,
        porcentaje_iva=IVA_ORIGINACION,
        valor_iva=valor_iva,
    )


def simular_libranza_normal(*, fecha_referencia, monto, plazo, tasa_mensual):
    costo = resolver_costo_originacion_libranza(
        fecha_referencia=fecha_referencia,
        monto=monto,
        plazo=plazo,
    )
    tasa_porcentaje = Decimal(str(tasa_mensual)).quantize(CUATRO_DECIMALES, rounding=ROUND_HALF_UP)
    tasa = tasa_porcentaje / Decimal('100')
    capital_financiado = _moneda(
        costo.monto_base + costo.valor_originacion + costo.valor_iva
    )
    if tasa > Decimal('0.00'):
        factor = (tasa * (Decimal('1.00') + tasa) ** costo.plazo) / (
            ((Decimal('1.00') + tasa) ** costo.plazo) - Decimal('1.00')
        )
        cuota = _moneda(capital_financiado * factor)
    else:
        cuota = _moneda(capital_financiado / Decimal(costo.plazo))
    total = _moneda(cuota * Decimal(costo.plazo))
    intereses = _moneda(max(Decimal('0.00'), total - capital_financiado))
    return {
        **costo.as_dict(),
        'tasa_mensual': tasa_porcentaje,
        'capital_financiado': capital_financiado,
        'cuota_mensual': cuota,
        'total_intereses': intereses,
        'total_a_pagar': total,
    }


@transaction.atomic
def crear_snapshot_originacion_libranza(*, credito, regla_especial=None):
    from gestion_creditos.models import CondicionOriginacionLibranza, Credito

    if not credito.pk:
        raise CostoOriginacionLibranzaError(
            'credito_no_persistido',
            'El credito debe estar persistido antes de consolidar sus condiciones.',
        )

    # Serializa la consolidacion por credito sin bloquear relaciones asociadas.
    credito = (
        Credito.objects
        .select_for_update(of=('self',))
        .get(pk=credito.pk)
    )

    if credito.linea != Credito.LineaCredito.LIBRANZA:
        raise CostoOriginacionLibranzaError(
            'linea_invalida',
            'La politica de originacion solo aplica a creditos de Libranza.',
        )
    if regla_especial is not None:
        if credito.tipo_regla_credito != Credito.TipoReglaCredito.ESPECIAL:
            raise CostoOriginacionLibranzaError(
                'credito_no_es_especial',
                'Una regla especial solo puede vincularse a un credito marcado como especial.',
            )
        if regla_especial.credito_id != credito.id:
            raise CostoOriginacionLibranzaError(
                'regla_especial_no_vinculada',
                'La regla especial no esta vinculada al credito indicado.',
            )

    existente = CondicionOriginacionLibranza.objects.filter(credito=credito).first()
    if existente:
        validar_snapshot_originacion_libranza(credito, existente)
        return existente

    monto = credito.monto_aprobado or credito.monto_solicitado
    plazo = credito.plazo or credito.plazo_solicitado
    resultado = resolver_costo_originacion_libranza(
        fecha_referencia=credito.fecha_solicitud,
        monto=monto,
        plazo=plazo,
        es_especial=credito.tipo_regla_credito == Credito.TipoReglaCredito.ESPECIAL,
        regla_especial=regla_especial,
    )
    try:
        with transaction.atomic():
            snapshot = CondicionOriginacionLibranza.objects.create(
                credito=credito,
                regla_especial=regla_especial,
                **resultado.as_dict(),
            )
    except IntegrityError:
        snapshot = CondicionOriginacionLibranza.objects.get(credito=credito)
        validar_snapshot_originacion_libranza(credito, snapshot)

    credito.comision = snapshot.valor_originacion
    credito.iva_comision = snapshot.valor_iva
    credito.save(update_fields=['comision', 'iva_comision'])
    return snapshot


def validar_snapshot_originacion_libranza(credito, snapshot):
    monto_credito = _moneda(credito.monto_aprobado or credito.monto_solicitado)
    plazo_credito = int(credito.plazo or credito.plazo_solicitado)
    if snapshot.monto_base != monto_credito:
        raise CostoOriginacionLibranzaError(
            'monto_no_coincide_snapshot',
            'El monto del credito no coincide con sus condiciones de originacion.',
        )
    if snapshot.plazo != plazo_credito:
        raise CostoOriginacionLibranzaError(
            'plazo_no_coincide_snapshot',
            'El plazo del credito no coincide con sus condiciones de originacion.',
        )
    if snapshot.snapshot_hash != calcular_hash_snapshot(snapshot):
        raise CostoOriginacionLibranzaError(
            'snapshot_hash_invalido',
            'Las condiciones de originacion no superaron la validacion de integridad.',
        )
    return snapshot


def requiere_snapshot_originacion_libranza(credito):
    from gestion_creditos.models import Credito

    return (
        credito.linea == Credito.LineaCredito.LIBRANZA
        and _fecha_local(credito.fecha_solicitud) >= FECHA_INICIO_V2
    )


def calcular_hash_snapshot(snapshot):
    payload = {
        'credito_id': snapshot.credito_id,
        'fecha_referencia': snapshot.fecha_referencia.isoformat(),
        'codigo_politica': snapshot.codigo_politica,
        'version_politica': snapshot.version_politica,
        'origen': snapshot.origen,
        'monto_base': format(snapshot.monto_base, 'f'),
        'plazo': snapshot.plazo,
        'porcentaje_originacion': (
            format(snapshot.porcentaje_originacion, 'f')
            if snapshot.porcentaje_originacion is not None
            else None
        ),
        'valor_originacion': format(snapshot.valor_originacion, 'f'),
        'porcentaje_iva': format(snapshot.porcentaje_iva, 'f'),
        'valor_iva': format(snapshot.valor_iva, 'f'),
        'regla_especial_id': snapshot.regla_especial_id,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _resolver_regla_especial(*, fecha_referencia, monto, plazo, regla_especial):
    if _moneda(regla_especial.amount) != monto or int(regla_especial.term_months) != plazo:
        raise CostoOriginacionLibranzaError(
            'regla_especial_no_coincide',
            'La regla especial no coincide con el monto y plazo del credito.',
        )

    valor_originacion = _moneda(regla_especial.commission_amount)
    valor_iva = _moneda(regla_especial.vat_amount)
    porcentaje = (
        Decimal(regla_especial.commission_rate).quantize(CUATRO_DECIMALES, rounding=ROUND_HALF_UP)
        if regla_especial.commission_rate is not None
        else None
    )
    porcentaje_iva = _porcentaje_iva_especial(regla_especial, valor_originacion, valor_iva)
    return CostoOriginacionLibranza(
        codigo_politica=POLITICA_ESPECIAL,
        version_politica=f'AUDIT-{regla_especial.pk}',
        fecha_referencia=fecha_referencia,
        origen='ESPECIAL',
        monto_base=monto,
        plazo=plazo,
        porcentaje_originacion=porcentaje,
        valor_originacion=valor_originacion,
        porcentaje_iva=porcentaje_iva,
        valor_iva=valor_iva,
        regla_especial_id=regla_especial.pk,
    )


def _porcentaje_iva_especial(regla_especial, valor_originacion, valor_iva):
    payload = regla_especial.simulation_payload or {}
    raw_rate = payload.get('vat_rate')
    if raw_rate is not None:
        return Decimal(str(raw_rate)).quantize(CUATRO_DECIMALES, rounding=ROUND_HALF_UP)
    if valor_originacion > Decimal('0.00'):
        return (valor_iva * Decimal('100') / valor_originacion).quantize(
            CUATRO_DECIMALES,
            rounding=ROUND_HALF_UP,
        )
    return Decimal('0.0000')


def _porcentaje_v2(plazo):
    if plazo in (1, 2):
        return Decimal('10.0000')
    if plazo in (3, 4):
        return Decimal('11.0000')
    if plazo in (5, 6):
        return Decimal('12.0000')
    raise CostoOriginacionLibranzaError(
        'plazo_normal_fuera_politica',
        'Una Libranza normal bajo la politica vigente solo admite plazos de 1 a 6 meses.',
    )


def _normalizar_fecha_referencia(value):
    if value is None:
        return timezone.now()
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value
    if isinstance(value, date):
        return timezone.make_aware(
            datetime.combine(value, datetime.min.time()),
            timezone.get_current_timezone(),
        )
    raise CostoOriginacionLibranzaError('fecha_invalida', 'La fecha de referencia no es valida.')


def _fecha_local(value):
    normalized = _normalizar_fecha_referencia(value)
    return timezone.localtime(normalized).date()


def _moneda(value):
    try:
        return Decimal(str(value)).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CostoOriginacionLibranzaError('monto_invalido', 'El monto no es valido.') from exc


def _plazo(value):
    try:
        plazo = int(value)
    except (TypeError, ValueError) as exc:
        raise CostoOriginacionLibranzaError('plazo_invalido', 'El plazo no es valido.') from exc
    if plazo < 1:
        raise CostoOriginacionLibranzaError('plazo_invalido', 'El plazo debe ser mayor que cero.')
    return plazo
