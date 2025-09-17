from django import forms
from .models import CreditoLibranza, Empresa, CreditoEmprendimiento

#? --------- FORMULARIO DE CREDITO DE LIBRANZA ------------
class CreditoLibranzaForm(forms.ModelForm):
    class Meta:
        model = CreditoLibranza
        fields = [
            'valor_credito',
            'plazo', 
            'nombres',
            'apellidos',
            'cedula',
            'direccion',
            'telefono',
            'correo_electronico',
            'empresa',
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
        
        self.fields['plazo'] = forms.ChoiceField(
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
        
        archivos = ['cedula_frontal', 'cedula_trasera', 'certificado_laboral', 
                   'desprendible_nomina', 'certificado_bancario']
        
        for archivo in archivos:
            if archivo in self.fields:
                self.fields[archivo].error_messages = {
                    'required': f'El archivo {archivo.replace("_", " ")} es requerido.',
                }
    
    def clean_valor_credito(self):
        valor = self.cleaned_data.get('valor_credito')
        if valor is not None and valor <= 0:
            raise forms.ValidationError('El valor del crédito debe ser mayor a 0.')
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
    class Meta:
        model = CreditoEmprendimiento
        exclude = [
            'credito', 
            'monto_aprobado', 
            'saldo_pendiente', 
            'valor_cuota', 
            'fecha_proximo_pago', 
            'puntaje'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fecha_nac'].widget = forms.DateInput(attrs={'type': 'date'})
