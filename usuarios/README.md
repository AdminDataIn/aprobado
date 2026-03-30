# Directorio `usuarios/`

Este directorio contiene los componentes relacionados con la autenticación, gestión de usuarios y detección de contexto de producto.

---

## 📁 Estructura de Archivos

### `__init__.py`
Archivo de inicialización del módulo Django.

---

### `adapter.py`
Adaptador personalizado para Django Allauth.
- Gestiona el comportamiento de login/signup con Google OAuth
- Personaliza redirecciones después de autenticación

---

### `admin.py`
Configuración del panel de administración de Django para el modelo User.
- Registra modelos relacionados con usuarios en el admin de Django

---

### `apps.py`
Configuración de la aplicación Django `usuarios`.
- Define el nombre y configuración de la app

---

### `context_processors.py`
Context processors que inyectan variables en todos los templates.

#### `user_groups_processor(request)`
- Detecta si el usuario pertenece al grupo "Empleados"
- Retorna: `es_empleado` (bool)

#### `notificaciones_processor(request)`
- Obtiene las últimas 5 notificaciones no leídas del usuario
- Retorna: `notificaciones_no_leidas` (QuerySet), `count_notificaciones` (int)

#### `producto_context_processor(request)`
- Lee el producto actual (LIBRANZA o EMPRENDIMIENTO) desde la sesión
- Retorna: `producto_actual` (str), `es_libranza` (bool)
- Usado para logout dinámico y navegación contextual

---

### `middleware.py`
Middlewares personalizados para el proyecto.

#### `ProductoContextMiddleware`
- Detecta automáticamente el producto (LIBRANZA o EMPRENDIMIENTO) basándose en la URL actual
- Guarda `producto_actual` en la sesión del usuario
- Evita consultas a la base de datos para determinar el contexto de producto
- **URLs detectadas**:
  - `/libranza/`, `/pagador/` → `LIBRANZA`
  - `/emprendimiento/`, `/aplicando/` → `EMPRENDIMIENTO`
  - `/billetera/` → Mantiene el producto actual

---

### `models.py`
Modelos de datos para usuarios.
- Define extensiones al modelo User de Django (si aplica)

---

### `tests.py`
Tests unitarios para la aplicación usuarios.
- Pruebas de vistas, middleware, context processors

---

### `urls.py`
URLs principales de la aplicación usuarios.
- Incluye las URLs de emprendimiento y libranza mediante `include()`

---

### `urls_emprendimiento.py`
URLs específicas para el producto Emprendimiento.
- Namespace: `emprendimiento`
- Rutas:
  - `/emprendimiento/landing/` → Landing de emprendimiento
  - `/emprendimiento/solicitar/` → Formulario de solicitud
  - `/emprendimiento/logout/` → Logout de emprendimiento
  - `/emprendimiento/mi-credito/` → Dashboard de emprendimiento

---

### `urls_libranza.py`
URLs específicas para el producto Libranza.
- Namespace: `libranza`
- Rutas:
  - `/libranza/` → Landing de libranza
  - `/libranza/simulador/` → Simulador de libranza
  - `/libranza/login/` → Login de libranza
  - `/libranza/logout/` → Logout de libranza
  - `/libranza/mi-credito/` → Dashboard de libranza

---

### `views.py`
Vistas de la aplicación usuarios.

#### `index(request)`
- Vista principal del home (landing de emprendimiento)

#### `aplicar_formulario(request)`
- Vista del formulario de solicitud de crédito de emprendimiento
- Requiere autenticación (`@login_required`)

#### `simulador(request)`
- Vista del simulador de créditos
- Detecta si el usuario es empleado para mostrar simulador de libranza

#### `EmpresaLoginView`
- Vista de login para pagadores (empresas)
- Verifica que el usuario tenga perfil de pagador
- Redirige al dashboard de pagador

#### `libranza_landing(request)`
- Vista de la landing page de Crédito de Libranza
- Pública, no requiere autenticación

#### `simulador_libranza(request)`
- Vista del simulador exclusivo de Crédito de Libranza
- Pública, no requiere autenticación

#### `LoginLibranzaView`
- Vista de login específica para Libranza
- Usa template `account/libranza/login.html`

#### `LoginEmprendimientoView`
- Vista de login específica para Emprendimiento
- Usa template `account/emprendimiento/login.html`

#### `CustomLogoutView`
- Vista personalizada de logout que redirige según el producto del usuario
- Lee `producto_actual` desde la sesión (detectado por middleware)
- **OPTIMIZADO**: No consulta la base de datos
- Redirige a:
  - `libranza:landing` si producto = LIBRANZA
  - `home` si producto = EMPRENDIMIENTO

---

## 🔗 Integración con el Proyecto

### Middleware Registrado en `settings.py`:
```python
MIDDLEWARE = [
    ...
    'django.contrib.sessions.middleware.SessionMiddleware',
    'usuarios.middleware.ProductoContextMiddleware',  # <-- Detecta producto
    ...
]
```

### Context Processors Registrados en `settings.py`:
```python
TEMPLATES = [{
    'OPTIONS': {
        'context_processors': [
            ...
            'usuarios.context_processors.user_groups_processor',
            'usuarios.context_processors.notificaciones_processor',
            'usuarios.context_processors.producto_context_processor',  # <-- Producto actual
        ],
    },
}]
```

---

## 📊 Flujo de Detección de Producto

1. **Usuario navega** → URL `/libranza/landing/`
2. **Middleware** → Detecta `/libranza/` en path
3. **Sesión** → Guarda `request.session['producto_actual'] = 'LIBRANZA'`
4. **Context Processor** → Lee sesión y agrega `es_libranza = True` al contexto
5. **Templates** → Usan `{% if es_libranza %}` para mostrar contenido dinámico
6. **Logout** → `CustomLogoutView` lee sesión y redirige a `libranza:landing`

---

## 🎯 Beneficios del Sistema Actual

- ⚡ **Rendimiento**: Sin consultas a BD para detectar producto
- 🎯 **Precisión**: Basado en navegación real del usuario
- 🔄 **Reutilizable**: Context processor disponible globalmente
- 🧹 **Limpio**: Lógica centralizada en middleware

---

**Última actualización**: 2025-12-26
