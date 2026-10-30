from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from productos.models import Producto, ProductoVariante


@login_required
def fabricacion(request):
    """Vista general del panel de Fabricación y Control de Taller"""
    # 1. Obtenemos las variantes de los productos creados por la joyería logueada
    variantes = ProductoVariante.objects.filter(
        producto__usuario=request.user
    ).select_related('producto')

    # 2. Calculamos los totales de forma segura evitando mezcla de tipos
    material_total = 0.0
    total_mano_obra = 0.0
    costos_totales = 0.0

    for v in variantes:
        try:
            material_total += float(v.costo_material_con_merma or 0)
        except (ValueError, TypeError):
            pass

        try:
            # Evaluamos si es callable (método) o propiedad de mano de obra
            mo = v.costo_mano_obra() if callable(v.costo_mano_obra) else v.costo_mano_obra
            total_mano_obra += float(mo or 0)
        except (ValueError, TypeError):
            pass

        try:
            costos_totales += float(v.costo_total or 0)
        except (ValueError, TypeError):
            pass

    context = {
        'variantes': variantes,
        'material_total': material_total,
        'total_mano_obra': total_mano_obra,
        'costos_totales': costos_totales,
        'mensaje': "⚙️ Control de Fabricación Activo"
    }
    return render(request, 'fabricacion.html', context)

@login_required
def guardar_variante_fabricacion(request, producto_id):
    """Calculadora de Taller para crear y asociar variantes con costos a un producto"""
    producto = get_object_or_404(Producto, id=producto_id, usuario=request.user)
    
    if request.method == 'POST':
        # Captura de datos del formulario del taller
        nombre = request.POST.get('nombre_variante', 'Estándar')
        peso = request.POST.get('gramos', 0)  
        precio_gramo = request.POST.get('precio_gramo_metal', 0)
        merma = request.POST.get('porcentaje_merma', 0.10)
        mano_obra = request.POST.get('costo_mano_obra', 0)
        ganancia = request.POST.get('porcentaje_ganancia', 0.30)
        
        color = request.POST.get('color', '')
        talla = request.POST.get('talla', '')
        stock = request.POST.get('stock', 1)

        variante = ProductoVariante(
            producto=producto,
            nombre_variante=nombre,
            color=color,
            talla=talla,
            stock=stock,
            peso=peso,
            precio_gramo_metal=precio_gramo,
            porcentaje_merma=merma,
            costo_mano_obra=mano_obra,
            porcentaje_ganancia=ganancia,
        )

        variante.precio_venta = Decimal(str(round(variante.precio_venta_calculado, 2)))
        variante.save()
        
        return redirect('detalle_producto', producto_id=producto.id, slug=producto.slug)
        
    # ✅ AQUÍ DEFINIMOS EL CONTEXTO ANTES DE USARLO EN EL GET:
    context = {
        'producto': producto,
    }
    
    return render(request, 'productos/calculadora_taller.html', context)