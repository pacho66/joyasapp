from decimal import Decimal
from productos.models import Categoria, CarritoItem
from productos.models import Perfil

def categorias_menu(request):
    # 1. Valores base iniciales
    cantidad_carrito = 0
    total_carrito = Decimal('0.00')
    
    # 🎯 Obtenemos el perfil directamente del middleware de subdominios
    perfil = getattr(request, 'tenant_perfil', None)

    # 2. Lógica de Categorías limpia y segura
    if request.user.is_authenticated:
        categorias = Categoria.objects.filter(usuario=request.user)
        if not perfil and hasattr(request.user, 'perfil'):
            perfil = request.user.perfil
    else:
        if perfil:
            categorias = Categoria.objects.filter(usuario=perfil.user).order_by('nombre')
        else:
            categorias = Categoria.objects.none()
            # 🚫 Eliminado Perfil.objects.first() para evitar cruce de datos en joyasapp.com

    # 3. RÉPLICA EXACTA DE TU LOGICA DE CARRITO
    session_key = request.session.session_key
    if session_key:
        items = CarritoItem.objects.filter(session_key=session_key)

        # Contador Total Global
        for item in items:
            cantidad_carrito += item.cantidad or 0

        # Definir Nivel Global según tus reglas de escala
        if cantidad_carrito >= 12:
            tipo_global = "Mayorista"
        elif cantidad_carrito >= 6:
            tipo_global = "Semi-Mayorista"
        else:
            tipo_global = "Detal"

        # Calcular Totales y Subtotales de cada Item de forma global
        for item in items:
            cantidad = item.cantidad or 0
            producto = item.producto

            # A) Venta por Gramo
            if producto.tipo_venta == "gramo":
                peso = producto.peso_producto or 0
                total_gramos = Decimal(cantidad) * Decimal(peso)

                if tipo_global == "Mayorista":
                    precio = producto.precio_por_gramo_mayor or 0
                elif tipo_global == "Semi-Mayorista":
                    precio = producto.precio_por_gramo_semimayor or 0
                else:
                    precio = producto.precio_por_gramo_detal or 0

                subtotal_calculado = total_gramos * Decimal(precio)

            # B) Venta por Unidades
            else:
                if tipo_global == "Mayorista":
                    precio = producto.precio_mayor or 0
                elif tipo_global == "Semi-Mayorista":
                    precio = producto.precio_semimayor or 0
                else:
                    precio = producto.precio_detal or 0

                subtotal_calculado = Decimal(cantidad) * Decimal(precio)

            total_carrito += subtotal_calculado

    # 4. Retorno de variables globales
    return {
        'categorias_menu': categorias,
        'cantidad_carrito': cantidad_carrito,
        'total_carrito': total_carrito,
        'perfil': perfil,
    }
