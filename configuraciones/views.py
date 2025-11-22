from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from configuraciones.models import ConfiguracionPeso
from gestion_creditos.models import Credito, CreditoEmprendimiento
from datetime import datetime
from decimal import Decimal
from django.core.exceptions import ObjectDoesNotExist
import logging
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
logger = logging.getLogger(__name__)
from openai import OpenAI
from django.conf import settings


@csrf_exempt
@require_POST
def recibir_data(request):
    if request.method == 'POST':
        if not request.user.is_authenticated:
            return JsonResponse({'success': False, 'error': 'Authentication required'}, status=403)

        try:
            # Capturar y convertir los valores
            valor_credito = Decimal(request.POST.get('valor_cred', '0'))
            plazo = int(request.POST.get('plazo', '0'))
            nombre = request.POST.get('nombre', '').strip()
            numero_cedula = request.POST.get('numero_cedula', '').strip()
            fecha_nac_str = request.POST.get('fecha_nac', '')
            print("fecha_nac_str: ", fecha_nac_str)

            if not fecha_nac_str:
                return JsonResponse({'success': False, 'error': 'La fecha de nacimiento es obligatoria'}, status=400)

            try:
                fecha_nac = datetime.strptime(fecha_nac_str, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({'success': False, 'error': 'Formato de fecha inválido'}, status=400)

            celular_wh = request.POST.get('celular_wh', '').strip()
            direccion = request.POST.get('direccion', '').strip()
            estado_civil = request.POST.get('estado_civil', '').strip()
            numero_personas_cargo = int(request.POST.get('numero_personas_cargo', '0'))

            # Información del negocio
            nombre_negocio = request.POST.get('nombre_negocio', '').strip()
            ubicacion_negocio = request.POST.get('ubicacion_negocio', '').strip()
            tiempo_operando = request.POST.get('tiempo_operando', '').strip()
            dias_trabajados_sem = int(request.POST.get('dias_trabajados_sem', '0'))
            prod_serv_ofrec = request.POST.get('prod_serv_ofrec', '').strip()
            ingresos_prom_mes = request.POST.get('ingresos_prom_mes', '').strip()
            cli_aten_day = int(request.POST.get('cli_aten_day', '0'))
            inventario = request.POST.get('inventario', '').strip()

            # Referencias
            nomb_ref_per1 = request.POST.get('nomb_ref_per1', '').strip()
            cel_ref_per1 = request.POST.get('cel_ref_per1', '').strip()
            rel_ref_per1 = request.POST.get('rel_ref_per1', '').strip()
            nomb_ref_cl1 = request.POST.get('nomb_ref_cl1', '').strip()
            cel_ref_cl1 = request.POST.get('cel_ref_cl1', '').strip()
            rel_ref_cl1 = request.POST.get('rel_ref_cl1', '').strip()
            ref_conoc_lid_com = request.POST.get('ref_conoc_lid_com', '').strip()

            # Archivos
            fotos_neg = request.FILES.get('fotos_neg')
            desc_fotos_neg = request.POST.get('desc_fotos_neg', '').strip()

            # Otros campos
            tipo_cta_mno = request.POST.get('tipo_cta_mno', '').strip()
            ahorro_tand_alc = request.POST.get('ahorro_tand_alc', '').strip()
            depend_h = request.POST.get('depend_h', '').strip()
            desc_cred_nec = request.POST.get('desc_cred_nec', '').strip()
            redes_soc = request.POST.get('redes_soc', '').strip()
            fotos_prod = request.POST.get('fotos_prod', '').strip()

            # Evaluar la motivación con ChatGPT
            puntaje_motivacion = evaluar_motivacion_credito(desc_cred_nec)
            print("puntaje_motivacion IA: ", puntaje_motivacion)

            # Validación del archivo PDF
            if fotos_neg:
                if not fotos_neg.name.lower().endswith('.pdf'):
                    return JsonResponse({'success': False, 'error': 'El archivo debe ser un PDF'}, status=400)

                if fotos_neg.content_type != 'application/pdf':
                    return JsonResponse({'success': False, 'error': 'Tipo de archivo no permitido'}, status=400)

                if fotos_neg.size > 5 * 1024 * 1024:
                    return JsonResponse({'success': False, 'error': 'El archivo no debe exceder 5MB'}, status=400)

                datos = {
                    # 'id_usuario': request.POST.get('id_usuario'),
                    'Tiempo_operando': request.POST.get('tiempo_operando'),
                    'Actividad_diaria': request.POST.get('dias_trabajados_sem'),
                    'Ubicacion': request.POST.get('ubicacion_negocio'),
                    'Ingresos': request.POST.get('ingresos_prom_mes'),
                    'Herramientas digitales': request.POST.get('tipo_cta_mno'),
                    'Ahorro tandas': request.POST.get('ahorro_tand_alc'),
                    'Dependientes': request.POST.get('depend_h'),
                    'Redes sociales': request.POST.get('redes_soc'),
                }


            # Dividir los datos según las funciones requeridas
            parametros = {k: v for k, v in datos.items() if k in [
                'Tiempo_operando', 'Actividad_diaria', 'Ubicacion', 'Ingresos', 'Herramientas digitales',
                'Ahorro tandas', 'Dependientes', 'Redes sociales'
            ]}

            #suma_estimaciones = obtener_estimacion(parametros)
            #print("Suma estimaciones desde principal: ", suma_estimaciones)

            suma_estimaciones_internas = obtener_estimacion(parametros)
            print("suma estimaciones sin IA: ", suma_estimaciones_internas)
            suma_estimaciones = suma_estimaciones_internas + puntaje_motivacion
            print("Suma estimaciones completas con IA: ", suma_estimaciones)


            # Crear instancia del modelo base Credito
            credito_base = Credito.objects.create(
                usuario=request.user,
                linea=Credito.LineaCredito.EMPRENDIMIENTO,
                estado=Credito.EstadoCredito.EN_REVISION,
                monto_solicitado=valor_credito,
                plazo_solicitado=plazo
            )

            # Crear instancia del modelo de detalle CreditoEmprendimiento
            nuevo_credito_emprendimiento = CreditoEmprendimiento.objects.create(
                credito=credito_base,
                nombre=nombre,
                numero_cedula=numero_cedula,
                fecha_nac=fecha_nac,
                celular_wh=celular_wh,
                direccion=direccion,
                estado_civil=estado_civil,
                numero_personas_cargo=numero_personas_cargo,
                nombre_negocio=nombre_negocio,
                ubicacion_negocio=ubicacion_negocio,
                tiempo_operando=tiempo_operando,
                dias_trabajados_sem=dias_trabajados_sem,
                prod_serv_ofrec=prod_serv_ofrec,
                ingresos_prom_mes=ingresos_prom_mes,
                cli_aten_day=cli_aten_day,
                inventario=inventario,
                nomb_ref_per1=nomb_ref_per1,
                cel_ref_per1=cel_ref_per1,
                rel_ref_per1=rel_ref_per1,
                nomb_ref_cl1=nomb_ref_cl1,
                cel_ref_cl1=cel_ref_cl1,
                rel_ref_cl1=rel_ref_cl1,
                ref_conoc_lid_com=ref_conoc_lid_com,
                desc_fotos_neg=desc_fotos_neg,
                tipo_cta_mno=tipo_cta_mno,
                ahorro_tand_alc=ahorro_tand_alc,
                depend_h=depend_h,
                desc_cred_nec=desc_cred_nec,
                redes_soc=redes_soc,
                fotos_prod=fotos_prod,
                puntaje=suma_estimaciones
            )

            # Guardar archivo después de guardar el objeto
            if fotos_neg:
                nuevo_credito_emprendimiento.foto_negocio.save(fotos_neg.name, fotos_neg)

            return JsonResponse({
                'success': True,
                'suma_estimaciones': suma_estimaciones
                #'message': 'Datos guardados correctamente'
            })

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    else:
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)


def evaluar_motivacion_credito(texto):
    """
    Evalúa la motivación para el crédito usando ChatGPT (OpenAI API v1.0.0+).
    Devuelve un puntaje entre 1 y 5.
    """
    if not texto or len(texto) < 10:
        return 3  # Valor por defecto para textos muy cortos

    try:
        # Inicializa el cliente
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        prompt = f"""
        Evalúa esta justificación para un crédito y asigna un puntaje del 1 al 5:
        - 1: Muy pobre (sin explicación clara)
        - 2: Pobre (explicación vaga)
        - 3: Aceptable (propósito básico claro)
        - 4: Bueno (propósito y motivación claros)
        - 5: Excelente (propósito claro con plan detallado)

        Justificación: "{texto}"

        Responde SOLO con el número del puntaje (1-5), nada más.
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Eres un analista financiero experto en evaluar solicitudes de crédito."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=2
        )

        # Extraer el puntaje de la respuesta
        respuesta = response.choices[0].message.content.strip()
        puntaje = int(respuesta) if respuesta.isdigit() else 3

        return max(1, min(5, puntaje))  # Asegurarse que esté entre 1 y 5

    except Exception as e:
        print(f"Error al evaluar con ChatGPT: {e}")
        return 0  # Valor por defecto en caso de error


@csrf_exempt
def obtener_estimacion(parametros):
    # Lista para almacenar resultados de las estimaciones
    resultados = []

    # Procesar los parámetros dinámicamente
    for parametro, nivel in parametros.items():
        if nivel:  # Solo procesar si el nivel tiene un valor
            try:
                configuracion = ConfiguracionPeso.objects.get(parametro=parametro, nivel=nivel)
                resultados.append({
                    'parametro': parametro,
                    'nivel': nivel,
                    'estimacion': configuracion.estimacion
                })
            except ObjectDoesNotExist:
                # Manejar casos donde no se encuentra el registro
                print(f"No se encontró configuración para {parametro} con nivel {nivel}")
                resultados.append({
                    'parametro': parametro,
                    'nivel': nivel,
                    'estimacion': 'No disponible'
                })

    # Calcular la suma de las estimaciones
    suma_estimaciones = sum(int(r['estimacion']) for r in resultados if isinstance(r['estimacion'], (int, float, Decimal)))

    # Imprimir para verificar el cálculo
    print("Suma total de estimaciones:", suma_estimaciones)

    return suma_estimaciones