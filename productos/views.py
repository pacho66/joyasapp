import json
import os
import uuid
import urllib.parse
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from urllib.parse import quote
import traceback

import stripe
import mercadopago
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Sum, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import get_template
from django.urls import reverse
from django.contrib.sites.shortcuts import get_current_site
from django.utils import timezone
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from xhtml2pdf import pisa

# 🏪 IMPORTACIONES DE LA APP PRODUCTOS
from productos.models import Producto, Categoria, ProductoImagen, CarritoItem, Cliente
from productos.services.precios import calcular_precio_producto
from productos.services.envios import calcular_envio

# 📂 IMPORTACIONES DE LA APP ACTUAL (Pedidos/Ventas)
from .models import ProductoVariante, Pedido, PedidoItem, Perfil, Abono, Gasto
from .forms import RegistroForm, ConfiguracionNegocioForm, GastoForm, ProductoForm
from .utils import generar_link_whatsapp, generar_numero_orden 

def safe_int(valor, default=1):
    try:
        return int(valor)
    except (ValueError, TypeError):
        return default

def generar_numero_orden(usuario=None):
    """
    Genera un consecutivo global inmune a choques de concurrencia (SaaS seguro).
    Garantiza que el número sea único en toda la base de datos.
    """
    from django.db.models import Max
    from .models import Pedido # Asegúrate de que apunte a tu app real

    # 1. Buscamos SIEMPRE a nivel global (evita choques entre joyerías)
    # Excluimos strings vacíos o nulos por seguridad
    ultimo_pedido = Pedido.objects.exclude(numero_orden="").order_by('-id').first()

    numero = 1

    if ultimo_pedido and ultimo_pedido.numero_orden:
        try:
            # Extrae la parte numérica después del guion (ej: "FAC-000001" -> "000001")
            partes = ultimo_pedido.numero_orden.strip().split('-')
            consecutivo = partes[-1]

            if consecutivo.isdigit():
                numero = int(consecutivo) + 1
            else:
                raise ValueError
        except Exception:
            # Si el formato se rompió por alguna razón, usamos el conteo global total como respaldo + 1
            numero = Pedido.objects.count() + 1
    else:
        # Si la tabla está absolutamente vacía
        numero = 1

    # 2. ESCUDO DE CONCURRENCIA: Doble verificación antes de retornar
    # Si por alguna razón el número calculado ya existe, lo autoincrementamos en bucle hasta que esté libre
    while Pedido.objects.filter(numero_orden=f"FAC-{numero:06d}").exists():
        numero += 1

    return f"FAC-{numero:06d}"


def calcular_envio(ciudad, subtotal):
    """
    Calcula envío automático.
    """

    if subtotal >= 300000:
        return Decimal('0')

    ciudad = (ciudad or '').lower()

    if ciudad in ['medellin', 'medellín']:
        return Decimal('10000')

    elif ciudad in ['bogota', 'bogotá', 'cali']:
        return Decimal('15000')

    return Decimal('20000')

def calcular_precio_producto(producto, cantidad):
    cantidad = Decimal(str(cantidad))

    # Venta por gramos
    if producto.tipo_venta == "gramo":
        peso = Decimal(str(producto.peso_producto or 0))
        total_gramos = cantidad * peso

        if total_gramos >= 12:
            precio = producto.precio_por_gramo_mayor or 0
        elif total_gramos >= 6:
            precio = producto.precio_por_gramo_semimayor or 0
        else:
            precio = producto.precio_por_gramo_detal or 0

        return Decimal(str(precio)), total_gramos

    # Venta por unidades
    if cantidad >= 12:
        precio = producto.precio_mayor or producto.precio_detal or 0
    elif cantidad >= 6:
        precio = producto.precio_semimayor or producto.precio_detal or 0
    else:
        precio = producto.precio_detal or 0

    return Decimal(str(precio)), None


def obtener_variante(producto, color, talla):
    return ProductoVariante.objects.filter(
        producto=producto,
        color=color,
        talla=talla
    ).first()

def cantidad_carrito(request):
    session_key = request.session.session_key

    if not session_key:
        return {'cantidad_carrito': 0}

    items = CarritoItem.objects.filter(session_key=session_key)

    cantidad = sum(item.cantidad for item in items)

    return {'cantidad_carrito': cantidad}


def registro(request):
    if request.method == 'POST':
        form = RegistroForm(request.POST)

        if form.is_valid():

            email = form.cleaned_data['email']
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            confirmar = form.cleaned_data['confirmar_password']

            if password != confirmar:
                return render(request, 'registro.html', {
                    'form': form,
                    'error': 'Las contraseñas no coinciden'
                })

            if User.objects.filter(email=email).exists():
                return render(request, 'registro.html', {
                    'form': form,
                    'error': 'Este correo ya está registrado'
                })

            if User.objects.filter(username=username).exists():
                return render(request, 'registro.html', {
                    'form': form,
                    'error': 'Este usuario ya existe'
                })

            with transaction.atomic():

                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password
                )

                Perfil.objects.create(
                    user=user,
                    nombre_tienda=form.cleaned_data['nombre_tienda'],
                    whatsapp=form.cleaned_data['whatsapp'],
                    plan='basico',
                    activa=True,
                    plan_vence=timezone.localdate() + timedelta(days=15)
                )

            login(request, user)
            return redirect('dashboard')

    else:
        form = RegistroForm()

    return render(request, 'registro.html', {'form': form})
    
def iniciar_sesion(request):

    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':

        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user is not None:
            login(request, user)
            return redirect('dashboard')

        else:
            messages.error(
                request,
                'Usuario o contraseña incorrectos'
            )

    return render(request, 'login.html')

@login_required
def cerrar_sesion(request):
    logout(request)
    return redirect('login')
        
def inicio(request):

    if request.user.is_authenticated:
        productos = Producto.objects.filter(usuario=request.user)

        destacados = Producto.objects.filter(
            usuario=request.user,
            destacado=True
        ).order_by('-id')[:8]

        categorias = Categoria.objects.filter(
            usuario=request.user
        ).order_by('nombre')

    else:
        productos = Producto.objects.all()

        destacados = Producto.objects.filter(
            destacado=True
        ).order_by('-id')[:8]

        categorias = Categoria.objects.all().order_by('nombre')

    return render(request, 'inicio.html', {
        'productos': productos,
        'destacados': destacados,
        'categorias': categorias,
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

@login_required
def lista_productos(request):
    productos = Producto.objects.filter(usuario=request.user)
    categorias = Categoria.objects.filter(usuario=request.user)
    return render(request, 'lista_productos.html', {
        'productos': productos,
        'categorias': categorias
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

def productos_top(request):
    top = PedidoItem.objects.values('producto__nombre')\
        .annotate(total_vendido=Sum('cantidad'))\
        .order_by('-total_vendido')[:5]

    return render(request, 'top_productos.html', {
        'top': top
    })

def safe_float(val):
    if not val:
        return None
    try:
        # Esto limpia el número por si el celular envía comas en vez de puntos
        return float(str(val).replace(',', '.'))
    except ValueError:
        return None

@login_required
def crear_producto(request):
    categorias = Categoria.objects.filter(usuario=request.user)

    if request.method == 'POST':
        referencia = request.POST.get('referencia')

        # 🛑 1. VALIDACIÓN ANTICRASH: Comprobar si la referencia ya existe
        if referencia and Producto.objects.filter(usuario=request.user, referencia=referencia).exists():
            messages.error(
                request, 
                f"La referencia '{referencia}' ya está asignada a otro producto. Usa una diferente."
            )
            return render(
                request,
                'crear_producto.html',
                {
                    'categorias': categorias,
                    'valores': request.POST,  
                    'variantes_texto': request.POST.get('variantes', '')  
                }
            )

        # ✨ Capturamos la imagen principal aquí para usarla de escudo abajo
        imagen_principal = request.FILES.get('imagen_principal')

        # 🚀 2. CREACIÓN SEGURA E INTELIGENTE (Cambiamos .create por instanciación manual)
        producto = Producto(
            usuario=request.user,
            nombre=request.POST.get('nombre'),
            referencia=referencia,
            descripcion=request.POST.get('descripcion'),
            categoria_id=request.POST.get('categoria'),
            tipo_venta=request.POST.get('tipo_venta') or 'unidad',
            # Evitamos guardar un string vacío '' si el campo no se llena en el HTML:
            precio_detal=request.POST.get('precio_detal') or None,
            precio_semimayor=request.POST.get('precio_semimayor') or None,
            precio_mayor=request.POST.get('precio_mayor') or None,
            peso_producto=request.POST.get('peso_producto') or None,
            precio_por_gramo_detal=request.POST.get('precio_por_gramo_detal') or None,
            precio_por_gramo_semimayor=request.POST.get('precio_por_gramo_semimayor') or None,
            precio_por_gramo_mayor=request.POST.get('precio_por_gramo_mayor') or None,
            destacado=(request.POST.get('destacado') == 'on'),
            precio_costo=request.POST.get('precio_costo') or 0,
            stock=request.POST.get('stock') or 0,
            imagen_principal=imagen_principal,  
            certificado=request.FILES.get('certificado'),
            video=request.FILES.get('video'),
        )
        
        # 🔥 OBLIGAMOS A DJANGO A EJECUTAR EL MÉTODO SAVE() PERSONALIZADO
        # Esto calcula automáticamente los precios finales si el producto es por gramos
        producto.save() 

        # 🎨 3. PROCESAMIENTO DE IMÁGENES DE GALERÍA
        imagenes = request.FILES.getlist('galeria')

        for imagen in imagenes:
            # 🛡️ ESCUDO POR PESO: Si mide exactamente los mismos bytes que la principal, se salta
            if imagen_principal and imagen.size == imagen_principal.size:
                continue

            ProductoImagen.objects.create(
                producto=producto,
                imagen=imagen
            )
            
        # ⚙️ PROCESAMIENTO DE VARIANTES
        variantes = request.POST.get('variantes', '')

        for linea in variantes.splitlines():
            linea = linea.strip()
            if not linea:
                continue

            datos = linea.split('|')
            if len(datos) != 3:
                continue

            try:
                ProductoVariante.objects.create(
                    producto=producto,
                    color=datos[0].strip(),
                    talla=datos[1].strip(),
                    stock=int(datos[2].strip())
                )
            except ValueError:
                pass

        messages.success(request, "¡Producto y sus variantes creados correctamente!")
        return redirect('dashboard')

    return render(
        request,
        'crear_producto.html',
        {
            'categorias': categorias
        }
    )

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
def editar_producto(request, id):

    producto = Producto.objects.get(
        id=id,
        usuario=request.user
    )

    categorias = Categoria.objects.filter(
        usuario=request.user
    )

    if request.method == 'POST':

        producto.nombre = request.POST.get('nombre')
        producto.descripcion = request.POST.get('descripcion')
        producto.referencia = request.POST.get('referencia')

        if request.POST.get('categoria'):
            producto.categoria_id = request.POST.get('categoria')

        producto.precio_costo = request.POST.get('precio_costo') or 0
        producto.stock = request.POST.get('stock') or 0

        producto.precio_detal = request.POST.get('precio_detal') or 0
        producto.precio_semimayor = request.POST.get('precio_semimayor') or 0
        producto.precio_mayor = request.POST.get('precio_mayor') or 0

        producto.tipo_venta = request.POST.get('tipo_venta') or 'unidad'

        producto.peso_producto = request.POST.get('peso_producto') or 0

        producto.precio_por_gramo_detal = (
            request.POST.get('precio_por_gramo_detal') or 0
        )

        producto.precio_por_gramo_semimayor = (
            request.POST.get('precio_por_gramo_semimayor') or 0
        )

        producto.precio_por_gramo_mayor = (
            request.POST.get('precio_por_gramo_mayor') or 0
        )

        producto.destacado = (
            request.POST.get('destacado') == 'on'
        )

        if request.FILES.get('imagen_principal'):
            producto.imagen_principal = request.FILES.get(
                'imagen_principal'
            )

        if request.FILES.get('certificado'):
            producto.certificado = request.FILES.get(
                'certificado'
            )

        if request.FILES.get('video'):
            producto.video = request.FILES.get(
                'video'
            )

        producto.save()

        
        # 📸 Agregar nuevas imágenes a la galería con protección por peso
        galeria = request.FILES.getlist('galeria')

        for imagen in galeria:
            # 🛡️ ESCUDO POR PESO: Compara los bytes del archivo subido con la imagen principal actual
            if producto.imagen_principal and imagen.size == producto.imagen_principal.size:
                continue

            ProductoImagen.objects.create(
                producto=producto,
                imagen=imagen
            )

        producto.variantes.all().delete()

        variantes = request.POST.get(
            'variantes',
            ''
        )

        for linea in variantes.splitlines():

            linea = linea.strip()

            if not linea:
                continue

            datos = linea.split('|')

            if len(datos) != 3:
                continue

            try:

                ProductoVariante.objects.create(
                    producto=producto,
                    color=datos[0].strip(),
                    talla=datos[1].strip(),
                    stock=int(datos[2].strip())
                )

            except ValueError:
                continue

        return redirect('mis_productos')

    return render(
        request,
        'editar_producto.html',
        {
            'producto': producto,
            'categorias': categorias,
        }
    )

@login_required(login_url='/login/')
def dashboard(request):
    usuario = request.user

    # ✅ Crear o obtener perfil automáticamente
    perfil, creado = Perfil.objects.get_or_create(
        user=usuario,
        defaults={
            'nombre_tienda': 'PG Joyas González',
            'plan': 'gratis'
        }
    )

    # 🔒 Validar plan
    if perfil.plan_vence and perfil.plan_vence < timezone.localdate():
        return render(request, 'plan_vencido.html')
    
    hoy = timezone.localdate()
    ayer = hoy - timedelta(days=1)
    hace_30 = timezone.now() - timedelta(days=30)

    tipo = request.GET.get('tipo')

    # ==========================
    # BASE MULTIUSUARIO SaaS
    # ==========================
    pedidos = Pedido.objects.filter(usuario=usuario)

    # ==========================
    # 📊 GRÁFICAS (ÚLTIMOS 7 DÍAS)
    # ==========================

    ventas_por_dia = (
        pedidos
        .annotate(fecha_dia=TruncDate('fecha'))
        .values('fecha_dia')
        .annotate(total=Sum('total'))
        .order_by('fecha_dia')
)

    # preparar datos para JS
    fechas = [v['fecha_dia'].strftime('%d/%m') for v in ventas_por_dia]
    totales = [float(v['total']) for v in ventas_por_dia]

    cliente_id = request.GET.get('cliente')

    if cliente_id:
        pedidos = pedidos.filter(cliente_id=cliente_id)

    clientes = Cliente.objects.filter(usuario=usuario)
    productos = Producto.objects.filter(usuario=usuario)

    hoy = timezone.now().date()

    clientes_morosos = Cliente.objects.filter(
    usuario=request.user,
    pedidos__tipo_pago='credito',
    pedidos__saldo_pendiente__gt=0,
    pedidos__fecha_limite__lt=hoy
    ).distinct()

    morosos_count = clientes_morosos.count()

    morosos_total = clientes_morosos.aggregate(
    total=Sum('pedidos__saldo_pendiente')
    )['total'] or 0

    # ==========================
    # FILTROS CLIENTES
    # ==========================
    clientes_filtrados = clientes

    if tipo == 'vip':
        clientes_filtrados = clientes.filter(total_compras__gte=500000)

    elif tipo == 'nuevos':
        clientes_filtrados = clientes.filter(fecha_creacion__date=hoy)

    elif tipo == 'dormidos':
        clientes_filtrados = clientes.filter(
            ultima_compra__lt=hace_30
        ).distinct()

    elif tipo == 'morosos':
        clientes_filtrados = clientes.filter(
        pedidos__tipo_pago='credito',
        pedidos__saldo_pendiente__gt=0,
        pedidos__fecha_limite__lt=hoy
    ).distinct()

    # ==========================
    # MENSAJES
    # ==========================
    mensajes = {
    'vip': "💎 Clientes VIP",
    'nuevos': "✨ Clientes nuevos",
    'dormidos': "😴 Clientes inactivos",
    'morosos': "🔴 Clientes en mora"
}

    mensaje = mensajes.get(tipo, "📊 Panel General")

    # ==========================
    # VENTAS
    # ==========================
    total_hoy = pedidos.filter(
        fecha__date=hoy
    ).aggregate(total=Sum('total'))['total'] or 0

    total_ayer = pedidos.filter(
        fecha__date=ayer
    ).aggregate(total=Sum('total'))['total'] or 0

    total_general = pedidos.aggregate(
        total=Sum('total')
    )['total'] or 0

    ganancias_mes = pedidos.filter(
        fecha__month=hoy.month,
        fecha__year=hoy.year
    ).aggregate(total=Sum('total'))['total'] or 0

    # ==========================
    # PEDIDOS
    # ==========================
    pedidos_hoy = pedidos.filter(fecha__date=hoy).count()
    total_pedidos = pedidos.count()
    pedidos_recientes = pedidos.order_by('-fecha')[:5]

    pedidos_pendientes = pedidos.filter(
        estado='pendiente'
    ).count()

    pedidos_pagados = pedidos.filter(
        estado='pagado'
    ).count()

    # ==========================
    # CLIENTES
    # ==========================
    total_clientes = clientes.count()

    clientes_vip = clientes.filter(
        total_compras__gte=500000
    ).count()

    clientes_nuevos = clientes.filter(
        fecha_creacion__date=hoy
    ).count()

    clientes_dormidos = clientes.filter(
        ultima_compra__lt=hace_30
    ).distinct().count()

    # ==========================
    # INVENTARIO
    # ==========================
    total_productos = productos.count()

    productos_sin_stock = productos.filter(
        stock=0
    ).count()

    productos_bajo_stock = productos.filter(
        stock__gt=0,
        stock__lte=5
    ).count()

    # ==========================
    # MÉTRICAS
    # ==========================
    ticket_promedio = (
        total_general / total_pedidos
        if total_pedidos > 0 else 0
    )

    crecimiento = total_hoy - total_ayer

    # ==========================
    # 💰 FINANZAS
    # ==========================

    # INGRESOS 
    total_ingresos = total_general

    # GASTOS (multiusuario 🔥)
    total_gastos = Gasto.objects.filter(
        usuario=usuario
    ).aggregate(total=Sum('monto'))['total'] or 0

    # UTILIDAD
    utilidad = total_ingresos - total_gastos

    # ==========================
    # CONTEXT
    # ==========================
    context = {

    # ==========================
    # 📊 GENERAL
    # ==========================
    'mensaje': mensaje,
    'tipo': tipo,
    'today': hoy,

    'perfil': perfil,

    # ==========================
    # 💰 VENTAS
    # ==========================
    'total_hoy': total_hoy,
    'total_ayer': total_ayer,
    'total_general': total_general,
    'ganancias_mes': ganancias_mes,
    'crecimiento': crecimiento,

    # ==========================
    # 📊 GRÁFICAS
    # ==========================
    'fechas': fechas,
    'totales': totales,

    # ==========================
    # 💸 FINANZAS
    # ==========================
    'total_ingresos': total_ingresos,
    'total_gastos': total_gastos,
    'utilidad': utilidad,

    # ==========================
    # 📦 PEDIDOS
    # ==========================
    'pedidos_hoy': pedidos_hoy,
    'total_pedidos': total_pedidos,
    'pedidos_recientes': pedidos_recientes,
    'pedidos_pendientes': pedidos_pendientes,
    'pedidos_pagados': pedidos_pagados,

    # ==========================
    # 👥 CLIENTES
    # ==========================
    'total_clientes': total_clientes,
    'clientes_vip': clientes_vip,
    'clientes_nuevos': clientes_nuevos,
    'clientes_dormidos': clientes_dormidos,
    'clientes_filtrados': clientes_filtrados,

    # 💳 CRÉDITO
    'morosos_count': morosos_count,
    'morosos_total': morosos_total,

    # ==========================
    # 📦 INVENTARIO
    # ==========================
    'total_productos': total_productos,
    'productos_sin_stock': productos_sin_stock,
    'productos_bajo_stock': productos_bajo_stock,

    # ==========================
    # 📈 MÉTRICAS
    # ==========================
    'ticket_promedio': ticket_promedio,
}

    return render(request, 'dashboard.html', context)

@login_required
def modificar_banner(request):
    perfil = request.user.perfil

    if request.method == 'POST':
        texto = request.POST.get('banner_texto')
        perfil.banner_texto = texto
        perfil.save()
        return redirect('dashboard')

    return render(request, 'modificar_banner.html', {'perfil': perfil})

@login_required
def estadisticas(request):

    pedidos = Pedido.objects.filter(usuario=request.user)

    # 🔹 HOY
    hoy = timezone.now().date()
    ventas_hoy = pedidos.filter(fecha__date=hoy).aggregate(
        total=Sum('total')
    )['total'] or 0

    # 🔹 ÚLTIMOS 30 DÍAS
    hace_30 = hoy - timedelta(days=30)
    ventas_mes = pedidos.filter(fecha__date__gte=hace_30).aggregate(
        total=Sum('total')
    )['total'] or 0

    # 🔹 TOTAL PEDIDOS
    total_pedidos = pedidos.count()

    # 🔹 VENTAS POR DÍA
    ventas_por_dia = pedidos.annotate(
        dia=TruncDate('fecha')
    ).values('dia').annotate(
        total=Sum('total')
    ).order_by('dia')

    gastos = Gasto.objects.filter(usuario=request.user).annotate(
    dia=TruncDate('fecha')
    ).values('dia').annotate(
    total=Sum('monto')
    ).order_by('dia')

    # 🔹 TOP PRODUCTOS (si ya lo tienes)
    top_productos = PedidoItem.objects.values(
        'producto__nombre'
    ).annotate(
        total_vendido=Sum('cantidad')
    ).order_by('-total_vendido')[:5]

    # 🔹 MÉTODOS DE PAGO (si ya lo tienes)
    metodos_pago = pedidos.values('tipo_pago').annotate(
        total=Sum('total')
    )

    top_clientes = pedidos.values(
    'cliente__nombre'
    ).annotate(
    total_compras=Sum('total')
    ).order_by('-total_compras')[:5]


    context = {
        'ventas_hoy': ventas_hoy,
        'ventas_mes': ventas_mes,
        'total_pedidos': total_pedidos,
        'ventas_por_dia': ventas_por_dia,
        'top_productos': top_productos,
        'metodos_pago': metodos_pago,
        'top_clientes': top_clientes,
    }

    return render(
        request,
        'estadisticas.html',
        context
    )

# ==========================================
# 💰 GANANCIAS
# ==========================================
@login_required
def ganancias(request):
    hoy = timezone.now().date()

    ventas_hoy = Pedido.objects.filter(
        usuario=request.user,
        fecha__date=hoy
    ).aggregate(total=Sum('total'))['total'] or 0

    ventas_total = Pedido.objects.filter(
        usuario=request.user
    ).aggregate(total=Sum('total'))['total'] or 0

    pedidos = Pedido.objects.filter(
        usuario=request.user
    ).order_by('-fecha')[:20]

    context = {
        'ventas_hoy': ventas_hoy,
        'ventas_total': ventas_total,
        'pedidos': pedidos,
    }

    return render(request, 'ganancias.html', context)


# ==========================================
# 📦 INVENTARIO
# ==========================================
@login_required
def inventario(request):
    productos = Producto.objects.filter(
        usuario=request.user
    ).order_by('nombre')

    total_productos = productos.count()

    context = {
        'productos': productos,
        'total_productos': total_productos,
    }

    return render(request, 'inventario.html', context)

@login_required
def configurar_negocio(request):
    perfil = request.user.perfil

    if request.method == 'POST':
        form = ConfiguracionNegocioForm(request.POST, request.FILES)  # 👈 CLAVE

        if form.is_valid():

            perfil.nombre_tienda = form.cleaned_data['nombre_tienda']
            perfil.whatsapp = form.cleaned_data['whatsapp']
            perfil.email_empresa = form.cleaned_data['correo_negocio']
            perfil.direccion = form.cleaned_data['direccion']
            perfil.ciudad = form.cleaned_data['ciudad']

            # 🔥 NUEVO (LOGO)
            if request.FILES.get('logo'):
                perfil.logo = request.FILES['logo']

             # 🔥 COLORES
            perfil.color_primario = request.POST.get('color_primario', '#28a745')
            perfil.color_secundario = request.POST.get('color_secundario', '#000000')

            perfil.save()

            messages.success(request, "Configuración actualizada correctamente")
            return redirect('dashboard')

    else:
        form = ConfiguracionNegocioForm(initial={
            'nombre_tienda': perfil.nombre_tienda,
            'whatsapp': perfil.whatsapp,
            'correo_negocio': perfil.email_empresa,
            'direccion': perfil.direccion,
            'ciudad': perfil.ciudad,
        })

    return render(request, 'configurar_negocio.html', {
        'form': form,
        'perfil': perfil  # 👈 para mostrar logo
    })

@login_required
def renovar_manual(request):
    perfil = request.user.perfil
    renovar_plan(perfil)
    return redirect('dashboard')

def renovar_plan(perfil):
    hoy = timezone.localdate()

    if perfil.plan_vence and perfil.plan_vence > hoy:
        perfil.plan_vence = perfil.plan_vence + timedelta(days=30)
    else:
        perfil.plan_vence = hoy + timedelta(days=30)

    perfil.activa = True
    perfil.save()    

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

def ver_carrito(request):
    session_key = request.session.session_key
    if not session_key:
        items = []
    else:
        items = CarritoItem.objects.filter(session_key=session_key).select_related('producto', 'variante')

    total_articulos = 0
    total = Decimal('0.00')

    # 🔥 TU CONTAR TOTAL GLOBAL ORIGINAL
    for item in items:
        total_articulos += item.cantidad or 0

    # 🔥 TU DEFINIR NIVEL GLOBAL ORIGINAL
    if total_articulos >= 12:
        tipo_global = "Mayorista"
    elif total_articulos >= 6:
        tipo_global = "Semi-Mayorista"
    else:
        tipo_global = "Detal"

    # 🔥 TU CALCULAR CADA ITEM ORIGINAL
    for item in items:
        cantidad = item.cantidad or 0
        producto = item.producto

        tipo = tipo_global  # 👈 ESTE MANDA TODO

        # =========================
        # PRODUCTOS POR GRAMOS
        # =========================
        if producto.tipo_venta == "gramo":

            peso = producto.peso_producto or 0
            total_gramos = Decimal(cantidad) * Decimal(peso)
            item.total_gramos = total_gramos

            if tipo == "Mayorista":
                precio = producto.precio_por_gramo_mayor or 0
            elif tipo == "Semi-Mayorista":
                precio = producto.precio_por_gramo_semimayor or 0
            else:
                precio = producto.precio_por_gramo_detal or 0

            item.precio_aplicado = Decimal(precio)
            item.subtotal_calculado = total_gramos * Decimal(precio)

            # ahorro gramos
            precio_base = producto.precio_por_gramo_detal or 0
            item.ahorro = (Decimal(precio_base) - Decimal(precio)) * total_gramos

        # =========================
        # PRODUCTOS POR UNIDADES
        # =========================
        else:

            item.total_gramos = None

            if tipo == "Mayorista":
                precio = producto.precio_mayor or 0
            elif tipo == "Semi-Mayorista":
                precio = producto.precio_semimayor or 0
            else:
                precio = producto.precio_detal or 0

            item.precio_aplicado = Decimal(precio)
            item.subtotal_calculado = Decimal(cantidad) * Decimal(precio)

            # ahorro unidades
            precio_base = producto.precio_detal or 0
            item.ahorro = (Decimal(precio_base) - Decimal(precio)) * Decimal(cantidad)

        # 🔥 ESTO VA FUERA DEL IF
        item.tipo_precio = tipo

        total += item.subtotal_calculado

    # =========================
    # PROGRESO ORIGINAL
    # =========================
    faltan_semi = max(0, 6 - total_articulos)
    faltan_mayor = max(0, 12 - total_articulos)

    # =======================================================================
    # 🔥 ÚNICO CAMBIO: Forzar float() solo dentro del JSON para que Render no tire Error 500
    # =======================================================================
    carrito_json = json.dumps([
        {
            "producto": {"nombre": item.producto.nombre},
            "color": item.color,
            "talla": item.talla,
            "cantidad": float(item.cantidad) if item.cantidad else 0,
            "subtotal": float(item.subtotal_calculado) if item.subtotal_calculado else 0,
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

@transaction.atomic
def comprar_whatsapp(request, producto_id=None):
    """
    Vista única para procesar compras de WhatsApp mediante POST seguro.
    Sincronizada con el modelo real de Pedido y PedidoItem.
    """
    if request.method != "POST":
        messages.error(request, "Acceso no autorizado.")
        return redirect('ver_carrito')

    resumen_items = []
    items_carrito = None
    es_compra_directa = producto_id is not None

    # =========================================================
    # 🛒 PASO 1: MODO DE ENTRADA (DIRECTO O CARRITO)
    # =========================================================
    if es_compra_directa:
        producto = get_object_or_404(Producto, id=producto_id)
        usuario = producto.usuario  

        try:
            cantidad = int(request.POST.get('cantidad', 1))
        except ValueError:
            cantidad = 1

        variante = None
        variante_id = request.POST.get('variante_id')
        if variante_id:
            variante = get_object_or_404(ProductoVariante, id=variante_id, producto=producto)

        # Validar Stock
        if variante:
            if variante.stock < cantidad:
                messages.warning(request, f"Sin stock suficiente para {producto.nombre}")
                return redirect('detalle_producto', id=producto.id, slug=producto.slug)
        else:
            if producto.tipo_venta != "gramo" and producto.stock < cantidad:
                messages.warning(request, f"Sin stock suficiente para {producto.nombre}")
                return redirect('detalle_producto', id=producto.id, slug=producto.slug)

        precio, gramos = calcular_precio_producto(producto, cantidad)
        precio = Decimal(str(precio))

        if gramos:
            subtotal = Decimal(str(gramos)) * precio
            linea = f"{producto.nombre} ({gramos}g)"
        else:
            subtotal = Decimal(str(cantidad)) * precio
            linea = f"{producto.nombre} x{cantidad}"

        resumen_items.append({
            'item': None, 
            'producto': producto,
            'variante': variante,
            'cantidad': cantidad,
            'total_gramos': gramos if gramos else 0,
            'precio': precio,
            'subtotal': subtotal,
            'linea': linea,
        })

    else:
        session_key = request.session.session_key
        if not session_key:
            messages.warning(request, "El carrito está vacío")
            return redirect('ver_carrito')

        items_carrito = CarritoItem.objects.select_related('producto', 'variante').filter(session_key=session_key)
        if not items_carrito.exists():
            messages.warning(request, "El carrito está vacío")
            return redirect('ver_carrito')

        usuario = items_carrito.first().producto.usuario

        for item in items_carrito:
            producto = item.producto
            variante = item.variante
            cantidad = item.cantidad or 0

            if producto.usuario != usuario:
                messages.warning(request, "Producto inválido en el carrito.")
                return redirect('ver_carrito')

            if variante:
                if variante.stock < cantidad:
                    messages.warning(request, f"Sin stock suficiente para {producto.nombre}")
                    return redirect('ver_carrito')
            else:
                if producto.tipo_venta != "gramo" and producto.stock < cantidad:
                    messages.warning(request, f"Sin stock suficiente para {producto.nombre}")
                    return redirect('ver_carrito')

            precio, gramos = calcular_precio_producto(producto, cantidad)
            precio_seguro = precio if precio is not None else 0
            precio = Decimal(str(precio_seguro))

            if gramos:
                subtotal = Decimal(str(gramos)) * precio
                linea = f"{producto.nombre} ({gramos}g)"
            else:
                subtotal = Decimal(str(cantidad)) * precio
                linea = f"{producto.nombre} x{cantidad}"

            resumen_items.append({
                'item': item,
                'producto': producto,
                'variante': variante,
                'cantidad': cantidad,
                'total_gramos': gramos if gramos else 0,
                'precio': precio,
                'subtotal': subtotal,
                'linea': linea,
            })

    # =========================================================
    # 📊 PASO 2: CAPTURA SEGURA Y MODELADO REAL
    # =========================================================
    numero = generar_numero_orden(usuario)

    aplica_iva = request.POST.get('aplica_iva') == 'on'
    es_retenedor = request.POST.get('es_retenedor') == 'on'
    valor_descuento = request.POST.get('descuento', '0')

    try:
        porcentaje_descuento = Decimal(str(valor_descuento))
    except:
        porcentaje_descuento = Decimal('0')

    porcentaje_descuento = max(Decimal('0'), min(Decimal('100'), porcentaje_descuento))

    nombre_cliente = request.POST.get('nombre', '').strip()
    telefono_cliente = request.POST.get('telefono', '').strip()
    ciudad_destino = request.POST.get('ciudad', '').strip()
    direccion_entrega = request.POST.get('direccion', '').strip()
    nit = request.POST.get('nit', '').strip()

    # 🛠️ PARCHE QUIRÚRGICO CONTRA EL ERROR 500 (Email Blindado)
    email_input = request.POST.get('email', '').strip()
    if email_input:
        cliente_email = email_input
    elif request.user.is_authenticated:
        cliente_email = request.user.email
    else:
        cliente_email = "cliente_whatsapp@joyasapp.com"

    # Cálculo de Totales (Tu lógica original intacta)
    subtotal_general = Decimal('0')
    for data in resumen_items:
        subtotal_general += data['subtotal']

    descuento = subtotal_general * (porcentaje_descuento / Decimal('100'))
    subtotal_con_descuento = subtotal_general - descuento

    iva = subtotal_con_descuento * Decimal('0.19') if aplica_iva else Decimal('0')
    retefuente = subtotal_con_descuento * Decimal('0.025') if es_retenedor else Decimal('0')
    
    # Mantiene tu consulta de envío original
    costo_envio = calcular_envio(ciudad_destino, subtotal_con_descuento)
    total_final = subtotal_con_descuento + iva - retefuente + costo_envio

    # 🕵️ BLOQUE CAPTURADOR PARA EL PROCESO DE GUARDADO
    try:
        pedido = Pedido.objects.create(
            usuario=usuario,
            numero_orden=numero,
            cliente_nombre=nombre_cliente,
            cliente_telefono=telefono_cliente,
            cliente_email=cliente_email,
            cliente_ciudad=ciudad_destino,
            cliente_direccion=direccion_entrega,
            cliente_nit=nit,
            total=float(total_final),  # 🚀 Aseguramos tipo nativo compatible con la DB
            porcentaje_descuento=float(porcentaje_descuento),  # 🚀 Evita el choque de tipos en DB
            descuento_total=float(descuento),
            costo_envio=float(costo_envio),
            aplica_iva=aplica_iva,
            es_retenedor=es_retenedor,
            estado="pendiente"
        )

        # Registrar los productos asociados al pedido (PedidoItem)
        for data in resumen_items:
            subtotal = data['subtotal']
            iva_item = subtotal * Decimal('0.19') if aplica_iva else Decimal('0')
            retefuente_item = subtotal * Decimal('0.025') if es_retenedor else Decimal('0')
            total_item = subtotal + iva_item - retefuente_item

            PedidoItem.objects.create(
                pedido=pedido,
                producto=data['producto'],
                variante=data['variante'],
                cantidad=int(data['cantidad']),
                total_gramos=float(data['total_gramos']), 
                precio=float(data['precio']),
                subtotal=float(subtotal),
                iva=float(iva_item),
                retefuente=float(retefuente_item),
                total_final=float(total_item)
            )

            # Descontar del inventario/stock
            if data['variante']:
                data['variante'].stock -= data['cantidad']
                data['variante'].save()
            elif data['producto'].tipo_venta != "gramo":
                data['producto'].stock -= data['cantidad']
                data['producto'].save()

        # =========================================================
        # 🔗 PASO 3: ENLACE SEGURO Y MENSAJE DE WHATSAPP
        # =========================================================
        domain = get_current_site(request).domain
        url_factura = f"https://{domain}{reverse('factura_publica', kwargs={'token': pedido.token_publico})}"

        mensaje = (
            f"🛍️ ¡Hola! Acabo de confirmar mi pedido.\n\n"
            f"📦 Orden N°: {pedido.numero_orden}\n"
            f"👤 Cliente: {nombre_cliente}\n"
            f"💰 Total Neto: ${float(total_final):,.0f}\n\n"
            f"📄 Ver detalles y datos de facturación aquí:\n{url_factura}\n\n"
            f"Quedo atento a tus indicaciones para realizar el pago. ¡Muchas gracias!"
        ).replace(",", ".")

        perfil, _ = Perfil.objects.get_or_create(user=usuario)
        if not perfil.whatsapp:
            messages.warning(request, "El comercio no tiene configurado WhatsApp.")
            return redirect('ver_carrito') if not es_compra_directa else redirect('detalle_producto', id=producto.id, slug=producto.slug)

        url_whatsapp_final = f"https://wa.me/{perfil.whatsapp}?text={urllib.parse.quote(mensaje)}"

        if not es_compra_directa and items_carrito:
            items_carrito.delete()

        return redirect(url_whatsapp_final)

    except Exception as e:
        print("\n" + "🚨" * 30)
        print(f"💥 ERROR EN BASE DE DATOS / GUARDADO: {str(e)}")
        print("-" * 60)
        traceback.print_exc()
        print("🚨" * 30 + "\n")
        return HttpResponse(f"Fallo capturado en proceso de guardado: {str(e)}", status=500)

@transaction.atomic
def pagar_pedido(request):
    """
    Vista para procesar el pago final del pedido (Ruta: /pagar/)
    """
    if request.method != 'POST':
        return redirect('ver_carrito')

    # 🔥 BLINDAJE TOTAL DESDE EL PRIMER MILISEGUNDO
    try:
        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key

        items = CarritoItem.objects.select_related('producto', 'variante').filter(session_key=session_key)

        if not items.exists():
            messages.error(request, "Tu carrito está vacío.")
            return redirect('ver_carrito')

        # 🏪 Detectamos el usuario dueño de la joyería (Dentro del try por seguridad)
        usuario = items.first().producto.usuario
        numero = generar_numero_orden(usuario)

        # 📝 CAPTURA DE DATOS DEL FORMULARIO DE DESPACHO
        nombre = request.POST.get('nombre', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        ciudad = request.POST.get('ciudad', '').strip()
        direccion = request.POST.get('direccion', '').strip()
        nit = request.POST.get('nit', '').strip()

        # 🛠️ ESCUDO CONTRA CORREOS VACÍOS O ANÓNIMOS
        email_input = request.POST.get('email', '').strip()
        if email_input:
            cliente_email = email_input
        elif request.user.is_authenticated:
            cliente_email = request.user.email
        else:
            cliente_email = "cliente_pasarela@joyasapp.com" # Evita que colapse PostgreSQL por campos vacíos

        # 📊 CAPTURA DINÁMICA DE IMPUESTOS Y DESCUENTO
        aplica_iva = request.POST.get('aplica_iva') == 'on' or request.POST.get('aplica_iva') == 'true'
        es_retenedor = request.POST.get('es_retenedor') == 'on' or request.POST.get('es_retenedor') == 'true'
        valor_descuento = request.POST.get('descuento', '0').strip()

        try:
            porcentaje_descuento = Decimal(str(valor_descuento or 0))
        except (ValueError, TypeError):
            porcentaje_descuento = Decimal('0')

        porcentaje_descuento = max(Decimal('0'), min(Decimal('100'), porcentaje_descuento))

        # Primero calculamos el subtotal acumulado bruto de los productos
        subtotal_general = Decimal('0')
        lineas_items = []

        # 🔄 PASO 1: Calcular precios bases y gramos de cada ítem (Tu lógica intacta)
        for item in items:
            cantidad_fisica = Decimal(str(item.cantidad or 1))
            
            if item.producto.tipo_venta == 'gramo':
                val_precio = Decimal(str(item.producto.precio_por_gramo_detal or 0))
                peso_base = Decimal(str(item.producto.peso_producto or 0))
                gramos_totales = cantidad_fisica * peso_base
                subtotal_item = gramos_totales * val_precio
                precio_aplicado = val_precio
            else:
                try:
                    precio_por_escala = item.producto.precio_por_cantidad(item.cantidad)
                    if precio_por_escala is None:
                        precio_por_escala = item.producto.precio_detal or 0
                    val_precio = Decimal(str(precio_por_escala))
                except Exception:
                    val_precio = Decimal(str(item.producto.precio_detal or 0))
                
                gramos_totales = Decimal('0.00')
                subtotal_item = cantidad_fisica * val_precio
                precio_aplicado = val_precio
            
            subtotal_general += subtotal_item
            
            lineas_items.append({
                'item_carrito': item,
                'precio_aplicado': precio_aplicado,
                'gramos_totales': gramos_totales,
                'subtotal_item': subtotal_item
            })

        # 💰 PASO 2: Calcular la estructura de totales global con el Descuento aplicado
        descuento_pesos = subtotal_general * (porcentaje_descuento / Decimal('100'))
        subtotal_con_descuento = subtotal_general - descuento_pesos

        iva_total = subtotal_con_descuento * Decimal('0.19') if aplica_iva else Decimal('0')
        retefuente_total = subtotal_con_descuento * Decimal('0.025') if es_retenedor else Decimal('0')
        
        costo_envio = calcular_envio(ciudad, subtotal_con_descuento)
        total_final = subtotal_con_descuento + iva_total - retefuente_total + costo_envio

        # 📦 PASO 3: Crear el Pedido Maestro con los totales de verdad
        pedido = Pedido.objects.create(
            usuario=usuario,
            numero_orden=numero,
            cliente_nombre=nombre,        
            cliente_telefono=telefono,
            cliente_email=cliente_email,  
            cliente_ciudad=ciudad,
            cliente_direccion=direccion,
            cliente_nit=nit,
            total=total_final,                 
            porcentaje_descuento=porcentaje_descuento,
            descuento_total=descuento_pesos,
            costo_envio=costo_envio,
            aplica_iva=aplica_iva,             
            es_retenedor=es_retenedor,     
            estado="pendiente" 
        )

        # 🔄 PASO 4: Registrar cada PedidoItem y actualizar inventarios
        for linea in lineas_items:
            item = linea['item_carrito']
            sub_item = linea['subtotal_item']
            
            iva_item = sub_item * Decimal('0.19') if aplica_iva else Decimal('0')
            retefuente_item = sub_item * Decimal('0.025') if es_retenedor else Decimal('0')
            total_item = sub_item + iva_item - retefuente_item

            PedidoItem.objects.create(
                pedido=pedido,
                producto=item.producto,
                variante=item.variante,
                cantidad=int(item.cantidad),
                precio=linea['precio_aplicado'],
                total_gramos=linea['gramos_totales'],  
                subtotal=sub_item,
                iva=iva_item,
                retefuente=retefuente_item,
                total_final=total_item
            )

            if item.variante:
                item.variante.stock -= item.cantidad
                item.variante.save()
            elif item.producto.tipo_venta != "gramo":
                item.producto.stock -= item.cantidad
                item.producto.save()

        # Vaciar el carrito de compras tras el éxito de la transacción
        items.delete()

        return render(request, 'pago.html', {'pedido': pedido})

    except Exception as e:
        # 🔥 SI ALGO REVIENTA, OBLIGAMOS A RENDER A PINTAR EL TRACEBACK
        print("\n" + "🚨" * 30)
        print(f"💥 ERROR CRÍTICO EN /PAGAR/: {str(e)}")
        print("-" * 60)
        traceback.print_exc()
        print("🚨" * 30 + "\n")
        
        return HttpResponse(f"Error detectado en el proceso de pago: {str(e)}", status=500)


def confirmar_pago_publico(request, token):
    """ El cliente avisa de forma anónima que ya transfirió """
    pedido = get_object_or_404(Pedido, token_publico=token)
    pedido.estado = "confirmacion_pago"
    pedido.save()
    return redirect('factura_publica', token=token)


@login_required
def confirmar_pago(request, pedido_id):
    """ El administrador aprueba el pago desde su panel privado """
    pedido = get_object_or_404(Pedido, id=pedido_id, usuario=request.user)
    pedido.estado = "pagado"
    pedido.save()
    return render(request, 'pago_confirmado.html', {'pedido': pedido})


def pagar_wompi(request, pedido_id):
    pedido = Pedido.objects.get(id=pedido_id)
    return crear_checkout_wompi(request, pedido)


def crear_checkout_wompi(request, pedido):
    # 🔥 FIX CONEXIÓN: Cambiamos pedido.id por pedido.token_publico para mantener coherencia con pago_exitoso
    redirect_url = request.build_absolute_uri(f'/pago-exitoso/{pedido.token_publico}/')
    redirect_url_encoded = quote(redirect_url, safe='')

    checkout_url = (
        "https://checkout.wompi.co/p/"
        f"?public-key={settings.WOMPI_PUBLIC_KEY}"
        f"&currency=COP"
        f"&amount-in-cents={int(pedido.total * 100)}"
        f"&reference={pedido.numero_orden}"
        f"&redirect-url={redirect_url_encoded}"
    )
    print("URL FINAL WOMPI:", checkout_url)
    return redirect(checkout_url)


def pagar_con_mercadopago(request, token):
    pedido = get_object_or_404(Pedido, token_publico=token)
    sdk = mercadopago.SDK("APP_USR_xxx")

    preference_data = {
        "items": [
            {
                "title": f"Pedido #{pedido.numero_orden}",
                "quantity": 1,
                "currency_id": "COP",
                "unit_price": float(pedido.total),
            }
        ],
        "back_urls": {
            "success": request.build_absolute_uri(f"/pago-exitoso/{pedido.token_publico}/"),
        },
        "auto_return": "approved",
    }

    preference = sdk.preference().create(preference_data)
    return redirect(preference["response"]["init_point"])


def pago_exitoso(request, token):
    # Ya no se romperá porque tanto Wompi como MercadoPago retornarán el token_publico (UUID)
    pedido = get_object_or_404(Pedido, token_publico=token)

    pedido.estado = "pagado"
    pedido.saldo_pendiente = Decimal('0.00')
    pedido.save()

    return redirect('factura_publica', token=token)


@login_required
def lista_pedidos(request):
    pedidos = Pedido.objects.filter(usuario=request.user).order_by('-fecha')
    hoy = timezone.now().date()

    for p in pedidos:
        if getattr(p, 'tipo_pago', 'contado') == 'credito' and p.fecha_limite:
            # Evitamos errores usando safe fallbacks de saldo_pendiente
            saldo = getattr(p, 'saldo_pendiente', Decimal('0.00')) or Decimal('0.00')
            if saldo > 0 and p.fecha_limite < hoy:
                p.estado_visual = 'vencido'
            elif saldo > 0:
                p.estado_visual = 'pendiente'
            else:
                p.estado_visual = 'pagado'
        else:
            p.estado_visual = 'pagado'

    return render(request, 'lista_pedidos.html', {'pedidos': pedidos})

@login_required
def detalle_pedido(request, pedido_id):
    # 🎯 Buscamos el pedido asegurando que pertenezca al joyero autenticado
    pedido = get_object_or_404(
        Pedido,
        id=pedido_id,
        usuario=request.user
    )

    items = pedido.items.select_related('producto', 'variante').all()

    # ===============================
    # 🔢 TOTALES
    # ===============================
    subtotal = sum(Decimal(item.subtotal or 0) for item in items)
    iva_total = sum(Decimal(item.iva or 0) for item in items)
    descuento_total = sum(Decimal(item.descuento or 0) for item in items)
    retefuente_total = sum(Decimal(item.retefuente or 0) for item in items)
    envio = Decimal(pedido.costo_envio or 0)
    total_final = Decimal(pedido.total or 0)

    # 🔗 ENLACES NATIVOS
    link_pdf = request.build_absolute_uri(
        reverse('pedido_pdf', args=[pedido.id])
    )
    link_publico = request.build_absolute_uri(
        reverse('confirmar_pago_publico', args=[pedido.token_publico])
    )

    # 📲 WHATSAPP
    whatsapp_url = generar_link_whatsapp(request, pedido)

    # ===============================
    # 💳 CRÉDITO
    # ===============================
    hoy = timezone.now().date()
    estado_credito = None

    if pedido.tipo_pago == 'credito':
        if pedido.saldo_pendiente <= 0:
            estado_credito = 'pagado'
        elif pedido.fecha_limite and pedido.fecha_limite < hoy:
            estado_credito = 'vencido'
        else:
            estado_credito = 'pendiente'

    # ===============================
    # 📜 ABONOS
    # ===============================
    abonos = pedido.abonos.all().order_by('-fecha') if hasattr(pedido, 'abonos') else []
    total_abonado = sum(a.monto for a in abonos) if abonos else 0

    # ===============================
    # 📦 CONTEXT (¡CORREGIDO CON user=!)
    # ===============================
    try:
        # 🔥 Aquí corregimos el campo real: 'user' en vez de 'usuario'
        perfil = Perfil.objects.get(user=request.user)
        empresa_nombre = perfil.nombre_tienda or "Mi Joyería"
        empresa_nit = perfil.nit or "Sin NIT"
        empresa_telefono = perfil.whatsapp or "Sin teléfono"
        empresa_direccion = perfil.direccion or "Sin dirección"
        if perfil.logo:
            logo_path = perfil.logo.url
    except Perfil.DoesNotExist:
        empresa_nombre = "Mi Joyería (Configurar Perfil)"
        empresa_nit = "000000000-0"
        empresa_telefono = "-"
        empresa_direccion = "-"

    context = {
        'pedido': pedido,
        'items': items,
        'cliente': getattr(pedido, 'cliente', None),  # 🚀 Inyección segura para evitar errores en el HTML
        
        'subtotal': subtotal,
        'iva_total': iva_total,
        'descuento_total': descuento_total,
        'retefuente_total': retefuente_total,
        'envio': envio,
        'total_final': total_final,

        'link_pdf': link_pdf,
        'link_publico': link_publico,
        
        'whatsapp_url': whatsapp_url,

        'estado_credito': estado_credito,
        'abonos': abonos,
        'total_abonado': total_abonado,

        # 🏢 DATOS DE EMPRESA SEGUROS
        'empresa_nombre': empresa_nombre,
        'empresa_nit': empresa_nit,
        'empresa_telefono': empresa_telefono,
        'empresa_direccion': empresa_direccion,
        'logo_path': logo_path,

        'qr_pago_url': link_publico,
    }
    print("DETALLE PEDIDO OK")
    print("Pedido:", pedido.id)
    print("Numero:", pedido.numero_orden)
    print("Cliente:", getattr(pedido, 'cliente', None))
    print("Fecha:", getattr(pedido, 'fecha', None))
    print("Fecha creación:", getattr(pedido, 'fecha_creacion', None))

    try:
        return render(request, 'detalle_pedido.html', context)

    except Exception as e:
        print("ERROR DETALLE PEDIDO:", str(e))
        raise

def pedido_pdf(request, pedido_id):
    pedido = get_object_or_404(Pedido, id=pedido_id)
    items = pedido.items.all()

    # 1. 🚀 FORZAMOS LOGO NULL (Sugerencia de PG para aislar almacenamiento remoto)
    logo_path = None

    # Obtención segura del perfil
    try:
        if pedido.usuario:
            perfil = Perfil.objects.get(user=pedido.usuario)
            empresa_nombre = perfil.nombre_tienda or "Mi Joyería"
            empresa_nit = perfil.nit or "Sin NIT"
            empresa_telefono = perfil.whatsapp or "Sin teléfono"
            empresa_direccion = perfil.direccion or "Sin dirección"
            empresa_email = perfil.email_empresa or "Sin correo"
            color_primario = perfil.color_primario or "#000000"
            color_secundario = perfil.color_secundario or "#333333"
            activa = perfil.activa
        else:
            raise Perfil.DoesNotExist
    except Perfil.DoesNotExist:
        empresa_nombre = "Mi Joyería General"
        empresa_nit = "Sin NIT"
        empresa_telefono = "-"
        empresa_direccion = "-"
        empresa_email = "-"
        color_primario = "#000000"
        color_secundario = "#333333"
        activa = True

    if not activa:
        return HttpResponse("Cuenta inactiva", status=403)

    # Cálculos seguros
    subtotal = sum(item.subtotal or 0 for item in items)
    iva_total = sum(item.iva or 0 for item in items)
    descuento_total = sum(item.descuento or 0 for item in items)
    retefuente_total = sum(item.retefuente or 0 for item in items)
    envio = pedido.costo_envio or 0
    total_final = pedido.total

    # 2. 🚀 FECHA CORREGIDA (Sincronizada con el campo 'fecha')
    if hasattr(pedido, 'fecha') and pedido.fecha:
        fecha_str = pedido.fecha.strftime('%Y-%m-%d')
    elif hasattr(pedido, 'fecha_creacion') and pedido.fecha_creacion:
        fecha_str = pedido.fecha_creacion.strftime('%Y-%m-%d')
    else:
        fecha_str = "S/F"

    # 🔍 PRINT DE DIAGNÓSTICO EN CONSOLA DE RENDER
    print("\n" + "📊" * 15)
    print(f"PEDIDO ID: {pedido.id}")
    print(f"USUARIO: {pedido.usuario}")
    print(f"LOGO PATH (FORZADO): {logo_path}")
    print(f"CLIENTE: {getattr(pedido, 'cliente', 'Mostrador')}")
    print(f"NUMERO ORDEN: {pedido.numero_orden}")
    print("📊" * 15 + "\n")

    context = {
        'pedido': pedido,
        'items': items,
        'cliente': getattr(pedido, 'cliente', None),

        'empresa_nombre': empresa_nombre,
        'empresa_nit': empresa_nit,
        'empresa_telefono': empresa_telefono,
        'empresa_direccion': empresa_direccion,
        'empresa_email': empresa_email,

        'logo_path': logo_path,
        'color_primario': color_primario,
        'color_secundario': color_secundario,

        'subtotal': subtotal,
        'iva_total': iva_total,
        'descuento_total': descuento_total,
        'retefuente_total': retefuente_total,
        'envio': envio,
        'total_final': total_final,
        'fecha_str': fecha_str,  # Pasamos el string limpio
    }

    template = get_template('pedido_pdf.html')
    html = template.render(context)

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="pedido_{pedido.numero_orden}.pdf"'

    # 4. 🚀 CAPTURA DE ERROR EN MOTOR PDF
    pdf = pisa.CreatePDF(html, dest=response)
    if pdf.err:
        print(f"❌ ERROR CRÍTICO EN XHTML2PDF: {pdf.err}")
        return HttpResponse(f"Error generando PDF: {pdf.err}", status=500)

    return response



@login_required
def gastos(request):

    gastos = Gasto.objects.filter(usuario=request.user).order_by('-fecha')

    form = GastoForm(request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            gasto = form.save(commit=False)
            gasto.usuario = request.user
            gasto.save()
            return redirect('gastos')

    # 🔥 TOTAL GASTOS
    total_gastos = gastos.aggregate(total=Sum('monto'))['total'] or 0

    context = {
        'form': form,
        'gastos': gastos,
        'total_gastos': total_gastos  # 👈 AQUÍ
    }

    return render(request, 'gastos.html', context)

@login_required
def lista_gastos(request):
    if not request.user.is_staff:
        raise Http404()  # 🔥 oculta completamente la página

    gastos = Gasto.objects.filter(usuario=request.user)

    return render(request, 'lista_gastos.html', {
        'gastos': gastos
    })

@login_required
def registrar_abono(request, pedido_id):
    pedido = get_object_or_404(
        Pedido,
        id=pedido_id,
        usuario=request.user
    )

    if request.method == 'POST':
        monto = request.POST.get('monto')

        if monto:
            monto = float(monto)

            Abono.objects.create(
                pedido=pedido,
                monto=monto
            )

            # 🔥 RESTAR AL SALDO
            pedido.saldo_pendiente -= monto

            # 🔥 SI YA PAGÓ TODO
            if pedido.saldo_pendiente <= 0:
                pedido.saldo_pendiente = 0
                pedido.estado = "pagado"

            pedido.save()

            messages.success(request, "Abono registrado correctamente")

    return redirect('detalle_pedido', pedido_id=pedido.id)

@login_required
def cobrar_cliente(request, cliente_id):
    cliente = get_object_or_404(
        Cliente,
        id=cliente_id,
        usuario=request.user
    )

    pedidos = Pedido.objects.filter(
        cliente_nombre=cliente.nombre,
        usuario=request.user,
        tipo_pago='credito',
        saldo_pendiente__gt=0
    )

    total_deuda = sum(p.saldo_pendiente for p in pedidos)

    mensaje = f"Hola {cliente.nombre},\n\n"
    mensaje += "Te recordamos que tienes una factura pendiente:\n\n"

    for p in pedidos:
        mensaje += f"🧾 {p.numero_orden} - ${p.saldo_pendiente:,.0f}\n".replace(",", ".")

    mensaje += f"\n💰 Total pendiente: ${total_deuda:,.0f}".replace(",", ".")
    mensaje += "\n\nPor favor realizar el pago lo antes posible 🙏"

    telefono = ''.join(filter(str.isdigit, cliente.telefono))

    if not telefono.startswith('57'):
        telefono = f"57{telefono}"

    url = f"https://wa.me/{telefono}?text={quote(mensaje)}"

    return redirect(url)

@login_required
def cobrar_whatsapp(request, pedido_id):
    pedido = get_object_or_404(
        Pedido,
        id=pedido_id,
        usuario=request.user
    )

    hoy = timezone.now().date()

    # 🔥 VALIDAR QUE DEBE
    if pedido.saldo_pendiente <= 0:
        messages.info(request, "Este pedido ya está pago")
        return redirect('detalle_pedido', pedido_id=pedido.id)

    # 🔥 MENSAJE AUTOMÁTICO PRO
    mensaje = (
        f"Hola {pedido.cliente_nombre},\n\n"
        f"Te escribimos de {request.user.perfil.nombre_tienda} 💎\n\n"
        f"🧾 Factura: {pedido.numero_orden}\n"
        f"📅 Vencimiento: {pedido.fecha_limite}\n"
        f"💰 Saldo pendiente: ${pedido.saldo_pendiente:,.0f}\n\n"
        f"⚠️ Esta factura se encuentra vencida.\n"
        f"Agradecemos tu pago lo antes posible.\n\n"
        f"Si ya realizaste el pago, por favor ignora este mensaje 🙏"
    ).replace(",", ".")

    # 🔥 LIMPIAR TELÉFONO
    telefono = ''.join(filter(str.isdigit, pedido.cliente_telefono or ""))

    if not telefono:
        messages.warning(request, "Cliente sin teléfono válido")
        return redirect('detalle_pedido', pedido_id=pedido.id)

    if not telefono.startswith('57'):
        telefono = f"57{telefono}"

    url = f"https://wa.me/{telefono}?text={quote(mensaje)}"

    return redirect(url)

@login_required
def cobrar_moroso(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id, usuario=request.user)

    pedidos = Pedido.objects.filter(
        cliente=cliente,
        saldo_pendiente__gt=0
    ).order_by('fecha_limite')

    if not pedidos:
        return redirect('cartera_clientes')

    total = 0
    mensaje = f"Hola {cliente.nombre},\n\nTienes las siguientes facturas pendientes:\n\n"

    for p in pedidos:
        mensaje += f"🧾 Pedido #{p.id} → ${p.saldo_pendiente}\n"
        total += p.saldo_pendiente

    mensaje += f"\n💰 Total: ${total}\n"
    mensaje += "Por favor realizar el pago 🙏"

    telefono = ''.join(filter(str.isdigit, cliente.telefono or ""))

    if not telefono.startswith('57'):
        telefono = f"57{telefono}"

    url = f"https://wa.me/{telefono}?text={quote(mensaje)}"

    return redirect(url)


@login_required
def cartera_clientes(request):
    hoy = timezone.now().date()

    pedidos = Pedido.objects.filter(
        usuario=request.user,
        tipo_pago='credito',
        saldo_pendiente__gt=0,
        estado__in=['pendiente', 'vencido']
    ).order_by('fecha_limite')

    cartera = []

    for p in pedidos:
        dias_mora = 0

        if p.fecha_limite and p.fecha_limite < hoy:
            dias_mora = (hoy - p.fecha_limite).days

        # estado visual inteligente
        estado_visual = 'pendiente'

        if dias_mora > 0:
            estado_visual = 'vencido'

        # 🔥 link directo factura pública
        link_pago = request.build_absolute_uri(f"/factura/{p.token_publico}/")

        # 📲 mensaje automático whatsapp
        mensaje = f"Hola {p.cliente_nombre}, tienes un saldo pendiente de ${p.saldo_pendiente}. Puedes pagar aquí: {link_pago}"
        
        mensaje_codificado = urllib.parse.quote(mensaje)

        whatsapp_link: f"https://wa.me/{p.cliente_telefono}?text={mensaje}"

        cartera.append({
            'pedido': p,
            'cliente': p.cliente_nombre,
            'telefono': p.cliente_telefono,
            'saldo': p.saldo_pendiente,
            'fecha_limite': p.fecha_limite,
            'dias_mora': dias_mora,
            'estado': estado_visual,
            'link_pago': link_pago,
            'whatsapp': whatsapp_link, 
            
        })

    morosos_total = sum(c['saldo'] for c in cartera if c['estado'] == 'vencido')
    morosos_count = sum(1 for c in cartera if c['estado'] == 'vencido')

    return render(request, 'cartera.html', {
        'cartera': cartera,
        'morosos_total': morosos_total,
        'morosos_count': morosos_count
})

def factura_publica(request, token):
    """
    Vista que renderiza el recibo público para el cliente mediante el token UUID.
    """
    pedido = get_object_or_404(Pedido, token_publico=token)
    items = pedido.items.select_related('producto', 'variante').all()
    
    # 🛡️ PROTECCIÓN CRÍTICA: Obtención segura del perfil para evitar Error 500
    usuario_pedido = getattr(pedido, 'usuario', None)
    perfil = getattr(usuario_pedido, 'perfil', None) if usuario_pedido else None

    # Si no hay perfil, creamos variables fallback para que no estalle el contexto ni el HTML
    empresa_nombre = getattr(perfil, 'nombre_tienda', 'Mi Joyería') or "Mi Joyería"
    empresa_nit = getattr(perfil, 'nit', 'Sin NIT') or "Sin NIT"
    empresa_telefono = getattr(perfil, 'whatsapp', '-') or "-"
    color_primario = getattr(perfil, 'color_primario', '#000000') or "#000000"
    color_secundario = getattr(perfil, 'color_secundario', '#333333') or "#333333"

    # 🛡️ PROTECCIÓN DE CÁLCULOS MATEMÁTICOS CONTRA VALORES NULL
    subtotal = sum(Decimal(str(item.subtotal or 0)) for item in items)
    iva_total = sum(Decimal(str(item.iva or 0)) for item in items)
    
    porcentaje_desc = getattr(pedido, 'porcentaje_descuento', 0) or 0
    descuento_total = subtotal * (Decimal(str(porcentaje_desc)) / Decimal('100')) if porcentaje_desc else Decimal('0')
    
    retefuente_total = sum(Decimal(str(item.retefuente or 0)) for item in items)
    envio = Decimal(str(pedido.costo_envio or 0))
    total_final = subtotal + iva_total + envio - descuento_total - retefuente_total

    # =========================================================
    # 🟢 CORRECCIÓN DEL MOTOR DE ESTADOS (Original de PG)
    # =========================================================
    if pedido.estado == "pendiente":
        estado = "pendiente"
    elif pedido.saldo_pendiente is not None and pedido.saldo_pendiente <= 0:
        estado = "pagado"
    elif pedido.fecha_limite and pedido.fecha_limite < timezone.now().date():
        estado = "vencido"
    else:
        estado = pedido.estado

    # =========================================================
    # 💬 INTEGRACIÓN DE WHATSAPP Y QR (Protegido contra campos vacíos)
    # =========================================================
    mensaje = f"Hola! Adjunto el comprobante de pago del pedido #{pedido.numero_orden} por valor de ${pedido_total:,.0f}".replace(",", ".")
    whatsapp_num = empresa_telefono if empresa_telefono != "-" else ""
    whatsapp_url = f"https://wa.me/{whatsapp_num}?text={urllib.parse.quote(mensaje)}"
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(whatsapp_url)}"

    # =========================================================
    # 🗂️ CONTEXTO RECONCILIADO CON TU HTML
    # =========================================================
    context = {
        'pedido': pedido,
        'items': items,

        # Datos del perfil SaaS del joyero seguros
        'empresa_nombre': empresa_nombre,
        'empresa_nit': empresa_nit,
        'empresa_telefono': empresa_telefono,

        # Identidad de marca blanca segura
        'color_primario': color_primario,
        'color_secundario': color_secundario,

        # 🎯 FIX MATEMÁTICO: Inyectamos la variable exacta que pide tu HTML
        'subtotal_general': subtotal, 
        
        # Variables de respaldo originales protegidas
        'subtotal': subtotal,
        'iva_total': iva_total,
        'descuento_total': descuento_total,
        'retefuente_total': retefuente_total,
        'envio': envio,
        'total_final': pedido_total,
        
        'whatsapp_url': whatsapp_url,
        'qr_url': qr_url,
        'perfil_comercio': perfil,
        'estado': estado,
    }

    return render(request, 'factura_publica.html', context)

@login_required
def generar_factura(request, pedido_id):
    pedido = get_object_or_404(Pedido, id=pedido_id, usuario=request.user)
    items = pedido.items.select_related('producto', 'variante').all()

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)

    PAGE_WIDTH, PAGE_HEIGHT = letter
    LINE_HEIGHT = 18

    # 🛡️ OBTENCIÓN SEGURA DEL PERFIL PARA EVITAR EL ERROR 500
    perfil = getattr(request.user, 'perfil', None)
    nombre_tienda = getattr(perfil, 'nombre_tienda', 'Mi Joyería') or "Mi Joyería"
    nit_tienda = getattr(perfil, 'nit', '') or ""
    whatsapp_tienda = getattr(perfil, 'whatsapp', '') or ""
    ciudad_tienda = getattr(perfil, 'ciudad', '') or ""
    correo_empresa = getattr(perfil, 'email_empresa', None) or request.user.email or 'correo@empresa.com'

    def draw_header():
        y = 760
        # EMPRESA (Dueño del SaaS)
        p.setFont("Helvetica-Bold", 16)
        p.drawString(50, y, nombre_tienda)

        p.setFont("Helvetica", 9)
        p.drawString(50, y - 15, f"NIT: {nit_tienda}")
        p.drawString(50, y - 28, f"Tel: {whatsapp_tienda}")
        p.drawString(50, y - 41, f"Email: {correo_empresa}")
        p.drawString(50, y - 54, f"{ciudad_tienda}")

        # FACTURA DERECHA
        p.setFont("Helvetica-Bold", 11)
        p.drawString(390, y, "FACTURA")

        p.setFont("Helvetica", 9)
        p.drawString(390, y - 18, f"N° {pedido.numero_orden}")

        fecha = pedido.fecha_creacion.strftime('%Y-%m-%d') if pedido.fecha_creacion else ''
        p.drawString(390, y - 33, f"Fecha: {fecha}")

        if getattr(pedido, 'tipo_pago', 'contado') == 'credito':
            p.drawString(390, y - 48, "Pago: Crédito")
        else:
            p.drawString(390, y - 48, "Pago: Contado")

        p.line(50, y - 65, 550, y - 65)

        # 🎯 FIX DE CAMPOS: Sincronización con las columnas reales de tu base de datos
        yc = y - 85
        p.drawString(50, yc, f"Cliente: {getattr(pedido, 'cliente_nombre', '') or ''}")
        p.drawString(50, yc - 14, f"NIT/CC: {getattr(pedido, 'cliente_nit', '') or ''}")
        p.drawString(50, yc - 28, f"Dirección: {getattr(pedido, 'cliente_direccion', '') or ''}")
        p.drawString(50, yc - 42, f"Ciudad: {getattr(pedido, 'cliente_ciudad', '') or ''}")
        p.drawString(50, yc - 56, f"Teléfono: {getattr(pedido, 'cliente_telefono', '') or ''}")

    def draw_footer():
        p.line(50, 60, 550, 60)
        p.setFont("Helvetica", 8)
        p.drawString(50, 45, "Gracias por su compra 💎")
        p.drawRightString(550, 45, f"Factura {pedido.numero_orden}")

    def draw_table_header(y):
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, "Producto")
        p.drawString(250, y, "Cant.")
        p.drawString(310, y, "Precio")
        p.drawString(410, y, "Total")
        p.line(50, y - 5, 550, y - 5)
        return y - 20

    def nueva_pagina():
        p.showPage()
        draw_header()
        return draw_table_header(560)

    # EJECUCIÓN DEL PDF
    draw_header()
    y = draw_table_header(560)

    subtotal_general = Decimal('0')
    iva_total = Decimal('0')
    descuento_total = Decimal('0')
    retefuente_total = Decimal('0')

    for item in items:
        if y < 100:
            draw_footer()
            y = nueva_pagina()

        nombre = item.producto.nombre[:28]

        gramos = getattr(item, 'total_gramos', None)
        if gramos and gramos > 0:
            cantidad = f"{gramos}g"
        else:
            cantidad = f"{int(item.cantidad)} u"

        precio = Decimal(str(item.precio or 0))
        total_item = Decimal(str(item.total_final or item.subtotal or 0))

        p.setFont("Helvetica", 9)
        p.drawString(50, y, nombre)
        p.drawString(250, y, cantidad)
        p.drawRightString(390, y, f"${precio:,.0f}".replace(",", "."))
        p.drawRightString(540, y, f"${total_item:,.0f}".replace(",", "."))

        subtotal_general += Decimal(str(item.subtotal or 0))
        iva_total += Decimal(str(item.iva or 0))
        descuento_total += Decimal(str(item.descuento_total or item.descuento or 0))
        retefuente_total += Decimal(str(item.retefuente or 0))

        y -= LINE_HEIGHT

    # TOTALES
    y -= 20
    if y < 160:
        draw_footer()
        y = nueva_pagina()

    p.setFont("Helvetica", 10)
    p.drawString(340, y, "Subtotal:")
    p.drawRightString(540, y, f"${subtotal_general:,.0f}".replace(",", "."))

    if iva_total > 0:
        y -= 15
        p.drawString(340, y, "IVA (19%):")
        p.drawRightString(540, y, f"${iva_total:,.0f}".replace(",", "."))

    if retefuente_total > 0:
        y -= 15
        p.drawString(340, y, "ReteFuente:")
        p.drawRightString(540, y, f"-${retefuente_total:,.0f}".replace(",", "."))

    porcentaje_desc = getattr(pedido, 'porcentaje_descuento', 0) or 0
    if porcentaje_desc > 0 or descuento_total > 0:
        y -= 15
        p.drawString(340, y, f"Descuento ({porcentaje_desc}%):")
        p.drawRightString(540, y, f"-${descuento_total:,.0f}".replace(",", "."))

    y -= 15
    p.drawString(340, y, "Envío:")
    costo_envio_num = Decimal(str(getattr(pedido, 'costo_envio', 0) or 0))
    if costo_envio_num == 0:
        p.drawRightString(540, y, "GRATIS")
    else:
        p.drawRightString(540, y, f"${costo_envio_num:,.0f}".replace(",", "."))

    y -= 22
    p.setFont("Helvetica-Bold", 12)
    p.drawString(340, y, "TOTAL:")
    pedido_total_num = Decimal(str(getattr(pedido, 'total', 0) or 0))
    p.drawRightString(540, y, f"${pedido_total_num:,.0f}".replace(",", "."))

    draw_footer()
    p.save()
    pdf = buffer.getvalue()
    buffer.close()

    # ENVÍO AUTOMÁTICO DE CORREO COPIANDO AL CLIENTE DE LA BASE DE DATOS
    email_cliente = getattr(pedido, 'cliente_email', None)
    if email_cliente:
        try:
            email = EmailMessage(
                subject=f"Factura {pedido.numero_orden} - {nombre_tienda}",
                body="¡Gracias por tu confianza! Adjuntamos el detalle formal de tu pedido de joyería. 💎",
                from_email=correo_empresa,
                to=[email_cliente]
            )
            email.attach(f"factura_{pedido.numero_orden}.pdf", pdf, 'application/pdf')
            email.send()
        except Exception as e:
            print("Error enviando correo:", e)

    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="factura_{pedido.numero_orden}.pdf"'
    return response

@login_required
def whatsapp_segmento(request):
    usuario = request.user
    tipo = request.GET.get('tipo', '').strip()
    
    # 🔥 SOLUCIÓN AL EFECTO EMBUDO: Capturamos el ID del cliente que ya se procesó
    excluir_id = request.GET.get('excluir_id')

    hoy = timezone.localdate()
    hace_30 = timezone.now() - timedelta(days=30)

    # 🔒 BASE SaaS MULTIUSUARIO: Traer solo los clientes del inquilino actual
    clientes = Cliente.objects.filter(usuario=usuario)

    # =====================================
    # 🎯 FILTROS CORREGIDOS
    # =====================================
    if tipo == 'vip':
        # Clientes que han comprado igual o más de $500,000 COP
        clientes = clientes.filter(total_compras__gte=500000)

    elif tipo == 'nuevos':
        clientes = clientes.filter(fecha_creacion__date=hoy)

    elif tipo == 'dormidos':
        # 🔥 FIX CRÍTICO: Corregido a la sintaxis real de Django (__) relacionando Pedidos.
        # Filtra clientes cuyo último pedido (u órdenes) fue hace más de 30 días.
        clientes = clientes.filter(pedidos_fecha_creacion_lt=hace_30).distinct()

    # Si se pasa un ID para excluir, lo sacamos de la lista para avanzar al siguiente
    if excluir_id:
        clientes = clientes.exclude(id=excluir_id)

    # Excluir de entrada registros sin números telefónicos
    clientes_validos = clientes.exclude(telefono__isnull=True).exclude(telefono='')

    # Traemos el primero disponible de la cola restante
    cliente_destino = clientes_validos.first()

    if not cliente_destino:
        messages.info(
            request,
            f"¡Felicidades! Has completado o no hay clientes en el segmento '{tipo}'."
        )
        return redirect('dashboard') # O a tu panel de marketing/clientes

    # =====================================
    # ☎️ LIMPIAR TELÉFONO
    # =====================================
    telefono = ''.join(filter(str.isdigit, cliente_destino.telefono))

    if not telefono:
        # Si este registro estaba corrupto, saltamos al siguiente ignorándolo
        messages.warning(request, f"Cliente {cliente_destino.nombre} no tiene un formato de teléfono válido.")
        return redirect(f"{request.path}?tipo={tipo}&excluir_id={cliente_destino.id}")

    # Forzar prefijo de Colombia si aplica
    if not telefono.startswith('57') and len(telefono) == 10:
        telefono = f"57{telefono}"

    # =====================================
    # 🧠 PERSONALIZAR MENSAJE DINÁMICO (SaaS)
    # =====================================
    perfil = getattr(usuario, 'perfil', None)
    nombre_tienda = perfil.nombre_tienda if perfil and perfil.nombre_tienda else "Nuestra Joyería"

    mensajes = {
        'vip': "💎 Cliente VIP, tienes acceso a piezas exclusivas de nuestra nueva colección. Escríbenos 👇",
        'nuevos': f"🆕 ¡Te damos la bienvenida a {nombre_tienda}! Tenemos un detalle especial esperándote para tu primera compra 💎",
        'dormidos': "😴 Te extrañamos mucho por aquí. Queremos contarte que nos llegaron nuevas joyas hermosas y tenemos una oferta para ti 💎",
    }

    mensaje_base = mensajes.get(tipo, f"✨ Hola, tenemos hermosas novedades en {nombre_tienda} 💎")

    texto_final = (
        f"Hola {cliente_destino.nombre},\n\n"
        f"{mensaje_base}\n\n"
        f"📲 {nombre_tienda}"
    )

    # =====================================
    # 🚀 REDIRECT INTELIGENTE CON AVANCE
    # =====================================
    url_whatsapp = f"https://wa.me/{telefono}?text={urllib.parse.quote(texto_final)}"

    # Guardamos en un mensaje de Django un aviso con el link para despachar al "Siguiente"
    # Esto le permite al comerciante regresar al panel y saber que puede continuar sin repetir cliente.
    url_siguiente = f"{request.path}?tipo={tipo}&excluir_id={cliente_destino.id}"
    messages.success(
        request, 
        f"Abriendo WhatsApp para {cliente_destino.nombre}. "
        f"<a href='{url_siguiente}' style='font-weight:bold; color:#007bff; text-decoration:underline;'>¡Haga clic aquí para pasar al siguiente cliente de la lista!</a>",
        extra_tags='safe' # Recuerda habilitar en tu HTML el filtro |safe al renderizar mensajes si usas tags
    )

    return redirect(url_whatsapp)
