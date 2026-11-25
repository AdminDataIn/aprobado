"""Staff-only preview and downloads; never provides a provider artifact."""
from datetime import date
from functools import wraps
from hashlib import sha256
import json

from django import forms
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from gestion_creditos.models import CierreCarteraSME
from gestion_creditos.services.facilidat_xlsx import (
    REFERENCIA_FACILIDAT_SHA256, XLSX_CONTENT_TYPE, xlsx_excepciones, xlsx_sintetico_facilidat,
)
from gestion_creditos.services.reporte_sme import PRODUCTOS, construir_cierre, validar_parametros


PERMISO = 'gestion_creditos.operar_cierre_cartera_sme'


def requiere_operador(func):
    @wraps(func)
    def comprobar(request, *args, **kwargs):
        if not request.user.has_perm(PERMISO) or hasattr(request.user, 'perfil_pagador'):
            raise PermissionDenied
        return func(request, *args, **kwargs)
    return never_cache(staff_member_required(comprobar))


def serializar(data):
    return json.dumps(data, cls=DjangoJSONEncoder, sort_keys=True, ensure_ascii=False, indent=2).encode('utf-8')


class CierreCarteraSMEForm(forms.Form):
    corte = forms.DateField(label='Fecha de corte', widget=forms.DateInput(
        format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}))
    producto = forms.ChoiceField(label='Producto', choices=PRODUCTOS, required=False,
                                 widget=forms.Select(attrs={'class': 'form-select'}))

    def clean(self):
        cleaned = super().clean()
        if not self.errors:
            try:
                validar_parametros(cleaned['corte'], cleaned['producto'])
            except ValueError:
                self.add_error('corte', 'Seleccione un día ya cerrado en la hora de Bogotá.')
        return cleaned


@requiere_operador
@require_http_methods(['GET', 'POST'])
def cartera_sme(request, cierre_id=None):
    cierre = get_object_or_404(CierreCarteraSME, pk=cierre_id) if cierre_id else None
    form = CierreCarteraSMEForm(
        request.POST if request.method == 'POST' else None,
        initial={'corte': cierre.corte if cierre else date(2026, 10, 31),
                 'producto': cierre.producto if cierre else ''},
    )
    if request.method == 'POST' and form.is_valid():
        try:
            datos = construir_cierre(**form.cleaned_data)
        except (RuntimeError, DatabaseError):
            form.add_error(None, 'No fue posible obtener una lectura histórica consistente. No se guardó un cierre.')
        else:
            datos['plantilla_sha256'] = REFERENCIA_FACILIDAT_SHA256
            datos['operador_id'] = request.user.pk
            archivo = serializar(datos)
            copia = CierreCarteraSME.objects.create(
                corte=form.cleaned_data['corte'], producto=form.cleaned_data['producto'],
                generado_por=request.user, plantilla_sha256=REFERENCIA_FACILIDAT_SHA256,
                sha256=sha256(archivo).hexdigest(), contenido=json.loads(archivo),
            )
            return redirect('gestion:cartera_sme_detalle', cierre_id=copia.pk)
    return render(request, 'gestion_creditos/admin_cartera_sme.html', {
        'form': form, 'cierre': cierre, 'historial': CierreCarteraSME.objects.defer('contenido')[:30],
    })


@requiere_operador
@require_GET
def descargar_cartera_sme(request, cierre_id, clase):
    if clase not in ('conciliacion', 'excepciones', 'excepciones-xlsx', 'facilidat-sintetico', 'facilidat-real'):
        raise Http404
    cierre = get_object_or_404(CierreCarteraSME, pk=cierre_id)
    contenido = serializar(cierre.contenido)
    if sha256(contenido).hexdigest() != cierre.sha256:
        return HttpResponse('Huella del cierre no coincide. Descarga bloqueada.', status=409)
    if clase == 'facilidat-real':
        return HttpResponse('Carga real bloqueada: homologación y soportes no acreditados.', status=409)
    tipo = 'application/json; charset=utf-8'
    nombre = f'sme-interno-{clase}-{cierre.corte}-{cierre.pk}.json'
    if clase == 'excepciones':
        contenido = serializar({
            'tipo': 'EXCEPCIONES_INTERNAS_NO_REMITIR', 'corte': cierre.corte,
            'huella_conciliacion': cierre.sha256,
            'filas': [{k: row[k] for k in ('credito_id_interno', 'numero_obligacion_interno', 'producto', 'categoria',
                                           'incidencia_financiera', 'impedimentos_reporte') if k in row}
                      for row in cierre.contenido['filas'] if row['impedimentos_reporte']],
        })
    elif clase == 'excepciones-xlsx':
        contenido = xlsx_excepciones(cierre.contenido, cierre.sha256)
        tipo = XLSX_CONTENT_TYPE
        nombre = f'SME_EXCEPCIONES_{cierre.corte}_NO_REMITIR.xlsx'
    elif clase == 'facilidat-sintetico':
        contenido = xlsx_sintetico_facilidat()
        tipo = XLSX_CONTENT_TYPE
        nombre = 'FACILIDAT_CARGA_SINTETICA_NO_REMITIR.xlsx'
    respuesta = HttpResponse(contenido, content_type=tipo)
    respuesta['Content-Disposition'] = f'attachment; filename="{nombre}"'
    respuesta['X-Content-Type-Options'] = 'nosniff'
    respuesta['X-Reporte-SHA256'] = sha256(contenido).hexdigest()
    respuesta['X-Cierre-SHA256'] = cierre.sha256
    return respuesta
