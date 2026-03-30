import re
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone
from django.utils.dateparse import parse_datetime


class MarketplaceCheckoutForm(forms.Form):
    checkout_token = forms.CharField(widget=forms.HiddenInput())
    submitted_at = forms.CharField(widget=forms.HiddenInput())
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'mp-honeypot',
            'autocomplete': 'off',
            'tabindex': '-1',
            'aria-hidden': 'true',
        }),
    )
    comprador_nombre = forms.CharField(
        label='Nombre completo',
        max_length=160,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Tu nombre completo',
            'autocomplete': 'name',
        }),
    )
    comprador_email = forms.EmailField(
        label='Correo',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'correo@dominio.com',
            'autocomplete': 'email',
        }),
    )
    comprador_telefono = forms.CharField(
        label='Telefono',
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '3001234567',
            'autocomplete': 'tel',
            'inputmode': 'numeric',
            'maxlength': '10',
        }),
    )
    cantidad = forms.IntegerField(
        label='Cantidad',
        min_value=1,
        initial=1,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '1',
            'step': '1',
        }),
    )
    nombre_contacto = forms.CharField(
        label='Recibe el pedido',
        max_length=160,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Nombre de quien recibe',
            'autocomplete': 'shipping name',
        }),
    )
    telefono_contacto = forms.CharField(
        label='Telefono de entrega',
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '3001234567',
            'autocomplete': 'shipping tel',
            'inputmode': 'numeric',
            'maxlength': '10',
        }),
    )
    direccion_linea_1 = forms.CharField(
        label='Direccion',
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Calle 123 #45-67',
            'autocomplete': 'shipping street-address',
        }),
    )
    direccion_linea_2 = forms.CharField(
        label='Complemento',
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Apartamento, torre, oficina',
        }),
    )
    ciudad = forms.CharField(
        label='Ciudad',
        max_length=120,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Bogota',
            'autocomplete': 'shipping address-level2',
        }),
    )
    departamento = forms.CharField(
        label='Departamento',
        required=False,
        max_length=120,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Cundinamarca',
            'autocomplete': 'shipping address-level1',
        }),
    )
    referencia = forms.CharField(
        label='Referencia',
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Porteria, casa esquinera, etc.',
        }),
    )
    instrucciones = forms.CharField(
        label='Instrucciones',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Detalles utiles para la entrega o gestion del pedido',
        }),
    )
    notas = forms.CharField(
        label='Notas del pedido',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Observaciones adicionales para la empresa o para Aprobado',
        }),
    )

    def _clean_phone(self, field_name):
        value = ''.join(filter(str.isdigit, (self.cleaned_data.get(field_name) or '').strip()))
        if len(value) != 10:
            raise forms.ValidationError('Ingresa un numero de telefono valido de 10 digitos.')
        return value

    def _clean_name(self, field_name):
        value = re.sub(r'\s+', ' ', (self.cleaned_data.get(field_name) or '').strip())
        if len(re.findall(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]', value)) < 2:
            raise forms.ValidationError('Ingresa un nombre valido.')
        if not re.match(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' -]+$", value):
            raise forms.ValidationError('Usa solo letras, espacios, apostrofes o guiones.')
        return value.title()

    def clean_comprador_nombre(self):
        return self._clean_name('comprador_nombre')

    def clean_nombre_contacto(self):
        return self._clean_name('nombre_contacto')

    def clean_comprador_telefono(self):
        return self._clean_phone('comprador_telefono')

    def clean_telefono_contacto(self):
        return self._clean_phone('telefono_contacto')

    def clean_website(self):
        value = (self.cleaned_data.get('website') or '').strip()
        if value:
            raise forms.ValidationError('No fue posible procesar la solicitud.')
        return ''

    def clean_submitted_at(self):
        raw_value = (self.cleaned_data.get('submitted_at') or '').strip()
        if not raw_value:
            raise forms.ValidationError('No fue posible procesar la solicitud.')

        parsed = parse_datetime(raw_value)
        if not parsed:
            raise forms.ValidationError('No fue posible procesar la solicitud.')
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())

        if timezone.now() - parsed < timedelta(seconds=2):
            raise forms.ValidationError('Espera un momento antes de enviar el formulario.')
        return parsed


def parse_marketplace_price(raw_price):
    raw_price = (raw_price or '').strip()
    if not raw_price:
        raise forms.ValidationError('Esta publicacion no tiene un precio configurado para checkout.')

    digits = re.sub(r'[^\d]', '', raw_price)
    if not digits:
        raise forms.ValidationError('El precio configurado no es valido para generar un pedido.')

    value = Decimal(digits)
    if value <= 0:
        raise forms.ValidationError('El precio configurado debe ser mayor a cero.')
    return value
