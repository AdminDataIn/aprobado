from django.contrib import admin
from .models import PerfilPagador

@admin.register(PerfilPagador)
class PerfilPagadorAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'empresa', 'es_pagador')
    list_filter = ('empresa', 'es_pagador')
    search_fields = ('usuario__username', 'empresa__nombre')