from django import forms

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    MAPA_CAMPOS_DOCUMENTOS_PRESTADOR,
)
from gestion_creditos.models import Empresa


class SolicitudPrestadorForm(forms.ModelForm):
    MAPA_DOCUMENTOS = MAPA_CAMPOS_DOCUMENTOS_PRESTADOR

    documento_identidad_frontal = forms.FileField(
        label='Cédula frontal',
        required=True,
        widget=forms.ClearableFileInput(attrs={'class': 'campo documento-input', 'accept': 'image/jpeg,image/png,application/pdf'}),
        error_messages={'required': 'Carga la cédula frontal.'},
    )
    documento_identidad_reverso = forms.FileField(
        label='Cédula trasera',
        required=True,
        widget=forms.ClearableFileInput(attrs={'class': 'campo documento-input', 'accept': 'image/jpeg,image/png,application/pdf'}),
        error_messages={'required': 'Carga la cédula trasera.'},
    )
    certificado_bancario = forms.FileField(
        label='Certificado bancario PDF',
        required=True,
        widget=forms.ClearableFileInput(attrs={'class': 'campo documento-input', 'accept': 'application/pdf'}),
        error_messages={'required': 'Carga el certificado bancario en PDF.'},
    )
    contrato_actual = forms.FileField(
        label='Contrato vigente PDF',
        required=True,
        widget=forms.ClearableFileInput(attrs={'class': 'campo documento-input', 'accept': 'application/pdf'}),
        error_messages={'required': 'Carga el contrato vigente en PDF.'},
    )

    empresa = forms.ModelChoiceField(
        label='Empresa contratante',
        queryset=Empresa.objects.none(),
        empty_label='Selecciona una empresa registrada',
        widget=forms.Select(attrs={'class': 'campo'}),
        error_messages={
            'required': 'Debes elegir una empresa valida de la lista.',
            'invalid_choice': 'Debes elegir una empresa valida de la lista.',
        },
    )

    class Meta:
        model = ContractorApplication
        fields = [
            'escenario_credito',
            'tipo_documento',
            'numero_documento',
            'nombres',
            'apellidos',
            'celular',
            'correo',
            'direccion',
            'cargo',
            'tipo_contrato',
            'empresa',
            'fecha_inicio_contrato',
            'fecha_fin_contrato',
            'valor_total_contrato',
            'valor_pagado_contrato',
            'valor_pendiente_cobrar',
            'observaciones_contrato',
            'monto_solicitado',
            'plazo_meses',
            'acepta_terminos',
            'acepta_politica_privacidad',
            'autoriza_analisis_contractual_asistido',
            'autoriza_consulta_centrales',
        ]
        widgets = {
            'escenario_credito': forms.Select(attrs={'class': 'campo'}),
            'tipo_documento': forms.Select(attrs={'class': 'campo'}),
            'numero_documento': forms.TextInput(attrs={'class': 'campo', 'placeholder': 'Ej. 1020304050'}),
            'nombres': forms.TextInput(attrs={'class': 'campo', 'placeholder': 'Ej. Ana María'}),
            'apellidos': forms.TextInput(attrs={'class': 'campo', 'placeholder': 'Ej. Pérez Gómez'}),
            'celular': forms.TextInput(attrs={'class': 'campo', 'placeholder': '3001234567'}),
            'correo': forms.EmailInput(attrs={'class': 'campo', 'placeholder': 'correo@dominio.com'}),
            'direccion': forms.TextInput(attrs={'class': 'campo', 'placeholder': 'Ej. Calle 10 # 20-30, Bogotá'}),
            'cargo': forms.TextInput(attrs={'class': 'campo', 'placeholder': 'Cargo o servicio prestado'}),
            'tipo_contrato': forms.Select(attrs={'class': 'campo'}),
            'fecha_inicio_contrato': forms.DateInput(attrs={'class': 'campo', 'type': 'date'}),
            'fecha_fin_contrato': forms.DateInput(attrs={'class': 'campo', 'type': 'date'}),
            'valor_total_contrato': forms.NumberInput(attrs={'class': 'campo', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 12000000'}),
            'valor_pagado_contrato': forms.NumberInput(attrs={'class': 'campo', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 4000000'}),
            'valor_pendiente_cobrar': forms.NumberInput(attrs={'class': 'campo', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 8000000'}),
            'observaciones_contrato': forms.Textarea(attrs={'class': 'campo', 'rows': 3, 'placeholder': 'Observaciones contractuales opcionales'}),
            'monto_solicitado': forms.NumberInput(attrs={'class': 'campo', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 3000000'}),
            'plazo_meses': forms.NumberInput(attrs={'class': 'campo', 'min': '1', 'placeholder': 'Ej. 12'}),
            'acepta_terminos': forms.CheckboxInput(attrs={'class': 'authorization-checkbox'}),
            'acepta_politica_privacidad': forms.CheckboxInput(attrs={'class': 'authorization-checkbox'}),
            'autoriza_analisis_contractual_asistido': forms.CheckboxInput(attrs={'class': 'authorization-checkbox'}),
            'autoriza_consulta_centrales': forms.CheckboxInput(attrs={'class': 'authorization-checkbox'}),
        }
        labels = {
            'escenario_credito': 'Escenario',
            'tipo_documento': 'Tipo de documento',
            'numero_documento': 'Número de documento',
            'nombres': 'Nombres',
            'apellidos': 'Apellidos',
            'celular': 'Celular',
            'correo': 'Correo electrónico',
            'direccion': 'Dirección',
            'cargo': 'Cargo o actividad',
            'tipo_contrato': 'Tipo de contrato',
            'fecha_inicio_contrato': 'Fecha inicio contrato',
            'fecha_fin_contrato': 'Fecha fin contrato',
            'valor_total_contrato': 'Valor total contrato',
            'valor_pagado_contrato': 'Valor pagado del contrato',
            'valor_pendiente_cobrar': 'Valor pendiente por cobrar',
            'observaciones_contrato': 'Observaciones del contrato',
            'monto_solicitado': 'Monto solicitado',
            'plazo_meses': 'Plazo solicitado en meses',
            'acepta_terminos': 'Acepto los términos y condiciones',
            'acepta_politica_privacidad': 'Acepto la política de privacidad',
            'autoriza_analisis_contractual_asistido': 'Autorizo el análisis contractual asistido',
            'autoriza_consulta_centrales': 'Autorizo la consulta futura ante centrales de información',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['empresa'].queryset = Empresa.objects.filter(
            convenio_activo=True,
        ).order_by('nombre')
        for campo in (
            'fecha_inicio_contrato',
            'fecha_fin_contrato',
            'valor_total_contrato',
            'valor_pagado_contrato',
            'valor_pendiente_cobrar',
            'monto_solicitado',
            'plazo_meses',
        ):
            self.fields[campo].required = True

        documentos_existentes = set()
        if self.instance and self.instance.pk:
            documentos_existentes = set(
                self.instance.documentos.values_list('tipo_documento', flat=True)
            )
        for campo, tipo_documento in self.MAPA_DOCUMENTOS.items():
            existe = tipo_documento in documentos_existentes
            self.fields[campo].required = not existe
            self.fields[campo].widget.attrs['data-existing'] = 'true' if existe else 'false'

    def clean(self):
        cleaned_data = super().clean()
        fecha_inicio = cleaned_data.get('fecha_inicio_contrato')
        fecha_fin = cleaned_data.get('fecha_fin_contrato')
        valor_total = cleaned_data.get('valor_total_contrato')
        valor_pagado = cleaned_data.get('valor_pagado_contrato')
        valor_pendiente = cleaned_data.get('valor_pendiente_cobrar')
        archivos = {
            'documento_identidad_frontal': cleaned_data.get('documento_identidad_frontal'),
            'documento_identidad_reverso': cleaned_data.get('documento_identidad_reverso'),
            'certificado_bancario': cleaned_data.get('certificado_bancario'),
            'contrato_actual': cleaned_data.get('contrato_actual'),
        }

        if fecha_inicio and fecha_fin and fecha_fin < fecha_inicio:
            self.add_error('fecha_fin_contrato', 'La fecha fin no puede ser menor a la fecha inicio.')

        if valor_total is not None and valor_pendiente is not None and valor_pendiente > valor_total:
            self.add_error('valor_pendiente_cobrar', 'El valor pendiente no puede superar el valor total del contrato.')

        if (
            valor_total is not None
            and valor_pagado is not None
            and valor_pendiente is not None
            and valor_pagado + valor_pendiente > valor_total
        ):
            self.add_error(
                'valor_pendiente_cobrar',
                'La suma del valor pagado y pendiente no puede superar el valor total del contrato.',
            )

        for campo in ('certificado_bancario', 'contrato_actual'):
            archivo = archivos.get(campo)
            if archivo and not archivo.name.lower().endswith('.pdf'):
                self.add_error(campo, 'Este documento debe cargarse en PDF.')

        for campo in ('documento_identidad_frontal', 'documento_identidad_reverso'):
            archivo = archivos.get(campo)
            if archivo and not archivo.name.lower().endswith(('.jpg', '.jpeg', '.png', '.pdf')):
                self.add_error(campo, 'Carga una imagen o PDF válido.')

        for campo, tipo_documento in self.MAPA_DOCUMENTOS.items():
            archivo = archivos.get(campo)
            existe = bool(
                self.instance
                and self.instance.pk
                and self.instance.documentos.filter(tipo_documento=tipo_documento).exists()
            )
            if not archivo and not existe:
                self.add_error(campo, self.fields[campo].error_messages['required'])
            if archivo and archivo.size > 8 * 1024 * 1024:
                self.add_error(campo, 'El documento no debe superar 8MB.')

        return cleaned_data


class DocumentoPrestadorForm(forms.ModelForm):
    class Meta:
        model = ContractorApplicationDocument
        fields = ['tipo_documento', 'archivo']
        widgets = {
            'tipo_documento': forms.Select(attrs={'class': 'campo'}),
            'archivo': forms.ClearableFileInput(attrs={'class': 'campo'}),
        }
        labels = {
            'tipo_documento': 'Documento',
            'archivo': 'Archivo',
        }

    def clean(self):
        cleaned_data = super().clean()
        documento = ContractorApplicationDocument(
            tipo_documento=cleaned_data.get('tipo_documento'),
            archivo=cleaned_data.get('archivo'),
            solicitud=ContractorApplication(),
        )
        if cleaned_data.get('tipo_documento') and cleaned_data.get('archivo'):
            documento.clean()
        return cleaned_data


class CambiarEstadoPrestadorForm(forms.Form):
    estado = forms.ChoiceField(
        label='Estado',
        choices=(
            (
                ContractorApplication.Estado.DOCUMENTOS_PENDIENTES,
                ContractorApplication.Estado.DOCUMENTOS_PENDIENTES.label,
            ),
            (
                ContractorApplication.Estado.DOCUMENTOS_CARGADOS,
                ContractorApplication.Estado.DOCUMENTOS_CARGADOS.label,
            ),
            (
                ContractorApplication.Estado.EN_REVISION,
                ContractorApplication.Estado.EN_REVISION.label,
            ),
        ),
        widget=forms.Select(attrs={'class': 'campo'}),
    )
