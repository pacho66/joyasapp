# =========================================================================
# 🐍 LIBRERÍAS ESTÁNDAR
# =========================================================================
import json
from decimal import Decimal

# =========================================================================
# 📡 LOGGER
# =========================================================================
import logging

# =========================================================================
# ⚡ DJANGO
# =========================================================================
from django.contrib import messages
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404

# =========================================================================
# 🏪 MODELOS
# =========================================================================
from ..models import (
    Producto,
    CarritoItem,
)

logger = logging.getLogger(__name__)

# =========================================================================
# 🛒 BLOQUE 4: GESTIÓN DEL CARRITO DE COMPRAS (SESIÓN O BASE DE DATOS)
# =========================================================================
       
def cantidad_carrito(request):
    session_key = request.session.session_key

    if not session_key:
        return {'cantidad_carrito': 0}

    items = CarritoItem.objects.filter(session_key=session_key)

    cantidad = sum(item.cantidad for item in items)

    return {'cantidad_carrito': cantidad}

def ver_carrito(request):
    session_key = request.session.session_key
    if not session_key:
        items = []
    else:
        items = CarritoItem.objects.filter(session_key=session_key).select_related('producto', 'variante')

    # 1. Contamos el total de artículos físicos combinados para definir la escala comercial
    total_articulos = sum(item.cantidad or 0 for item in items)

    if total_articulos >= 12:
        tipo_global = "Mayorista"
    elif total_articulos >= 6:
        tipo_global = "Semi-Mayorista"
    else:
        tipo_global = "Detal"

    total = Decimal('0.00')

    # 2. Procesamos cada artículo de forma segura
    for item in items:
        # Sincronizamos el nivel comercial en la memoria del objeto
        item._tipo_global = tipo_global

        # Calculamos el subtotal ejecutando el método original sin pisar su nombre
        item.subtotal_calculado = item.subtotal()
        
        if item.producto.tipo_venta == "gramo":
            item.total_gramos = Decimal(str(item.cantidad or 0)) * Decimal(str(item.producto.peso_producto or 0))
        else:
            item.total_gramos = None

        item.tipo_precio = tipo_global
        total += item.subtotal_calculado
    
    # Indicadores de progreso para las barras informativas en el frontend
    faltan_semi = max(0, 6 - total_articulos)
    faltan_mayor = max(0, 12 - total_articulos)

    # Convertimos a tipos nativos para el script de WhatsApp (se mantiene idéntico y seguro)
    carrito_json = json.dumps([
        {
            "nombre": item.producto.nombre,
            "cantidad": int(item.cantidad or 0),
            "subtotal": float(item.subtotal_calculado),
            "total_gramos": float(item.total_gramos) if item.total_gramos else None
        } for item in items
    ], cls=DjangoJSONEncoder)
    
    return render(request, 'carrito.html', {
        'items': items,
        'total': total,
        'total_articulos': total_articulos,
        'faltan_semi': faltan_semi,
        'faltan_mayor': faltan_mayor,
        'carrito_json': carrito_json
    })

def agregar_al_carrito(request, producto_id):
    if request.method == 'POST':
        producto = get_object_or_404(Producto, id=producto_id)
        
        color_recibido = request.POST.get('color')
        talla_recibido = request.POST.get('talla')
        
        try:
            cantidad = int(request.POST.get('cantidad', 1))
            if cantidad < 1:
                cantidad = 1
        except (ValueError, TypeError):
            cantidad = 1

        color = None if not color_recibido or str(color_recibido).strip() in ['None', ''] else str(color_recibido).strip()
        talla = None if not talla_recibido or str(talla_recibido).strip() in ['None', ''] else str(talla_recibido).strip()

        # 🔥 LÓGICA HÍBRIDA SaaS: Validar si el producto requiere variantes o es simple/por gramos
        variante = None
        
        # Si el producto se vende por unidades y tiene variantes configuradas
        if producto.tipo_venta != 'gramo' and producto.variantes.exists():
            filtros = Q(producto=producto)
            if color:
                filtros &= Q(color=color)
            else:
                filtros &= Q(color__isnull=True) | Q(color="")

            if talla:
                filtros &= Q(talla=talla)
            else:
                filtros &= Q(talla__isnull=True) | Q(talla="")

            variante = producto.variantes.filter(filtros).first()

            if not variante:
                messages.error(request, "La combinación seleccionada no está disponible en este momento.")
                return redirect('detalle_producto', id=producto.id, slug=producto.slug)

            # Control de stock basado en la variante
            if variante.stock < cantidad:
                messages.error(request, f"Lo sentimos, solo quedan {variante.stock} unidades disponibles de esta opción.")
                return redirect('detalle_producto', id=producto.id, slug=producto.slug)
        else:
            # Si es por gramos o producto simple, controlamos el stock desde el Producto base
            if producto.stock < cantidad:
                messages.error(request, f"Lo sentimos, solo quedan {producto.stock} unidades disponibles.")
                return redirect('detalle_producto', id=producto.id, slug=producto.slug)

        if not request.session.session_key:
            request.session.create()
        session_key = request.session.session_key

        # 🔥 GUARDADO SEGURO: 'variante' ahora puede ser None de forma legal
        item, created = CarritoItem.objects.get_or_create(
            session_key=session_key,
            producto=producto,
            variante=variante, 
            defaults={'cantidad': cantidad}
        )

        if not created:
            stock_limite = variante.stock if variante else producto.stock
            if item.cantidad + cantidad > stock_limite:
                messages.error(request, f"No puedes agregar más unidades. Ya tienes {item.cantidad} en tu carrito.")
                return redirect('detalle_producto', id=producto.id, slug=producto.slug)
            
            item.cantidad += cantidad
            item.save()

        messages.success(request, f"¡{producto.nombre} se agregó al carrito!")
        return redirect('ver_carrito')

    try:
        producto_aux = Producto.objects.get(id=producto_id)
        return redirect('detalle_producto', id=producto_aux.id, slug=producto_aux.slug)
    except Producto.DoesNotExist:
        return redirect('inicio')

def aumentar_cantidad(request, item_id):
    # 1. Capturar la sesión del visitante (anonimo o registrado)
    session_key = request.session.session_key
    if not session_key:
        return redirect('ver_carrito')

    # 2. Buscar el ítem asegurando que pertenezca a este visitante
    item = get_object_or_404(
        CarritoItem,
        id=item_id,
        session_key=session_key
    )

    # 3. 🔥 CONTROL DE STOCK DESDE LA VARIANTE UNIFICADA
    # Si el ítem tiene una variante asignada, usamos ese stock; si no, el del producto global
    stock_disponible = item.variante.stock if item.variante else item.producto.stock

    if item.cantidad >= stock_disponible:
        messages.warning(request, f"No puedes agregar más unidades. El inventario máximo para esta opción es de {stock_disponible} unidades.")
    else:
        item.cantidad += 1
        item.save()

    return redirect('ver_carrito')

def disminuir_cantidad(request, item_id):
    # 1. Capturar la sesión del visitante
    session_key = request.session.session_key
    if not session_key:
        return redirect('ver_carrito')

    # 2. Buscar el ítem asignado a esta sesión
    item = get_object_or_404(
        CarritoItem,
        id=item_id,
        session_key=session_key
    )

    # 3. Restar o eliminar si llega a cero
    if item.cantidad > 1:
        item.cantidad -= 1
        item.save()
    else:
        item.delete()
        messages.info(request, "Producto removido del carrito.")

    return redirect('ver_carrito')

def eliminar_del_carrito(request, item_id):
    # 1. Capturar la sesión del visitante
    session_key = request.session.session_key
    if not session_key:
        return redirect('ver_carrito')

    # 2. Buscar y destruir de forma segura
    item = get_object_or_404(
        CarritoItem,
        id=item_id,
        session_key=session_key
    )
    
    nombre_producto = item.producto.nombre
    item.delete()
    
    messages.success(request, f"Se eliminó '{nombre_producto}' del carrito.")
    return redirect('ver_carrito')