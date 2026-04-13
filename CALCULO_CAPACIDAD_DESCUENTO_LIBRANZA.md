# 📊 Cálculo de Capacidad de Descuento

## 💰 Ingresos
| Ítem                          | Valor |
|-------------------------------|--------|
| Salario Básico               | 1,750,905 |
| Auxilio de transporte        | 249,095 |
| Variable promedio (3 meses)  | 100,000 |
| Ajuste variable              | 70,000 |
| **Total Ingresos**           | **2,070,000** |

---

## 📉 Deducciones
| Ítem                     | Valor |
|--------------------------|--------|
| Salud + Pensión          | 140,072 |
| Fondo Solidaridad        | 0 |
| Descuentos libranzas     | 0 |
| Retefuente               | 0 |
| Otros descuentos         | 0 |
| **Total Deducciones**    | **140,072** |

---

## 🧾 Resultado Neto
| Concepto         | Valor |
|------------------|--------|
| **Total Neto**   | **1,929,928** |

---

## ⚖️ Capacidad de Descuento
| Concepto                              | Valor |
|---------------------------------------|--------|
| **Total Capacidad de descuento**      | **441,658** |

---

## 📆 Simulación de Adelanto

| Escenario                | Valor |
|--------------------------|--------|
| Cuota                    | 298,645 |
| Valor diario             | 64,331 |
| Capacidad total          | 441,658 |
| Monto posible            | 143,013 |
| Días calculados          | 2.223088835 |

---

## ✅ Resultado Final

- **Días que puede adelantar:** `2` días *(redondeado hacia abajo)*
- **Monto correspondiente a 2 días:** `143,013 COP`

---

## 🧠 Lógica del cálculo

```python
dias = capacidad_descuento / valor_diario
dias_final = int(dias)  # siempre hacia abajo

monto = dias_final * valor_diario
💡 Notas
Todos los valores están en pesos colombianos (COP).
El cálculo de días siempre se aproxima hacia abajo.
El monto final depende directamente del valor diario.