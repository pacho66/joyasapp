# =========================================================================
# 🐍 LIBRERÍAS ESTÁNDAR
# =========================================================================
import logging

# =========================================================================
# ⚡ DJANGO
# =========================================================================
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Q
from django.shortcuts import render, redirect, get_object_or_404

# =========================================================================
# 🏪 MODELOS
# =========================================================================
from ..models import (
    Producto,
    Categoria,
    ProductoImagen,
)

# =========================================================================
# 📝 FORMULARIOS
# =========================================================================
from ..forms import ProductoForm

# =========================================================================
# 🧠 SERVICIOS
# =========================================================================
from ..services.producto_service import ProductoService

# =========================================================================
# 📡 LOGGER
# =========================================================================
logger = logging.getLogger(__name__)

# =========================================================================
# 📦 BLOQUE 8: GESTIÓN INTERNA DEL CATÁLOGO (ADMINISTRADOR)
# =========================================================================

@login_required
def mis_productos(request):

    productos = Producto.objects.filter(
        usuario=request.user
    ).order_by('-id')

    return render(
        request,
        'mis_productos.html',
        {
            'productos': productos
        }
    )

@login_required
def inventario(request):
    productos_base = Producto.objects.filter(usuario=request.user).prefetch_related('variantes').order_by('nombre')

    # 🔍 SISTEMA DE BÚSQUEDA
    q = request.GET.get('q', '').strip()
    if q:
        productos_base = productos_base.filter(nombre__icontains=q)

    total_productos = productos_base.count()
    productos_stock = 0
    stock_bajo = 0
    agotados = 0

    # 🔄 Inyectamos dinámicamente el stock real consolidado a cada objeto fila
    for p in productos_base:
        if p.variantes.exists():
            p.stock_real = p.variantes.aggregate(total=Sum('stock'))['total'] or 0
        else:
            p.stock_real = p.stock or 0

        # Clasificación de métricas
        if p.stock_real > 5:
            productos_stock += 1
        elif 0 < p.stock_real <= 5:
            stock_bajo += 1
        else:
            agotados += 1

    context = {
        'productos': productos_base,
        'total_productos': total_productos,
        'productos_stock': productos_stock,  
        'stock_bajo': stock_bajo,            
        'agotados': agotados,
        'q': q,  
    }
    return render(request, 'inventario.html', context)

@login_required
def lista_productos(request):
    productos = Producto.objects.filter(usuario=request.user)
    categorias = Categoria.objects.filter(usuario=request.user)
    return render(request, 'lista_productos.html', {
        'productos': productos,
        'categorias': categorias
    })

@login_required
def crear_categoria(request):

    if request.method == 'POST':

        nombre = request.POST.get('nombre')

        Categoria.objects.create(
            nombre=nombre,
            usuario=request.user
        )

        return redirect('dashboard')

    return render(
        request,
        'crear_categoria.html'
    )

def guardar_producto_view(request, pk=None):
    """
    Controlador unificado de nivel Enterprise.
    Delega la lógica pesada a ProductoService y gestiona el flujo HTTP y UX.
    """
    if pk:
        producto = get_object_or_404(Producto, pk=pk)
        accion = "actualizado"
    else:
        producto = Producto()
        accion = "creado"

    if request.method == 'POST':
        try:
            # Delegamos toda la lógica interna (Slug, variantes, transacciones) al servicio
            producto_guardado = ProductoService.guardar(request, pk=pk)
            
            messages.success(request, f"¡Producto '{producto_guardado.nombre}' {accion} con éxito rotundo!")
            return redirect('mis_productos')

        except Exception as e:
            logger.error(f"Error crítico en controlador al procesar producto: {str(e)}")
            messages.error(request, f"⚠️ Error interno: No se guardaron los cambios. Motivo: {str(e)}")
            
            # ✨ RUTAS CORREGIDAS: Apuntando a la raíz de templates para evitar el TemplateDoesNotExist
            return render(request, f'{"editar_producto" if pk else "crear_producto"}.html', {
                'producto': producto,
                'valores': request.POST,  
                'categorias': Categoria.objects.all()
            })

    # 🔄 Carga por petición GET normal (Edición o Creación limpia)
    valores_iniciales = producto if pk else None

    # ✨ RUTAS CORREGIDAS: Apuntando a la raíz de templates para evitar el TemplateDoesNotExist
    return render(request, f'{"editar_producto" if pk else "crear_producto"}.html', {
        'producto': producto,
        'valores': valores_iniciales,
        'categorias': Categoria.objects.all()
    })

@login_required
def eliminar_producto(request, id):

    producto = Producto.objects.get(
        id=id,
        usuario=request.user
    )

    producto.delete()

    return redirect('mis_productos')

@login_required
def eliminar_imagen_producto(request, id):

    producto = get_object_or_404(
        Producto,
        id=id,
        usuario=request.user
    )

    producto.imagen_principal = None
    producto.save()

    return redirect('editar_producto', id=id)

@login_required
def eliminar_imagen_galeria(request, id):

    imagen = get_object_or_404(
        ProductoImagen,
        id=id
    )

    producto_id = imagen.producto.id

    imagen.delete()

    return redirect(
        'editar_producto',
        producto_id
    )