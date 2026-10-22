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

logger = logging.getLogger(__name__)

# =========================================================================
# 🛍️ BLOQUE 3: CATÁLOGO PÚBLICO DE PRODUCTOS (VISTA DEL CLIENTE)
# =========================================================================

def catalogo_tienda(request):
    """
    Vista principal que distingue entre el subdominio de una tienda activa
    y la landing page principal del SaaS (joyasapp.com).
    """
    if getattr(request, 'tenant_perfil', None):
        # 🛒 Petición desde un subdominio activo (ej: pgjoyas.joyasapp.com)
        perfil = request.tenant_perfil
        
        # Filtramos los productos y destacados asociados a este perfil/usuario de forma segura
        if hasattr(perfil.user, 'productos'):
            productos = perfil.user.productos.all()
            destacados = Producto.objects.filter(usuario=perfil.user, destacado=True).exclude(slug='').exclude(slug__isnull=True).order_by('-id')[:8]
            categorias = Categoria.objects.filter(usuario=perfil.user).order_by('nombre')
        else:
            productos = Producto.objects.filter(usuario=perfil.user) if hasattr(perfil, 'user') else []
            destacados = Producto.objects.filter(destacado=True).exclude(slug='').exclude(slug__isnull=True).order_by('-id')[:8]
            categorias = Categoria.objects.all().order_by('nombre')

        context = {
            'perfil': perfil,
            'productos': productos,
            'destacados': destacados,
            'categorias': categorias,
            'es_subdominio': True,
        }
        # ✅ Apuntamos a 'inicio.html' que es la plantilla que tienes creada
        return render(request, 'inicio.html', context)
    else:
        # 🌐 Petición desde el dominio principal (joyasapp.com), reutiliza la vista inicio limpia
        return inicio(request)
    
def inicio(request):
    try:
        # 1. Obtener el perfil exclusivamente desde el middleware de subdominio
        perfil = getattr(request, 'tenant_perfil', None)
        if request.user.is_authenticated and not perfil:
            perfil = getattr(request.user, 'perfil', None)

        # 2. Consultas filtradas de forma estricta
        if request.user.is_authenticated:
            productos = Producto.objects.filter(usuario=request.user).exclude(slug='').exclude(slug__isnull=True)
            destacados = Producto.objects.filter(usuario=request.user, destacado=True).exclude(slug='').exclude(slug__isnull=True).order_by('-id')[:8]
            categorias = Categoria.objects.filter(usuario=request.user).order_by('nombre')
        elif perfil:
            productos = Producto.objects.filter(usuario=perfil.user).exclude(slug='').exclude(slug__isnull=True)
            destacados = Producto.objects.filter(usuario=perfil.user, destacado=True).exclude(slug='').exclude(slug__isnull=True).order_by('-id')[:8]
            categorias = Categoria.objects.filter(usuario=perfil.user).order_by('nombre')
        else:
            # 🌐 Dominio principal (joyasapp.com) sin tienda activa
            productos = Producto.objects.none()
            destacados = Producto.objects.none()
            categorias = Categoria.objects.none()

        context = {
            'perfil': perfil,
            'productos': productos,
            'destacados': destacados,
            'categorias': categorias,
            'es_subdominio': bool(perfil),
        }
        return render(request, 'inicio.html', context)

    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Error crítico en la vista inicio: {str(e)}")
        return render(request, 'inicio.html', {
            'perfil': None, 
            'productos': [], 
            'destacados': [], 
            'categorias': [],
            'error': str(e)
        })
    
def buscar_productos(request):
    query = request.GET.get('q', '')
    categoria_id = request.GET.get('categoria', '')
    
    perfil = getattr(request, 'tenant_perfil', None)

    # 🛒 Determinamos el queryset según el usuario o el subdominio activo
    if request.user.is_authenticated:
        productos = Producto.objects.filter(usuario=request.user).exclude(slug='').exclude(slug__isnull=True)
        categorias = Categoria.objects.filter(usuario=request.user).order_by('nombre')
        if not perfil:
            perfil = getattr(request.user, 'perfil', None)
    elif perfil:
        productos = Producto.objects.filter(usuario=perfil.user).exclude(slug='').exclude(slug__isnull=True)
        categorias = Categoria.objects.filter(usuario=perfil.user).order_by('nombre')
    else:
        productos = Producto.objects.none()
        categorias = Categoria.objects.none()

    # 🔎 Filtro por texto de búsqueda
    if query:
        productos = productos.filter(
            Q(nombre__icontains=query) |
            Q(descripcion__icontains=query) |
            Q(categoria_nombre_icontains=query)
        )

    # 📂 Filtro por categoría seleccionada desde el menú
    if categoria_id and categoria_id != 'todas':
        productos = productos.filter(categoria_id=categoria_id)

    return render(request, 'lista_productos.html', {
        'productos': productos,
        'categorias': categorias,
        'query': query,
        'perfil': perfil,
        'es_subdominio': bool(perfil),
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

    # 🔄 Obtener productos relacionados del mismo usuario o categoría
    productos_relacionados = Producto.objects.filter(
        usuario=producto.usuario,
        categoria=producto.categoria
    ).exclude(id=producto.id).order_by('-id')[:4]

    session_key = request.session.session_key
    cantidad_en_carrito = 0

    if session_key:
        item = CarritoItem.objects.filter(
            producto=producto,
            session_key=session_key
        ).first()

        if item:
            cantidad_en_carrito = item.cantidad

    # 🔥 STOCK Y DISPONIBILIDAD SEGÚN TIPO
    if tiene_variantes:
        tiene_stock = producto.variantes.filter(stock__gt=0).exists()
        stock_disponible = None
    else:
        stock_disponible = producto.stock - cantidad_en_carrito
        tiene_stock = stock_disponible > 0

    return render(request, 'detalle_producto.html', {
        'producto': producto,
        'perfil_tienda': perfil_tienda,
        'tiene_variantes': tiene_variantes,
        'stock_disponible': stock_disponible,
        'tiene_stock': tiene_stock,
        'productos_relacionados': productos_relacionados,
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