# =========================================================================
# 🐍 LIBRERÍAS ESTÁNDAR
# =========================================================================
from decimal import Decimal
import logging

# =========================================================================
# ⚡ DJANGO
# =========================================================================
from django.db.models import Sum, Q
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required

# =========================================================================
# 🏪 MODELOS
# =========================================================================
from ..models import (
    Perfil,
    Producto,
    Categoria,
    ProductoImagen,
    ProductoVariante,
    PedidoItem,
    CarritoItem,
)

# =========================================================================
# 🧠 SERVICIOS
# =========================================================================
from ..services.precios import calcular_precio_producto

# =========================================================================
# ⚙️ UTILIDADES
# =========================================================================
from .utilidades import (
    safe_int,
    safe_float,
    obtener_variante,
)

logger = logging.getLogger(__name__)

# =========================================================================
# 🛍️ BLOQUE 3: CATÁLOGO PÚBLICO DE PRODUCTOS (VISTA DEL CLIENTE)
# =========================================================================

def inicio(request):
    try:
        # 1. Obtener el perfil de forma segura
        perfil = None
        if request.user.is_authenticated:
            perfil = getattr(request.user, 'perfil', None)
        else:
            perfil = Perfil.objects.first()

        # 2. Consultas seguras excluyendo productos sin slug para evitar roturas en los enlaces {% url %}
        # Nota: Si tu modelo no usa el campo 'activo', Django simplemente usará el filtro del slug.
        if request.user.is_authenticated:
            productos = Producto.objects.filter(usuario=request.user).exclude(slug='').exclude(slug__isnull=True)
            destacados = Producto.objects.filter(usuario=request.user, destacado=True).exclude(slug='').exclude(slug__isnull=True).order_by('-id')[:8]
            categorias = Categoria.objects.filter(usuario=request.user).order_by('nombre')
        else:
            productos = Producto.objects.exclude(slug='').exclude(slug__isnull=True)
            destacados = Producto.objects.filter(destacado=True).exclude(slug='').exclude(slug__isnull=True).order_by('-id')[:8]
            categorias = Categoria.objects.all().order_by('nombre')

        context = {
            'perfil': perfil,
            'productos': productos,
            'destacados': destacados,
            'categorias': categorias,
        }
        return render(request, 'inicio.html', context)

    except Exception as e:
        # Captura el error real en los logs de Render para que sepas exactamente qué columna falló
        logger = logging.getLogger(__name__)
        logger.error(f"Error crítico en la vista inicio: {str(e)}")
        
        # 🛡️ FALLBACK ABSOLUTO: Se envían listas vacías para que el HTML renderice el mensaje {% empty %} sin morir
        return render(request, 'inicio.html', {
            'perfil': None, 
            'productos': [], 
            'destacados': [], 
            'categorias': [],
            'error': str(e)
        })

def buscar_productos(request):
    query = request.GET.get('q', '')

    if request.user.is_authenticated:
        productos = Producto.objects.filter(usuario=request.user)
        categorias = Categoria.objects.filter(usuario=request.user)
    else:
        productos = Producto.objects.none()
        categorias = []

    if query:
        productos = productos.filter(
            Q(nombre__icontains=query) |
            Q(descripcion__icontains=query) |
            Q(categoria__nombre__icontains=query)
        )

    return render(request, 'lista_productos.html', {
        'productos': productos,
        'categorias': categorias,
        'query': query
    })

def detalle_producto(request, id, slug):
    producto = get_object_or_404(
        Producto,
        id=id,
        slug=slug,
    )

    perfil_tienda = Perfil.objects.filter(
        user=producto.usuario
    ).first()

    tiene_variantes = producto.variantes.exists()

    session_key = request.session.session_key
    cantidad_en_carrito = 0

    if session_key:
        item = CarritoItem.objects.filter(
            producto=producto,
            session_key=session_key
        ).first()

        if item:
                cantidad_en_carrito = item.cantidad

    # 🔥 STOCK REAL (VARIANTES)
    tiene_stock = producto.variantes.filter(stock__gt=0).exists()

    # 🔥 SI TIENE VARIANTES, EL STOCK LO MANEJAN ELLAS
    if producto.variantes.exists():
        stock_disponible = None
    else:
        stock_disponible = producto.stock - cantidad_en_carrito

    return render(request, 'detalle_producto.html', {
        'producto': producto,
        'perfil_tienda': perfil_tienda,
        'tiene_variantes': tiene_variantes,
        'stock_disponible': stock_disponible,
        'tiene_stock': tiene_stock
})

@login_required
def productos_por_categoria(request, categoria_id):
    categoria = get_object_or_404(
    Categoria,
    id=categoria_id,
    usuario=request.user
)
    productos = Producto.objects.filter(
        categoria=categoria,
        usuario=request.user
    ).order_by('-id')
    return render(request, 'productos_por_categoria.html', {
        'categoria': categoria,
        'productos': productos
    }) 

def productos_top(request):
    top = PedidoItem.objects.values('producto__nombre')\
        .annotate(total_vendido=Sum('cantidad'))\
        .order_by('-total_vendido')[:5]

    return render(request, 'top_productos.html', {
        'top': top
    })