from django.contrib import admin
from .models import PerfilPagador, PerfilEmpresaMarketing

@admin.register(PerfilPagador)
class PerfilPagadorAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'empresa', 'es_pagador')
    list_filter = ('empresa', 'es_pagador')
    search_fields = ('usuario__username', 'empresa__nombre')


@admin.register(PerfilEmpresaMarketing)
class PerfilEmpresaMarketingAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'empresa', 'activo')
    list_filter = ('empresa', 'activo')
    search_fields = ('usuario__username', 'empresa__nombre')
