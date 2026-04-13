import importlib.util

from django.apps import AppConfig


class UsuariosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'usuarios'

    def ready(self):
        try:
            has_allauth_signals = importlib.util.find_spec('allauth.account.signals') is not None
        except ModuleNotFoundError:
            has_allauth_signals = False
        if not has_allauth_signals:
            return
        import usuarios.signals
