from django import forms

from contractors.models import ContractorApplication, ContractorApplicationDocument
from gestion_creditos.models import Empresa


DOCUMENTO_INICIAL_CAMPOS = {
    'cedula_frontal': ContractorApplicationDocument.TipoDocumento.CEDULA_FRONTAL,
    'cedula_trasera': ContractorApplicationDocument.TipoDocumento.CEDULA_TRASERA,
    'certificado_bancario': ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO,
    'contrato_vigente': ContractorApplicationDocument.TipoDocumento.CONTRATO,
}


class SolicitudPrestadorForm(forms.ModelForm):
    empresa = forms.ModelChoiceField(
        label='Empresa contratante',
        queryset=Empresa.objects.none(),
        empty_label='Selecciona una empresa registrada',
        widget=forms.Select(attrs={'class': 'campo'}),
        error_messages={
            'required': 'Debes elegir una empresa válida de la lista.',
            'invalid_choice': 'Debes elegir una empresa válida de la lista.',
        },
    )
    cedula_frontal = forms.FileField(
        label='Cédula frontal',
        required=False,
        widget=forms.ClearableFileInput(
            attrs={'class': 'campo document-input', 'accept': 'image/jpeg,image/png,application/pdf'},
        ),
    )
    cedula_trasera = forms.FileField(
        label='Cédula trasera',
        required=False,
        widget=forms.ClearableFileInput(
            attrs={'class': 'campo document-input', 'accept': 'image/jpeg,image/png,application/pdf'},
        ),
    )
    certificado_bancario = forms.FileField(
        label='Certificado bancario PDF',
        required=False,
        widget=forms.ClearableFileInput(attrs={'class': 'campo document-input', 'accept': 'application/pdf'}),
    )
    contrato_vigente = forms.FileField(
        label='Contrato vigente PDF',
        required=False,
        widget=forms.ClearableFileInput(attrs={'class': 'campo document-input', 'accept': 'application/pdf'}),
    )
    autorizacion_analisis_asistido = forms.BooleanField(
        label=(
            'Autorizo el análisis asistido del contrato para extraer datos y sugerencias editables. '
            'Este análisis no reemplaza mi confirmación.'
        ),
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'check-input'}),
    )
    terminos_privacidad_aceptados = forms.BooleanField(
        label='Acepto términos, condiciones y política de privacidad.',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'check-input'}),
    )
    autorizacion_datacredito_visual = forms.BooleanField(
        label='Autorizo la consulta ante centrales de riesgo cuando el flujo sea habilitado.',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'check-input'}),
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
            'nombres': forms.TextInput(attrs={'class': 'campo', 'placeholder': 'Tus nombres'}),
            'apellidos': forms.TextInput(attrs={'class': 'campo', 'placeholder': 'Tus apellidos'}),
            'celular': forms.TextInput(attrs={'class': 'campo', 'placeholder': '3001234567'}),
            'correo': forms.EmailInput(attrs={'class': 'campo', 'placeholder': 'correo@dominio.com'}),
            'direccion': forms.TextInput(attrs={'class': 'campo', 'placeholder': 'Dirección de residencia'}),
            'cargo': forms.TextInput(attrs={'class': 'campo', 'placeholder': 'Cargo o servicio prestado'}),
            'fecha_inicio_contrato': forms.DateInput(attrs={'class': 'campo', 'type': 'date'}),
            'fecha_fin_contrato': forms.DateInput(attrs={'class': 'campo', 'type': 'date'}),
            'valor_total_contrato': forms.NumberInput(
                attrs={'class': 'campo', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 12000000'},
            ),
            'valor_pendiente_cobrar': forms.NumberInput(
                attrs={'class': 'campo', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 8000000'},
            ),
            'monto_solicitado': forms.NumberInput(
                attrs={'class': 'campo', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 3000000'},
            ),
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
        self.fields['tipo_documento'].choices = (
            ('', '---------'),
            (ContractorApplication.TipoDocumento.CEDULA_CIUDADANIA, 'Cédula de ciudadanía'),
            (ContractorApplication.TipoDocumento.CEDULA_EXTRANJERIA, 'Cédula de extranjería'),
        )
        self.fields['escenario_credito'].choices = (
            (ContractorApplication.EscenarioCredito.NUEVO_CREDITO, 'Nuevo crédito'),
            (ContractorApplication.EscenarioCredito.SEGUNDO_CREDITO, 'Segundo crédito'),
            (ContractorApplication.EscenarioCredito.RECOGIDA_CARTERA, 'Recogida de cartera'),
        )

    def clean(self):
        cleaned_data = super().clean()
        fecha_inicio = cleaned_data.get('fecha_inicio_contrato')
        fecha_fin = cleaned_data.get('fecha_fin_contrato')
        valor_total = cleaned_data.get('valor_total_contrato')
        valor_pendiente = cleaned_data.get('valor_pendiente_cobrar')

        if fecha_inicio and fecha_fin and fecha_fin < fecha_inicio:
            self.add_error('fecha_fin_contrato', 'La fecha fin no puede ser menor a la fecha inicio.')

        if valor_total is not None and valor_pendiente is not None and valor_pendiente > valor_total:
            self.add_error('valor_pendiente_cobrar', 'El valor pendiente no puede superar el valor total del contrato.')

        for nombre_campo, tipo_documento in DOCUMENTO_INICIAL_CAMPOS.items():
            archivo = cleaned_data.get(nombre_campo)
            if not archivo:
                continue
            documento = ContractorApplicationDocument(
                tipo_documento=tipo_documento,
                archivo=archivo,
                solicitud=ContractorApplication(),
            )
            try:
                documento.clean()
            except Exception as exc:
                self.add_error(nombre_campo, exc)

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
