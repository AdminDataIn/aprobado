from django import forms
from .models import CreditoLibranza, Empresa, CreditoEmprendimiento, MovimientoAhorro, MarketplaceItem
from decimal import Decimal
import hashlib
# Agregar al archivo forms.py existente
from django.core.validators import FileExtensionValidator
from django.conf import settings

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
        if cedula and CreditoLibranza.objects.filter(cedula=cedula).exists():
            raise forms.ValidationError(
                'Ya existe una solicitud registrada con esta cédula. '
                'Si necesitas ayuda, contáctanos por WhatsApp.'
            )
        return cedula
    
    def clean_telefono(self):
        telefono = self.cleaned_data.get('telefono', '').strip()
        telefono_limpio = ''.join(filter(str.isdigit, telefono))
        if telefono_limpio and len(telefono_limpio) < 7:
            raise forms.ValidationError('Ingrese un número de teléfono válido.')
        return telefono


    def clean_cedula_frontal(self):
        archivo = self.cleaned_data.get('cedula_frontal')
        return self._validar_documento_imagen(
            archivo,
            'La cedula frontal debe cargarse unicamente como imagen valida (JPG, PNG o WEBP).'
        )

    def clean_cedula_trasera(self):
        archivo = self.cleaned_data.get('cedula_trasera')
        return self._validar_documento_imagen(
            archivo,
            'La cedula trasera debe cargarse unicamente como imagen valida (JPG, PNG o WEBP).'
        )

    def clean_certificado_bancario(self):
        archivo = self.cleaned_data.get('certificado_bancario')
        if not archivo:
            return archivo

        extension = archivo.name.split('.')[-1].lower() if '.' in archivo.name else ''
        content_type = (getattr(archivo, 'content_type', '') or '').lower()

        if extension != 'pdf':
            raise forms.ValidationError('El certificado bancario debe cargarse unicamente en formato PDF.')

        if content_type and content_type not in {'application/pdf', 'application/x-pdf'}:
            raise forms.ValidationError('El certificado bancario debe ser un archivo PDF valido.')

        return archivo

    def clean(self):
        cleaned_data = super().clean()
        campos_archivo = [
            'cedula_frontal',
            'cedula_trasera',
            'certificado_bancario',
            'certificado_laboral',
            'desprendible_nomina',
        ]

        hashes_vistos = {}
        errores = {}

        for campo in campos_archivo:
            archivo = cleaned_data.get(campo)
            if not archivo:
                continue

            archivo_hash = self._calcular_hash_archivo(archivo)
            if archivo_hash in hashes_vistos:
                campo_original = hashes_vistos[archivo_hash]
                errores[campo] = (
                    f'Este archivo es identico al cargado en "{self.fields[campo_original].label}". '
                    'Sube documentos diferentes en cada campo.'
                )
                errores.setdefault(
                    campo_original,
                    f'Este archivo esta duplicado con "{self.fields[campo].label}".'
                )
            else:
                hashes_vistos[archivo_hash] = campo

        if errores:
            raise forms.ValidationError(errores)

        return cleaned_data

    def _calcular_hash_archivo(self, archivo):
        hasher = hashlib.sha256()
        for chunk in archivo.chunks():
            hasher.update(chunk)
        if hasattr(archivo, 'seek'):
            archivo.seek(0)
        return hasher.hexdigest()


    def _validar_documento_imagen(self, archivo, mensaje_error):
        if not archivo:
            return archivo

        extension = archivo.name.split('.')[-1].lower() if '.' in archivo.name else ''
        content_type = (getattr(archivo, 'content_type', '') or '').lower()
        extensiones_validas = {'jpg', 'jpeg', 'png', 'webp'}
        mime_validos = {'image/jpeg', 'image/png', 'image/webp'}

        if extension not in extensiones_validas:
            raise forms.ValidationError(mensaje_error)

        if content_type and content_type not in mime_validos:
            raise forms.ValidationError(mensaje_error)

        return archivo


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


class MarketplaceItemForm(forms.ModelForm):
    class Meta:
        model = MarketplaceItem
        fields = ['titulo', 'descripcion', 'beneficio', 'tipo', 'precio', 'imagen', 'video', 'whatsapp_contacto']
        widgets = {
            'titulo': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Titulo del producto/servicio'}),
            'descripcion': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'beneficio': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Beneficio principal'}),
            'tipo': forms.Select(attrs={'class': 'form-select'}),
            'precio': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: $120.000 o Consultivo'}),
            'imagen': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'video': forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.mp4,.webm,video/mp4,video/webm'}),
            'whatsapp_contacto': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej: 573001112233'}),
        }

    def clean_imagen(self):
        imagen = self.cleaned_data.get('imagen')
        if not imagen:
            return imagen

        max_size_bytes = int(getattr(settings, 'MARKETPLACE_MAX_IMAGE_BYTES', 5 * 1024 * 1024))
        if imagen.size > max_size_bytes:
            raise forms.ValidationError('La imagen no debe superar 5MB.')

        allowed_types = {'image/jpeg', 'image/png', 'image/webp'}
        content_type = getattr(imagen, 'content_type', None)
        if content_type and content_type.lower() not in allowed_types:
            raise forms.ValidationError('Formato de imagen no permitido. Usa JPG, PNG o WEBP.')

        # Validacion real del archivo para evitar uploads con extension falsa.
        try:
            from PIL import Image
            img = Image.open(imagen)
            img.verify()
            imagen.seek(0)
        except Exception:
            raise forms.ValidationError('El archivo de imagen esta corrupto o no es valido.')

        # Limite razonable para proteger render y almacenamiento.
        try:
            from PIL import Image
            img_check = Image.open(imagen)
            width, height = img_check.size
            imagen.seek(0)
            if width > 5000 or height > 5000:
                raise forms.ValidationError('La resolucion maxima permitida es 5000x5000 px.')
        except forms.ValidationError:
            raise
        except Exception:
            raise forms.ValidationError('No se pudo validar la resolucion de la imagen.')

        return imagen

    def clean_video(self):
        video = self.cleaned_data.get('video')
        if not video:
            return video

        max_size_bytes = int(getattr(settings, 'MARKETPLACE_MAX_VIDEO_BYTES', 20 * 1024 * 1024))
        if video.size > max_size_bytes:
            raise forms.ValidationError('El video no debe superar 20MB.')

        allowed_types = {'video/mp4', 'video/webm'}
        content_type = getattr(video, 'content_type', None)
        if content_type and content_type.lower() not in allowed_types:
            raise forms.ValidationError('Formato de video no permitido. Usa MP4 o WEBM.')

        ext = video.name.split('.')[-1].lower() if '.' in video.name else ''
        if ext not in {'mp4', 'webm'}:
            raise forms.ValidationError('Extension de video no valida. Usa .mp4 o .webm.')

        return video


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
