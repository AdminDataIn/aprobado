from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from django import forms

from contractors.models import (
    ContractorApplication,
    ContractorApplicationDocument,
    InformacionLaboralSolicitudContratista,
)
from contractors.services.autorizacion_datacredito import obtener_configuracion_texto_autorizacion
from gestion_creditos.models import Empresa


PATRON_NOMBRE_PERSONA = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ' -]{1,}$")
PATRON_TEXTO_BASURA = re.compile(r'^(.)\1{3,}$|^(asdf|asdasd|asdasdasd|qwerty|test|prueba)$', re.IGNORECASE)


class FormularioSimulacionContratista(forms.Form):
    monto = forms.DecimalField(
        label='Monto a simular',
        min_value=Decimal('0.01'),
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                'class': 'form-input',
                'step': '0.01',
                'min': '0.01',
                'placeholder': 'Ej. 3000000',
            },
        ),
        error_messages={
            'required': 'Ingrese el monto a simular.',
            'min_value': 'El monto debe ser mayor a cero.',
            'invalid': 'Ingrese un monto valido.',
        },
    )
    plazo_meses = forms.IntegerField(
        label='Plazo en meses',
        min_value=1,
        widget=forms.NumberInput(
            attrs={
                'class': 'form-input',
                'min': '1',
                'placeholder': 'Ej. 12',
            },
        ),
        error_messages={
            'required': 'Ingrese el plazo en meses.',
            'min_value': 'El plazo debe ser de al menos un mes.',
            'invalid': 'Ingrese un plazo valido.',
        },
    )


class FormularioSolicitudContratista(forms.Form):
    class TipoDocumentoIdentidad:
        CEDULA_CIUDADANIA = 'CC'
        CEDULA_EXTRANJERIA = 'CE'

        choices = (
            (CEDULA_CIUDADANIA, 'Cédula de ciudadanía'),
            (CEDULA_EXTRANJERIA, 'Cédula de extranjería'),
        )

    escenario_credito = forms.ChoiceField(
        label='Escenario de credito',
        choices=ContractorApplication.EscenarioCredito.choices,
        initial=ContractorApplication.EscenarioCredito.NUEVO_CREDITO,
        widget=forms.Select(attrs={'class': 'form-select'}),
        error_messages={
            'required': 'Seleccione el escenario de credito.',
            'invalid_choice': 'El escenario de credito no es valido.',
        },
    )
    tipo_documento = forms.ChoiceField(
        label='Tipo de documento',
        choices=TipoDocumentoIdentidad.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
        error_messages={
            'required': 'Seleccione el tipo de documento.',
            'invalid_choice': 'Seleccione un tipo de documento valido.',
        },
    )
    numero_documento = forms.CharField(
        label='Numero de documento',
        max_length=40,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ej. 1020304050'}),
        error_messages={'required': 'Ingrese el numero de documento.'},
    )
    nombres = forms.CharField(
        label='Nombres',
        max_length=120,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ej. Ana Maria'}),
        error_messages={'required': 'Ingrese los nombres.'},
    )
    apellidos = forms.CharField(
        label='Apellidos',
        max_length=120,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ej. Perez Gomez'}),
        error_messages={'required': 'Ingrese los apellidos.'},
    )
    celular = forms.CharField(
        label='Celular',
        max_length=40,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ej. 3001234567'}),
        error_messages={'required': 'Ingrese el celular.'},
    )
    correo = forms.EmailField(
        label='Correo electronico',
        widget=forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'Ej. correo@dominio.com'}),
        error_messages={
            'required': 'Ingrese el correo electronico.',
            'invalid': 'Ingrese un correo electronico valido.',
        },
    )
    direccion = forms.CharField(
        label='Direccion',
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ej. Calle 10 # 20-30, Bogota'}),
        error_messages={'required': 'Ingrese la direccion.'},
    )
    cargo = forms.CharField(
        label='Cargo o actividad',
        max_length=160,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ej. Consultor comercial'}),
        error_messages={'required': 'Ingrese el cargo o actividad.'},
    )
    empresa = forms.ModelChoiceField(
        label='Empresa contratante',
        queryset=Empresa.objects.none(),
        empty_label='Seleccione una empresa',
        widget=forms.HiddenInput(),
        error_messages={
            'required': 'Seleccione la empresa contratante.',
            'invalid_choice': 'La empresa seleccionada no es valida.',
        },
    )
    empresa_busqueda = forms.CharField(
        label='Busca tu empresa',
        required=False,
        widget=forms.TextInput(
            attrs={
                'class': 'form-input',
                'autocomplete': 'off',
                'placeholder': 'Escribe minimo 2 letras. Ej. APR',
            },
        ),
    )
    tipo_contrato = forms.ChoiceField(
        label='Tipo de contrato',
        choices=InformacionLaboralSolicitudContratista.TipoContrato.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
        error_messages={
            'required': 'Seleccione el tipo de contrato.',
            'invalid_choice': 'El tipo de contrato no es valido.',
        },
    )
    fecha_inicio_contrato = forms.DateField(
        label='Fecha inicio contrato',
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
        error_messages={
            'required': 'Ingrese la fecha de inicio del contrato.',
            'invalid': 'Ingrese una fecha de inicio valida.',
        },
    )
    fecha_fin_contrato = forms.DateField(
        label='Fecha fin contrato',
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
        error_messages={
            'required': 'Ingrese la fecha de fin del contrato.',
            'invalid': 'Ingrese una fecha de fin valida.',
        },
    )
    valor_total_contrato = forms.DecimalField(
        label='Valor total del contrato',
        min_value=Decimal('0.00'),
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 12000000'}),
        error_messages={
            'required': 'Ingrese el valor total del contrato.',
            'min_value': 'El valor total del contrato no puede ser negativo.',
            'invalid': 'Ingrese un valor total valido.',
        },
    )
    valor_pagado_contrato = forms.DecimalField(
        label='Valor pagado del contrato',
        min_value=Decimal('0.00'),
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 4000000'}),
        error_messages={
            'required': 'Ingrese el valor pagado del contrato.',
            'min_value': 'El valor pagado del contrato no puede ser negativo.',
            'invalid': 'Ingrese un valor pagado valido.',
        },
    )
    valor_pendiente_cobrar = forms.DecimalField(
        label='Valor pendiente por cobrar',
        min_value=Decimal('0.00'),
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-input', 'step': '0.01', 'min': '0', 'placeholder': 'Ej. 8000000'}),
        error_messages={
            'required': 'Ingrese el valor pendiente por cobrar.',
            'min_value': 'El valor pendiente por cobrar no puede ser negativo.',
            'invalid': 'Ingrese un valor pendiente valido.',
        },
    )
    observaciones = forms.CharField(
        label='Observaciones',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2, 'placeholder': 'Notas adicionales si aplica'}),
    )
    analisis_contractual_metadata = forms.CharField(required=False, widget=forms.HiddenInput())
    confirmar_fecha_fin_inferida = forms.BooleanField(required=False, widget=forms.CheckboxInput())
    confirmar_valor_pendiente_inferido = forms.BooleanField(required=False, widget=forms.CheckboxInput())
    terminos_aceptados = forms.BooleanField(
        label='Acepto los terminos y condiciones y autorizo el tratamiento de mis datos personales de acuerdo con la politica de privacidad.',
        required=True,
        error_messages={'required': 'Debe aceptar terminos y condiciones.'},
    )
    autorizacion_datacredito_aceptada = forms.BooleanField(
        label='Autorizo la consulta de mi informacion financiera y crediticia ante centrales de riesgo.',
        required=False,
        error_messages={'required': 'Debe aceptar la autorizacion especifica de consulta DataCredito.'},
    )
    documento_identidad_frontal_capturado = forms.CharField(required=False, widget=forms.HiddenInput())
    documento_identidad_reverso_capturado = forms.CharField(required=False, widget=forms.HiddenInput())
    documento_identidad_frontal = forms.FileField(
        label='Cedula frontal',
        widget=forms.ClearableFileInput(
            attrs={
                'class': 'form-input document-input',
                'accept': '.jpg,.jpeg,.png,image/jpeg,image/png',
                'capture': 'environment',
            },
        ),
        error_messages={'required': 'Cargue la cedula frontal.'},
    )
    documento_identidad_reverso = forms.FileField(
        label='Cedula trasera',
        widget=forms.ClearableFileInput(
            attrs={
                'class': 'form-input document-input',
                'accept': '.jpg,.jpeg,.png,image/jpeg,image/png',
                'capture': 'environment',
            },
        ),
        error_messages={'required': 'Cargue la cedula trasera.'},
    )
    contrato_actual = forms.FileField(
        label='Contrato vigente PDF',
        widget=forms.ClearableFileInput(
            attrs={
                'class': 'form-input document-input',
                'accept': '.pdf,application/pdf',
            },
        ),
        error_messages={'required': 'Cargue el contrato vigente en PDF.'},
    )
    certificado_bancario = forms.FileField(
        label='Certificado bancario PDF',
        widget=forms.ClearableFileInput(
            attrs={
                'class': 'form-input document-input',
                'accept': '.pdf,application/pdf',
            },
        ),
        error_messages={'required': 'Cargue el certificado bancario en PDF.'},
    )

    def __init__(self, *args, configuracion_producto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configuracion_producto = configuracion_producto
        self.autorizacion_datacredito_requerida = False
        self.fields['autorizacion_datacredito_aceptada'].required = self.autorizacion_datacredito_requerida
        self.fields['empresa'].queryset = (
            Empresa.objects
            .filter(convenio_activo=True)
            .exclude(tipo_empresa=Empresa.TipoEmpresa.MARKETPLACE_EXTERNA)
            .order_by('nombre')
        )
        empresa_id = self.data.get(self.add_prefix('empresa')) if self.is_bound else self.initial.get('empresa')
        if empresa_id:
            empresa = self.fields['empresa'].queryset.filter(pk=empresa_id).first()
            if empresa:
                self.fields['empresa_busqueda'].initial = empresa.razon_social or empresa.nombre

    def clean_nombres(self):
        valor = (self.cleaned_data.get('nombres') or '').strip()
        if not _texto_persona_valido(valor):
            raise forms.ValidationError('Ingresa un nombre valido.')
        return valor

    def clean_apellidos(self):
        valor = (self.cleaned_data.get('apellidos') or '').strip()
        if not _texto_persona_valido(valor):
            raise forms.ValidationError('Ingresa un apellido valido.')
        return valor

    def clean_numero_documento(self):
        valor = (self.cleaned_data.get('numero_documento') or '').strip()
        if not re.fullmatch(r'\d{6,10}', valor):
            raise forms.ValidationError('Ingresa un numero de documento valido.')
        if _documento_identidad_invalido(valor):
            raise forms.ValidationError('Ingresa un numero de documento valido.')
        return valor

    def clean_celular(self):
        valor = (self.cleaned_data.get('celular') or '').strip()
        if not re.fullmatch(r'\d{10}', valor):
            raise forms.ValidationError('El celular debe tener exactamente 10 numeros.')
        return valor

    def clean_correo(self):
        valor = (self.cleaned_data.get('correo') or '').strip()
        if not valor:
            raise forms.ValidationError('Este campo es obligatorio.')
        return valor

    def clean_direccion(self):
        valor = (self.cleaned_data.get('direccion') or '').strip()
        if len(valor) < 8 or PATRON_TEXTO_BASURA.fullmatch(valor.replace(' ', '')):
            raise forms.ValidationError('Ingresa una direccion valida.')
        if not re.search(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', valor) or not re.search(r'\d', valor):
            raise forms.ValidationError('Ingresa una direccion valida.')
        return valor

    def clean(self):
        datos = super().clean()

        if not self.configuracion_producto:
            raise forms.ValidationError('configuracion_producto_no_encontrada')

        empresa = datos.get('empresa')
        empresa_busqueda = (datos.get('empresa_busqueda') or '').strip()
        if empresa_busqueda and not empresa:
            self.add_error('empresa_busqueda', 'Debes elegir una empresa de la lista de resultados.')
        if empresa and not empresa.permite_libranza:
            self.add_error('empresa', 'La empresa seleccionada no tiene convenio activo para libranza.')

        _validar_archivo_pdf(self, 'contrato_actual', 'El contrato vigente debe cargarse en PDF.')
        _validar_archivo_pdf(self, 'certificado_bancario', 'El certificado bancario debe cargarse en PDF.')
        _validar_captura_camara(
            self,
            'documento_identidad_frontal',
            'documento_identidad_frontal_capturado',
            'La cedula frontal debe capturarse en vivo desde la camara.',
        )
        _validar_captura_camara(
            self,
            'documento_identidad_reverso',
            'documento_identidad_reverso_capturado',
            'La cedula trasera debe capturarse en vivo desde la camara.',
        )
        _validar_archivo_imagen(self, 'documento_identidad_frontal', 'La cedula frontal debe ser imagen JPG o PNG.')
        _validar_archivo_imagen(self, 'documento_identidad_reverso', 'La cedula trasera debe ser imagen JPG o PNG.')
        _validar_archivos_no_repetidos(
            self,
            (
                'documento_identidad_frontal',
                'documento_identidad_reverso',
                'contrato_actual',
                'certificado_bancario',
            ),
        )

        fecha_inicio = datos.get('fecha_inicio_contrato')
        fecha_fin = datos.get('fecha_fin_contrato')
        if fecha_inicio and fecha_fin and fecha_fin < fecha_inicio:
            self.add_error('fecha_fin_contrato', 'La fecha de fin no puede ser menor que la fecha de inicio.')

        valor_total = datos.get('valor_total_contrato')
        valor_pagado = datos.get('valor_pagado_contrato')
        valor_pendiente = datos.get('valor_pendiente_cobrar')
        if (
            valor_total is not None
            and valor_pagado is not None
            and valor_pendiente is not None
            and valor_pagado + valor_pendiente > valor_total
        ):
            self.add_error(
                'valor_pendiente_cobrar',
                'La suma de valor pagado y valor pendiente no puede superar el valor total del contrato.',
            )

        metadata_contractual = self._limpiar_metadata_contractual(datos.get('analisis_contractual_metadata'))
        datos['analisis_contractual_metadata'] = metadata_contractual
        if metadata_contractual:
            bloqueos = set(metadata_contractual.get('bloqueos') or ())
            metadata_segura = metadata_contractual.get('metadata') or {}
            if 'contrato_vencido_detectado' in bloqueos or metadata_segura.get('contrato_vencido_detectado'):
                self.add_error(
                    'fecha_fin_contrato',
                    'El contrato detectado esta vencido. La solicitud requiere revision manual antes de continuar.',
                )
            if 'empresa_contrato_conflicto_nit_nombre' in bloqueos:
                self.add_error(
                    'empresa_busqueda',
                    'La empresa detectada por NIT no coincide con el nombre del contrato. Debes confirmar la empresa con soporte.',
                )
            if metadata_segura.get('fecha_fin_requiere_confirmacion') and not datos.get('confirmar_fecha_fin_inferida'):
                self.add_error(
                    'confirmar_fecha_fin_inferida',
                    'Confirma la fecha final detectada o inferida antes de continuar.',
                )
            if metadata_segura.get('valor_pendiente_requiere_confirmacion') and not datos.get('confirmar_valor_pendiente_inferido'):
                self.add_error(
                    'confirmar_valor_pendiente_inferido',
                    'Confirma el valor pendiente detectado o inferido antes de continuar.',
                )

        autorizacion_datacredito = datos.get('autorizacion_datacredito_aceptada')
        if autorizacion_datacredito:
            configuracion_autorizacion = obtener_configuracion_texto_autorizacion()
            if not configuracion_autorizacion.configurada:
                self.add_error(
                    'autorizacion_datacredito_aceptada',
                    'La autorizacion DataCredito no esta configurada. Intenta mas tarde.',
                )
        elif self.autorizacion_datacredito_requerida:
            self.add_error(
                'autorizacion_datacredito_aceptada',
                'Debe aceptar la autorizacion especifica de consulta DataCredito.',
            )

        return datos

    @staticmethod
    def _limpiar_metadata_contractual(valor):
        if not valor:
            return {}
        try:
            datos = json.loads(valor)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise forms.ValidationError('La metadata del analisis contractual no es valida.')
        if not isinstance(datos, dict):
            raise forms.ValidationError('La metadata del analisis contractual no es valida.')
        permitidas = {
            'metadata',
            'advertencias',
            'bloqueos',
            'eventos',
            'sugerencia_empresa',
            'requiere_revision_manual',
        }
        return {llave: datos.get(llave) for llave in permitidas if llave in datos}


class FormularioDocumentoSolicitudContratista(forms.Form):
    tipo_documento = forms.ChoiceField(
        label='Tipo de documento',
        choices=ContractorApplicationDocument.TipoDocumento.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
        error_messages={
            'required': 'Seleccione el tipo de documento.',
            'invalid_choice': 'El tipo de documento no esta permitido.',
        },
    )
    archivo = forms.FileField(
        label='Archivo',
        widget=forms.ClearableFileInput(
            attrs={
                'class': 'form-input document-input',
                'accept': '.pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png',
            },
        ),
        error_messages={
            'required': 'Seleccione un archivo.',
            'invalid': 'El archivo no es valido.',
        },
    )

    def __init__(self, *args, solicitud=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.solicitud = solicitud

    def clean(self):
        datos = super().clean()
        tipo_documento = datos.get('tipo_documento')
        archivo = datos.get('archivo')
        if not tipo_documento or not archivo:
            return datos

        content_type = getattr(archivo, 'content_type', '')
        nombre = getattr(archivo, 'name', '') or ''
        extension = Path(nombre).suffix.lower()

        documentos_pdf = {
            ContractorApplicationDocument.TipoDocumento.CONTRATO_ACTUAL,
            ContractorApplicationDocument.TipoDocumento.CERTIFICADO_BANCARIO,
        }
        documentos_imagen = {
            ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_FRONTAL,
            ContractorApplicationDocument.TipoDocumento.DOCUMENTO_IDENTIDAD_REVERSO,
        }

        if tipo_documento in documentos_pdf and (content_type != 'application/pdf' or extension != '.pdf'):
            self.add_error('archivo', 'Contrato y certificado bancario deben cargarse en PDF.')

        if tipo_documento in documentos_imagen and content_type not in {'image/jpeg', 'image/png'}:
            self.add_error('archivo', 'La cedula debe cargarse como imagen JPG o PNG.')

        if self.solicitud and nombre:
            existe_repetido = self.solicitud.documents.filter(
                original_filename=nombre,
                file_size=getattr(archivo, 'size', 0),
            ).exists()
            if existe_repetido:
                self.add_error('archivo', 'No puedes subir el mismo archivo para documentos diferentes.')

        return datos


def _texto_persona_valido(valor):
    if len(valor) < 2:
        return False
    if PATRON_TEXTO_BASURA.fullmatch(valor.replace(' ', '')):
        return False
    return bool(PATRON_NOMBRE_PERSONA.fullmatch(valor))


def _documento_identidad_invalido(valor):
    secuencias_invalidas = {
        '000000',
        '111111',
        '1111111',
        '11111111',
        '222222',
        '123456',
        '123456789',
    }
    if valor in secuencias_invalidas:
        return True
    if len(set(valor)) == 1:
        return True
    return False


def _validar_captura_camara(formulario, nombre_campo_archivo, nombre_campo_marca, mensaje):
    archivo = formulario.cleaned_data.get(nombre_campo_archivo)
    marca = (formulario.cleaned_data.get(nombre_campo_marca) or '').strip()
    if archivo and marca != '1':
        formulario.add_error(nombre_campo_archivo, mensaje)


def _validar_archivo_pdf(formulario, nombre_campo, mensaje):
    archivo = formulario.cleaned_data.get(nombre_campo)
    if not archivo:
        return
    content_type = getattr(archivo, 'content_type', '')
    extension = Path(getattr(archivo, 'name', '') or '').suffix.lower()
    if content_type != 'application/pdf' or extension != '.pdf':
        formulario.add_error(nombre_campo, mensaje)


def _validar_archivo_imagen(formulario, nombre_campo, mensaje):
    archivo = formulario.cleaned_data.get(nombre_campo)
    if not archivo:
        return
    content_type = getattr(archivo, 'content_type', '')
    extension = Path(getattr(archivo, 'name', '') or '').suffix.lower()
    if content_type not in {'image/jpeg', 'image/png'} or extension not in {'.jpg', '.jpeg', '.png'}:
        formulario.add_error(nombre_campo, mensaje)


def _validar_archivos_no_repetidos(formulario, nombres_campos):
    vistos = {}
    for nombre_campo in nombres_campos:
        archivo = formulario.cleaned_data.get(nombre_campo)
        if not archivo:
            continue
        llave = (
            getattr(archivo, 'name', '') or '',
            getattr(archivo, 'size', 0),
            getattr(archivo, 'content_type', '') or '',
            _hash_temporal_archivo(archivo),
        )
        if llave in vistos:
            formulario.add_error(nombre_campo, 'No puedes cargar el mismo archivo en documentos diferentes.')
            formulario.add_error(vistos[llave], 'No puedes cargar el mismo archivo en documentos diferentes.')
        vistos[llave] = nombre_campo


def _hash_temporal_archivo(archivo):
    posicion = None
    if hasattr(archivo, 'tell') and hasattr(archivo, 'seek'):
        try:
            posicion = archivo.tell()
            archivo.seek(0)
        except Exception:
            posicion = None
    digest = hashlib.sha256()
    for bloque in getattr(archivo, 'chunks', lambda: iter(lambda: archivo.read(8192), b''))():
        if not bloque:
            break
        digest.update(bloque)
    if posicion is not None:
        archivo.seek(posicion)
    return digest.hexdigest()
