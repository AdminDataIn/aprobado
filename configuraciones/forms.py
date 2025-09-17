from django import forms
from .models import SolicitudCredito

class SolicitudCreditoForm(forms.ModelForm):
    class Meta:
        model = SolicitudCredito
        fields = '__all__'
        exclude = ['usuario', 'estado', 'puntaje']
