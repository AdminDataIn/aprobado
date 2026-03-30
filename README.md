# 🚀 Integración WOMPI - Sistema Aprobado

Guía completa para integrar WOMPI en proyectos Django/Python. Incluye cliente Python, ejemplos completos, webhooks y datos de prueba.

---

## 📚 Tabla de Contenido

- [Inicio Rápido (5 minutos)](#-inicio-rápido-5-minutos)
- [Instalación Completa](#-instalación-completa)
- [Métodos de Pago](#-métodos-de-pago)
- [Frontend (Templates HTML)](#-frontend-templates-html)
- [Backend (Vistas Django)](#-backend-vistas-django)
- [Webhooks](#-webhooks)
- [Datos de Prueba](#-datos-de-prueba-sandbox)
- [Troubleshooting](#-troubleshooting)
- [Referencias](#-referencias)

---

## 🚀 Inicio Rápido (5 minutos)

### Opción 1: Cliente Python (Recomendado)

**Paso 1:** Copiar `wompi_client.py` a tu proyecto
```
tu_proyecto/
├── gestion_creditos/
│   └── services/
│       └── wompi_client.py  ← Copiar aquí
```

**Paso 2:** Configurar en `settings.py`
```python
WOMPI_PUBLIC_KEY = config('WOMPI_PUBLIC_KEY')
WOMPI_PRIVATE_KEY = config('WOMPI_PRIVATE_KEY')
WOMPI_INTEGRITY_KEY = config('WOMPI_INTEGRITY_KEY')
WOMPI_EVENTS_SECRET = config('WOMPI_EVENTS_SECRET')
WOMPI_ENVIRONMENT = 'sandbox'  # o 'production'
WOMPI_API_BASE_URL = 'https://sandbox.wompi.co/v1'
```

**Paso 3:** Usar en tu código
```python
from .services.wompi_client import WompiClient

client = WompiClient()

# Obtener acceptance token
acceptance = client.get_acceptance_token()
token = acceptance['data']['presigned_acceptance']['acceptance_token']

# Crear transacción con Nequi
payment_method = WompiClient.build_nequi_payment_method("3991111111")

transaction = client.create_transaction(
    amount_in_cents=5000000,  # $50,000 COP
    currency="COP",
    customer_email="cliente@example.com",
    payment_method=payment_method,
    reference="CUOTA-001-2024",
    acceptance_token=token,
    redirect_url="https://tu-app.com/callback/"
)

# Redirigir al usuario
redirect_url = transaction['data']['payment_method']['extra']['async_payment_url']
```

### Opción 2: Widget HTML (Más Simple)

Abrir `checkout_widget_simplificado.html` y modificar:
```javascript
const WOMPI_CONFIG = {
    publicKey: 'pub_test_TU_LLAVE_AQUI',
    integrityKey: 'int_test_TU_LLAVE_AQUI',
    amountInCents: 5000000,  // $50,000 COP
    currency: 'COP',
    redirectUrl: 'https://tu-app.com/payment/callback/'
};
```

---

## 📦 Instalación Completa

### 1. Cliente WOMPI (`wompi_client.py`)

**Ubicación:** `gestion_creditos/services/wompi_client.py`

**Características:**
- ✅ Maneja los 4 métodos de pago (Tarjeta, PSE, Nequi, Bancolombia)
- ✅ Tokenización segura de tarjetas
- ✅ Cálculo automático de firmas de integridad
- ✅ Manejo de errores y bloqueos de WAF
- ✅ Datos de prueba incluidos
- ✅ Completamente documentado

**Dependencias:**
```python
import requests
import logging
import hashlib
from typing import Dict, Optional, List
from django.conf import settings
```

### 2. Variables de Entorno

**Archivo `.env`:**
```env
WOMPI_PUBLIC_KEY=pub_test_xxxxxxxxxxxxx
WOMPI_PRIVATE_KEY=priv_test_xxxxxxxxxxxxx
WOMPI_INTEGRITY_KEY=int_test_xxxxxxxxxxxxx
WOMPI_EVENTS_SECRET=evt_test_xxxxxxxxxxxxx
WOMPI_ENVIRONMENT=sandbox
```

### 3. Obtener Llaves de WOMPI

1. Crear cuenta en https://comercios.wompi.co
2. Ir a **Configuración → Llaves API**
3. Copiar las llaves de **Sandbox** para pruebas

**Tipos de llaves:**
- `pub_test_xxx` - Llave pública (frontend)
- `priv_test_xxx` - Llave privada (backend)
- `int_test_xxx` - Llave de integridad (firma)
- `evt_test_xxx` - Llave de eventos (webhooks)

---

## 💳 Métodos de Pago

### 1. Tarjeta de Crédito/Débito

**Paso 1: Tokenizar tarjeta** (en FRONTEND)
```python
# IMPORTANTE: Hacer desde JavaScript para nunca enviar el número completo al backend
token_response = client.tokenize_card(
    card_number="4242424242424242",  # Sandbox: APPROVED
    cvc="123",
    exp_month="12",
    exp_year="29",
    card_holder="JUAN PEREZ"
)
card_token = token_response['data']['id']
```

**Paso 2: Crear transacción**
```python
payment_method = WompiClient.build_card_payment_method(
    token=card_token,
    installments=1  # Número de cuotas (1-36)
)

transaction = client.create_transaction(
    amount_in_cents=5000000,
    currency="COP",
    customer_email="cliente@example.com",
    payment_method=payment_method,
    reference="CUOTA-001-2024",
    acceptance_token=acceptance_token,
    redirect_url="https://tu-app.com/payment/callback"
)

# Verificar estado
if transaction['data']['status'] == 'APPROVED':
    # Pago exitoso
    pass
```

---

### 2. PSE (Débito Bancario)

**Paso 1: Obtener lista de bancos**
```python
banks = client.get_pse_financial_institutions()
# Retorna: [
#   {"financial_institution_code": "1051", "financial_institution_name": "Bancolombia"},
#   {"financial_institution_code": "1001", "financial_institution_name": "Banco de Bogotá"},
#   ...
# ]
```

**Paso 2: Crear transacción**
```python
payment_method = WompiClient.build_pse_payment_method(
    financial_institution_code="1051",  # Código del banco
    user_type=0,  # 0 = Persona Natural, 1 = Persona Jurídica
    user_legal_id_type="CC",  # CC, CE, NIT, TI, PP
    user_legal_id="1234567890",
    payment_description="Pago de cuota"  # Máximo 30 caracteres
)

customer_data = WompiClient.build_customer_data(
    phone_number="573001234567",  # Con código país (57)
    full_name="Juan Perez"
)

transaction = client.create_transaction(
    amount_in_cents=5000000,
    currency="COP",
    customer_email="cliente@example.com",
    payment_method=payment_method,
    reference="CUOTA-001-2024",
    acceptance_token=acceptance_token,
    customer_data=customer_data,  # OBLIGATORIO para PSE
    redirect_url="https://tu-app.com/payment/callback"
)

# Redirigir al usuario
redirect_to = transaction['data']['payment_method']['extra']['async_payment_url']
```

---

### 3. Nequi

```python
payment_method = WompiClient.build_nequi_payment_method(
    phone_number="3001234567"  # Sandbox: 3991111111 (APPROVED)
)

transaction = client.create_transaction(
    amount_in_cents=5000000,
    currency="COP",
    customer_email="cliente@example.com",
    payment_method=payment_method,
    reference="CUOTA-001-2024",
    acceptance_token=acceptance_token,
    redirect_url="https://tu-app.com/payment/callback"
)

# Redirigir al usuario
redirect_to = transaction['data']['payment_method']['extra']['async_payment_url']
```

---

### 4. Bancolombia Transfer (Botón Bancolombia)

```python
payment_method = WompiClient.build_bancolombia_transfer_payment_method(
    payment_description="Pago de cuota mensual"  # Máximo 64 caracteres
)

transaction = client.create_transaction(
    amount_in_cents=5000000,
    currency="COP",
    customer_email="cliente@example.com",
    payment_method=payment_method,
    reference="CUOTA-001-2024",
    acceptance_token=acceptance_token,
    redirect_url="https://tu-app.com/payment/callback"
)

# Redirigir al usuario
redirect_to = transaction['data']['payment_method']['extra']['async_payment_url']
```

---

### 5. Consultar Estado de Transacción

```python
transaction_info = client.get_transaction("1234-1610641025-49201")
status = transaction_info['data']['status']
# Posibles valores: PENDING, APPROVED, DECLINED, VOIDED, ERROR
```

---

## 🎨 Frontend (Templates HTML)

### Template Completo con Botones Individuales

Ver archivo completo en `INTEGRACION_WOMPI_ESENCIAL.md` (líneas 209-507) o crear tu propio template con esta estructura:

**Estructura básica:**
```html
<div class="payment-methods">
    <!-- Botones para seleccionar método -->
    <label class="payment-method" data-method="card">
        <input type="radio" name="payment_method" value="CARD">
        <i class="fas fa-credit-card"></i>
        <span>Tarjeta</span>
    </label>

    <label class="payment-method" data-method="pse">
        <input type="radio" name="payment_method" value="PSE">
        <i class="fas fa-university"></i>
        <span>PSE</span>
    </label>

    <label class="payment-method" data-method="nequi">
        <input type="radio" name="payment_method" value="NEQUI">
        <i class="fas fa-mobile-alt"></i>
        <span>Nequi</span>
    </label>

    <label class="payment-method" data-method="bancolombia">
        <input type="radio" name="payment_method" value="BANCOLOMBIA_TRANSFER">
        <i class="fas fa-building"></i>
        <span>Bancolombia</span>
    </label>
</div>

<!-- Formularios específicos para cada método -->
<div id="cardForm" class="payment-form">
    <!-- Campos de tarjeta -->
</div>

<div id="pseForm" class="payment-form">
    <!-- Campos PSE -->
</div>

<div id="nequiForm" class="payment-form">
    <!-- Campo teléfono Nequi -->
</div>

<div id="bancolombiaForm" class="payment-form">
    <!-- Sin campos adicionales -->
</div>
```

**JavaScript para cambiar entre métodos:**
```javascript
document.querySelectorAll('.payment-method').forEach(method => {
    method.addEventListener('click', function() {
        // Remover active
        document.querySelectorAll('.payment-method').forEach(m => m.classList.remove('active'));
        document.querySelectorAll('.payment-form').forEach(f => f.classList.remove('active'));

        // Activar seleccionado
        this.classList.add('active');
        const methodType = this.dataset.method;
        document.getElementById(methodType + 'Form').classList.add('active');
    });
});
```

---

## 🔧 Backend (Vistas Django)

### Vista Principal de Procesamiento

```python
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .services.wompi_client import WompiClient, WompiAPIException

@require_http_methods(["POST"])
def process_payment(request):
    """Procesa el pago según el método seleccionado"""
    client = WompiClient()

    try:
        # Datos comunes
        payment_method_type = request.POST.get('payment_method')
        amount_in_cents = int(request.POST.get('amount_in_cents'))
        reference = request.POST.get('reference')
        customer_email = request.POST.get('customer_email')

        # Obtener acceptance token
        acceptance_response = client.get_acceptance_token()
        acceptance_token = acceptance_response['data']['presigned_acceptance']['acceptance_token']

        # URL de callback
        redirect_url = request.build_absolute_uri('/payment/callback/')

        # Construir payment_method según el tipo
        if payment_method_type == 'CARD':
            # Tokenizar tarjeta
            card_token_response = client.tokenize_card(
                card_number=request.POST.get('card_number').replace(' ', ''),
                cvc=request.POST.get('cvc'),
                exp_month=request.POST.get('exp_month'),
                exp_year=request.POST.get('exp_year'),
                card_holder=request.POST.get('card_holder')
            )
            card_token = card_token_response['data']['id']

            payment_method = WompiClient.build_card_payment_method(
                token=card_token,
                installments=int(request.POST.get('installments', 1))
            )
            customer_data = None

        elif payment_method_type == 'PSE':
            payment_method = WompiClient.build_pse_payment_method(
                financial_institution_code=request.POST.get('financial_institution_code'),
                user_type=int(request.POST.get('user_type')),
                user_legal_id_type=request.POST.get('user_legal_id_type'),
                user_legal_id=request.POST.get('user_legal_id'),
                payment_description=f"Pago {reference}"
            )
            customer_data = WompiClient.build_customer_data(
                phone_number=f"57{request.POST.get('phone_number')}",
                full_name=request.POST.get('full_name')
            )

        elif payment_method_type == 'NEQUI':
            payment_method = WompiClient.build_nequi_payment_method(
                phone_number=request.POST.get('nequi_phone')
            )
            customer_data = None

        elif payment_method_type == 'BANCOLOMBIA_TRANSFER':
            payment_method = WompiClient.build_bancolombia_transfer_payment_method(
                payment_description=f"Pago {reference}"
            )
            customer_data = None

        # Crear transacción
        transaction = client.create_transaction(
            amount_in_cents=amount_in_cents,
            currency='COP',
            customer_email=customer_email,
            payment_method=payment_method,
            reference=reference,
            acceptance_token=acceptance_token,
            redirect_url=redirect_url,
            customer_data=customer_data
        )

        # Guardar transaction_id en sesión
        request.session['wompi_transaction_id'] = transaction['data']['id']

        # Redirigir según el método
        if payment_method_type in ['PSE', 'NEQUI', 'BANCOLOMBIA_TRANSFER']:
            async_url = transaction['data']['payment_method']['extra']['async_payment_url']
            return redirect(async_url)

        # Si es tarjeta, verificar estado
        if transaction['data']['status'] == 'APPROVED':
            return redirect('/payment/success/')
        elif transaction['data']['status'] == 'DECLINED':
            return redirect('/payment/failed/')
        else:
            return redirect('/payment/pending/')

    except WompiAPIException as e:
        return render(request, 'payment_error.html', {
            'error': str(e),
            'status_code': e.status_code
        })
```

### Vista de Callback

```python
@require_http_methods(["GET"])
def payment_callback(request):
    """Callback después del pago"""
    transaction_id = request.GET.get('id') or request.session.get('wompi_transaction_id')

    if not transaction_id:
        return redirect('/payment/error/')

    client = WompiClient()

    try:
        transaction = client.get_transaction(transaction_id)
        status = transaction['data']['status']

        if status == 'APPROVED':
            # Actualizar tu BD: marcar cuota como pagada
            return redirect('/payment/success/')
        elif status == 'DECLINED':
            return redirect('/payment/failed/')
        else:
            return redirect('/payment/pending/')

    except WompiAPIException as e:
        return redirect('/payment/error/')
```

### API Endpoint para Bancos PSE

```python
@require_http_methods(["GET"])
def get_pse_banks(request):
    """Retorna lista de bancos PSE"""
    client = WompiClient()
    try:
        banks = client.get_pse_financial_institutions()
        return JsonResponse(banks, safe=False)
    except WompiAPIException as e:
        return JsonResponse({'error': str(e)}, status=500)
```

---

## 🎣 Webhooks

Los webhooks son notificaciones que WOMPI envía cuando cambia el estado de una transacción.

### Implementación del Webhook

```python
import hashlib
import json
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.conf import settings

@csrf_exempt
@require_http_methods(["POST"])
def wompi_webhook(request):
    """
    Recibe notificaciones de WOMPI sobre cambios en transacciones
    Documentación: https://docs.wompi.co/docs/colombia/eventos/
    """
    try:
        payload = json.loads(request.body)

        # 1. VALIDAR FIRMA (MUY IMPORTANTE)
        signature_data = payload.get('signature', {})
        checksum_received = signature_data.get('checksum')
        properties = signature_data.get('properties', [])
        timestamp = payload.get('timestamp')
        event = payload.get('event')
        data = payload.get('data', {})

        # Reconstruir string para validar firma
        concat_parts = []
        for prop in properties:
            keys = prop.split('.')
            value = data
            for key in keys:
                value = value.get(key, '')
            concat_parts.append(str(value))

        concat_string = ''.join(concat_parts) + str(timestamp) + settings.WOMPI_EVENTS_SECRET
        expected_checksum = hashlib.sha256(concat_string.encode()).hexdigest()

        # Validar firma
        if checksum_received != expected_checksum:
            return JsonResponse({'error': 'Invalid signature'}, status=401)

        # 2. PROCESAR EVENTO
        if event == 'transaction.updated':
            transaction_id = data.get('transaction', {}).get('id')
            status = data.get('transaction', {}).get('status')

            # Actualizar tu BD aquí
            if status == 'APPROVED':
                # Marcar cuota como pagada
                pass
            elif status == 'DECLINED':
                # Marcar cuota como rechazada
                pass

        return JsonResponse({'status': 'received'}, status=200)

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)
```

### URLs

```python
from django.urls import path

urlpatterns = [
    path('payment/form/', payment_form_view, name='payment_form'),
    path('payment/process/', process_payment, name='process_payment'),
    path('payment/callback/', payment_callback, name='payment_callback'),
    path('api/pse-banks/', get_pse_banks, name='get_pse_banks'),
    path('webhook/wompi/', wompi_webhook, name='wompi_webhook'),
]
```

### Configurar Webhook en WOMPI

1. Ir al dashboard de WOMPI
2. Configuración → Eventos
3. Agregar URL: `https://tu-app.com/webhook/wompi/`
4. Seleccionar evento: `transaction.updated`

---

## 🧪 Datos de Prueba (Sandbox)

### Tarjetas
- **APROBADA:** `4242424242424242`
- **RECHAZADA:** `4111111111111111`
- **CVC:** Cualquier 3 dígitos (ej: `123`)
- **Fecha:** Cualquier fecha futura (ej: `12/29`)
- **Titular:** Cualquier nombre

### Nequi
- **APROBADO:** `3991111111`
- **RECHAZADO:** `3992222222`

### PSE
- **Banco APROBADO:** Código `1`
- **Banco RECHAZADO:** Código `2`
- **Tipo Persona:** 0 (Natural) o 1 (Jurídica)
- **Tipo Documento:** CC
- **Número Documento:** `1234567890`

### Datos Genéricos
- **Email:** `test@example.com`
- **Nombre:** `JUAN PEREZ`
- **Teléfono:** `3001234567` (agregar 57 para formato internacional)

---

## 🚨 Troubleshooting

### Error: "Invalid signature"
**Causa:** `WOMPI_INTEGRITY_KEY` incorrecta o no configurada

**Solución:** Verificar que la llave en `.env` coincida con la del dashboard de WOMPI

---

### Error: "Bloqueado por WAF"
**Causa:** WOMPI bloquea solicitudes sospechosas

**Solución:**
- Verificar que los headers estén correctos (Origin, Referer)
- Cambiar de red o esperar unos minutos
- Asegurarse de usar las llaves correctas

---

### Tarjeta no tokeniza
**Causa:** Tokenización debe hacerse desde el frontend

**Solución:** Nunca enviar número de tarjeta completo al backend, tokenizar en JavaScript primero

---

### PSE sin customer_data
**Causa:** PSE requiere datos del cliente obligatoriamente

**Solución:** Siempre enviar `customer_data` con nombre y teléfono para PSE

---

### Montos incorrectos
**Causa:** Montos deben estar en centavos

**Solución:** Multiplicar el monto en pesos por 100
- Ejemplo: $50.000 COP = 5000000 centavos

---

## ✅ Checklist de Implementación

- [ ] Copiar `wompi_client.py` a tu proyecto
- [ ] Configurar variables en `settings.py`
- [ ] Agregar variables a `.env`
- [ ] Obtener llaves de WOMPI (sandbox)
- [ ] Crear vistas para procesar pagos
- [ ] Crear template HTML con botones
- [ ] Configurar URLs
- [ ] Probar con datos de sandbox
- [ ] Implementar webhook (opcional pero recomendado)
- [ ] Probar todos los métodos de pago
- [ ] Configurar llaves de producción
- [ ] Probar en producción

---

## 💡 Casos de Uso

### Para Fintech (Pago de Cuotas)
```python
# Procesar pago de cuota mensual con PSE
payment_method = WompiClient.build_pse_payment_method(
    financial_institution_code="1051",
    user_type=0,
    user_legal_id_type="CC",
    user_legal_id="1234567890",
    payment_description="Cuota mes 3/12"
)

customer_data = WompiClient.build_customer_data(
    phone_number="573001234567",
    full_name="Juan Perez"
)

transaction = client.create_transaction(
    amount_in_cents=150000000,  # $1,500,000
    currency="COP",
    customer_email="juan@example.com",
    payment_method=payment_method,
    reference=f"CUOTA-{loan_id}-{month}",
    acceptance_token=acceptance_token,
    customer_data=customer_data,
    redirect_url="https://fintech.com/cuotas/confirmacion/"
)
```

### Para SaaS (Suscripciones)
```python
# Cobro mensual con tarjeta
payment_method = WompiClient.build_card_payment_method(
    token=card_token,
    installments=1
)

transaction = client.create_transaction(
    amount_in_cents=4999900,  # $49,999
    currency="COP",
    customer_email="usuario@example.com",
    payment_method=payment_method,
    reference=f"SUB-{user_id}-{datetime.now().strftime('%Y%m')}",
    acceptance_token=acceptance_token,
    redirect_url="https://saas.com/subscription/success/"
)
```

---

## 📚 Referencias

### Documentación Oficial
- **Docs WOMPI:** https://docs.wompi.co/docs/colombia/metodos-de-pago/
- **API Reference:** https://app.swaggerhub.com/apis-docs/waybox/wompi/1.2.0
- **Datos de prueba:** https://docs.wompi.co/docs/colombia/datos-de-prueba-en-sandbox/
- **Dashboard WOMPI:** https://comercios.wompi.co

### Archivos del Proyecto
- `gestion_creditos/services/wompi_client.py` - Cliente Python completo
- `Flujo de Pago.md` - Arquitectura completa del sistema de pagos
- `PENDIENTES.md` - Tareas y mejoras futuras

### Soporte
Si encuentras problemas:
1. Revisar logs del cliente (`WompiClient` usa `logging`)
2. Verificar que las llaves sean correctas
3. Consultar esta documentación
4. Leer la documentación oficial de WOMPI

---

**¡Listo para usar! 🚀**

Desarrollado para el Sistema Aprobado - Fintech de Créditos
