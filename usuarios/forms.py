from django import forms
from django.contrib.auth.forms import SetPasswordForm


class PagadorPasswordResetRequestForm(forms.Form):
    """
    Solicita usuario o correo. El backend respondera siempre de forma neutra
    para no filtrar si la cuenta existe o no.
    """

    identificador = forms.CharField(
        label='Usuario o correo',
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ingresa tu usuario o correo corporativo',
            'autocomplete': 'username',
        }),
    )


class PagadorActivationForm(SetPasswordForm):
    """
    Reutiliza la validacion nativa de Django para fijar la contrasena inicial
    del pagador sin exponer logica criptografica en vistas.
    """

    new_password1 = forms.CharField(
        label='Nueva contrasena',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Crea tu contrasena',
            'data-password-toggle': 'true',
        }),
    )
    new_password2 = forms.CharField(
        label='Confirmar contrasena',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Repite tu contrasena',
            'data-password-toggle': 'true',
        }),
    )
