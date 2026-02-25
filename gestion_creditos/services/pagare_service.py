"""
Servicio para generacion de pagares en PDF con plantilla HTML.
Incluye generacion de hash SHA-256 para trazabilidad legal.
"""

import hashlib
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from gestion_creditos.models import Credito, Pagare
from .pagare_utils import numero_a_letras, numero_a_letras_simple, formatear_cop


# Diccionario de meses en español
MESES_ESPANOL = {
    1: 'enero', 2: 'febrero', 3: 'marzo', 4: 'abril',
    5: 'mayo', 6: 'junio', 7: 'julio', 8: 'agosto',
    9: 'septiembre', 10: 'octubre', 11: 'noviembre', 12: 'diciembre'
}


def _fecha_en_espanol(fecha):
    """Formatea una fecha en español: 'dd de mes de yyyy'"""
    dia = fecha.day
    mes = MESES_ESPANOL.get(fecha.month, '')
    anio = fecha.year
    return f"{dia} de {mes} de {anio}"


def _mes_en_espanol(fecha):
    """Retorna el nombre del mes en español"""
    return MESES_ESPANOL.get(fecha.month, '')


def generar_pagare_pdf(credito, usuario_creador=None):
    """
    Genera el PDF del pagare para un credito aprobado.

    Args:
        credito (Credito): Instancia del credito
        usuario_creador (User, optional): Usuario que genera el pagare

    Returns:
        Pagare: Instancia del pagare creado con el PDF generado

    Raises:
        ValueError: Si el credito no es de tipo EMPRENDIMIENTO o LIBRANZA
    """
    lineas_permitidas = [
        Credito.LineaCredito.EMPRENDIMIENTO,
        Credito.LineaCredito.LIBRANZA,
    ]
    if credito.linea not in lineas_permitidas:
        raise ValueError("Solo se pueden generar pagares para creditos de EMPRENDIMIENTO o LIBRANZA")

    estados_permitidos = [
        Credito.EstadoCredito.APROBADO,
        Credito.EstadoCredito.PENDIENTE_FIRMA,
        Credito.EstadoCredito.FIRMADO,
        Credito.EstadoCredito.PENDIENTE_TRANSFERENCIA,
        Credito.EstadoCredito.ACTIVO,
    ]
    if credito.estado not in estados_permitidos:
        raise ValueError(
            "El credito debe estar en estados: "
            + ", ".join([e.label for e in estados_permitidos])
        )

    detalle = credito.detalle
    if not detalle:
        raise ValueError("El credito no tiene detalle asociado")

    pagare_existente = getattr(credito, 'pagare', None)
    if pagare_existente and _archivo_existe(pagare_existente.archivo_pdf.name):
        return pagare_existente

    pagare = pagare_existente or _crear_pagare_base(credito, usuario_creador)
    pagare_nuevo = pagare_existente is None

    try:
        contexto = _preparar_contexto_pagare(credito, detalle, pagare.numero_pagare)
        html_string = render_to_string('pagares/pagare_v1.0.html', contexto)

        pdf_bytes = HTML(string=html_string).write_pdf()
        hash_pdf = hashlib.sha256(pdf_bytes).hexdigest()

        nombre_archivo = f"pagare_{pagare.numero_pagare}.pdf"
        ruta_relativa = f"pagares/{timezone.now().year}/{timezone.now().month:02d}/{nombre_archivo}"

        ruta_completa = Path(settings.MEDIA_ROOT) / ruta_relativa
        ruta_completa.parent.mkdir(parents=True, exist_ok=True)

        with open(ruta_completa, 'wb') as f:
            f.write(pdf_bytes)

        pagare.archivo_pdf.name = ruta_relativa
        pagare.hash_pdf = hash_pdf
        pagare.version_plantilla = '1.0'
        pagare.save(update_fields=['archivo_pdf', 'hash_pdf', 'version_plantilla'])

        return pagare

    except Exception:
        if pagare_nuevo:
            pagare.delete()
        raise


def _archivo_existe(ruta_relativa):
    if not ruta_relativa:
        return False
    return (Path(settings.MEDIA_ROOT) / ruta_relativa).exists()


def _crear_pagare_base(credito, usuario_creador=None):
    nombre_archivo = f"pagare_pendiente_{uuid4().hex}.pdf"
    ruta_relativa = f"pagares/{timezone.now().year}/{timezone.now().month:02d}/{nombre_archivo}"

    return Pagare.objects.create(
        credito=credito,
        estado=Pagare.EstadoPagare.CREATED,
        version_plantilla='1.0',
        archivo_pdf=ruta_relativa,
        creado_por=usuario_creador,
    )


def _preparar_contexto_pagare(credito, detalle, numero_pagare):
    """
    Prepara el contexto de datos para renderizar la plantilla del pagare.
    """
    tasa_interes = _obtener_tasa_interes(credito)
    tasa_mensual = tasa_interes / Decimal('100') if tasa_interes else Decimal('0.00')

    usuario = credito.usuario
    if credito.linea == Credito.LineaCredito.LIBRANZA:
        nombre_deudor = detalle.nombre_completo or f"{usuario.first_name} {usuario.last_name}".strip() or usuario.username
        cedula_deudor = detalle.cedula or "NO REGISTRADA"
        telefono_deudor = detalle.telefono or usuario.email
        direccion_deudor = detalle.direccion or "NO REGISTRADA"
        email_deudor = detalle.correo_electronico or usuario.email
    else:
        nombre_deudor = detalle.nombre or f"{usuario.first_name} {usuario.last_name}".strip() or usuario.username
        cedula_deudor = detalle.numero_cedula or "NO REGISTRADA"
        telefono_deudor = detalle.celular_wh or usuario.email
        direccion_deudor = detalle.direccion or "NO REGISTRADA"
        email_deudor = usuario.email

    nombre_acreedor = "APROBADO SOLUCIONES DIGITALES SAS"
    nit_acreedor = "901949137-2"

    monto_base = credito.monto_aprobado or credito.monto_solicitado or Decimal('0.00')
    comision = credito.comision if credito.comision is not None else (monto_base * Decimal('0.10'))
    iva_comision = credito.iva_comision if credito.iva_comision is not None else (comision * Decimal('0.19'))
    capital_financiado = monto_base + comision + iva_comision

    valor_cuota = credito.valor_cuota
    if not valor_cuota:
        valor_cuota = _calcular_valor_cuota(capital_financiado, tasa_mensual, credito.plazo or 0)
    else:
        valor_cuota = Decimal(str(valor_cuota)).quantize(Decimal('0.01'))

    hoy = timezone.localdate()
    fecha_expedicion = _fecha_en_espanol(hoy)
    fecha_primer_pago = credito.fecha_proximo_pago or (hoy + timedelta(days=30))
    fecha_vencimiento = _calcular_fecha_vencimiento(fecha_primer_pago, credito.plazo or 0)

    plazo_cuotas = credito.plazo or 0
    plazo_cuotas_letras = numero_a_letras_simple(plazo_cuotas) if plazo_cuotas else "cero"
    periodicidad = "mensuales"

    # Obtener ciudad del deudor o usar Villavicencio por defecto
    if credito.linea == Credito.LineaCredito.LIBRANZA:
        ciudad_deudor = getattr(detalle, 'ciudad', "Villavicencio")
    else:
        # Para emprendimiento, extraer de la dirección o usar default
        ciudad_deudor = "Villavicencio"

    lugar_expedicion = "Villavicencio, Meta, Colombia"
    lugar_pago = f"{ciudad_deudor}, Meta, Colombia"

    # Calcular otros conceptos (comision + IVA)
    otros_conceptos = (comision or Decimal('0.00')) + (iva_comision or Decimal('0.00'))

    # Calcular intereses totales
    valor_total_pagar = valor_cuota * Decimal(str(plazo_cuotas))
    intereses_totales = valor_total_pagar - capital_financiado
    if intereses_totales < 0:
        intereses_totales = Decimal('0.00')

    # Fecha actual en partes (en español).
    # Se pasan como string para evitar localización numérica ("2.026").
    dia_actual = str(hoy.day)
    mes_actual = _mes_en_espanol(hoy)
    anio_actual = str(hoy.year)

    return {
        'numero_pagare': numero_pagare,
        'deudor_nombres': nombre_deudor,
        'ciudad_domicilio': ciudad_deudor,
        'cedula_deudor': cedula_deudor,
        'telefono_deudor': telefono_deudor,
        'direccion_deudor': direccion_deudor,
        'email_deudor': email_deudor,
        'acreedor_nombre': nombre_acreedor,
        'acreedor_detalle': f"(NIT {nit_acreedor})",
        'capital_valor': formatear_cop(monto_base),
        'intereses_valor': formatear_cop(intereses_totales),
        'otros_conceptos_valor': formatear_cop(otros_conceptos) if otros_conceptos > 0 else '',
        'monto_numeros': formatear_cop(monto_base),
        'monto_letras': numero_a_letras(monto_base),
        'valor_cuota': formatear_cop(valor_cuota),
        'tasa_interes': f"{tasa_interes:.2f}",
        'fecha_expedicion': fecha_expedicion,
        'fecha_vencimiento': _fecha_en_espanol(fecha_vencimiento),
        'fecha_primer_pago': _fecha_en_espanol(fecha_primer_pago),
        'lugar_expedicion': lugar_expedicion,
        'lugar_pago': lugar_pago,
        'ciudad_firma': ciudad_deudor,
        'plazo_cuotas': plazo_cuotas,
        'plazo_cuotas_letras': plazo_cuotas_letras,
        'periodicidad': periodicidad,
        # Fechas en partes (en español)
        # Para el texto inicial del pagaré usamos fecha de suscripción.
        # Esto alinea ambas páginas con la fecha real de firma del documento.
        'dia_pago': dia_actual,
        'mes_pago': mes_actual,
        'anio_pago': anio_actual,
        'dia_firma': dia_actual,
        'mes_firma': mes_actual,
        'anio_firma': anio_actual,
    }


def _obtener_tasa_interes(credito):
    if credito.tasa_interes is not None:
        return Decimal(str(credito.tasa_interes))

    if credito.linea == Credito.LineaCredito.EMPRENDIMIENTO:
        return Decimal('3.5')
    if credito.linea == Credito.LineaCredito.LIBRANZA:
        return Decimal('2.0')
    return Decimal('0.00')


def _calcular_valor_cuota(capital_financiado, tasa_mensual, plazo_cuotas):
    if not plazo_cuotas:
        return Decimal('0.00')

    if tasa_mensual <= 0:
        return (capital_financiado / plazo_cuotas).quantize(Decimal('0.01'))

    factor = (tasa_mensual * (1 + tasa_mensual) ** plazo_cuotas) / (((1 + tasa_mensual) ** plazo_cuotas) - 1)
    cuota = capital_financiado * factor
    return cuota.quantize(Decimal('0.01'))


def _calcular_fecha_vencimiento(fecha_primer_pago, plazo_cuotas):
    """
    Calcula la fecha de vencimiento final basandose en el primer pago y el plazo.
    """
    if plazo_cuotas <= 1:
        return fecha_primer_pago

    from dateutil.relativedelta import relativedelta
    meses_totales = plazo_cuotas - 1
    return fecha_primer_pago + relativedelta(months=meses_totales)


def calcular_hash_pdf(archivo_pdf):
    """
    Calcula el hash SHA-256 de un archivo PDF existente.
    """
    sha256_hash = hashlib.sha256()
    archivo_pdf.seek(0)
    for byte_block in iter(lambda: archivo_pdf.read(4096), b""):
        sha256_hash.update(byte_block)
    archivo_pdf.seek(0)
    return sha256_hash.hexdigest()
