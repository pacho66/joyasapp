from decimal import Decimal

def calcular_envio(ciudad, subtotal):

    if not ciudad:
        return Decimal('0')
    
    subtotal = Decimal(subtotal)
    ciudad = ciudad.lower()

    # 🔥 ENVÍO GRATIS
    if subtotal >= Decimal('200000'):
        return Decimal('0')

    tarifas = {
        'medellin': 10000,
        'bogota': 15000,
        'cali': 15000,
        'envigado': 8000,
        'sabaneta': 8000,
        'itagui': 8000,
    }

    return Decimal(tarifas.get(ciudad, 15000))