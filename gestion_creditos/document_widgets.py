"""Enlaces iniciales de formularios sin exponer nombres de almacenamiento."""
from types import SimpleNamespace

from django import forms
from django.utils.html import format_html

from gestion_creditos.services.documentos_privados import recurso_documental, url_documento


class DocumentoInicial(SimpleNamespace):
    def __str__(self):
        return 'Documento protegido'


class DocumentoPrivadoInput(forms.ClearableFileInput):
    def get_context(self, name, value, attrs):
        if value and hasattr(value, 'instance'):
            value = DocumentoInicial(url=url_documento(value))
        return super().get_context(name, value, attrs)


class DocumentosPrivadosAdminMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._enlaces_documentales = {}
        for field in self.model._meta.fields:
            if recurso_documental(self.model, field.name):
                nombre = 'documento_protegido_' + field.name
                def enlace(instance, campo=field.name):
                    url = url_documento(getattr(instance, campo))
                    return format_html('<a href="{}">Documento protegido</a>', url) if url else '-'
                enlace.short_description = field.verbose_name
                setattr(self, nombre, enlace)
                self._enlaces_documentales[field.name] = nombre

    def get_readonly_fields(self, request, obj=None):
        readonly = tuple(super().get_readonly_fields(request, obj))
        return readonly + tuple(nombre for campo, nombre in self._enlaces_documentales.items()
                                if campo in readonly or not self.has_change_permission(request, obj))

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if recurso_documental(self.model, db_field.name):
            kwargs['widget'] = DocumentoPrivadoInput
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        readonly = super().get_readonly_fields(request, obj)
        def proteger(field):
            if isinstance(field, (tuple, list)):
                return tuple(proteger(item) for item in field)
            if isinstance(field, str) and recurso_documental(self.model, field) and (
                field in readonly or not self.has_change_permission(request, obj)
            ):
                return self._enlaces_documentales[field]
            return field
        return [(title, {**options, 'fields': proteger(options['fields'])})
                for title, options in fieldsets]
