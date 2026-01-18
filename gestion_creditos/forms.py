from django import forms
from .models import CreditoLibranza, Empresa, CreditoEmprendimiento, MovimientoAhorro
from decimal import Decimal
# Agregar al archivo forms.py existente
from django.core.validators import FileExtensionValidator

#? --------- FORMULARIO DE CREDITO DE LIBRANZA ------------
class CreditoLibranzaForm(forms.ModelForm):
    valor_credito = forms.CharField(label='Valor crédito solicitado', required=True)
    ingresos_mensuales = forms.CharField(label='Ingresos mensuales', required=True)
    plazo = forms.ChoiceField(
        choices=[
            ('', 'Seleccione una opción'),
            (1, '1 mes'),
            (2, '2 meses'),
            (3, '3 meses'),
            (4, '4 meses'),
            (5, '5 meses'),
            (6, '6 meses'),
        ],
        required=True
    )

    class Meta:
        model = CreditoLibranza
        fields = [
            'nombres',
            'apellidos',
            'cedula',
            'direccion',
            'telefono',
            'correo_electronico',
            'empresa',
            'ingresos_mensuales',
            'cedula_frontal',
            'cedula_trasera',
            'certificado_laboral',
            'desprendible_nomina',
            'certificado_bancario'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['empresa'].queryset = Empresa.objects.all()
        self.fields['empresa'].empty_label = "Seleccione una empresa"

        self.fields['valor_credito'].error_messages = {
            'required': 'El valor del crédito es requerido.',
            'invalid': 'Ingrese un valor numérico válido.',
        }
        
        self.fields['nombres'].error_messages = {
            'required': 'Los nombres son requeridos.',
        }
        
        self.fields['apellidos'].error_messages = {
            'required': 'Los apellidos son requeridos.',
        }
        
        self.fields['cedula'].error_messages = {
            'required': 'El número de cédula es requerido.',
        }
        
        self.fields['correo_electronico'].error_messages = {
            'required': 'El correo electrónico es requerido.',
            'invalid': 'Ingrese un correo electrónico válido.',
        }

        self.fields['ingresos_mensuales'].error_messages = {
            'required': 'Los ingresos mensuales son requeridos.',
            'invalid': 'Ingrese un valor numérico válido.',
        }

        archivos = ['cedula_frontal', 'cedula_trasera', 'certificado_bancario']
        
        for archivo in archivos:
            if archivo in self.fields:
                self.fields[archivo].error_messages = {
                    'required': f'El archivo {archivo.replace("_", " ")} es requerido.',
                }

        for optional_field in ['certificado_laboral', 'desprendible_nomina']:
            if optional_field in self.fields:
                self.fields[optional_field].required = False
    
    def clean_valor_credito(self):
        valor_str = self.cleaned_data.get('valor_credito')
        if not valor_str:
            raise forms.ValidationError(self.fields['valor_credito'].error_messages['required'])
        
        valor_str_cleaned = ''.join(filter(str.isdigit, valor_str))
        
        try:
            valor = Decimal(valor_str_cleaned)
        except (ValueError, TypeError):
            raise forms.ValidationError(self.fields['valor_credito'].error_messages['invalid'])

        if valor <= 0:
            raise forms.ValidationError('El valor del crédito debe ser mayor a 0.')
        
        if valor < 100000:
            raise forms.ValidationError('El valor del crédito debe ser de al menos $100.000.')

        if valor > 2000000:
            raise forms.ValidationError('El valor del crédito no puede ser mayor a $2.000.000.')

        return valor

    def clean_ingresos_mensuales(self):
        valor_str = self.cleaned_data.get('ingresos_mensuales')
        if not valor_str:
            raise forms.ValidationError(self.fields['ingresos_mensuales'].error_messages['required'])

        valor_str_cleaned = ''.join(filter(str.isdigit, valor_str))

        try:
            valor = Decimal(valor_str_cleaned)
        except (ValueError, TypeError):
            raise forms.ValidationError(self.fields['ingresos_mensuales'].error_messages['invalid'])

        if valor <= 0:
            raise forms.ValidationError('Los ingresos mensuales deben ser mayores a 0.')

        return valor
    
    def clean_cedula(self):
        cedula = self.cleaned_data.get('cedula', '').strip()
        if cedula and not cedula.isdigit():
            raise forms.ValidationError('La cédula debe contener solo números.')
        if cedula and len(cedula) < 7:
            raise forms.ValidationError('La cédula debe tener al menos 7 dígitos.')
        return cedula
    
    def clean_telefono(self):
        telefono = self.cleaned_data.get('telefono', '').strip()
        telefono_limpio = ''.join(filter(str.isdigit, telefono))
        if telefono_limpio and len(telefono_limpio) < 7:
            raise forms.ValidationError('Ingrese un número de teléfono válido.')
        return telefono


#? --------- FORMULARIO DE CREDITO DE EMPRENDIMIENTO ------------
class CreditoEmprendimientoForm(forms.ModelForm):
    valor_credito = forms.CharField(label='Valor crédito solicitado', required=True)
    plazo = forms.ChoiceField(
        choices=[
            ('', 'Seleccione una opción'),
            (1, '1 mes'),
            (2, '2 meses'),
            (3, '3 meses'),
        ],
        required=True
    )

    class Meta:
        model = CreditoEmprendimiento
        exclude = [
            'credito', 
            'puntaje'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fecha_nac'].widget = forms.DateInput(attrs={'type': 'date'})

    def clean_valor_credito(self):
        valor_str = self.cleaned_data.get('valor_credito')
        if not valor_str:
            raise forms.ValidationError('El valor del crédito es requerido.')
        
        valor_str_cleaned = ''.join(filter(str.isdigit, valor_str))
        
        try:
            valor = Decimal(valor_str_cleaned)
        except (ValueError, TypeError):
            raise forms.ValidationError('Ingrese un valor numérico válido.')

        if valor <= 0:
            raise forms.ValidationError('El valor del crédito debe ser mayor a 0.')
        
        if valor > 800000:
            raise forms.ValidationError('El valor del crédito no puede ser mayor a $800.000.')

        return valor
    
    def clean_plazo(self):
        plazo = self.cleaned_data.get('plazo')
        if not plazo:
            raise forms.ValidationError('El plazo es requerido.')
        
        if int(plazo) > 3:
            raise forms.ValidationError('El plazo no puede ser mayor a 3 meses.')

        return plazo

class ConsignacionOfflineForm(forms.ModelForm):
    """Formulario para consignaciones offline con comprobante"""
    
    class Meta:
        model = MovimientoAhorro
        fields = ['monto', 'comprobante', 'descripcion']
        widgets = {
            'monto': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ingrese el monto',
                'min': '1000',
                'step': '1000'
            }),
            'comprobante': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.pdf,.jpg,.jpeg,.png'
            }),
            'descripcion': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Descripción opcional del depósito'
            })
        }
        labels = {
            'monto': 'Monto a Consignar',
            'comprobante': 'Comprobante de Pago',
            'descripcion': 'Descripción (Opcional)'
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['descripcion'].required = False

    def clean_comprobante(self):
        comprobante = self.cleaned_data.get('comprobante')
        if comprobante:
            # Validar tamaño (5MB máximo)
            if comprobante.size > 5 * 1024 * 1024:
                raise forms.ValidationError('El archivo no debe superar los 5MB.')
            
            # Validar extensión
            ext = comprobante.name.split('.')[-1].lower()
            if ext not in ['pdf', 'jpg', 'jpeg', 'png']:
                raise forms.ValidationError('Solo se permiten archivos PDF, JPG o PNG.')
        
        return comprobante


class AbonoManualAdminForm(forms.Form):
    """Formulario para que el admin cargue abonos manualmente"""
    
    usuario_email = forms.EmailField(
        label='Correo del Usuario',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'usuario@ejemplo.com'
        })
    )
    
    monto = forms.DecimalField(
        label='Monto a Abonar',
        min_value=1000,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '50000',
            'step': '1000'
        })
    )
    
    comprobante = forms.FileField(
        label='Comprobante de Transacción (Opcional)',
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png'])],
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.pdf,.jpg,.jpeg,.png'
        })
    )
    
    nota = forms.CharField(
        label='Nota Administrativa',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Nota interna sobre este abono...'
        })
    )
