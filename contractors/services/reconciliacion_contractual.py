"""Compare private evidence, never overwrite data or verify identity."""
import hashlib
import json
from decimal import Decimal

from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone

from contractors.validators import clave_texto, normalizar_nit


MAPA = {
    'nombres': 'nombres', 'apellidos': 'apellidos', 'cargo': 'cargo_o_servicio',
    'tipo_contrato': 'tipo_contrato', 'fecha_inicio_contrato': 'fecha_inicio_contrato',
    'fecha_fin_contrato': 'fecha_fin_contrato', 'valor_total_contrato': 'valor_total_contrato',
    'valor_pagado_contrato': 'valor_pagado_estimado', 'valor_pendiente_cobrar': 'valor_pendiente_estimado',
    'valor_mensual_contractual': 'valor_mensual_o_honorarios',
    'duracion_contrato_meses': 'duracion_meses_contrato', 'forma_pago': 'forma_pago',
}

ETIQUETAS = {
    'nombres': 'Nombres', 'apellidos': 'Apellidos', 'titular': 'Titular',
    'numero_documento': 'Documento', 'empresa': 'Empresa', 'nit_empresa': 'NIT',
    'cargo': 'Cargo o actividad', 'tipo_contrato': 'Tipo de contrato',
    'fecha_inicio_contrato': 'Fecha de inicio', 'fecha_fin_contrato': 'Fecha de fin',
    'valor_total_contrato': 'Valor total', 'valor_pagado_contrato': 'Valor pagado',
    'valor_pendiente_cobrar': 'Saldo pendiente', 'valor_mensual_contractual': 'Valor mensual',
    'duracion_contrato_meses': 'Duracion en meses', 'forma_pago': 'Forma de pago',
}


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
    extraidos = metadata.get('datos_sugeridos', {})
    filas = [comparar(campo, datos.get(campo), extraidos.get(origen)) for campo, origen in MAPA.items()]
    filas.append(comparar('titular', ' '.join(filter(None, (datos.get('nombres'), datos.get('apellidos')))),
                          extraidos.get('nombre_contratista')))
    coincide = metadata.get('identidad', {}).get('documento_coincide')
    filas.append({'campo': 'numero_documento', 'formulario': '****' + str(datos.get('numero_documento', ''))[-4:],
                  'documento': extraidos.get('documento_detectado', ''), 'fuente': 'CONTRATO',
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
                          normalizar_nombre_empresa(extraidos.get('empresa_contratante')), 'CONVENIO/CONTRATO'))
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
        fila['etiqueta'] = ETIQUETAS[fila['campo']]
    return filas


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
    pendientes = [f for f in filas if f['estado'] == 'DIFIERE' or
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
        return evidencia, 'Revisa las diferencias entre tu formulario y los documentos y confirma los datos antes de continuar.'
    metadata['reconciliacion'] = {'version': 1, 'campos': filas, 'requiere_revision': bool(pendientes),
                                  'confirmada': confirmado, 'actor_id': actor.pk,
                                  'registrada_en': timezone.now().isoformat()}
    return {**evidencia, 'metadata_segura': metadata}, ''
