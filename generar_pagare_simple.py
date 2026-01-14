"""
Script simple para generar un pagaré.
Uso: python generar_pagare_simple.py <numero_credito>
Ejemplo: python generar_pagare_simple.py CR-2025-00004
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aprobado_web.settings')
django.setup()

from gestion_creditos.models import Credito, Pagare
from gestion_creditos.services.pagare_service import generar_pagare_pdf


def generar_pagare(numero_credito):
    """Genera un pagaré para un crédito específico."""

    print("\n" + "="*70)
    print("GENERADOR DE PAGARE - APROBADO")
    print("="*70)

    try:
        # Buscar el crédito
        credito = Credito.objects.get(numero_credito=numero_credito)

        print(f"\n[OK] Credito encontrado: {credito.numero_credito}")
        print(f"    Usuario: {credito.usuario.get_full_name() or credito.usuario.username}")
        print(f"    Estado: {credito.get_estado_display()}")
        print(f"    Monto: ${credito.monto_aprobado or credito.monto_solicitado:,.0f}")
        print(f"    Plazo: {credito.plazo} cuotas")

        # Verificar si ya tiene pagaré
        pagare_existente = Pagare.objects.filter(credito=credito).first()

        if pagare_existente:
            print(f"\n[!] ATENCION: Este credito ya tiene un pagare:")
            print(f"    Numero: {pagare_existente.numero_pagare}")
            print(f"    Estado: {pagare_existente.get_estado_display()}")
            print(f"    PDF: {pagare_existente.archivo_pdf.path if pagare_existente.archivo_pdf else 'N/A'}")
            print(f"\n[...] Eliminando pagare anterior...")
            pagare_existente.delete()

        # Generar el pagaré
        print(f"\n[...] Generando pagare para {credito.numero_credito}...")
        pagare = generar_pagare_pdf(credito)

        print("\n" + "="*70)
        print("[OK] PAGARE GENERADO EXITOSAMENTE!")
        print("="*70)
        print(f"\nNumero de pagare: {pagare.numero_pagare}")
        print(f"Archivo PDF: {pagare.archivo_pdf.path}")
        print(f"Tamano: {os.path.getsize(pagare.archivo_pdf.path) / 1024:.2f} KB")
        print(f"Hash SHA-256: {pagare.hash_pdf}")
        print(f"Estado: {pagare.get_estado_display()}")

        print("\n" + "="*70)
        print(f"Ubicacion del PDF:")
        print(f"   {pagare.archivo_pdf.path}")
        print("="*70 + "\n")

        return True

    except Credito.DoesNotExist:
        print(f"\n[X] ERROR: No se encontro el credito '{numero_credito}'")
        print("\nCreditos disponibles:")

        creditos = Credito.objects.filter(
            linea=Credito.LineaCredito.EMPRENDIMIENTO
        ).order_by('-fecha_solicitud')[:10]

        for c in creditos:
            print(f"  - {c.numero_credito} ({c.get_estado_display()})")

        return False

    except Exception as e:
        print(f"\n[X] ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("\n[X] ERROR: Debes proporcionar el numero de credito")
        print("\nUso: python generar_pagare_simple.py <numero_credito>")
        print("Ejemplo: python generar_pagare_simple.py CR-2025-00004")

        print("\n\nCreditos disponibles:")
        creditos = Credito.objects.filter(
            linea=Credito.LineaCredito.EMPRENDIMIENTO
        ).order_by('-fecha_solicitud')[:10]

        for c in creditos:
            print(f"  - {c.numero_credito} ({c.get_estado_display()})")

        sys.exit(1)

    numero_credito = sys.argv[1]
    success = generar_pagare(numero_credito)
    sys.exit(0 if success else 1)
