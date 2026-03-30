Funciones implementadas en gestion_creditos/services.py:
calcular_cuotas_restantes(credito) - Calcula cuotas pendientes
generar_plan_pagos_actual(credito) - Genera JSON con plan actual
calcular_plan_con_abono(credito, monto_abono, tipo_abono) - Calcula nuevo plan después del abono
calcular_cuota_fija(capital, tasa_mensual, num_cuotas) - Cálculo de cuota con amortización francesa
calcular_ahorro_intereses(credito, monto_abono, tipo_abono) - Calcula ahorro en intereses
analizar_abono_credito(credito, monto_abono, tipo_abono) - Análisis completo del abono
aplicar_abono_credito(credito, monto_abono, tipo_abono, usuario, referencia_pago) - Aplica el abono con transacciones atómicas
_recalcular_amortizacion_por_capital(credito, plan_nuevo) - Recalcula tabla para abonos a capital
_marcar_cuotas_pagadas(credito, monto_abono, pago) - Marca cuotas pagadas para abonos normales/mayores