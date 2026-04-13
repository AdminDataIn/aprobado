# Flujo de Pago (WOMPI) - Emprendimiento

Esta guia documenta el flujo de pago para clientes de emprendimiento
integrado con WOMPI. Incluye arquitectura, endpoints, ejecucion y
pasos de validacion.

## 1) Arquitectura

Componentes y responsabilidades:
- Template dashboard: expone el boton "Realizar Pago" y redirige al flujo WOMPI.
- Vista de inicio: prepara datos (token de aceptacion, bancos PSE).
- Vista de procesamiento: crea la transaccion en WOMPI.
- Callback: consulta estado y registra el pago.
- Servicios: WompiClient y actualizacion de saldo.

Archivos clave:
- templates/usuariocreditos/dashboard_emprendimiento.html
- templates/usuariocreditos/pago_wompi_emprendimiento.html
- usuarios/urls_emprendimiento.py
- gestion_creditos/views.py
- gestion_creditos/services/wompi_client.py

## 2) Endpoints (namespace emprendimiento)

GET  /emprendimiento/mi-credito/<credito_id>/pago/wompi/
POST /emprendimiento/mi-credito/pago/wompi/procesar/
GET  /emprendimiento/mi-credito/pago/wompi/callback/

## 3) Flujo de ejecucion

1. Dashboard
   - Boton "Realizar Pago" lleva al endpoint de inicio.

2. Inicio
   - Valida que el credito es EMPRENDIMIENTO y pertenece al usuario.
   - Obtiene acceptance_token y bancos PSE desde WOMPI.
   - Renderiza el formulario de pago.

3. Procesamiento
   - Lee metodo (CARD, PSE, NEQUI, BANCOLOMBIA_TRANSFER).
   - Crea payment_method y transaccion WOMPI.
   - Si es async, redirige a WOMPI.
   - Si es tarjeta:
     - Si APPROVED: registra HistorialPago y actualiza saldo.
     - Si DECLINED/PENDING: informa al usuario.

4. Callback
   - WOMPI redirige con el id de transaccion.
   - Se consulta estado en WOMPI.
   - Si APPROVED: registra HistorialPago y actualiza saldo.
   - Limpia la sesion y redirige al detalle del credito.

## 4) Registro de pagos

Cuando una transaccion queda APPROVED:
- Se crea HistorialPago con referencia y monto.
- Se ejecuta services.actualizar_saldo_tras_pago(credito, monto_decimal).

## 5) Datos y validaciones

Validaciones de seguridad:
- El credito debe ser de linea EMPRENDIMIENTO.
- El credito debe pertenecer al usuario autenticado.

Campos relevantes (POST):
- payment_method
- credito_id
- amount_in_cents
- reference
- customer_email
- acceptance_token

Campos adicionales segun metodo:
- CARD: card_number, cvc, exp_month, exp_year, card_holder, installments
- PSE: financial_institution_code, user_type, user_legal_id_type, user_legal_id,
       full_name, phone_number
- NEQUI: nequi_phone
- BANCOLOMBIA_TRANSFER: sin campos extra

## 6) Pruebas rapidas (sandbox)

Tarjeta aprobada:
- 4242 4242 4242 4242
- CVC 123
- Fecha 12/29

Nequi:
- Aprobado: 3991111111
- Rechazado: 3992222222

PSE:
- Banco aprobado: codigo 1
- Banco rechazado: codigo 2

## 7) Rutas de prueba manual

1) Abrir formulario:
   /emprendimiento/mi-credito/<credito_id>/pago/wompi/

2) Completar pago (tarjeta):
   - Enviar formulario
   - Verificar redireccion al detalle del credito
   - Confirmar registro en HistorialPago

3) Completar pago (PSE/Nequi/Bancolombia):
   - Se redirige a WOMPI
   - WOMPI retorna al callback
   - Verificar registro en HistorialPago

## 8) Errores comunes

- "No se encontro informacion de la transaccion":
  la sesion no tiene id o WOMPI no devolvio id en callback.

- "Metodo de pago no valido":
  el formulario no envio payment_method o viene vacio.

- "Error al conectar con la pasarela":
  revisar llaves WOMPI y configuracion en settings/.env.
