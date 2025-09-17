from django.dispatch import receiver
from allauth.account.signals import user_signed_up
from django.contrib.auth.models import Group

@receiver(user_signed_up)
def handle_user_signup(request, user, **kwargs):
    """
    Esta función se ejecuta cada vez que un nuevo usuario se registra.
    """
    # Verificamos si el flujo de login se inició en la página de empresa
    login_flow = request.session.get('login_flow')

    if login_flow == 'empresa':
        # Limpiamos la marca de la sesión para que no persista
        # Es importante hacer esto por si el usuario inicia sesión de otra forma después
        if 'login_flow' in request.session:
            del request.session['login_flow']

        # Añadimos al usuario al grupo 'Empleados'
        try:
            empleados_group = Group.objects.get(name='Empleados')
            user.groups.add(empleados_group)
            print(f"Usuario {user.username} añadido al grupo 'Empleados' a través del flujo de empresa.")
        except Group.DoesNotExist:
            print(f"ADVERTENCIA: El grupo 'Empleados' no existe. El usuario {user.username} no fue añadido.")
            pass
