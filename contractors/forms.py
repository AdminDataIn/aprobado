from decimal import Decimal, InvalidOperation
import re

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    MAPA_CAMPOS_DOCUMENTOS_PRESTADOR,
)
from gestion_creditos.models import Empresa


def normalizar_monto_colombiano(valor):
    if valor in (None, ''):
        return valor
    if isinstance(valor, (Decimal, int, float)):
        return str(valor)

    texto = re.sub(r'[\s\u00a0$]', '', str(valor).strip())
    if not texto or not re.fullmatch(r'\d[\d.,]*', texto):
        raise ValidationError('Ingresa un valor monetario válido.')

    if '.' in texto and ',' in texto:
        separador_decimal = '.' if texto.rfind('.') > texto.rfind(',') else ','
        separador_miles = ',' if separador_decimal == '.' else '.'
        entero, decimales = texto.rsplit(separador_decimal, 1)
        if len(decimales) not in (1, 2) or not decimales.isdigit():
            raise ValidationError('Ingresa un valor monetario válido.')
        entero = entero.replace(separador_miles, '')
        if not entero.isdigit():
            raise ValidationError('Ingresa un valor monetario válido.')
        return f'{entero}.{decimales}'

    separador = '.' if '.' in texto else ',' if ',' in texto else None
    if not separador:
        return texto

    partes = texto.split(separador)
    if any(not parte.isdigit() for parte in partes):
        raise ValidationError('Ingresa un valor monetario válido.')
    if len(partes) > 2 or len(partes[-1]) == 3:
        if any(len(parte) != 3 for parte in partes[1:]):
            raise ValidationError('Ingresa un valor monetario válido.')
        return ''.join(partes)
    if len(partes) == 2 and len(partes[-1]) in (1, 2):
        return f'{partes[0]}.{partes[1]}'
    raise ValidationError('Ingresa un valor monetario válido.')


class MontoContratoField(forms.DecimalField):
    def to_python(self, value):
        return super().to_python(normalizar_monto_colombiano(value))

    def prepare_value(self, value):
        if value in (None, ''):
            return value
        if isinstance(value, str) and not re.fullmatch(r'\d+(\.\d+)?', value.strip()):
            return value
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return value
        entero, _, decimales = format(decimal, 'f').partition('.')
        entero_formateado = f'{int(entero):,}'.replace(',', '.')
        decimales = decimales.rstrip('0')
        return f'{entero_formateado},{decimales}' if decimales else entero_formateado


class SimulacionPrestadorForm(forms.Form):
    monto = forms.DecimalField(
        label='Monto solicitado',
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'step': '50000'}),
    )
    plazo_meses = forms.IntegerField(
        label='Plazo en meses',
        widget=forms.NumberInput(attrs={'step': '1'}),
    )

    def __init__(self, *args, **kwargs):
        configuracion = kwargs.pop('configuracion', None)
        super().__init__(*args, **kwargs)
        monto_minimo = Decimal(str(
            configuracion.monto_minimo
            if configuracion else getattr(settings, 'CONTRACTORS_MIN_AMOUNT', '1000000')
        ))
        monto_maximo = Decimal(str(
            configuracion.monto_maximo
            if configuracion else getattr(settings, 'CONTRACTORS_MAX_AMOUNT', '10000000')
        ))
        plazo_minimo = int(
            configuracion.plazo_minimo_meses
            if configuracion else getattr(settings, 'CONTRACTORS_MIN_TERM_MONTHS', 3)
        )
        plazo_maximo = int(
            configuracion.plazo_maximo_meses
            if configuracion else getattr(settings, 'CONTRACTORS_MAX_TERM_MONTHS', 24)
        )

        self.fields['monto'].min_value = monto_minimo
        self.fields['monto'].max_value = monto_maximo
        self.fields['monto'].widget.attrs.update({
            'min': str(monto_minimo),
            'max': str(monto_maximo),
        })
        self.fields['plazo_meses'].min_value = plazo_minimo
        self.fields['plazo_meses'].max_value = plazo_maximo
        self.fields['plazo_meses'].widget.attrs.update({
            'min': str(plazo_minimo),
            'max': str(plazo_maximo),
        })
        self.monto_minimo = monto_minimo
        self.monto_maximo = monto_maximo
        self.plazo_minimo = plazo_minimo
        self.plazo_maximo = plazo_maximo

    def clean_monto(self):
        monto = self.cleaned_data['monto']
        if monto < self.monto_minimo or monto > self.monto_maximo:
            raise forms.ValidationError(
                f'El monto debe estar entre ${self.monto_minimo:,.0f} y ${self.monto_maximo:,.0f}.'
            )
        return monto

    def clean_plazo_meses(self):
        plazo = self.cleaned_data['plazo_meses']
        if plazo < self.plazo_minimo or plazo > self.plazo_maximo:
            raise forms.ValidationError(
                f'El plazo debe estar entre {self.plazo_minimo} y {self.plazo_maximo} meses.'
            )
        return plazo


class SolicitudPrestadorForm(forms.ModelForm):
    MAPA_DOCUMENTOS = MAPA_CAMPOS_DOCUMENTOS_PRESTADOR

    valor_total_contrato = MontoContratoField(
        label='Valor total contrato',
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0'),
        widget=forms.TextInput(attrs={
            'class': 'campo money-contract-input',
            'inputmode': 'numeric',
            'autocomplete': 'off',
            'placeholder': 'Ej. 80.000.000',
            'data-money-contract': 'true',
        }),
    )
    valor_pagado_contrato = MontoContratoField(
        label='Valor pagado del contrato',
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0'),
        widget=forms.TextInput(attrs={
            'class': 'campo money-contract-input',
            'inputmode': 'numeric',
            'autocomplete': 'off',
            'placeholder': 'Ej. 5.000.000',
            'data-money-contract': 'true',
        }),
    )
    valor_pendiente_cobrar = MontoContratoField(
        label='Valor pendiente por cobrar',
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0'),
        widget=forms.TextInput(attrs={
            'class': 'campo money-contract-input',
            'inputmode': 'numeric',
            'autocomplete': 'off',
            'placeholder': 'Ej. 75.000.000',
            'data-money-contract': 'true',
        }),
    )

    documento_identidad_frontal = forms.FileField(
        label='Cédula frontal',
        required=True,
        widget=forms.ClearableFileInput(attrs={
            'class': 'campo documento-input',
            'accept': 'image/*',
            'capture': 'environment',
        }),
        error_messages={'required': 'Carga la cédula frontal.'},
    )
    documento_identidad_reverso = forms.FileField(
        label='Cédula trasera',
        required=True,
        widget=forms.ClearableFileInput(attrs={
            'class': 'campo documento-input',
            'accept': 'image/*',
            'capture': 'environment',
        }),
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
            'observaciones_contrato': forms.Textarea(attrs={'class': 'campo', 'rows': 3, 'placeholder': 'Observaciones contractuales opcionales'}),
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
            if archivo and not archivo.name.lower().endswith(('.jpg', '.jpeg', '.png')):
                self.add_error(campo, 'Captura una imagen válida de la cédula.')

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
