from django.shortcuts import redirect


def portal_entrypoint_view(request):
    """
    Enruta la raiz segun el host:
    - market.aprobado.com.co      -> marketplace general
    - emprender.aprobado.com.co   -> emprendimiento
    - aprobado.com.co / www       -> libranza
    """
    host = request.get_host().split(':')[0].lower()

    if host.startswith('market.'):
        return redirect('marketplace:home')

    if host.startswith('emprender.'):
        return redirect('emprendimiento:landing')

    return redirect('libranza:landing')
