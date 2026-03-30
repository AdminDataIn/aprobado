from allauth.socialaccount.models import SocialAccount
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)

from .product_flow import get_flow_label, get_user_flow


class EmailAuthenticationForm(AuthenticationForm):
    """
    Permite ingresar correo o username y muestra un mensaje util cuando
    la cuenta existe pero aun no tiene password local usable.
    """

    username = forms.CharField(
        label='Correo o usuario',
        widget=forms.TextInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Ingresa tu correo o usuario',
                'autocomplete': 'username',
            }
        ),
    )
    password = forms.CharField(
        label='Contrasena',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Ingresa tu contrasena',
                'autocomplete': 'current-password',
            }
        ),
    )

    def clean(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if username and '@' in username:
            User = get_user_model()
            user = User.objects.filter(email__iexact=username).first()
            if user:
                if not user.has_usable_password():
                    raise forms.ValidationError(
                        'Tu cuenta ya existe, pero aun no tiene una contrasena local. '
                        'Usa "Olvide mi contrasena" para activar tu acceso por correo o continua con Google.'
                    )
                self.cleaned_data['username'] = user.get_username()
        return super().clean()


class ProductPasswordResetForm(PasswordResetForm):
    """
    Incluye cuentas sociales sin password usable para permitir fijar
    una contrasena local sobre el mismo usuario.
    """

    def get_users(self, email):
        UserModel = get_user_model()
        email_field_name = UserModel.get_email_field_name()
        active_users = UserModel._default_manager.filter(
            **{
                f'{email_field_name}__iexact': email,
                'is_active': True,
            }
        )
        for user in active_users:
            if user.has_usable_password() or SocialAccount.objects.filter(user=user).exists():
                yield user


class PagadorPasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        label='Correo corporativo',
        widget=forms.EmailInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'correo@empresa.com',
                'autocomplete': 'email',
            }
        ),
    )


class PagadorAuthenticationForm(EmailAuthenticationForm):
    username = forms.EmailField(
        label='Correo corporativo',
        widget=forms.EmailInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'correo@empresa.com',
                'autocomplete': 'email',
            }
        ),
    )


class InvestorAuthenticationForm(EmailAuthenticationForm):
    username = forms.EmailField(
        label='Correo',
        widget=forms.EmailInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'correo@dominio.com',
                'autocomplete': 'email',
            }
        ),
    )


class PagadorActivationForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label='Nueva contrasena',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Crea tu contrasena',
                'data-password-toggle': 'true',
            }
        ),
    )
    new_password2 = forms.CharField(
        label='Confirmar contrasena',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Repite tu contrasena',
                'data-password-toggle': 'true',
            }
        ),
    )


class InvestorActivationForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label='Nueva contrasena',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Crea tu contrasena',
                'data-password-toggle': 'true',
            }
        ),
    )
    new_password2 = forms.CharField(
        label='Confirmar contrasena',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Repite tu contrasena',
                'data-password-toggle': 'true',
            }
        ),
    )


class ProductUserRegistrationForm(UserCreationForm):
    email = forms.EmailField(
        label='Correo',
        widget=forms.EmailInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'tu@correo.com',
                'autocomplete': 'email',
            }
        ),
    )
    first_name = forms.CharField(
        label='Nombre',
        max_length=150,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Tu nombre',
                'autocomplete': 'given-name',
            }
        ),
    )
    last_name = forms.CharField(
        label='Apellido',
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Tu apellido',
                'autocomplete': 'family-name',
            }
        ),
    )
    password1 = forms.CharField(
        label='Contrasena',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Crea tu contrasena',
                'autocomplete': 'new-password',
            }
        ),
    )
    password2 = forms.CharField(
        label='Confirmar contrasena',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'Confirma tu contrasena',
                'autocomplete': 'new-password',
            }
        ),
    )

    def __init__(self, *args, target_flow=None, **kwargs):
        self.target_flow = target_flow
        super().__init__(*args, **kwargs)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ('email', 'first_name', 'last_name')

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            raise forms.ValidationError('El correo es obligatorio.')

        User = get_user_model()
        existing_user = User.objects.filter(email__iexact=email).first()
        if not existing_user:
            return email

        existing_flow = get_user_flow(existing_user)
        if existing_flow and self.target_flow and existing_flow != self.target_flow:
            raise forms.ValidationError(
                f'Este correo ya pertenece al flujo de {get_flow_label(existing_flow)}. '
                'Debes ingresar por ese producto.'
            )

        if not existing_user.has_usable_password():
            raise forms.ValidationError(
                'Este correo ya tiene una cuenta creada con Google o sin contrasena local. '
                'Usa "Olvide mi contrasena" para definir tu acceso por correo.'
            )

        raise forms.ValidationError(
            'Ya existe una cuenta con este correo. Inicia sesi?n o usa recuperaci?n de contrase?a.'
        )

    def save(self, commit=True):
        user = super().save(commit=False)
        email = self.cleaned_data['email']
        user.email = email
        user.username = email
        user.first_name = (self.cleaned_data.get('first_name') or '').strip()
        user.last_name = (self.cleaned_data.get('last_name') or '').strip()
        if commit:
            user.save()
        return user


class MarketplaceBuyerRegistrationForm(ProductUserRegistrationForm):
    pass
