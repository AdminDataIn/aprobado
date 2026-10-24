"""Permission-controlled administrative lifecycle; never editable as ordinary rows."""
from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from gestion_creditos.models import Empresa, RestriccionEmpresa
from gestion_creditos.services.restriccion_empresa import (
    MENSAJE_PUBLICO, crear_restriccion, exigir_gestor, levantar_restriccion,
)


class RestriccionForm(forms.Form):
    empresa = forms.ModelChoiceField(queryset=Empresa.objects.order_by('nombre'))
    producto = forms.ChoiceField(choices=RestriccionEmpresa.Producto.choices)
    motivo_interno = forms.CharField(widget=forms.Textarea)
    mensaje_publico = forms.CharField(max_length=300, initial=MENSAJE_PUBLICO, widget=forms.Textarea,
        help_text='Visible al solicitante: no incluir saldos, detalle de cartera ni datos personales.')
    inicio = forms.DateTimeField(required=False)
    vence_en = forms.DateTimeField(required=False)
    umbral_mora_dias = forms.IntegerField(min_value=1, max_value=32767, initial=90,
        help_text='Referencia administrativa. No calcula ni activa restricciones automaticamente.')


class LevantamientoForm(forms.Form):
    motivo = forms.CharField(widget=forms.Textarea)


@admin.register(RestriccionEmpresa)
class RestriccionEmpresaAdmin(admin.ModelAdmin):
    list_display = ('id', 'empresa', 'producto', 'estado', 'inicio', 'vence_en', 'levantada_en')
    list_filter = ('producto',)
    readonly_fields = tuple(field.name for field in RestriccionEmpresa._meta.fields) + ('levantar',)
    actions = None

    @admin.display(description='Estado')
    def estado(self, obj):
        from django.utils import timezone
        if obj.levantada_en:
            return 'LEVANTADA'
        if obj.vence_en and obj.vence_en <= timezone.now():
            return 'VENCIDA'
        return 'PROGRAMADA' if obj.inicio > timezone.now() else 'VIGENTE'

    def _autorizado(self, request):
        try:
            exigir_gestor(request.user)
        except PermissionDenied:
            return False
        return True

    def has_module_permission(self, request):
        return self._autorizado(request)

    def has_view_permission(self, request, obj=None):
        return self._autorizado(request)

    def has_add_permission(self, request):
        return self._autorizado(request)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Gestion')
    def levantar(self, obj):
        from django.utils.html import format_html
        if obj.levantada_en:
            return 'Levantada'
        return format_html('<a href="{}">Levantar restriccion</a>',
            reverse('admin:gestion_creditos_restriccionempresa_levantar', args=[obj.pk]))

    def get_urls(self):
        return [path('<int:object_id>/levantar/', self.admin_site.admin_view(self.levantar_view),
                     name='gestion_creditos_restriccionempresa_levantar')] + super().get_urls()

    def _formulario(self, request, form, titulo):
        return TemplateResponse(request, 'admin/gestion_creditos/restriccion_empresa_form.html', {
            **self.admin_site.each_context(request), 'opts': self.model._meta,
            'title': titulo, 'form': form,
        })

    def add_view(self, request, form_url='', extra_context=None):
        exigir_gestor(request.user)
        form = RestriccionForm(request.POST if request.method == 'POST' else None)
        if request.method == 'POST' and form.is_valid():
            datos = dict(form.cleaned_data)
            datos['empresa_id'] = datos.pop('empresa').pk
            try:
                registro = crear_restriccion(actor=request.user, **datos)
            except ValidationError as exc:
                form.add_error(None, '; '.join(exc.messages))
            else:
                messages.success(request, 'Restriccion registrada y auditada.')
                return redirect('admin:gestion_creditos_restriccionempresa_change', registro.pk)
        return self._formulario(request, form, 'Nueva restriccion empresarial')

    def levantar_view(self, request, object_id):
        exigir_gestor(request.user)
        get_object_or_404(RestriccionEmpresa, pk=object_id)
        form = LevantamientoForm(request.POST if request.method == 'POST' else None)
        if request.method == 'POST' and form.is_valid():
            levantar_restriccion(restriccion_id=object_id, actor=request.user, **form.cleaned_data)
            messages.success(request, 'Levantamiento registrado y auditado.')
            return redirect('admin:gestion_creditos_restriccionempresa_change', object_id)
        return self._formulario(request, form, 'Levantar restriccion empresarial')
