
def user_groups_processor(request):
    es_empleado = False
    if request.user.is_authenticated:
        if request.user.groups.filter(name='Empleados').exists():
            es_empleado = True
    return {'es_empleado': es_empleado}
