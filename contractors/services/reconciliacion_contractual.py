"""Compare private evidence, never overwrite data or verify identity."""
import hashlib
import json
from decimal import Decimal

from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone

from contractors.validators import clave_texto, normalizar_nit
from contractors.services.extraccion_campos import ESQUEMA, datos_canonicos


def comparar(campo, formulario, documento, fuente='CONTRATO', *, comparable=True):
    vacio = lambda value: value in (None, '', 'NO_IDENTIFICADA')
    estado = 'NO_COMPARABLE'
    if comparable:
        if vacio(formulario) or vacio(documento):
            estado = 'FALTANTE'
        else:
            normalizar = (lambda v: Decimal(str(v))) if campo.startswith('valor_') else clave_texto
            try:
                estado = 'COINCIDE' if normalizar(formulario) == normalizar(documento) else 'DIFIERE'
            except (ValueError, ArithmeticError):
                pass
    return {'campo': campo, 'formulario': '' if formulario is None else str(formulario), 'documento': '' if documento is None else str(documento),
            'fuente': fuente, 'estado': estado}


def reconciliar(metadata, datos, *, sesion=None):
    extraidos = datos_canonicos(metadata.get('datos_sugeridos', {}))
    filas = [comparar(campo, datos.get(campo), extraidos.get(campo))
             for campo, (_, _, editable) in ESQUEMA.items() if editable]
    for fila in filas:
        if fila['campo'] == 'valor_pagado_contrato' and fila['estado'] == 'DIFIERE':
            fila.update(estado='DIFERENCIA_TEMPORAL', etiqueta='Pagado actual declarado',
                        mensaje=(f"Pagado al corte del documento: {fila['documento']}. "
                                 f"Pagado actual declarado: {fila['formulario']}. Confirma el dato actual; "
                                 'el calendario no demuestra pagos efectivos.'))
        if fila['campo'] == 'valor_pendiente_cobrar' and saldo_es_derivado(metadata):
            fila.update(estado='NO_COMPARABLE', fuente='SALDO_DERIVADO_ACTUAL')
    filas.append(comparar('titular', ' '.join(filter(None, (datos.get('nombres'), datos.get('apellidos')))),
                          extraidos.get('titular')))
    coincide = metadata.get('identidad', {}).get('documento_coincide')
    filas.append({'campo': 'numero_documento', 'formulario': '****' + str(datos.get('numero_documento', ''))[-4:],
                  'documento': metadata.get('datos_sugeridos', {}).get('documento_detectado', ''), 'fuente': 'CONTRATO',
                  'estado': 'COINCIDE' if coincide is True else 'DIFIERE' if coincide is False else 'FALTANTE'})
    empresa = datos.get('empresa')
    nit = extraidos.get('nit_empresa')
    if empresa and nit:
        try:
            base = normalizar_nit(nit).split('-')[0]
            registrada = normalizar_nit(empresa.nit).split('-')[0]
            filas.append(comparar('nit_empresa', registrada, base, 'CONVENIO/CONTRATO'))
        except ValidationError:
            filas.append(comparar('nit_empresa', empresa.nit, nit, 'CONVENIO/CONTRATO', comparable=False))
    else:
        filas.append(comparar('nit_empresa', empresa.nit if empresa else '', nit, 'CONVENIO/CONTRATO'))
    from contractors.services.analisis_contractual_seguro import normalizar_nombre_empresa
    filas.append(comparar('empresa', normalizar_nombre_empresa(empresa.razon_social or empresa.nombre) if empresa else '',
                          normalizar_nombre_empresa(extraidos.get('empresa')), 'CONVENIO/CONTRATO'))
    if sesion and datos.get('tipo_documento') == 'CC':
        ocr = sesion.procesamientos_ocr.filter(vigente=True, purgado_en__isnull=True,
            estado='COMPLETADO', captura_frontal__activo=True, captura_trasera__activo=True,
            tipo_documento_detectado='CC').order_by('-finalizado_en', '-pk').first()
        if ocr:
            for campo in ('numero_documento', 'nombres', 'apellidos'):
                fila = comparar(campo, datos.get(campo), getattr(ocr, campo + '_normalizado'), 'OCR_CEDULA_NO_IDENTIDAD')
                if campo == 'numero_documento':
                    fila.update(formulario='****' + fila['formulario'][-4:], documento='****' + fila['documento'][-4:])
                filas.append(fila)
    for fila in filas:
        fila.setdefault('etiqueta', ESQUEMA[fila['campo']][1])
    return filas


def saldo_es_derivado(metadata):
    return (metadata.get('campos_extraidos', {}).get('valor_pendiente_cobrar', {}).get('fuente')
            == 'DERIVADO_DETERMINISTICAMENTE')


def ajustar_datos_contractuales(datos, metadata):
    """Fill missing periodicity; refresh only a previously suggested derived balance."""
    sugeridos = datos_canonicos(metadata.get('datos_sugeridos', {}))
    if datos.get('forma_pago') in (None, '', 'NO_IDENTIFICADA') and sugeridos.get('forma_pago'):
        datos['forma_pago'] = sugeridos['forma_pago']
    if not saldo_es_derivado(metadata):
        return
    total, pagado, saldo = (datos.get(c) for c in
                           ('valor_total_contrato', 'valor_pagado_contrato', 'valor_pendiente_cobrar'))
    try:
        anterior = Decimal(str(sugeridos.get('valor_pendiente_cobrar')))
        if total is not None and pagado is not None and 0 <= pagado <= total and saldo == anterior:
            datos['valor_pendiente_cobrar'] = total - pagado
    except (ValueError, ArithmeticError):
        return


def confirmar_reconciliacion(*, form, evidencia, actor, sesion=None):
    from gestion_creditos.models import SesionCapturaDocumental

    solicitud_id = getattr(getattr(form, 'instance', None), 'pk', None)
    if sesion is None and solicitud_id:
        sesion = SesionCapturaDocumental.objects.filter(
            usuario=actor, producto='PRESTADORES', solicitud_id=solicitud_id,
            estado='UTILIZADA', revocado_en__isnull=True,
        ).order_by('-utilizado_en').first()
    if sesion and (sesion.usuario_id != actor.pk or sesion.producto != 'PRESTADORES'
                   or sesion.solicitud_id not in (None, solicitud_id)):
        raise ValidationError('La evidencia documental no corresponde a esta solicitud.')
    metadata = dict(evidencia.get('metadata_segura') or {})
    filas = reconciliar(metadata, form.cleaned_data, sesion=sesion)
    pendientes = [f for f in filas if f['estado'] in ('DIFIERE', 'DIFERENCIA_TEMPORAL') or
                  (f['campo'] == 'nit_empresa' and f['estado'] == 'NO_COMPARABLE')]
    form.reconciliacion = filas
    material = {'filas': filas, 'archivo': evidencia['archivo_hash_sha256'],
                'actor': actor.pk,
                'empresa': getattr(form.cleaned_data.get('empresa'), 'pk', None)}
    digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
    confirmado = False
    if pendientes and form.cleaned_data.get('confirma_discrepancias'):
        try:
            confirmado = signing.loads(form.cleaned_data.get('reconciliacion_token', ''),
                                        salt='prestador-reconciliacion', max_age=3600) == digest
        except signing.BadSignature:
            pass
    if pendientes and not confirmado:
        form.data = form.data.copy()
        form.data['reconciliacion_token'] = signing.dumps(digest, salt='prestador-reconciliacion')
        form.data.pop('confirma_discrepancias', None)
        detalle = '; '.join(f.get('mensaje') or f"{f['etiqueta']}: tu formulario {f['formulario']}; documento {f['documento']}"
                            for f in pendientes)
        return evidencia, 'Revisa las diferencias y confirma los datos antes de continuar. ' + detalle
    metadata['reconciliacion'] = {'version': 1, 'campos': filas, 'requiere_revision': bool(pendientes),
                                  'confirmada': confirmado, 'actor_id': actor.pk,
                                  'registrada_en': timezone.now().isoformat()}
    if saldo_es_derivado(metadata):
        total = form.cleaned_data.get('valor_total_contrato')
        pagado = form.cleaned_data.get('valor_pagado_contrato')
        saldo = form.cleaned_data.get('valor_pendiente_cobrar')
        metadata['saldo_actual_declarado'] = {
            'valor': str(saldo),
            'fuente': ('TOTAL_MENOS_PAGADO_ACTUAL_DECLARADO' if total is not None and pagado is not None
                       and saldo == total - pagado else 'DECLARACION_USUARIO'),
        }
    return {**evidencia, 'metadata_segura': metadata}, ''
