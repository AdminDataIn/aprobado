from django import forms

from contractors.models import ContractorApplication, ContractorApplicationDocument
from gestion_creditos.models import Empresa


class SolicitudPrestadorForm(forms.ModelForm):
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
            'empresa',
            'fecha_inicio_contrato',
            'fecha_fin_contrato',
            'valor_total_contrato',
            'valor_pendiente_cobrar',
            'monto_solicitado',
            'plazo_meses',
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
            'fecha_inicio_contrato': forms.DateInput(attrs={'class': 'campo', 'type': 'date'}),
            'fecha_fin_contrato': forms.DateInput(attrs={'class': 'campo', 'type': 'date'}),
            'valor_total_contrato': forms.NumberInput(attrs={'class': 'campo', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 12000000'}),
            'valor_pendiente_cobrar': forms.NumberInput(attrs={'class': 'campo', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 8000000'}),
            'monto_solicitado': forms.NumberInput(attrs={'class': 'campo', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 3000000'}),
            'plazo_meses': forms.NumberInput(attrs={'class': 'campo', 'min': '1', 'placeholder': 'Ej. 12'}),
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
            'fecha_inicio_contrato': 'Fecha inicio contrato',
            'fecha_fin_contrato': 'Fecha fin contrato',
            'valor_total_contrato': 'Valor total contrato',
            'valor_pendiente_cobrar': 'Valor pendiente por cobrar',
            'monto_solicitado': 'Monto solicitado',
            'plazo_meses': 'Plazo solicitado en meses',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['empresa'].queryset = Empresa.objects.filter(
            convenio_activo=True,
        ).order_by('nombre')

    def clean(self):
        cleaned_data = super().clean()
        fecha_inicio = cleaned_data.get('fecha_inicio_contrato')
        fecha_fin = cleaned_data.get('fecha_fin_contrato')
        valor_total = cleaned_data.get('valor_total_contrato')
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

        for campo in ('certificado_bancario', 'contrato_actual'):
            archivo = archivos.get(campo)
            if archivo and not archivo.name.lower().endswith('.pdf'):
                self.add_error(campo, 'Este documento debe cargarse en PDF.')

        for campo in ('documento_identidad_frontal', 'documento_identidad_reverso'):
            archivo = archivos.get(campo)
            if archivo and not archivo.name.lower().endswith(('.jpg', '.jpeg', '.png', '.pdf')):
                self.add_error(campo, 'Carga una imagen o PDF válido.')

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
