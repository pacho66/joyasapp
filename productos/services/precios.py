from decimal import Decimal

def calcular_precio_producto(producto, cantidad):
    """
    Motor único de precios del sistema
    NO depende de carrito, PDF ni admin
    """

    cantidad = Decimal(cantidad)

    # =========================
    # 🔵 GRAMOS
    # =========================
    if producto.tipo_venta == 'gramo':

        if not producto.peso_producto:
            return Decimal('0'), Decimal('0')

        total_gramos = cantidad * producto.peso_producto

        if total_gramos >= 12:
            precio = producto.precio_por_gramo_mayor or Decimal('0')
        elif total_gramos >= 6:
            precio = producto.precio_por_gramo_semimayor or Decimal('0')
        else:
            precio = producto.precio_por_gramo_detal or Decimal('0')

        return precio, total_gramos

    # =========================
    # 🟡 UNIDADES
    # =========================
    if cantidad >= 12:
        precio = producto.precio_mayor or Decimal('0')
    elif cantidad >= 6:
        precio = producto.precio_semimayor or producto.precio_detal or Decimal('0')
    else:
        precio = producto.precio_detal or Decimal('0')

    return precio, None