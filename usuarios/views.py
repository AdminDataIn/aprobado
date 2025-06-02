from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from allauth.socialaccount.models import SocialAccount


# Create your views here.
def index(request):
    return render(request, 'index.html')

#def aplicar_formulario(request):
#    return render(request, 'aplicando.html')

@login_required
def aplicar_formulario(request):
    # Verifica si el usuario inició sesión con Google
    if not SocialAccount.objects.filter(user=request.user, provider='google').exists():
        return redirect('/accounts/google/login/?next=/usuarios/aplicar/')
    return render(request, 'aplicando.html')


def simulador(request):
    return render(request, 'simulacion.html')
