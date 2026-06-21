import re

from django import forms
from django.contrib.auth import get_user_model
from django.forms import HiddenInput
from .models import (
    AsesorComercial,
    CreditoLibranza,
    HistorialPago,
    Empresa,
    CreditoEmprendimiento,
    LotePagoEmpresa,
    MovimientoAhorro,
    MarketplaceItem,
    PagoComisionEjecutivo,
    VinculoLaboralEmpresa,
)
from decimal import Decimal
import hashlib
import os
# Agregar al archivo forms.py existente
from django.core.validators import FileExtensionValidator
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from gestion_creditos.services.libranza_rules import (
    LIBRANZA_MONTO_MAXIMO,
    LIBRANZA_MONTO_MINIMO_SOLICITUD,
    obtener_creditos_libranza_bloqueantes,
    permitir_multiples_creditos_libranza_en_pruebas,
)
from gestion_creditos.services.name_normalization import normalize_name_upper
from libranza.services.special_cases import (
    MAX_REASONABLE_COMMISSION_RATE,
    MAX_SPECIAL_CASE_AMOUNT,
    MAX_SPECIAL_CASE_MONTHLY_RATE,
    MAX_SPECIAL_CASE_TERM_MONTHS,
)


NAME_ALLOWED_RE = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' -]+$")
OBVIOUS_GARBAGE_PARTS = (
    'asdf', 'asd', 'qwe', 'zxc', 'test', 'prueba', 'nombre',
    'apellido', 'xxxxx', 'xxxx', 'abc', 'demo',
)


def _contains_obvious_garbage(value):
    lowered = (value or '').lower().replace(' ', '')
    if any(part in lowered for part in OBVIOUS_GARBAGE_PARTS):
        return True
    if re.search(r'(.)\1{2,}', lowered):
        return True
    return False


def _clean_person_name(value, field_label):
    normalized = re.sub(r'\s+', ' ', (value or '').strip())
    if not normalized:
        raise forms.ValidationError(f'{field_label} son requeridos.')
    if not NAME_ALLOWED_RE.match(normalized):
        raise forms.ValidationError(f'{field_label} solo pueden contener letras, espacios, apostrofes o guiones.')
    letters = re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', normalized)
    if len(letters) < 2:
        raise forms.ValidationError(f'{field_label} deben tener al menos 2 letras.')
    if _contains_obvious_garbage(normalized):
        raise forms.ValidationError(f'{field_label} no parecen validos. Ingresa tu nombre real.')
    return normalize_name_upper(normalized)


def _clean_address(value):
    normalized = re.sub(r'\s+', ' ', (value or '').strip())
    if len(normalized) < 8:
        raise forms.ValidationError('La direccion debe ser mas completa.')
    if _contains_obvious_garbage(normalized):
        raise forms.ValidationError('Ingresa una direccion valida.')
    if len(re.findall(r'[A-Za-z0-9]', normalized)) < 6:
        raise forms.ValidationError('Ingresa una direccion valida.')
    return normalized


class MoneyTextDecimalField(forms.DecimalField):
    """
    Acepta montos digitados como texto con separadores visuales de miles.
    Ejemplo: 2.400.000 -> 2400000
    """

    def to_python(self, value):
        if isinstance(value, str):
            normalized = value.strip().replace('.', '').replace(' ', '')
            if ',' in normalized and normalized.count(',') == 1:
                normalized = normalized.replace(',', '.')
            value = normalized
        return super().to_python(value)

# --------- FORMULARIO DE CREDITO DE LIBRANZA ------------
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
        self.vinculo_laboral = kwargs.pop('vinculo_laboral', None)
        super().__init__(*args, **kwargs)

        self.fields['empresa'].queryset = Empresa.objects.filter(convenio_activo=True).exclude(
            tipo_empresa=Empresa.TipoEmpresa.MARKETPLACE_EXTERNA
        )
        self.fields['empresa'].empty_label = "Seleccione una empresa"
        self.fields['empresa'].widget = HiddenInput(attrs={
            'data-company-select': 'true',
        })
        self.fields['nombres'].widget.attrs.update({
            'autocomplete': 'given-name',
            'maxlength': '80',
            'placeholder': 'Tus nombres',
        })
        self.fields['apellidos'].widget.attrs.update({
            'autocomplete': 'family-name',
            'maxlength': '80',
            'placeholder': 'Tus apellidos',
        })
        self.fields['cedula'].widget.attrs.update({
            'inputmode': 'numeric',
            'maxlength': '10',
            'autocomplete': 'off',
            'placeholder': 'Solo numeros',
        })
        self.fields['telefono'].widget.attrs.update({
            'inputmode': 'numeric',
            'maxlength': '10',
            'autocomplete': 'tel',
            'placeholder': '3001234567',
        })
        self.fields['correo_electronico'].widget.attrs.update({
            'autocomplete': 'email',
            'placeholder': 'tu@correo.com',
        })
        self.fields['direccion'].widget.attrs.update({
            'autocomplete': 'street-address',
            'maxlength': '180',
            'placeholder': 'Direccion completa',
        })

        if self.vinculo_laboral:
            self.fields['empresa'].initial = self.vinculo_laboral.empresa
            self.fields['empresa'].widget = HiddenInput()
            self.fields['nombres'].initial = self.vinculo_laboral.nombre_empleado.split(' ')[0]
            self.fields['apellidos'].initial = ' '.join(self.vinculo_laboral.nombre_empleado.split(' ')[1:]).strip()
            self.fields['cedula'].initial = self.vinculo_laboral.documento_empleado
            self.fields['telefono'].initial = self.vinculo_laboral.telefono_empleado
            self.fields['correo_electronico'].initial = self.vinculo_laboral.correo_empleado
            self.fields['ingresos_mensuales'].initial = self.vinculo_laboral.ingreso_laboral_total

        self.fields['valor_credito'].error_messages.update({
            'required': 'El valor del crédito es requerido.',
            'invalid': 'Ingrese un valor numérico válido.',
        })
        
        self.fields['nombres'].error_messages.update({
            'required': 'Los nombres son requeridos.',
        })
        
        self.fields['apellidos'].error_messages.update({
            'required': 'Los apellidos son requeridos.',
        })
        
        self.fields['cedula'].error_messages.update({
            'required': 'El número de cédula es requerido.',
        })
        
        self.fields['correo_electronico'].error_messages.update({
            'required': 'El correo electrónico es requerido.',
            'invalid': 'Ingrese un correo electrónico válido.',
        })

        self.fields['ingresos_mensuales'].error_messages.update({
            'required': 'Los ingresos mensuales son requeridos.',
            'invalid': 'Ingrese un valor numérico válido.',
        })

        archivos = ['cedula_frontal', 'cedula_trasera', 'certificado_bancario']
        
        for archivo in archivos:
            if archivo in self.fields:
                self.fields[archivo].error_messages.update({
                    'required': f'El archivo {archivo.replace("_", " ")} es requerido.',
                    'max_length': 'El nombre del archivo es demasiado largo. Se requiere un nombre mas corto.',
                })

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
        
        if valor < LIBRANZA_MONTO_MINIMO_SOLICITUD:
            raise forms.ValidationError('El valor del crédito debe ser de al menos $100.000.')

        if valor > LIBRANZA_MONTO_MAXIMO:
            raise forms.ValidationError('El valor del crédito no puede ser mayor a $3.000.000.')

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
            raise forms.ValidationError('La cedula debe contener solo numeros.')
        if cedula and len(cedula) < 6:
            raise forms.ValidationError('La cedula debe tener al menos 6 digitos.')
        if cedula and len(cedula) > 10:
            raise forms.ValidationError('La cedula no puede superar 10 digitos.')
        if cedula and not permitir_multiples_creditos_libranza_en_pruebas():
            creditos_bloqueantes = obtener_creditos_libranza_bloqueantes(cedula)
            if creditos_bloqueantes.exists():
                credito_existente = creditos_bloqueantes.first().credito
                raise forms.ValidationError(
                    'Ya existe un credito de libranza vigente para esta cedula '
                    f'({credito_existente.numero_credito} - {credito_existente.get_estado_display()}).'
                )
        return cedula

    def clean_telefono(self):
        telefono = self.cleaned_data.get('telefono', '').strip()
        telefono_limpio = ''.join(filter(str.isdigit, telefono))
        if not telefono_limpio:
            raise forms.ValidationError('El celular es requerido.')
        if len(telefono_limpio) != 10:
            raise forms.ValidationError('El celular debe contener exactamente 10 numeros.')
        return telefono_limpio

    def clean_nombres(self):
        return _clean_person_name(self.cleaned_data.get('nombres'), 'Los nombres')

    def clean_apellidos(self):
        return _clean_person_name(self.cleaned_data.get('apellidos'), 'Los apellidos')

    def clean_direccion(self):
        return _clean_address(self.cleaned_data.get('direccion'))

    def clean_correo_electronico(self):
        return (self.cleaned_data.get('correo_electronico') or '').strip().lower()

    def clean_empresa(self):
        empresa = self.cleaned_data.get('empresa') or getattr(self.vinculo_laboral, 'empresa', None)
        if not empresa:
            raise forms.ValidationError('Debes seleccionar una empresa con convenio activo.')
        if not empresa.convenio_activo:
            raise forms.ValidationError('La empresa seleccionada no tiene convenio activo.')
        if empresa.tipo_empresa == Empresa.TipoEmpresa.MARKETPLACE_EXTERNA:
            raise forms.ValidationError('La empresa seleccionada no pertenece al canal de convenio de libranza.')
        return empresa

    def clean_cedula_frontal(self):
        archivo = self.cleaned_data.get('cedula_frontal')
        archivo = self._normalizar_nombre_archivo(archivo, 'cedula-frontal')
        return self._validar_documento_imagen(
            archivo,
            'La cedula frontal debe cargarse unicamente como imagen valida (JPG, PNG o WEBP).'
        )

    def clean_cedula_trasera(self):
        archivo = self.cleaned_data.get('cedula_trasera')
        archivo = self._normalizar_nombre_archivo(archivo, 'cedula-trasera')
        return self._validar_documento_imagen(
            archivo,
            'La cedula trasera debe cargarse unicamente como imagen valida (JPG, PNG o WEBP).'
        )

    def clean_certificado_bancario(self):
        archivo = self.cleaned_data.get('certificado_bancario')
        if not archivo:
            return archivo

        archivo = self._normalizar_nombre_archivo(archivo, 'certificado-bancario')

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


    def _normalizar_nombre_archivo(self, archivo, prefijo):
        if not archivo:
            return archivo

        _, extension = os.path.splitext(archivo.name or '')
        extension = extension.lower() or ''
        timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
        base = slugify(os.path.splitext(os.path.basename(archivo.name or ''))[0])[:18]
        base = base or prefijo
        archivo.name = f'{prefijo}-{base}-{timestamp}{extension}'
        return archivo

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


# --------- FORMULARIO DE CREDITO DE EMPRENDIMIENTO ------------
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


class CreditoAdelantoNominaForm(forms.Form):
    monto_solicitado = MoneyTextDecimalField(
        label='Monto solicitado',
        min_value=Decimal('1'),
        decimal_places=2,
        max_digits=12,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej. 1.200.000',
            'inputmode': 'numeric',
            'autocomplete': 'off',
            'data-money-input': 'true',
        }),
    )
    observaciones = forms.CharField(
        label='Observaciones',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Si necesitas aclarar algo para el pagador, indicalo aqui.',
        }),
    )

    def __init__(self, *args, vinculo_laboral=None, **kwargs):
        self.vinculo_laboral = vinculo_laboral
        super().__init__(*args, **kwargs)
        monto_maximo = getattr(self.vinculo_laboral, 'adelanto_maximo', None) or self.initial.get('monto_solicitado')
        if monto_maximo:
            self.fields['monto_solicitado'].widget.attrs.update({
                'data-max-amount': f'{Decimal(monto_maximo):.0f}',
            })

    def clean_monto_solicitado(self):
        monto = self.cleaned_data['monto_solicitado']
        if not self.vinculo_laboral:
            raise forms.ValidationError('No se encontro un vinculo laboral valido para solicitar el adelanto.')
        monto_maximo = self.initial.get('monto_solicitado') or self.vinculo_laboral.adelanto_maximo
        if monto > monto_maximo:
            raise forms.ValidationError(
                f'El monto solicitado no puede superar ${monto_maximo:,.0f} para este adelanto.'.replace(',', '.')
            )
        return monto


class EmployeeBulkUploadForm(forms.Form):
    archivo = forms.FileField(
        label='Archivo de empleados',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx',
        }),
    )

    def clean_archivo(self):
        archivo = self.cleaned_data['archivo']
        extension = (archivo.name.rsplit('.', 1)[-1] if '.' in archivo.name else '').lower()
        if extension != 'xlsx':
            raise forms.ValidationError('Usa la plantilla oficial en formato Excel (.xlsx).')
        return archivo


class EmployeeDirectUpdateForm(forms.Form):
    nombre_empleado = forms.CharField(
        label='Nombre completo',
        max_length=160,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    documento_empleado = forms.CharField(
        label='Documento',
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    correo_empleado = forms.EmailField(
        label='Correo',
        required=False,
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )
    telefono_empleado = forms.CharField(
        label='Telefono',
        required=False,
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    fecha_alta_aprobado = forms.DateField(
        label='Fecha alta Aprobado',
        input_formats=['%Y-%m-%d', '%d/%m/%Y'],
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    )
    salario_base_mensual = MoneyTextDecimalField(
        label='Salario base mensual',
        required=False,
        decimal_places=2,
        max_digits=12,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'inputmode': 'numeric',
            'autocomplete': 'off',
        }),
    )
    auxilio_transporte_mensual = MoneyTextDecimalField(
        label='Auxilio transporte mensual',
        required=False,
        decimal_places=2,
        max_digits=12,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'inputmode': 'numeric',
            'autocomplete': 'off',
        }),
    )
    descuentos_fijos_mensuales = MoneyTextDecimalField(
        label='Descuentos fijos mensuales',
        required=False,
        decimal_places=2,
        max_digits=12,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'inputmode': 'numeric',
            'autocomplete': 'off',
        }),
    )
    estado_vinculo = forms.ChoiceField(
        label='Estado',
        choices=VinculoLaboralEmpresa.EstadoVinculo.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    validado_por_pagador = forms.BooleanField(
        label='Validado por pagador',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    def clean_documento_empleado(self):
        documento = ''.join(ch for ch in self.cleaned_data['documento_empleado'] if ch.isdigit())
        if not documento:
            raise forms.ValidationError('El documento es obligatorio.')
        return documento


class PagoCreditoOfflineForm(forms.Form):
    monto = forms.DecimalField(
        label='Monto a aplicar',
        min_value=Decimal('0.01'),
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.01',
            'min': '0.01',
            'placeholder': 'Se cargará el valor esperado de la cuota',
        }),
    )
    referencia_pago = forms.CharField(
        label='Referencia de pago',
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: TRANSF-FERTOBRA-20260331',
        }),
    )
    metodo_pago = forms.ChoiceField(
        label='Metodo de pago',
        choices=[
            (HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA, 'Transferencia directa'),
            (HistorialPago.MetodoPago.OFFLINE_MANUAL, 'Registro offline manual'),
        ],
        initial=HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    comprobante = forms.FileField(
        label='Comprobante (opcional)',
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png', 'webp'])],
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.pdf,.jpg,.jpeg,.png,.webp',
        }),
    )
    nota = forms.CharField(
        label='Motivo o contexto del pago',
        required=True,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Ej: Pago de nómina de marzo confirmado por tesorería.',
        }),
    )

    def clean_comprobante(self):
        comprobante = self.cleaned_data.get('comprobante')
        if comprobante and comprobante.size > 8 * 1024 * 1024:
            raise forms.ValidationError('El comprobante no debe superar 8MB.')
        return comprobante


class PagoObligacionesSeleccionadasForm(forms.Form):
    metodo_pago = forms.ChoiceField(
        label='Metodo de pago',
        choices=[
            (HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA, 'Transferencia directa'),
            (HistorialPago.MetodoPago.OFFLINE_MANUAL, 'Registro offline manual'),
        ],
        initial=HistorialPago.MetodoPago.TRANSFERENCIA_DIRECTA,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    comprobante = forms.FileField(
        label='Comprobante (opcional)',
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png', 'webp'])],
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.pdf,.jpg,.jpeg,.png,.webp',
        }),
    )
    nota = forms.CharField(
        label='Notas de la aplicacion',
        required=True,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Ej: Pago agrupado de obligaciones de la nomina del periodo.',
        }),
    )

    def clean_comprobante(self):
        comprobante = self.cleaned_data.get('comprobante')
        if comprobante and comprobante.size > 8 * 1024 * 1024:
            raise forms.ValidationError('El comprobante no debe superar 8MB.')
        return comprobante


class PagoMasivoEmpresaUploadForm(forms.ModelForm):
    archivo = forms.FileField(
        label='Archivo de pagos',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx',
        }),
        help_text='Usa la plantilla oficial en Excel. Puedes identificar cada fila por número de crédito o cédula.',
    )

    class Meta:
        model = LotePagoEmpresa
        fields = ['archivo']

    def clean_archivo(self):
        archivo = self.cleaned_data['archivo']
        extension = (archivo.name.rsplit('.', 1)[-1] if '.' in archivo.name else '').lower()
        if extension != 'xlsx':
            raise forms.ValidationError('Usa la plantilla oficial en formato Excel (.xlsx).')
        return archivo


class PagoMasivoEmpresaConfirmForm(forms.ModelForm):
    class Meta:
        model = LotePagoEmpresa
        fields = ['comprobante', 'notas']
        widgets = {
            'comprobante': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.pdf,.jpg,.jpeg,.png,.webp',
            }),
            'notas': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Ej: Pago de nómina de marzo confirmado por transferencia bancaria de la empresa.',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['notas'].required = True
        self.fields['notas'].help_text = 'Describe brevemente a qué pago corresponde esta carga y cómo fue confirmada.'
        self.fields['comprobante'].help_text = 'Opcional, pero recomendado cuando la empresa ya cuenta con soporte del pago.'

    def clean_comprobante(self):
        comprobante = self.cleaned_data.get('comprobante')
        if comprobante and comprobante.size > 8 * 1024 * 1024:
            raise forms.ValidationError('El comprobante no debe superar 8MB.')
        return comprobante


class PagoComisionEjecutivoForm(forms.ModelForm):
    monto = forms.CharField(
        label='Monto pagado',
        widget=forms.TextInput(attrs={
            'class': 'form-control money-input',
            'inputmode': 'numeric',
            'autocomplete': 'off',
            'placeholder': '$80.000',
            'data-money-input': 'cop',
        }),
    )

    class Meta:
        model = PagoComisionEjecutivo
        fields = ['asesor', 'monto', 'fecha_pago', 'observacion', 'comprobante']
        widgets = {
            'asesor': forms.Select(attrs={'class': 'form-select'}),
            'fecha_pago': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'observacion': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Detalle breve del pago de comision',
            }),
            'comprobante': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.pdf,.jpg,.jpeg,.png,.webp',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['asesor'].queryset = AsesorComercial.objects.filter(activo=True).order_by('nombre')
        self.fields['asesor'].label = 'Ejecutivo'
        self.fields['fecha_pago'].label = 'Fecha de pago'
        self.fields['fecha_pago'].initial = timezone.localdate
        self.fields['observacion'].required = False
        self.fields['comprobante'].required = False
        self.fields['comprobante'].help_text = 'PDF o imagen del soporte. Maximo 8 MB.'

    def clean_monto(self):
        raw_value = str(self.cleaned_data.get('monto') or '').strip()
        normalized = raw_value.replace('$', '').replace(' ', '')
        if ',' in normalized:
            normalized = normalized.replace('.', '').replace(',', '.')
        elif normalized.count('.') == 1 and len(normalized.rsplit('.', 1)[1]) <= 2:
            normalized = normalized
        else:
            normalized = normalized.replace('.', '')
        try:
            amount = Decimal(normalized)
        except Exception as exc:
            raise forms.ValidationError('Ingresa un monto valido.') from exc
        if amount <= Decimal('0'):
            raise forms.ValidationError('El monto debe ser mayor a cero.')
        return amount

    def clean_comprobante(self):
        comprobante = self.cleaned_data.get('comprobante')
        if comprobante and comprobante.size > 8 * 1024 * 1024:
            raise forms.ValidationError('El comprobante no debe superar 8 MB.')
        return comprobante


class InvestorInviteForm(forms.Form):
    email = forms.EmailField(label='Correo del inversionista')
    first_name = forms.CharField(label='Nombre', max_length=150)
    last_name = forms.CharField(label='Apellido', max_length=150, required=False)

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            raise forms.ValidationError('El correo es obligatorio.')
        return email

    def save_user(self):
        User = get_user_model()
        email = self.cleaned_data['email']
        user = User.objects.filter(email__iexact=email).first()
        created = user is None
        if created:
            user = User.objects.create(
                username=email,
                email=email,
                first_name=normalize_name_upper(self.cleaned_data.get('first_name', ''))[:150],
                last_name=normalize_name_upper(self.cleaned_data.get('last_name', ''))[:150],
                is_active=True,
            )
        if created:
            user.set_unusable_password()
            user.save(update_fields=['password'])
        else:
            updates = []
            if not user.username:
                user.username = email
                updates.append('username')
            if not user.first_name:
                user.first_name = normalize_name_upper(self.cleaned_data.get('first_name', ''))[:150]
                updates.append('first_name')
            if not user.last_name and self.cleaned_data.get('last_name'):
                user.last_name = normalize_name_upper(self.cleaned_data.get('last_name', ''))[:150]
                updates.append('last_name')
            if updates:
                user.save(update_fields=updates)
        return user


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


class SpecialCaseLibranzaSimulationForm(forms.Form):
    amount = forms.DecimalField(
        label='Monto solicitado',
        min_value=Decimal('0.01'),
        max_value=MAX_SPECIAL_CASE_AMOUNT,
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.01',
            'placeholder': '10000000',
        }),
    )
    term_months = forms.IntegerField(
        label='Plazo en meses',
        min_value=1,
        max_value=MAX_SPECIAL_CASE_TERM_MONTHS,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '1',
            'max': str(MAX_SPECIAL_CASE_TERM_MONTHS),
        }),
    )
    monthly_rate = forms.DecimalField(
        label='Tasa mensual',
        min_value=Decimal('0.00'),
        max_value=MAX_SPECIAL_CASE_MONTHLY_RATE,
        max_digits=5,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.01',
            'placeholder': '1.90',
        }),
    )
    commission_rate = forms.DecimalField(
        label='Comision porcentual',
        required=False,
        min_value=Decimal('0.00'),
        max_value=MAX_REASONABLE_COMMISSION_RATE,
        max_digits=7,
        decimal_places=4,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.0001',
            'placeholder': '5.0000',
        }),
    )
    commission_amount = forms.DecimalField(
        label='Comision fija',
        required=False,
        min_value=Decimal('0.00'),
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.01',
            'placeholder': '500000',
        }),
    )
    vat_rate = forms.DecimalField(
        label='IVA sobre comision',
        min_value=Decimal('0.00'),
        initial=Decimal('19.00'),
        max_digits=7,
        decimal_places=4,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.0001',
            'placeholder': '19.0000',
        }),
    )
    business_reason = forms.CharField(
        label='Motivo del caso especial',
        min_length=10,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Describe la justificacion comercial, operativa o de riesgo del caso especial.',
        }),
    )

class SpecialCaseLibranzaOriginationForm(forms.Form):
    tipo_documento = forms.ChoiceField(
        label='Tipo de documento',
        choices=[('CC', 'Cedula de ciudadania'), ('CE', 'Cedula de extranjeria'), ('NIT', 'NIT')],
        initial='CC',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    numero_documento = forms.CharField(
        label='Numero de documento',
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '123456789'}),
    )
    nombres = forms.CharField(
        label='Nombres',
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    apellidos = forms.CharField(
        label='Apellidos',
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    celular = forms.CharField(
        label='Celular',
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '3001234567'}),
    )
    correo = forms.EmailField(
        label='Correo',
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )
    direccion = forms.CharField(
        label='Direccion',
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    empresa = forms.ModelChoiceField(
        label='Empresa / pagador',
        queryset=Empresa.objects.filter(convenio_activo=True).exclude(
            tipo_empresa=Empresa.TipoEmpresa.MARKETPLACE_EXTERNA
        ),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    ingresos_mensuales = forms.DecimalField(
        label='Ingresos mensuales',
        required=False,
        min_value=Decimal('0.00'),
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
    )
    cedula_frontal = forms.FileField(
        label='Cedula frontal',
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp'])],
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.jpg,.jpeg,.png,.webp'}),
    )
    cedula_trasera = forms.FileField(
        label='Cedula trasera',
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp'])],
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.jpg,.jpeg,.png,.webp'}),
    )
    certificado_laboral = forms.FileField(
        label='Certificado laboral',
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png', 'webp'])],
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png,.webp'}),
    )
    desprendible_nomina = forms.FileField(
        label='Desprendible de nomina',
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png', 'webp'])],
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png,.webp'}),
    )
    certificado_bancario = forms.FileField(
        label='Certificado bancario',
        validators=[FileExtensionValidator(allowed_extensions=['pdf'])],
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf'}),
    )

    def clean_numero_documento(self):
        value = (self.cleaned_data.get('numero_documento') or '').strip()
        if len(value) < 6:
            raise forms.ValidationError('El documento debe tener al menos 6 caracteres.')
        return value

    def clean_celular(self):
        value = ''.join(filter(str.isdigit, self.cleaned_data.get('celular') or ''))
        if len(value) != 10:
            raise forms.ValidationError('El celular debe contener exactamente 10 numeros.')
        return value
