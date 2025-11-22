# 1. OPERACIONES CON DOS NUMEROS (NO SE PUDE DIVIDIR POR 0)

num1 = int(input("Ingrese el primer numero: "))
num2 = int(input("Ingrese el segundo numero: "))

suma = num1 + num2
print("La suma es: ", suma)
resta = num1 - num2
print("La resta es: ", num1-num2)
multiplicacion = num1 * num2
print(f"La multiplicacion es: {num1 * num2}")

if num2 != 0:
    print("La division es: ", num1/num2)
else:
    print("No se puede dividir por 0")