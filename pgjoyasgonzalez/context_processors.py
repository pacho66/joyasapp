from decimal import Decimal
from productos.models import Categoria, CarritoItem
from productos.models import Perfil

def categorias_menu(request):
    # 1. Valores base iniciales
    cantidad_carrito = 0
    total_carrito = Decimal('0.00')
    perfil = None

    # 2. Lógica de Categorías (Mantenemos tu bloque exacto)
    if request.user.is_authenticated:
        categorias = Categoria.objects.filter(usuario=request.user)
        # Traemos el perfil del comerciante logueado de forma segura
        if hasattr(request.user, 'perfil'):
            perfil = request.user.perfil
    else:
        categorias = Categoria.objects.none()
        # Fallback para vista pública: intentamos cargar un perfil para no ocultar el botón
        try:
            from .models import Perfil
            perfil = Perfil.objects.first()
        except:
            perfil = None

    # 3. RÉPLICA EXACTA DE TU LOGICA DE CARRITO (Sin alterar nada)
    session_key = request.session.session_key
    if session_key:
        items = CarritoItem.objects.filter(session_key=session_key)

        # Contador Total Global (Paso 1 de tu vista)
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

            # Sumatoria acumulada al total general del Navbar/WhatsApp
            total_carrito += subtotal_calculado

    # 4. Retorno de variables globales a la base.html
    return {
        'categorias_menu': categorias,
        'cantidad_carrito': cantidad_carrito,
        'total_carrito': total_carrito,
        'perfil': perfil,
    }
