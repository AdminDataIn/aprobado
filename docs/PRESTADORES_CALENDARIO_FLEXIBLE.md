# Calendario contractual flexible de Prestadores

La configuracion financiera Prestadores V1 conserva tasas, montos, capacidad,
plazos y ultima cuota proyectada. Solo cambia la construccion de flujos cuando
el PDF contiene evidencia admisible. No hay migraciones ni backfill.

## Evidencia admitida

1. **Mes calendario:** conserva el calculo de inicio dia 1/fin ultimo dia y
   las reglas documentales existentes (fin de mes, dia fijo o primeros dias
   habiles del mes siguiente).
2. **Aniversario:** el PDF debe indicar las fechas de vigencia y contener
   una clausula afirmativa: `Calendario: periodos mensuales por aniversario contractual.`
   El dia de inicio es el ancla original. Cada periodo es inclusivo hasta el dia
   anterior al siguiente aniversario; el ultimo debe coincidir con el fin
   contractual. Si el ancla no existe en un mes, se acepta el ultimo dia de ese
   mes solo si el PDF dice `Ajuste de aniversario: ultimo dia del mes sin cambiar el dia ancla.`
   Se recupera el ancla original en el siguiente mes que la tenga.
3. **Vencimiento por periodo:** usar `Pago: al cierre de cada periodo.` o
   `Pago: 5 dias calendario despues del cierre de cada periodo.`
   El numero de dias puede ser de 1 a 99. No se infieren dias habiles, se ajusta
   por festivos ni se interpreta el fin de mes calendario como fin de un aniversario.
4. **Periodos parciales:** usar un listado completo y textual, cada fila en su
   propia linea del PDF, con fechas ISO e importe COP sin separadores de miles:

   ```text
   Calendario de pagos documentado:
   Periodo documentado: 2026-11-18 a 2026-11-30; vence 2026-12-05; valor COP 7800000.00.
   Periodo documentado: 2026-12-01 a 2026-12-31; vence 2027-01-05; valor COP 18000000.00.
   ```

   El parser no interpreta tablas arbitrarias ni combina el listado con otra
   regla reconocida de vencimiento. Debe cubrir toda la vigencia, sin huecos
   ni solapes, con periodos de hasta 31 dias, importes positivos cuya suma coincida
   con el total y un vencimiento por mes, en orden y no anterior al cierre. Si se declara una cantidad de meses,
   no se admiten mas ni menos filas que las mensualidades declaradas.

## Seguridad operativa

Se admiten tambien enunciados completos equivalentes, por ejemplo:
\`Los periodos mensuales se contaran por aniversario desde la fecha de inicio.\`
\`Cada mensualidad corresponde al periodo comprendido entre aniversarios contractuales.\`
\`El pago se realizara al cierre de cada periodo mensual.\`
\`Los honorarios se pagaran el ultimo dia de cada periodo.\`
\`Se pagara cinco (5) dias calendario despues de terminado cada periodo.\`
Los dias en palabras o cifras de 1 a 99 se normalizan al mismo
desplazamiento; si se indican ambos, deben coincidir. \`A periodo vencido\` por si solo,
\`dentro de cinco dias\` y plazos contados desde una aprobacion sin fecha no fijan
un vencimiento. El reconocimiento de enunciados completos no generaliza a
cualquier redaccion contractual ni sustituye la lectura documental.

- Las reglas nuevas se extraen del texto del PDF, no de campos del navegador ni
  de texto inferido por IA. Volver a analizar no basta: el usuario debe confirmar
  el contrato por el flujo vigente; el servidor contrasta el hash del archivo
  actual. La vigencia por aniversario debe coincidir con la extraida del PDF.
  La evidencia detallada queda en los metadatos existentes y vinculada al
  marcador de evidencia que ya participa en la version de evaluacion.
- Sin calendario confirmado y con un contrato no vencido, el simulador puede
  mostrar cifras indicativas con los limites del producto. No se generan flujos
  hipoteticos, no se confirma una simulacion nueva ni se despacha evaluacion.
  Los controles existentes de oferta y plazo siguen requiriendo horizonte soportado.
  Una contradiccion en un calendario detallado no se convierte en una estimacion
  habilitada por ese mismo calendario.
- Se cuentan por separado corte de causacion y vencimiento. Que un flujo sea
  causado o exigible no acredita que se haya pagado. No se altera la semantica
  del saldo pendiente ni la elegibilidad por ingreso neto.

## Ejemplo y pruebas

Con vigencia 18/11/2026 a 17/11/2027, clausula de aniversario y pago al cierre
de cada periodo, el motor genera exactamente doce flujos. El primero abarca
18/11 a 17/12/2026 y el ultimo vence el 17/11/2027. Sin esa regla escrita no se
generan flujos. Las frases muestran el formato tecnico; no autorizan cambiar
las condiciones efectivamente pactadas en un contrato real.

`contractors.tests.test_calendario_contractual_flexible` cubre el calendario
y el flujo Django. Configurar `PRESTADORES_CALENDARIO_BROWSER=edge` o
`webkit` y tener Playwright local para incluir la prueba de navegador.
Los fixtures son sinteticos; no ejecutar contra bases ni proveedores operativos.
