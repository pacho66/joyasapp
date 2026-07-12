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
import hashlib
from django.views.decorators.csrf import csrf_exempt
import openpyxl
import time
import logging
from django.db.models import Sum, Avg, Count
import re

# 🏪 IMPORTACIONES DE LA APP PRODUCTOS
from productos.models import Producto, Categoria, ProductoImagen, CarritoItem, Cliente, Perfil
from productos.services.precios import calcular_precio_producto
from productos.services.envios import calcular_envio

# 📂 IMPORTACIONES DE LA APP ACTUAL (Pedidos/Ventas)
from .models import ProductoVariante, Pedido, PedidoItem, Perfil, Abono, Gasto
from .forms import RegistroForm, ConfiguracionNegocioForm, GastoForm, ProductoForm
from django.db import IntegrityError
from .services.producto_service import ProductoService 
from .utils import calcular_totales_con_reglas_fiscales

from .utils import generar_link_whatsapp, generar_numero_orden 
from .utils import generar_link_whatsapp, generar_numero_orden, generar_pdf_pedido

# Configuración del Logger global para este módulo
logger = logging.getLogger('joyasapp.auditoria')

# Centro de control de tarifas del SaaS
PRECIOS_PLANES = {
    'basico': {
        'nombre': 'Básico',
        'precio_mensual': Decimal('50000.00'),  # Ejemplo en COP
        'descripcion': 'Ideal para tiendas que están iniciando.'
    },
    'pro': {
        'nombre': 'Pro',
        'precio_mensual': Decimal('120000.00'),
        'descripcion': 'Para joyerías en crecimiento con pasarelas activas.'
    },
    'premium': {
        'nombre': 'Premium',
        'precio_mensual': Decimal('250000.00'),
        'descripcion': 'Todo ilimitado + Facturación Electrónica e integración Siigo.'
    }
}

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

def calcular_envio(ciudad=None, subtotal=0):
    """
    JoyasApp

    El costo del envío NO se calcula automáticamente.

    Cada joyero define posteriormente el valor del envío
    según la transportadora, ciudad o país.

    El pedido inicia con envío = 0.
    """
    return Decimal("0")

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
    perfil = Perfil.objects.first()        

    return render(request, 'login.html', {'perfil': perfil})

@login_required
def cerrar_sesion(request):
    logout(request)
    return redirect('login')
        
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

#@login_required
#def crear_producto(request):
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
                }
            )

        imagen_principal = request.FILES.get('imagen_principal')

        # 🚀 2. INSTANCIACIÓN MANUAL SEGURA
        producto = Producto(
            usuario=request.user,
            nombre=request.POST.get('nombre'),
            referencia=referencia,
            descripcion=request.POST.get('descripcion'),
            categoria_id=request.POST.get('categoria'),
            tipo_venta=request.POST.get('tipo_venta') or 'unidad',
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
        producto.save() 

        # 🎨 3. PROCESAMIENTO DE IMÁGENES DE GALERÍA
        imagenes = request.FILES.getlist('galeria')
        for imagen in imagenes:
            if imagen_principal and imagen.size == imagen_principal.size:
                continue
            ProductoImagen.objects.create(producto=producto, imagen=imagen)
            
        # ⚙️ 4. CONSTRUCTOR DE VARIANTES INTELIGENTES (Lectura de Listas Paralelas)
        v_colores = request.POST.getlist('v_color[]')
        v_tallas = request.POST.getlist('v_talla[]')
        v_stocks = request.POST.getlist('v_stock[]')
        v_precios = request.POST.getlist('v_precio[]')

        for i in range(len(v_colores)):
            try:
                c_val = v_colores[i].strip() if i < len(v_colores) and v_colores[i] else None
                t_val = v_tallas[i].strip() if i < len(v_tallas) and v_tallas[i] else None
                s_val = int(v_stocks[i].strip()) if i < len(v_stocks) and v_stocks[i] else 0
                p_val = float(v_precios[i].strip()) if i < len(v_precios) and v_precios[i] else 0

                # Creamos la variante únicamente si tiene al menos un identificador
                if c_val or t_val:
                    ProductoVariante.objects.create(
                        producto=producto,
                        color=c_val,
                        talla=t_val,
                        stock=s_val,
                        precio_venta=p_val # Resguardado si actualizaste el modelo, sino lo calcula por el precio base
                    )
            except (IndexError, ValueError):
                continue
            except IntegrityError:
                continue # Evita la caída si el usuario generó un duplicado exacto de color/talla

        messages.success(request, "¡Producto y sus variantes creados correctamente!")
        return redirect('dashboard')

    return render(request, 'crear_producto.html', {'categorias': categorias})

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

#@login_required
#def editar_producto(request, id):
    producto = Producto.objects.get(id=id, usuario=request.user)
    categorias = Categoria.objects.filter(usuario=request.user)

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
        producto.precio_por_gramo_detal = request.POST.get('precio_por_gramo_detal') or 0
        producto.precio_por_gramo_semimayor = request.POST.get('precio_por_gramo_semimayor') or 0
        producto.precio_por_gramo_mayor = request.POST.get('precio_por_gramo_mayor') or 0
        producto.destacado = (request.POST.get('destacado') == 'on')

        if request.FILES.get('imagen_principal'):
            producto.imagen_principal = request.FILES.get('imagen_principal')
        if request.FILES.get('certificado'):
            producto.certificado = request.FILES.get('certificado')
        if request.FILES.get('video'):
            producto.video = request.FILES.get('video')
        producto.save()

        galeria = request.FILES.getlist('galeria')
        for imagen in galeria:
            if producto.imagen_principal and imagen.size == producto.imagen_principal.size:
                continue
            ProductoImagen.objects.create(producto=producto, imagen=imagen)

        # 🔄 Reconstrucción limpia de variantes basadas en la matriz
        producto.variantes.all().delete()

        v_colores = request.POST.getlist('v_color[]')
        v_tallas = request.POST.getlist('v_talla[]')
        v_stocks = request.POST.getlist('v_stock[]')
        v_precios = request.POST.getlist('v_precio[]')

        for i in range(len(v_colores)):
            try:
                c_val = v_colores[i].strip() if i < len(v_colores) and v_colores[i] else None
                t_val = v_tallas[i].strip() if i < len(v_tallas) and v_tallas[i] else None
                s_val = int(v_stocks[i].strip()) if i < len(v_stocks) and v_stocks[i] else 0
                p_val = float(v_precios[i].strip()) if i < len(v_precios) and v_precios[i] else 0

                if c_val or t_val:
                    ProductoVariante.objects.create(
                        producto=producto,
                        color=c_val,
                        talla=t_val,
                        stock=s_val,
                        precio_venta=p_val
                    )
            except (IndexError, ValueError):
                continue
            except IntegrityError:
                continue

        return redirect('mis_productos')

    return render(request, 'editar_producto.html', {'producto': producto, 'categorias': categorias})
def _asegurar_gastos_basicos(usuario):
    """Inyecta la estructura de gastos base de JoyasApp protegiendo contra duplicados."""
    tiene_categoria = hasattr(Gasto, 'categoria')
    
    gastos_base = [
        {"nombre": "Compra de Oro / Insumos", "categoria": "fabricacion"},
        {"nombre": "Compra de Plata / Insumos", "categoria": "fabricacion"},
        {"nombre": "Piedras, Circones y Gemas", "categoria": "fabricacion"},
        {"nombre": "Procesos de Fundición", "categoria": "fabricacion"},
        {"nombre": "Insumos de Pulido y Soldadura", "categoria": "fabricacion"},
        {"nombre": "Ligas, Químicos y Ácidos", "categoria": "fabricacion"},
        {"nombre": "Cajas y Empaques de Lujo", "categoria": "fabricacion"},
        {"nombre": "Arriendo Taller/Oficina", "categoria": "fijo"},
        {"nombre": "Servicios Públicos (Luz/Internet)", "categoria": "fijo"},
        {"nombre": "Publicidad y Marketing", "categoria": "marketing"},
        {"nombre": "Transporte y Envíos", "categoria": "logistica"},
        {"nombre": "Comisiones Bancarias y Pasarelas", "categoria": "financiero"},
        {"nombre": "Papelería y Administración", "categoria": "administrativo"},
    ]
    
    for g in gastos_base:
        defaults = {"monto": 0.0}
        if tiene_categoria:
            defaults["categoria"] = g["categoria"]
            
        Gasto.objects.get_or_create(usuario=usuario, nombre=g["nombre"], defaults=defaults)

@login_required(login_url='/login/')
def dashboard(request):
    usuario = request.user

    # ✅ Perfil multiusuario del SaaS
    perfil, creado = Perfil.objects.get_or_create(
        user=usuario,
        defaults={
            'nombre_tienda': 'JoyasApp',
            'plan': 'gratis'
        }
    )

    # 🛡️ Protección de Gastos Activa
    _asegurar_gastos_basicos(usuario)

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

    fechas = [v['fecha_dia'].strftime('%d/%m') for v in ventas_por_dia if v['fecha_dia']]
    # 🛡️ PROTECCIÓN ANTI-NONE: Si no hay ventas, asigna 0.0 en vez de romper
    totales = [float(v['total']) if v['total'] is not None else 0.0 for v in ventas_por_dia]
    
    cliente_id = request.GET.get('cliente')
    if cliente_id:
        pedidos = pedidos.filter(cliente_id=cliente_id)

    clientes = Cliente.objects.filter(usuario=usuario)
    productos = Producto.objects.filter(usuario=usuario)

    # ==========================
    # 🔴 CARTERA Y MOROSOS (UNIFICADO Y CORREGIDO)
    # ==========================
    # 1. Buscamos primero los pedidos vencidos del usuario de forma directa
    pedidos_en_mora = Pedido.objects.filter(
        usuario=usuario,
        tipo_pago='credito',
        saldo_pendiente__gt=0,
        fecha_limite__lt=hoy
    )

    # 2. Calculamos el total exacto de la cartera vencida sin riesgo de multiplicarse
    cartera_agg = pedidos_en_mora.aggregate(suma_mora=Sum('saldo_pendiente'))['suma_mora']
    morosos_total = float(cartera_agg) if cartera_agg is not None else 0.0

    # 3. Obtenemos los clientes únicos que son dueños de esos pedidos en mora
    # Usamos list(set(...)) para tener la lista limpia de IDs de clientes morosos
    clientes_morosos_ids = pedidos_en_mora.values_list('cliente_id', flat=True).distinct()
    clientes_morosos = Cliente.objects.filter(id__in=clientes_morosos_ids)
    
    # El conteo real de personas deudoras en el dashboard
    morosos_count = clientes_morosos.count()
    
    # ==========================
    # FILTROS CLIENTES
    # ==========================
    clientes_filtrados = clientes

    if tipo == 'vip':
        clientes_filtrados = clientes.filter(total_compras__gte=500000)
    elif tipo == 'nuevos':
        clientes_filtrados = clientes.filter(fecha_creacion__date=hoy)
    elif tipo == 'dormidos':
        clientes_filtrados = clientes.filter(ultima_compra__lt=hace_30).distinct()
    elif tipo == 'morosos':
        # 💎 SOLUCIÓN UNIFICADA: Buscamos los IDs desde Pedido para evitar el colapso en Render
        ids_morosos = Pedido.objects.filter(
            usuario=usuario,
            tipo_pago='credito',
            saldo_pendiente__gt=0,
            fecha_limite__lt=hoy
        ).values_list('cliente_id', flat=True).distinct()
        
        clientes_filtrados = clientes.filter(id__in=ids_morosos)
    
    mensajes = {
        'vip': "💎 Clientes VIP",
        'nuevos': "✨ Clientes nuevos",
        'dormidos': "😴 Clientes inactivos",
        'morosos': "🔴 Clientes en mora"
    }
    mensaje = mensajes.get(tipo, "📊 Panel General")

    # =======================================================================
    # 🔥 SOLUCIÓN: Recalcular en caliente el total real de compras acumuladas
    # =======================================================================
    for clie in clientes_filtrados:
        total_real = Pedido.objects.filter(
            usuario=usuario,
            cliente_nombre=clie.nombre,
            estado__iexact='pagado'
        ).aggregate(suma=Sum('total'))['suma'] or 0
        clie.total_compras = total_real

    # ==========================
    # VENTAS Y ENVIOS
    # ==========================
    total_hoy = pedidos.filter(fecha__date=hoy).aggregate(total=Sum('total'))['total'] or 0
    total_ayer = pedidos.filter(fecha__date=ayer).aggregate(total=Sum('total'))['total'] or 0
    total_general = pedidos.aggregate(total=Sum('total'))['total'] or 0
    
    ganancias_mes = pedidos.filter(
        fecha__month=hoy.month,
        fecha__year=hoy.year
    ).aggregate(total=Sum('total'))['total'] or 0

    # =======================================================================
    # 💰 PANEL FINANCIERO JOYASAPP - EL DIFERENCIAL PRO
    # =======================================================================
    total_ingresos = float(total_general or 0)

    costos_pedidos = pedidos.aggregate(
        mat=Sum('costo_material'),
        mo=Sum('costo_mano_obra')
    )
    total_material = float(costos_pedidos['mat'] or 0)
    total_mano_obra = float(costos_pedidos['mo'] or 0)
    total_costos = total_material + total_mano_obra

    try:
        total_gastos = float(Gasto.objects.filter(usuario=usuario).aggregate(total=Sum('monto'))['total'] or 0)
    except Exception:
        total_gastos = 0.0

    utilidad_bruta = total_ingresos - total_costos
    utilidad_neta = utilidad_bruta - total_gastos

    margen_bruto = (utilidad_bruta / total_ingresos * 100) if total_ingresos > 0 else 0
    margen_neto = (utilidad_neta / total_ingresos * 100) if total_ingresos > 0 else 0

    # =======================================================================
    # 💎 VALORIZACIÓN DE INVENTARIO (Sincronizado con precio_costo y Variantes)
    # =======================================================================
    inventario = Producto.objects.filter(usuario=usuario).prefetch_related('variantes')
    inv_valorizado_costo = 0.0
    inv_valorizado_venta = 0.0

    for prod in inventario:
        p_costo = float(prod.precio_costo or 0)
        p_detal = float(prod.precio_detal or 0)
        
        if prod.variantes.exists():
            cant = sum(int(v.stock or 0) for v in prod.variantes.all())
        else:
            cant = int(prod.stock or 0)
        
        inv_valorizado_costo += cant * p_costo
        inv_valorizado_venta += cant * p_detal

    utilidad_potencial_inv = inv_valorizado_venta - inv_valorizado_costo
    
    # ==========================
    # PEDIDOS E INVENTARIO
    # ==========================
    pedidos_hoy = pedidos.filter(fecha__date=hoy).count()
    total_pedidos = pedidos.count()
    pedidos_recientes = pedidos.order_by('-fecha')[:5]
    pedidos_pendientes = pedidos.filter(estado='pendiente').count()
    pedidos_pagados = pedidos.filter(estado='pagado').count()

    total_clientes = clientes.count()
    clientes_vip = clientes.filter(total_compras__gte=500000).count()
    clientes_nuevos = clientes.filter(fecha_creacion__date=hoy).count()
    clientes_dormidos = clientes.filter(ultima_compra__lt=hace_30).distinct().count()

    total_productos = productos.count()
    productos_sin_stock = productos.filter(stock=0).count()
    productos_bajo_stock = productos.filter(stock__gt=0, stock__lte=5).count()

    ticket_promedio = total_general / total_pedidos if total_pedidos > 0 else 0
    crecimiento = total_hoy - total_ayer

    # ==========================
    # CONTEXT CONSOLIDADO
    # ==========================
    context = {
        'mensaje': mensaje,
        'tipo': tipo,
        'today': hoy,
        'perfil': perfil,
        'total_hoy': total_hoy,
        'total_ayer': total_ayer,
        'total_general': total_general,
        'ganancias_mes': ganancias_mes,
        'crecimiento': crecimiento,
        'fechas': fechas,
        'totales': totales,
        
        # Variables financieras consistentes
        'total_ingresos': total_ingresos,
        'total_material': total_material,
        'total_mano_obra': total_mano_obra,
        'total_costos': total_costos,
        'total_gastos': total_gastos,
        'utilidad': utilidad_bruta,       # Asegurada para compatibilidad de plantillas
        'utilidad_bruta': utilidad_bruta,
        'utilidad_neta': utilidad_neta,
        'margen_bruto': margen_bruto,
        'margen_neto': margen_neto,
        
        # Métricas de inventario pro
        'inv_valorizado_costo': inv_valorizado_costo,
        'inv_valorizado_venta': inv_valorizado_venta,
        'utilidad_potencial_inv': utilidad_potencial_inv,

        'pedidos_hoy': pedidos_hoy,
        'total_pedidos': total_pedidos,
        'pedidos_recientes': pedidos_recientes,
        'pedidos_pendientes': pedidos_pendientes,
        'pedidos_pagados': pedidos_pagados,
        'total_clientes': total_clientes,
        'clientes_vip': clientes_vip,
        'clientes_nuevos': clientes_nuevos,
        'clientes_dormidos': clientes_dormidos,
        'clientes_filtrados': clientes_filtrados,
        'morosos_count': morosos_count,
        'morosos_total': morosos_total,
        'total_productos': total_productos,
        'productos_sin_stock': productos_sin_stock,
        'productos_bajo_stock': productos_bajo_stock,
        'ticket_promedio': ticket_promedio,
    }

    return render(request, 'dashboard.html', context)

@login_required
def panel_crm(request):
    """
    📊 CONTROLADOR CENTRAL DEL CRM (JoyasApp SaaS)
    Filtros ultra-seguros compatibles con PostgreSQL en Render y adaptados al modelo.
    """
    # 🏢 Regla de Oro SaaS: Filtrar estrictamente por la joyería actual
    clientes_base = Cliente.objects.filter(usuario=request.user, activo=True)

    # 1. CAPTURA DE FILTROS DESDE LA URL
    buscar = request.GET.get('q', '').strip()
    filtro_nivel = request.GET.get('nivel', '').strip()
    filtro_estado = request.GET.get('estado', '').strip()
    filtro_origen = request.GET.get('origen', '').strip()
    filtro_alerta = request.GET.get('alerta', '').strip()

    # Aplicar buscador por texto blindando campos vacíos
    if buscar:
        clientes_base = clientes_base.filter(
            Q(nombre__icontains=buscar) |
            Q(telefono__icontains=buscar) |
            Q(ciudad__icontains=buscar) |
            Q(etiquetas__icontains=buscar)
        )

    # Aplicar filtros específicos
    if filtro_nivel:
        clientes_base = clientes_base.filter(nivel=filtro_nivel)
    if filtro_estado:
        clientes_base = clientes_base.filter(estado=filtro_estado)
    if filtro_origen:
        clientes_base = clientes_base.filter(origen=filtro_origen)

    # 🚨 FILTROS INTELIGENTES INTEGRADOS EVITANDO VALORES NULL
    hoy = timezone.now().date()
    
    # Mapeamos el parámetro de la URL de forma segura
    if filtro_alerta == 'cumpleanos':
        # Buscamos usando el campo físico 'fecha_cumpleanos' de tu modelo
        clientes_base = clientes_base.filter(
            fecha_cumpleanos__isnull=False,
            fecha_cumpleanos__month=hoy.month, 
            fecha_cumpleanos__day=hoy.day
        )
    elif filtro_alerta == 'inactivos_90':
        clientes_base = clientes_base.filter(dias_sin_comprar__gte=90)
    elif filtro_alerta == 'saldo_pendiente':
        clientes_base = clientes_base.filter(saldo_pendiente__gt=0)
    elif filtro_alerta == 'whatsapp_ok':
        clientes_base = clientes_base.filter(acepta_whatsapp=True)

    # 📈 CÁLCULO DE MÉTRICAS GLOBALES TOTALMENTE SEGURO
    clientes_joyeria = Cliente.objects.filter(usuario=request.user, activo=True)
    
    total_clientes = clientes_joyeria.count()
    nuevos = clientes_joyeria.filter(estado='Nuevo').count()
    vips = clientes_joyeria.filter(estado='VIP').count()
    diamantes = clientes_joyeria.filter(nivel='Diamante').count()
    
    # Sumatoria segura del saldo pendiente global
    saldo_total_dict = clientes_joyeria.aggregate(Sum('saldo_pendiente'))
    saldo_pendiente_total = saldo_total_dict['saldo_pendiente__sum'] or 0.00
    
    # Métricas protegidas contra nulos en bases de datos de producción
    cumpleanos_hoy = clientes_joyeria.filter(
        fecha_cumpleanos__isnull=False,
        fecha_cumpleanos__month=hoy.month, 
        fecha_cumpleanos__day=hoy.day
    ).count()
    
    sin_comprar_90 = clientes_joyeria.filter(dias_sin_comprar__gte=90).count()
    whatsapp_habilitados = clientes_joyeria.filter(acepta_whatsapp=True).count()

    # Contexto unificado para inyectar en el HTML
    context = {
        'clientes': clientes_base,  
        'buscar': buscar,
        'filtros': {
            'nivel': filtro_nivel,
            'estado': filtro_estado,
            'origen': filtro_origen,
            'alerta': filtro_alerta,
        },
        'metricas': {
            'total_clientes': total_clientes,
            'nuevos': nuevos,
            'vips': vips,
            'diamantes': diamantes,
            'saldo_pendiente': saldo_pendiente_total,
            'cumpleanos_hoy': cumpleanos_hoy,  # Mapeado de forma limpia
            'sin_comprar_90': sin_comprar_90,
            'whatsapp_habilitados': whatsapp_habilitados,
        }
    }

    return render(request, 'crm/panel_crm.html', context)


@login_required
def exportar_excel_dashboard(request):
    # Traemos solo los pedidos del joyero que tiene la sesión iniciada
    if request.user.is_superuser:
        queryset = Pedido.objects.all()
    else:
        queryset = Pedido.objects.filter(usuario=request.user)

    wb = openpyxl.Workbook()
    ws_resumen = wb.active
    ws_resumen.title = "Resumen"
    ws_resumen.append(["Orden", "Fecha", "Cliente", "Subtotal", "IVA", "ReteFuente", "Descuento", "Total"])

    ws_detalle = wb.create_sheet(title="Detalle")
    ws_detalle.append(["Orden", "Producto", "Cantidad", "Gramos", "Precio Unitario", "Subtotal", "Descuento", "IVA", "Total"])

    for pedido in queryset:
        subtotal_pedido = Decimal('0')
        iva_total = Decimal('0')
        rete_total = Decimal('0')
        descuento_total = Decimal('0')

        for item in pedido.items.all():
            cantidad = Decimal(item.cantidad)
            precio = Decimal(item.precio)

            gramos = ''
            if item.producto.tipo_venta == 'gramo':
                peso = item.producto.peso_producto or 1
                gramos = cantidad * Decimal(peso)
                base = gramos * precio
            else:
                base = cantidad * precio

            subtotal = Decimal(item.subtotal or base)
            descuento = Decimal(item.descuento or 0)
            iva = Decimal(item.iva or 0)
            rete = Decimal(item.retefuente or 0)
            total = Decimal(item.total_final or 0)

            subtotal_pedido += subtotal
            iva_total += iva
            rete_total += rete
            descuento_total += descuento

            ws_detalle.append([
                pedido.numero_orden, item.producto.nombre, float(cantidad),
                float(gramos) if gramos else '', float(precio), float(subtotal),
                float(descuento), float(iva), float(total),
            ])

        ws_resumen.append([
            pedido.numero_orden, pedido.fecha.strftime('%Y-%m-%d') if pedido.fecha else '',
            pedido.cliente_nombre or '', float(subtotal_pedido), float(iva_total),
            float(rete_total), float(descuento_total), float(pedido.total),
        ])

    for ws in [ws_resumen, ws_detalle]:
        for col in ws.columns:
            max_length = 0
            col_letter = col[0].column_letter
            for cell in col:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except:
                    pass
            ws.column_dimensions[col_letter].width = max_length + 2

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename=ventas_contable.xlsx'
    wb.save(response)
    return response

def modificar_banner(request):
    # 🛡️ BLINDAJE 1: Controlar de forma segura que exista el perfil del usuario
    perfil = getattr(request.user, 'perfil', None)
    if not perfil:
        messages.error(request, "No se encontró un perfil comercial configurado para este usuario.")
        return redirect('inicio')

    if request.method == 'POST':
        try:
            # 🛡️ BLINDAJE 2: Recolección limpia con fallback seguro mediante cadenas vacías
            perfil.banner_texto = request.POST.get('banner_texto', '').strip()
            perfil.nombre_tienda = request.POST.get('nombre_tienda', '').strip()
            perfil.instagram = request.POST.get('instagram', '').strip()
            perfil.facebook = request.POST.get('facebook', '').strip()
            perfil.tiktok = request.POST.get('tiktok', '').strip()
            
            # 🛡️ BLINDAJE 3: Control estricto de color (Si viene manipulado o vacío, fuerza el negro)
            color_seleccionado = request.POST.get('estilo_color', '#000000')
            if not color_seleccionado.startswith('#') or len(color_seleccionado) != 7:
                perfil.color_principal = '#000000'
            else:
                perfil.color_principal = color_seleccionado

            # 🛡️ BLINDAJE 4: ESCUDO ANTI-DUPLICADOS INTEGRADO Y SEGURO
            if 'banner' in request.FILES:
                nueva_imagen = request.FILES['banner']
                
                # Comprobación de seguridad: Evitar duplicados si coincide el nombre exacto
                if perfil.banner and nueva_imagen.name == perfil.banner.name:
                    pass  
                else:
                    # Sanear la extensión y forzar un nombre único basado en timestamp para reventar caché
                    ext = nueva_imagen.name.split('.')[-1].lower()
                    if ext in ['jpg', 'jpeg', 'png', 'webp']:
                        nueva_imagen.name = f"banner_{int(time.time())}.{ext}"
                        perfil.banner = nueva_imagen
            
            # 🛡️ BLINDAJE 5: Guardado seguro en base de datos
            perfil.save()
            messages.success(request, "¡Centro de promociones actualizado con éxito!")
            return redirect('modificar_banner')

        except Exception as e:
            # 🛡️ EXCEPCIÓN TOTAL: Captura cualquier error interno y lo muestra en un mensaje de alerta de Django
            # Evita la pantalla blanca de Error 500 y te dice exactamente qué falló en caliente
            messages.error(request, f"Ocurrió un error inesperado al guardar los cambios: {str(e)}")
            return redirect('modificar_banner')

    return render(request, 'modificar_banner.html', {'perfil': perfil})

@login_required
def estadisticas(request):
    pedidos = Pedido.objects.filter(usuario=request.user)

    # 🔹 HOY
    hoy = timezone.now().date()
    ventas_hoy = pedidos.filter(fecha__date=hoy).aggregate(total=Sum('total'))['total'] or 0

    # 🔹 ÚLTIMOS 30 DÍAS
    hace_30 = hoy - timedelta(days=30)
    ventas_mes = pedidos.filter(fecha__date__gte=hace_30).aggregate(total=Sum('total'))['total'] or 0

    # 🔹 TOTAL PEDIDOS
    total_pedidos = pedidos.count()

    # 🔹 VENTAS POR DÍA
    ventas_por_dia = pedidos.annotate(
        dia=TruncDate('fecha')
    ).values('dia').annotate(
        total=Sum('total')
    ).order_by('dia')

    # 🔹 GASTOS (Se incluye en el contexto por si lo necesitas en el frontend)
    gastos = Gasto.objects.filter(usuario=request.user).annotate(
        dia=TruncDate('fecha')
    ).values('dia').annotate(
        total=Sum('monto')
    ).order_by('dia')

    # 🔹 TOP PRODUCTOS 
    top_productos = PedidoItem.objects.filter(
        pedido__usuario=request.user
    ).values(
        'producto__nombre'
    ).annotate(
        total_vendido=Sum('cantidad')
    ).order_by('-total_vendido')[:5]

    # 🔹 MÉTODOS DE PAGO 
    metodos_pago = pedidos.values('tipo_pago').annotate(
        total=Sum('total')
    )

    # 🛠️ CORRECCIÓN: Se usa 'cliente_nombre' que es el campo real de tu modelo Pedido
    top_clientes = pedidos.values(
        'cliente_nombre'
    ).annotate(
        total_compras=Sum('total')
    ).order_by('-total_compras')[:5]

    context = {
        'ventas_hoy': ventas_hoy,
        'ventas_mes': ventas_mes,
        'total_pedidos': total_pedidos,
        'ventas_por_dia': ventas_por_dia,
        'gastos_por_dia': gastos,
        'top_productos': top_productos,
        'metodos_pago': metodos_pago,
        'top_clientes': top_clientes,
    }
    return render(request, 'estadisticas.html', context)


# ==========================================
# 💰 GANANCIAS CORREGIDO
# ==========================================
@login_required
def ganancias(request):
    hoy = timezone.now().date()
    hace_30 = hoy - timedelta(days=30)
    
    pedidos_usuario = Pedido.objects.filter(usuario=request.user)

    ventas_hoy = pedidos_usuario.filter(fecha__date=hoy).aggregate(total=Sum('total'))['total'] or 0
    ventas_mes = pedidos_usuario.filter(fecha__date__gte=hace_30).aggregate(total=Sum('total'))['total'] or 0
    ventas_total = pedidos_usuario.aggregate(total=Sum('total'))['total'] or 0
    
    # 🛠️ PARCHE AL CORTO: Cálculo del Ticket Promedio real
    ticket_promedio = pedidos_usuario.aggregate(promedio=Avg('total'))['promedio'] or 0

    pedidos = pedidos_usuario.order_by('-fecha')[:20]

    context = {
        'ventas_hoy': ventas_hoy,
        'ventas_mes': ventas_mes,  # 🚀 Inyectado para la tarjeta del HTML
        'ventas_total': ventas_total,
        'ticket_promedio': ticket_promedio,  # 🚀 Inyectado para la tarjeta del HTML
        'pedidos': pedidos,
    }
    return render(request, 'ganancias.html', context)

# ==========================================
# 📦 INVENTARIO CORREGIDO
# ==========================================
@login_required
def inventario(request):
    # Traemos los productos del usuario cargando sus variantes en una sola consulta (eficiencia)
    productos = Producto.objects.filter(usuario=request.user).prefetch_related('variantes').order_by('nombre')

    total_productos = productos.count()
    
    # 🌟 CORRECCIÓN PRO: Evaluamos el stock real (del producto o de la suma de sus variantes)
    productos_stock = 0
    stock_bajo = 0
    agotados = 0

    for p in productos:
        # Si el producto tiene variantes, su stock real es la suma de ellas
        if p.variantes.exists():
            stock_real = p.variantes.aggregate(total=Sum('stock'))['total'] or 0
        else:
            stock_real = p.stock or 0

        # Clasificación exacta para los contadores del HTML
        if stock_real > 5:
            productos_stock += 1
        elif 0 < stock_real <= 5:
            stock_bajo += 1
        else:
            agotados += 1

    context = {
        'productos': productos,
        'total_productos': total_productos,
        'productos_stock': productos_stock,  
        'stock_bajo': stock_bajo,            
        'agotados': agotados,                
    }
    return render(request, 'inventario.html', context)

@login_required
def configurar_negocio(request):
    perfil = request.user.perfil

    if request.method == 'POST':
        form = ConfiguracionNegocioForm(request.POST, request.FILES)

        if form.is_valid():
            # Tus mapeos básicos existentes
            perfil.nombre_tienda = form.cleaned_data['nombre_tienda']
            perfil.nit = form.cleaned_data['nit']
            perfil.whatsapp = form.cleaned_data['whatsapp']
            perfil.email_empresa = form.cleaned_data['correo_negocio']
            perfil.direccion = form.cleaned_data['direccion']
            perfil.ciudad = form.cleaned_data['ciudad']
            perfil.wompi_public_key = form.cleaned_data.get('wompi_public_key', '').strip()
            perfil.mercadopago_access_token = form.cleaned_data.get('mercadopago_access_token', '').strip()
            perfil.costo_envio_estandar = form.cleaned_data.get('costo_envio_estandar') or Decimal('0.00')

            # 🔥 MAREO DE LOS NUEVOS BOTONES E INPUTS FISCALES
            perfil.responsable_iva = form.cleaned_data.get('responsable_iva', False)
            perfil.porcentaje_iva = form.cleaned_data.get('porcentaje_iva') or Decimal('19.00')
            perfil.mostrar_iva_discriminado = form.cleaned_data.get('mostrar_iva_discriminado', False)
            perfil.prefijo_factura = form.cleaned_data.get('prefijo_factura', '').strip()
            perfil.consecutivo_actual = form.cleaned_data.get('consecutivo_actual') or 1
            perfil.resolucion_dian = form.cleaned_data.get('resolucion_dian', '').strip()
            perfil.integracion_siigo_activa = form.cleaned_data.get('integracion_siigo_activa', False)

            # Campañas y Envíos Personales
            perfil.aplicar_descuentos = form.cleaned_data.get('aplicar_descuentos', False)
            perfil.porcentaje_descuento_promo = form.cleaned_data.get('porcentaje_descuento_promo') or Decimal('0.00')
            perfil.cobrar_envio = form.cleaned_data.get('cobrar_envio', False)
            perfil.envio_gratis_desde = form.cleaned_data.get('envio_gratis_desde') or Decimal('0.00')

            # Lógica del logo y colores que ya tenías funcional
            eliminar_logo = request.POST.get('eliminar_logo')
            if eliminar_logo and perfil.logo:
                perfil.logo.delete(save=False)
                perfil.logo = None
            if request.FILES.get('logo') and not eliminar_logo:
                perfil.logo = request.FILES['logo']

            perfil.color_primario = request.POST.get('color_primario', '#28a745')
            perfil.color_secundario = request.POST.get('color_secundario', '#000000')

            perfil.save()
            messages.success(request, "Configuración actualizada correctamente")
            return redirect('dashboard')
    else:
        # Pasa los datos iniciales al formulario (incluye los nuevos para que se pinten al recargar)
        form = ConfiguracionNegocioForm(initial={
            'nombre_tienda': perfil.nombre_tienda,
            'nit': perfil.nit,
            'whatsapp': perfil.whatsapp,
            'correo_negocio': perfil.email_empresa,
            'direccion': perfil.direccion,
            'ciudad': perfil.ciudad,
            'wompi_public_key': perfil.wompi_public_key,
            'mercadopago_access_token': perfil.mercadopago_access_token,
            'costo_envio_estandar': perfil.costo_envio_estandar,
            # Iniciales de la nueva configuración
            'responsable_iva': perfil.responsable_iva,
            'porcentaje_iva': perfil.porcentaje_iva,
            'mostrar_iva_discriminado': perfil.mostrar_iva_discriminado,
            'prefijo_factura': perfil.prefijo_factura,
            'consecutivo_actual': perfil.consecutivo_actual,
            'resolucion_dian': perfil.resolucion_dian,
            'integracion_siigo_activa': perfil.integracion_siigo_activa,
            'aplicar_descuentos': perfil.aplicar_descuentos,
            'porcentaje_descuento_promo': perfil.porcentaje_descuento_promo,
            'cobrar_envio': perfil.cobrar_envio,
            'envio_gratis_desde': perfil.envio_gratis_desde,
        })

    return render(request, 'configurar_negocio.html', {'form': form, 'perfil': perfil})

def calcular_totales_con_reglas_fiscales(pedido):
    """
    Única función centralizada en views.py para procesar el pedido.
    Recibe el objeto 'pedido' directamente, lo que facilita su uso en cualquier vista.
    """
    perfil = pedido.usuario.perfil  # Accedemos al centro de mando del joyero
    subtotal_productos = Decimal('0.00')
    
    # 1. Sumamos el valor base de los ítems del pedido
    for item in pedido.items.all():
        subtotal_productos += Decimal(str(item.subtotal or 0.00))

    # 🎁 REGLA DE CAMPAÑA: ¿El joyero encendió el interruptor de promociones?
    descuento_aplicado = Decimal('0.00')
    if perfil.aplicar_descuentos and perfil.porcentaje_descuento_promo > 0:
        porcentaje_desc = Decimal(str(perfil.porcentaje_descuento_promo)) / Decimal('100.00')
        descuento_aplicado = subtotal_productos * porcentaje_desc
    
    # Base sobre la que se calcula el IVA tras el descuento de campaña
    base_gravable = subtotal_productos - descuento_aplicado

    # ⚖️ REGLA FISCAL: ¿Es responsable de IVA?
    iva_calculado = Decimal('0.00')
    if perfil.responsable_iva and perfil.porcentaje_iva > 0:
        porcentaje_iva = Decimal(str(perfil.porcentaje_iva)) / Decimal('100.00')
        iva_calculado = base_gravable * porcentaje_iva

    retefuente_calculada = Decimal('0.00')
    if hasattr(perfil, 'aplicar_retefuente') and perfil.aplicar_retefuente:
        # Nota: Aquí puedes validar si el cliente del pedido es retenedor si tienes ese campo
        porcentaje_rete = Decimal(str(perfil.porcentaje_retefuente or 2.50)) / Decimal('100.00')
        retefuente_calculada = base_gravable * porcentaje_rete

    # 🚚 REGLA DE ENVÍOS: Control de costos y umbral gratis
    costo_envio = Decimal('0.00')
    if perfil.cobrar_envio:
        costo_envio = Decimal(str(perfil.costo_envio_estandar or 0.00))
        
        # Si el negocio configuró un tope de envío gratis y la base lo supera, queda en 0
        if perfil.envio_gratis_desde and base_gravable >= Decimal(str(perfil.envio_gratis_desde)):
            costo_envio = Decimal('0.00')

    # 💾 Guardamos de forma limpia los resultados en el Pedido
    pedido.descuento_total = descuento_aplicado
    pedido.aplica_iva = perfil.responsable_iva  # Sincronizamos el estado del IVA
    pedido.iva = iva_calculado
    pedido.costo_envio = costo_envio
    pedido.total = base_gravable + iva_calculado + costo_envio
    pedido.save()
    
    return pedido

@login_required
def renovar_manual(request):
    """Vista para forzar la renovación del plan SaaS desde el panel."""
    perfil = request.user.perfil
    renovar_plan(perfil)
    return redirect('dashboard')

def renovar_plan(perfil):
    """Lógica centralizada para extender la vigencia del plan 30 días."""
    hoy = timezone.localdate()

    # Si el plan sigue vigente, sumamos días al vencimiento actual (acumulativo)
    if perfil.plan_vence and perfil.plan_vence > hoy:
        perfil.plan_vence = perfil.plan_vence + timedelta(days=30)
    # Si ya venció, los 30 días arrancan desde hoy
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
    # 📊 PASO 2: CAPTURA SEGURA (Sin autonomía del cliente)
    # =========================================================
    numero = generar_numero_orden(usuario)

    # 🔒 OBTENEMOS LAS REGLAS DIRECTAMENTE DEL PERFIL DEL JOYERO (No del POST)
    perfil, _ = Perfil.objects.get_or_create(user=usuario)
    
    # Supongamos que en tu modelo Perfil tienes campos como 'cobra_iva' o 'es_autoretenedor'.
    # Si no existen, puedes dejarlos en False por defecto o usar los atributos correctos de tu modelo:
    aplica_iva = getattr(perfil, 'cobra_iva', False) 
    es_retenedor = getattr(perfil, 'es_retenedor', False)
    porcentaje_descuento = Decimal('0')  # El cliente público no puede auto-asignarse descuentos

    # Solo capturamos los datos personales y de envío del cliente
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

    # Cálculo de Totales basado en la configuración real del Joyero
    subtotal_general = Decimal('0')
    for data in resumen_items:
        subtotal_general += data['subtotal']

    descuento = subtotal_general * (porcentaje_descuento / Decimal('100'))
    subtotal_con_descuento = subtotal_general - descuento

    iva = subtotal_con_descuento * Decimal('0.19') if aplica_iva else Decimal('0')
    retefuente = subtotal_con_descuento * Decimal('0.025') if es_retenedor else Decimal('0')
    
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
            total=float(total_final),  
            porcentaje_descuento=float(porcentaje_descuento),  
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

        # 👤 Buscar o crear cliente
        cliente, creado = Cliente.objects.get_or_create(
        usuario=usuario,
        telefono=telefono,
        defaults={
        "nombre": nombre,
        "email": cliente_email,
        "ciudad": ciudad,
        "direccion": direccion,
        }
        )

        # Si ya existe, actualizamos sus datos
        if not creado:
            cliente.nombre = nombre
            cliente.email = cliente_email
            cliente.ciudad = ciudad
            cliente.direccion = direccion
            cliente.save()

        # 📦 PASO 3: Crear el Pedido Maestro con los totales de verdad
        pedido = Pedido.objects.create(
            usuario=usuario,
            cliente=cliente,
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
        print("✅ Cliente asociado:", pedido.cliente)
        print("✅ Nombre:", pedido.cliente.nombre if pedido.cliente else "NINGUNO")    

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
@transaction.atomic
def confirmar_pago(request, pedido_id):
    """ El administrador aprueba el pago desde su panel privado e impacta inventarios """
    pedido = get_object_or_404(Pedido, id=pedido_id, usuario=request.user)
    
    # 🌟 Evitamos procesar dos veces si el administrador da clic doble
    if pedido.estado != "pagado":
        # Recorremos los ítems del pedido para restar el stock real en este preciso momento
        for item in pedido.items.all():  # Ajusta 'items' según el related_name en tu PedidoItem
            if item.variante:
                item.variante.stock -= item.cantidad
                item.variante.save()
            elif item.producto.tipo_venta != "gramo":
                item.producto.stock -= item.cantidad
                item.producto.save()
        
        pedido.estado = "pagado"
        pedido.save()
        
    return render(request, 'pago_confirmado.html', {'pedido': pedido})

def pagar_wompi(request, token): # <-- Cambiado de pedido_id a token
    pedido = get_object_or_404(Pedido, token_publico=token) # <-- get_object_or_404 seguro
    return crear_checkout_wompi(request, pedido)

def crear_checkout_wompi(request, pedido):
    redirect_url = request.build_absolute_uri(f'/pago-exitoso/{pedido.token_publico}/')
    redirect_url_encoded = quote(redirect_url, safe='')

    # 🏪 1. Buscamos el perfil del dueño de la joyería
    perfil_dueno = pedido.usuario.perfil
    
    # 🔑 2. Buscamos ÚNICAMENTE la llave del usuario en la base de datos
    wompi_public_key = getattr(perfil_dueno, 'wompi_public_key', None)

    # 🚨 EL ESCUDO: Si el usuario NO tiene su llave configurada, frenamos todo
    if not wompi_public_key or wompi_public_key.strip() == "":
        print(f"⚠️ Alerta de Seguridad: El usuario {pedido.usuario.username} intentó recibir un pago pero no tiene configurada su Wompi Public Key.")
        return HttpResponse(
            "<h3>Método de pago temporalmente no disponible</h3>"
            "<p>Esta tienda aún no ha terminado de configurar sus credenciales de pago en línea. "
            "Por favor, ponte en contacto con el administrador de la joyería para completar tu compra por WhatsApp.</p>", 
            status=400
        )

    # 🚀 Si sí tiene su llave, el flujo continúa normal y el dinero va a SU cuenta bancaria
    checkout_url = (
        "https://checkout.wompi.co/p/"
        f"?public-key={wompi_public_key.strip()}" 
        f"&currency=COP"
        f"&amount-in-cents={int(pedido.total * 100)}"
        f"&reference={pedido.numero_orden}"
        f"&redirect-url={redirect_url_encoded}"
    )
    return redirect(checkout_url)

@csrf_exempt
def webhook_wompi(request):
    if request.method != 'POST':
        return HttpResponse("Método no permitido", status=405)
        
    try:
        # 1. Parsear los datos que envía Wompi
        data = json.loads(request.body)
        
        # Opcional: Validar integridad con la firma de Wompi (si configuras WOMPI_EVENTS_SECRET)
        # Por ahora procesamos el evento de forma directa y segura por ID de orden
        
        transaccion = data.get('data', {}).get('transaction', {})
        referencia_orden = transaccion.get('reference') # Ejemplo: #FAC-00022
        estado_wompi = transaccion.get('status') # APPROVED, DECLINED, VOIDED
        
        if referencia_orden and estado_wompi == 'APPROVED':
            # 2. Buscar el pedido por su número de orden único
            pedido = Pedido.objects.filter(numero_orden=referencia_orden).first()
            
            if pedido and pedido.estado != 'pagado':
                # 3. Actualizar el estado de manera definitiva
                pedido.estado = 'pagado'
                pedido.saldo_pendiente = Decimal('0.00')
                pedido.save()
                print(f"✅ Webhook exitoso: Pedido {referencia_orden} marcado como PAGADO.")
                
                # ========================================================
                # 🚀 DISPARADORES AUTOMÁTICOS INTEGRADOS (Wompi)
                # ========================================================
                from .utils import generar_pdf_pedido
                from django.core.mail import EmailMessage
                from .models import Perfil # Asegúrate de importar tu modelo Perfil aquí si no está arriba

                # Recuperamos el perfil de la joyería dueña de este pedido de forma dinámica
                perfil = Perfil.objects.filter(user=pedido.usuario).first()

                if perfil:
                    # Generamos el binario del PDF usando tu plantilla HTML
                    pdf, correo_empresa, nombre_tienda = generar_pdf_pedido(pedido, perfil, usuario_backup=perfil.user)

                    email_cliente = getattr(pedido, 'cliente_email', None)
                    if email_cliente and pdf:
                        try:
                            email = EmailMessage(
                                subject=f"Confirmación de Pago: Pedido #{pedido.numero_orden} - {nombre_tienda}",
                                body=f"¡Hola! Tu pago ha sido confirmado correctamente a través de Wompi. Adjuntamos tu factura detallada. 💎",
                                from_email=correo_empresa,
                                to=[email_cliente]
                            )
                            email.attach(f"factura_{pedido.numero_orden}.pdf", pdf, 'application/pdf')
                            email.send()
                            print(f"📧 Correo enviado al cliente {email_cliente} para la orden {pedido.numero_orden}")
                        except Exception as e:
                            print(f"Error en envío automático por Webhook Wompi: {str(e)}")
                # ========================================================
                
        # Wompi exige que le respondas un HTTP 200 para saber que recibiste la notificación
        return HttpResponse("Evento recibido", status=200)
        
    except Exception as e:
        print(f"💥 Error en Webhook Wompi: {str(e)}")
        # Corregido el typo 'HttpRespon tnse' que tenías en tu borrador original
        return HttpResponse("Error interno procesado", status=200)

def pagar_con_mercadopago(request, token):
    """
    Genera la preferencia de Mercado Pago asegurando la correcta obtención 
    del perfil del vendedor sin recalcular totales de forma prematura.
    """
    try:
        # 1. Obtener el pedido usando el token público correcto
        pedido = get_object_or_404(Pedido, token_publico=token)
        
        # 🎯 INTENTO 1: Buscar relación directa si tu modelo Pedido tiene 'perfil_joyeria' o similar
        perfil_tienda = getattr(pedido, 'perfil_joyeria', None) or getattr(pedido, 'perfil', None)
        
        # 🎯 INTENTO 2: Si no viene directo, lo extraemos a través del usuario de la tienda/vendedor
        if not perfil_tienda:
            primer_item = pedido.items.first()  # Ajusta 'items' según el related_name de PedidoItem
            if primer_item and hasattr(primer_item.producto, 'usuario'):
                vendedor = primer_item.producto.usuario
            elif primer_item and hasattr(primer_item.producto, 'perfil'):
                perfil_tienda = primer_item.producto.perfil
                vendedor = getattr(perfil_tienda, 'user', None)
            else:
                vendedor = pedido.usuario 
                
            if not perfil_tienda and vendedor:
                try:
                    perfil_tienda = Perfil.objects.get(user=vendedor)
                except Perfil.DoesNotExist:
                    perfil_tienda = None

        if not perfil_tienda:
            return HttpResponse(
                "<h3>Error de Configuración</h3><p>No se pudo determinar la joyería dueña de este pedido.</p>", 
                status=200
            )

        # 🛡️ EXTRACCIÓN Y LIMPIEZA DE TU TOKEN 'APP_USR'
        token_mp = getattr(perfil_tienda, 'mercadopago_access_token', None)
        
        if token_mp is None or str(token_mp).strip() in ["", "None"]:
            return HttpResponse(
                f"<div style='font-family:sans-serif; padding:20px; text-align:center; margin-top:50px;'>"
                f"   <h2 style='color:#1D4ED8;'>Módulo de Pago en Configuración</h2>"
                f"   <p style='color:#4B5563;'>La tienda '{perfil_tienda.nombre_tienda}' aún no ha enlazado sus credenciales de Mercado Pago.</p>"
                f"</div>", 
                status=200
            )

        token_limpio = str(token_mp).strip()

        # Inicialización del SDK con credenciales reales del vendedor
        sdk = mercadopago.SDK(token_limpio)

        # URLs de Redirección y Notificación usando el atributo real: token_publico
        ruta_relativa = f"/webhooks/mercadopago/{perfil_tienda.webhook_uuid}/"
        url_webhook = request.build_absolute_uri(ruta_relativa).replace("http://", "https://")
        
        url_success = request.build_absolute_uri(f"/pago-exitoso/{pedido.token_publico}/").replace("http://", "https://")
        url_failure = request.build_absolute_uri(f"/pago-fallido/{pedido.token_publico}/").replace("http://", "https://")

        # Configuración del valor a pagar extraído directamente de lo sellado en la base de datos
        monto_total = getattr(pedido, 'total_limpio', None) or getattr(pedido, 'total', 0)
        monto_total = float(monto_total)

        # Estructura de preferencia ÚNICA y limpia
        preference_data = {
            "items": [
                {
                    "title": f"Pedido #{pedido.numero_orden} - {perfil_tienda.nombre_tienda}",
                    "quantity": 1,
                    "currency_id": "COP",
                    "unit_price": monto_total,
                }
            ],
            "back_urls": {
                "success": url_success,
                "failure": url_failure,
                "pending": url_success
            },
            "auto_return": "approved",
            "external_reference": str(pedido.numero_orden),
        }

        # Excluir localhost del webhook para evitar rechazos de Mercado Pago en desarrollo local
        if "localhost" not in url_webhook and "127.0.0.1" not in url_webhook:
            preference_data["notification_url"] = url_webhook

        # 1. Hacemos la petición a Mercado Pago
        preference_response = sdk.preference().create(preference_data)
        
        # 2. LOG DE CONTROL: Imprimimos la respuesta completa en Render para auditorías rápidas
        print("====== RESPUESTA COMPLETA DE MERCADO PAGO ======")
        print(preference_response)
        print("================================================")

        # 3. Extraemos la respuesta usando .get() de forma segura
        preference = preference_response.get("response") if preference_response else None
        
        # 4. Validamos si Mercado Pago nos devolvió el punto de inicio de pago válido
        if not preference or not preference.get("init_point"):
            mensaje_error = preference_response.get("message", "Las credenciales no son válidas o la cuenta requiere homologación en Mercado Pago.")
            return HttpResponse(
                f"<div style='font-family:sans-serif; padding:20px; text-align:center; margin-top:50px;'>"
                f"   <h2 style='color:#DC2626;'>Mercado Pago: Rechazo de Solicitud</h2>"
                f"   <p style='color:#4B5563;'>La pasarela respondió pero no generó el punto de inicio.</p>"
                f"   <p style='color:#9CA3AF; font-size:14px;'><b>Detalle técnico:</b> {mensaje_error}</p>"
                f"   <a href='/factura/{pedido.token_publico}/' style='display:inline-block; margin-top:20px; padding:10px 20px; background:#1D4ED8; color:white; text-decoration:none; border-radius:5px;'>Volver al Pedido</a>"
                f"</div>",
                status=200
            )
        
        # 5. Redirección al checkout transparente de Mercado Pago
        return redirect(preference.get("init_point"))

    except Exception as e:
        print(f"💥 Error crítico detallado en Mercado Pago: {traceback.format_exc()}")
        return HttpResponse(f"Error interno al inicializar el pago: {str(e)}", status=200)

@csrf_exempt
def webhook_mercadopago(request, profile_uuid):
    """Recibe el UUID directamente en la URL, consulta el pago y procesa el flujo automático."""
    if request.method == 'POST':
        try:
            # 1. Buscamos el perfil de la joyería usando el UUID de la URL de forma directa
            perfil = get_object_or_404(Perfil, webhook_uuid=profile_uuid)

            # 2. Capturamos el ID del recurso que envía Mercado Pago
            topic = request.GET.get('topic') or request.POST.get('topic')
            id_recurso = request.GET.get('id') or request.POST.get('id')

            if not topic and request.body:
                data = json.loads(request.body)
                topic = data.get('type')
                if data.get('data'):
                    id_recurso = data.get('data').get('id')

            # 3. Si es una notificación de pago, procedemos con el SDK del vendedor
            if topic == 'payment' and id_recurso:
                # 🧠 Inicialización limpia usando el token del vendedor recuperado con el UUID
                sdk = mercadopago.SDK(perfil.mercadopago_access_token)
                payment_info = sdk.payment().get(id_recurso)
                payment_data = payment_info["response"]

                status = payment_data.get("status")
                numero_orden = payment_data.get("external_reference")
                monto_recibido = payment_data.get("transaction_amount")

                # 4. Localizamos el pedido real en el sistema
                pedido = get_object_or_404(Pedido, numero_orden=numero_orden)
                
                # 5. Validamos estado y monto
                if status == 'approved' and float(monto_recibido) >= float(pedido.total_limpio):
                    if pedido.estado_pago != 'pagado':
                        pedido.estado_pago = 'pagado'
                        pedido.mercadopago_payment_id = id_recurso  # Guardamos la auditoría
                        pedido.save()
                        
                        # ========================================================
                        # 🚀 DISPARADORES AUTOMÁTICOS (Tu hoja de ruta)
                        # ========================================================
                        
                        # 1. Enviar correo con factura PDF adjunta (Tu HTML usando xhtml2pdf)
                        from .utils import generar_pdf_pedido
                        from django.core.mail import EmailMessage

                        # Generamos el binario del PDF invocando tu función pura
                        # Pasamos perfil.user como usuario de respaldo (backup)
                        pdf, correo_empresa, nombre_tienda = generar_pdf_pedido(pedido, perfil, usuario_backup=perfil.user)

                        email_cliente = getattr(pedido, 'cliente_email', None)
                        if email_cliente and pdf:
                            try:
                                email = EmailMessage(
                                    subject=f"Confirmación de Pago: Pedido #{pedido.numero_orden} - {nombre_tienda}",
                                    body=f"¡Hola! Tu pago ha sido confirmado correctamente. Adjuntamos tu factura detallada. 💎",
                                    from_email=correo_empresa,
                                    to=[email_cliente]
                                )
                                email.attach(f"factura_{pedido.numero_orden}.pdf", pdf, 'application/pdf')
                                email.send()
                            except Exception as e:
                                print(f"Error en envío automático por Webhook MP para {profile_uuid}: {str(e)}")

                        # 2. Notificación push o WhatsApp al vendedor
                        # (Aquí pones tu lógica de notificaciones cuando la tengas lista)

                        # 3. Asiento en el registro financiero / contabilidad
                        # (Aquí pones tu lógica contable cuando la tengas lista)

                        # ========================================================

            return HttpResponse("OK", status=200)
                
        except Exception as e:
            print(f"Error crítico en Webhook MP ({profile_uuid}): {str(e)}")
            return HttpResponse(status=500)
            
    return HttpResponse(status=400)

def pago_exitoso(request, token):
    # Ya no se romperá porque tanto Wompi como MercadoPago retornarán el token_publico (UUID)
    pedido = get_object_or_404(Pedido, token_publico=token)

    pedido.estado = "pagado"
    pedido.saldo_pendiente = Decimal('0.00')
    pedido.save()

    return redirect('factura_publica', token=token)

def pago_fallido(request, token):
    """Vista para gestionar los pagos rechazados o fallidos de Mercado Pago/Wompi."""
    # Buscamos el pedido usando el token UUID que viene en la URL
    pedido = get_object_or_404(Pedido, token=token) # O el campo UUID que uses para identificarlo públicamente
    
    context = {
        'pedido': pedido,
        'status': request.GET.get('status'), # Captura el estado que manda Mercado Pago (ej. 'rejected')
    }
    return render(request, 'pedidos/pago_fallido.html', context)

@login_required
def lista_pedidos(request):
    pedidos = Pedido.objects.filter(usuario=request.user).order_by('-fecha')
    hoy = timezone.now().date()

    for p in pedidos:
        if getattr(p, 'tipo_pago', 'contado') == 'credito' and p.fecha_limite:
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

    # =======================================================
    # 🔄 BLINDAJE Y ACTUALIZACIÓN EN CALIENTE DEL CRM (Evita el $0)
    # =======================================================
    if pedido.cliente:
        cliente_actual = pedido.cliente
        
        # 1. Sumamos de forma real e histórica todos los pedidos pagados de este cliente
        total_historico = Pedido.objects.filter(
            cliente=cliente_actual,
            usuario=request.user,
            estado='pagado'
        ).aggregate(Sum('total'))['total__sum'] or Decimal('0.00')

        # 2. Sumamos lo que debe actualmente en cartera
        total_cartera = Pedido.objects.filter(
            cliente=cliente_actual,
            usuario=request.user,
            saldo_pendiente__gt=0
        ).aggregate(Sum('saldo_pendiente'))['saldo_pendiente__sum'] or Decimal('0.00')

        # 3. Forzamos la actualización física en la ficha del cliente
        cliente_actual.total_compras = total_historico
        cliente_actual.saldo_pendiente = total_cartera
        cliente_actual.save()  # ← ¡Esto borra el $0 del CRM para siempre!

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
    link_pdf = request.build_absolute_uri(reverse('pedido_pdf', args=[pedido.id]))
    link_publico = request.build_absolute_uri(reverse('confirmar_pago_publico', args=[pedido.token_publico]))

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
    # 📦 CONTEXT COPIADO TAL CUAL
    # ===============================
    logo_path = None
    try:
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
        'cliente': getattr(pedido, 'cliente', None),
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
        'empresa_nombre': empresa_nombre,
        'empresa_nit': empresa_nit,
        'empresa_telefono': empresa_telefono,
        'empresa_direccion': empresa_direccion,
        'logo_path': logo_path,
        'qr_pago_url': link_publico,
    }
    
    print("DETALLE PEDIDO OK - CRM SINCRONIZADO")
    return render(request, 'detalle_pedido.html', context)

def pedido_pdf(request, pedido_id):
    # (Todo el código de tu función pedido_pdf se queda idéntico, no requiere cambios)
    pedido = get_object_or_404(Pedido, id=pedido_id)
    items = pedido.items.select_related('producto', 'variante').all()
    usuario_pedido = pedido.usuario

    try:
        perfil = Perfil.objects.get(user=usuario_pedido)
    except Perfil.DoesNotExist:
        perfil = None

    empresa_nombre = perfil.nombre_tienda if perfil else "Mi Joyería"
    empresa_nit = perfil.nit if perfil else ""
    empresa_telefono = perfil.whatsapp if perfil else ""
    empresa_direccion = perfil.direccion if perfil else ""
    empresa_email = perfil.email_empresa if perfil else ""
    
    empresa_logo = None
    if perfil and perfil.logo:
        try:
            empresa_logo = perfil.logo.url
        except Exception:
            empresa_logo = None

    color_primario = perfil.color_primario if perfil else "#111111"
    color_secundario = perfil.color_secundario if perfil else "#333333"

    subtotal = Decimal('0')
    iva_total = Decimal('0')
    descuento_total = Decimal('0')
    retefuente_total = Decimal('0')

    for item in items:
        if item.subtotal:
            subtotal += Decimal(str(item.subtotal))
        if item.iva:
            iva_total += Decimal(str(item.iva))
        if item.descuento:
            descuento_total += Decimal(str(item.descuento))
        if item.retefuente:
            retefuente_total += Decimal(str(item.retefuente))

    porcentaje_desc = getattr(pedido, 'porcentaje_descuento', 0) or 0
    if porcentaje_desc and descuento_total == 0:
        descuento_total = subtotal * (Decimal(str(porcentaje_desc)) / Decimal('100'))
        
    envio = Decimal(str(pedido.costo_envio or 0))
    total_final = subtotal + iva_total + envio - descuento_total - retefuente_total

    if hasattr(pedido, 'fecha') and pedido.fecha:
        fecha_str = pedido.fecha.strftime('%Y-%m-%d')
    elif hasattr(pedido, 'fecha_creacion') and pedido.fecha_creacion:
        fecha_str = pedido.fecha_creacion.strftime('%Y-%m-%d')
    else:
        fecha_str = "S/F"

    context = {
        'pedido': pedido,
        'items': items,
        'cliente_nombre': pedido.cliente_nombre,
        'cliente_email': pedido.cliente_email,
        'cliente_telefono': pedido.cliente_telefono,
        'cliente_direccion': pedido.cliente_direccion,
        'cliente_ciudad': pedido.cliente_ciudad,
        'cliente_nit': pedido.cliente_nit,
        'empresa_nombre': empresa_nombre,
        'empresa_nit': empresa_nit,
        'empresa_telefono': empresa_telefono,
        'empresa_direccion': empresa_direccion,
        'empresa_email': empresa_email,
        'empresa_logo': empresa_logo,
        'color_primario': color_primario,
        'color_secundario': color_secundario,
        'subtotal': subtotal,
        'iva_total': iva_total,
        'descuento_total': descuento_total,
        'retefuente_total': retefuente_total,
        'envio': envio,
        'total_final': total_final,
        'fecha_str': fecha_str,
    }

    try:
        template = get_template('pedido_pdf.html')
        html = template.render(context)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="pedido_{pedido.numero_orden}.pdf"'
        pdf = pisa.CreatePDF(html, dest=response)
        if pdf.err:
            return HttpResponse(f"Error generando PDF: {pdf.err}", status=500)
        return response
    except Exception as e:
        return HttpResponse(f"Error interno: {str(e)}", status=500)

@login_required
def procesar_checkout(request):
    if request.method == 'POST':
        # 1. Creas el pedido base (asociado al usuario)
        # Cambia los campos según los que tenga tu modelo Pedido real
        pedido = Pedido.objects.create(
            usuario=request.user,
            estado='pendiente'
            # ... tus otros campos de envío o dirección si aplican ...
        )
        
        # 2. Tu lógica actual para agregar los ítems al pedido
        # (Este es un ejemplo estándar, déjalo como tú lo tengas en tu app)
        # Supongamos que recorres los productos de un carrito en sesión o BD:
        # for item_carrito in carrito:
        #     PedidoItem.objects.create(
        #         pedido=pedido,
        #         producto=item_carrito.producto,
        #         cantidad=item_carrito.cantidad,
        #         precio_unitario=item_carrito.producto.precio
        #     )
        
        # 3. Extraemos el perfil de la joyería vinculada al usuario
        perfil_vendedor = getattr(request.user, 'perfil', None)
        
        # 4. 🔥 Ejecutamos la matemática fiscal sobre el objeto 'pedido' real
        # Al pasarle 'pedido' y 'perfil_vendedor' se quita el subrayado amarillo
        calcular_totales_con_reglas_fiscales(pedido, perfil_vendedor)
        
        # 5. Redireccionas al flujo de pago o éxito
        return redirect('pago_exitoso')
        
    # Si es un método GET, renderizas el formulario o la vista de confirmación
    return render(request, 'tienda/checkout.html')

@login_required(login_url='/login/')
def gastos(request):
    # 🛡️ Asegura la estructura base de gastos al ingresar al formulario
    _asegurar_gastos_basicos(request.user)

    if request.method == 'POST':
        form = GastoForm(request.POST)
        if form.is_valid():
            gasto = form.save(commit=False)
            gasto.usuario = request.user
            gasto.save()
            messages.success(request, "Gasto registrado correctamente.")
            return redirect('gastos')
    else:
        form = GastoForm()

    gastos_list = Gasto.objects.filter(usuario=request.user).order_by('-fecha')
    total_gastos = gastos_list.aggregate(total=Sum('monto'))['total'] or 0

    return render(
        request,
        'gastos.html',
        {
            'form': form,
            'gastos': gastos_list,
            'total_gastos': total_gastos,
        }
    )

@login_required(login_url='/login/')
def lista_gastos(request):
    usuario = request.user
    _asegurar_gastos_basicos(usuario)

    # Filtro base: Solo registros contables en estado 'Activo'
    gastos_query = Gasto.objects.filter(usuario=usuario, activo=True)

    # 📊 CÁLCULO DE METRICAS KPI CONTINUAS (Para las Tarjetas Superiores)
    metricas = gastos_query.aggregate(
        total_global=Sum('monto'),
        total_fijo=Sum('monto', filter=Q(categoria='fijo')),
        total_fab=Sum('monto', filter=Q(categoria='fabricacion')),
        total_mkt=Sum('monto', filter=Q(categoria='marketing')),
    )

    total_global = metricas['total_global'] or 0
    total_fijo = metricas['total_fijo'] or 0
    total_fab = metricas['total_fab'] or 0
    total_mkt = metricas['total_mkt'] or 0

    # 🔢 Obtención de porcentajes reales para analítica visual
    porcentaje_fijo = (total_fijo / total_global * 100) if total_global > 0 else 0
    porcentaje_fab = (total_fab / total_global * 100) if total_global > 0 else 0
    porcentaje_mkt = (total_mkt / total_global * 100) if total_global > 0 else 0

    # Procesar filtros de búsqueda de la UI si existen
    buscar = request.GET.get('buscar')
    categoria_filtrada = request.GET.get('categoria')

    if buscar:
        gastos_query = gastos_query.filter(nombre__icontains=buscar)
    if categoria_filtrada:
        gastos_query = gastos_query.filter(categoria__iexact=categoria_filtrada)

    gastos_filtrados = gastos_query.order_by('-fecha')
    form = GastoForm()

    return render(request, "gastos.html", {
        "gastos": gastos_filtrados,
        "categoria_filtrada": categoria_filtrada,
        "total_gastos": total_global,
        
        # Datos empaquetados para las Tarjetas KPI
        "total_fijo": total_fijo, "porcentaje_fijo": porcentaje_fijo,
        "total_fab": total_fab, "porcentaje_fab": porcentaje_fab,
        "total_mkt": total_mkt, "porcentaje_mkt": porcentaje_mkt,
        
        "form": form,
    })

@login_required(login_url='/login/')
def editar_gasto(request, id):
    gasto = get_object_or_404(Gasto, id=id, usuario=request.user, activo=True)
    
    if request.method == 'POST':
        form = GastoForm(request.POST, instance=gasto)
        if form.is_valid():
            form.save()
            messages.success(request, f"Gasto '{gasto.nombre}' actualizado correctamente.")
            return redirect('lista_gastos')
        else:
            # ❌ Si hay errores, volvemos a renderizar la lista pasándole el formulario fallido con sus alertas
            gastos = Gasto.objects.filter(usuario=request.user, activo=True).order_by('-fecha')
            total_gastos = gastos.aggregate(total=Sum("monto"))["total"] or 0
            messages.error(request, "Por favor corrige los errores en el formulario.")
            return render(request, "lista_gastos.html", {
                "gastos": gastos, "total_gastos": total_gastos, "form": form
            })
    return redirect('lista_gastos')

@login_required(login_url='/login/')
def duplicar_gasto(request, id):
    gasto_original = get_object_or_404(Gasto, id=id, usuario=request.user, activo=True)
    
    # 🔢 Buscar el número de copia incremental
    nombre_base = re.sub(r' \(Copia \d+\)$', '', gasto_original.nombre)
    coincidencias = Gasto.objects.filter(
        usuario=request.user, 
        nombre__startswith=nombre_base,
        activo=True
    ).count()
    
    nuevo_nombre = f"{nombre_base} (Copia {coincidencias})"
    
    gasto_nuevo = Gasto(
        usuario=request.user,
        nombre=nuevo_nombre,
        monto=gasto_original.monto,
        categoria=gasto_original.categoria,
        observaciones=gasto_original.observaciones
    )
    gasto_nuevo.save()
    messages.success(request, f"Duplicado como: '{nuevo_nombre}'")
    return redirect('lista_gastos')

@login_required(login_url='/login/')
def eliminar_gasto(request, id):
    gasto = get_object_or_404(Gasto, id=id, usuario=request.user)
    nombre_eliminado = gasto.nombre
    
    # 📉 BORRADO LÓGICO: Mantiene el registro intacto para no quebrar estadísticas históricas
    gasto.activo = False
    gasto.save()
    
    messages.warning(request, f"Gasto '{nombre_eliminado}' archivado/eliminado del panel.")
    return redirect('lista_gastos')

@login_required
def registrar_abono(request, pedido_id):
    pedido = get_object_or_404(Pedido, id=pedido_id, usuario=request.user)

    if request.method == 'POST':
        monto_raw = request.POST.get('monto')

        if monto_raw:
            try:
                monto = Decimal(monto_raw)
            except (ValueError, TypeError):
                messages.error(request, "Monto de abono inválido.")
                return redirect('detalle_pedido', pedido_id=pedido.id)

            Abono.objects.create(pedido=pedido, monto=monto)

            # Restar al saldo del pedido
            pedido.saldo_pendiente -= monto

            if pedido.saldo_pendiente <= 0:
                pedido.saldo_pendiente = Decimal('0.00')
                pedido.estado = "pagado"

            pedido.save()

            # ALINEACIÓN CON EL CRM: Actualización inmediata del saldo global del cliente
            if pedido.cliente:
                cliente = pedido.cliente
                saldo_total_dict = Pedido.objects.filter(
                    cliente=cliente, 
                    usuario=request.user, 
                    saldo_pendiente__gt=0
                ).aggregate(total=Sum('saldo_pendiente'))
                
                cliente.saldo_pendiente = saldo_total_dict['total'] or Decimal('0.00')
                cliente.save()

            messages.success(request, "Abono registrado correctamente")

    return redirect('detalle_pedido', pedido_id=pedido.id)


@login_required
def cobrar_whatsapp(request, pedido_id):
    """📱 COBRO DE UN PEDIDO INDEPENDIENTE"""
    pedido = get_object_or_404(Pedido, id=pedido_id, usuario=request.user)

    if pedido.saldo_pendiente <= 0:
        messages.info(request, "Este pedido ya está pago")
        return redirect('detalle_pedido', pedido_id=pedido.id)

    saldo_formateado = f"{pedido.saldo_pendiente:,.0f}".replace(",", ".")

    try:
        nombre_tienda = request.user.perfil.nombre_tienda or "nuestra joyería"
    except AttributeError:
        nombre_tienda = "nuestra joyería"

    mensaje = (
        f"Hola {pedido.cliente_nombre},\n\n"
        f"Te escribimos de {nombre_tienda} 💎\n\n"
        f"🧾 Factura: {pedido.numero_orden}\n"
        f"📅 Vencimiento: {pedido.fecha_limite}\n"
        f"💰 Saldo pendiente: ${saldo_formateado}\n\n"
        f"⚠️ Esta factura se encuentra vencida.\n"
        f"Agradecemos tu pago lo antes posible.\n\n"
        f"Si ya realizaste el pago, por favor ignora este mensaje 🙏"
    )

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
    """💵 COBRO DE CARTERA TOTAL DE UN CLIENTE (Suma todos sus pedidos vencidos)"""
    cliente = get_object_or_404(Cliente, id=cliente_id, usuario=request.user)

    pedidos = Pedido.objects.filter(
        cliente=cliente,
        usuario=request.user,  # Importante asegurar que pertenezca al usuario de la sesión
        saldo_pendiente__gt=0
    ).order_by('fecha_limite')

    if not pedidos:
        messages.info(request, "Este cliente no tiene cuentas pendientes.")
        return redirect('cartera_clientes')

    total_acumulado = Decimal('0.00')
    mensaje = f"Hola {cliente.nombre},\n\nTienes las siguientes facturas pendientes en nuestra joyería:\n\n"

    for p in pedidos:
        saldo_pedido_formateado = f"{p.saldo_pendiente:,.0f}".replace(",", ".")
        mensaje += f"🧾 Pedido #{p.numero_orden or p.id} → ${saldo_pedido_formateado}\n"
        total_acumulado += p.saldo_pendiente

    if cliente.saldo_pendiente != total_acumulado:
        cliente.saldo_pendiente = total_acumulado
        cliente.save()

    total_formateado = f"{total_acumulado:,.0f}".replace(",", ".")
    mensaje += f"\n💰 Total Pendiente: ${total_formateado}\n\n"
    mensaje += "Por favor, agradeceríamos realizar el pago por transferencia o reportar tu comprobante. ¡Muchas gracias por tu confianza! 🙏"

    telefono = ''.join(filter(str.isdigit, cliente.telefono or ""))
    if not telefono:
        messages.warning(request, "El cliente no tiene un teléfono válido registrado")
        return redirect('cartera_clientes')

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

        estado_visual = 'vencido' if dias_mora > 0 else 'pendiente'
        link_pago = request.build_absolute_uri(f"/factura/{p.token_publico}/")

        # Ajuste de mensaje con el formato de moneda limpio
        saldo_formateado = f"{p.saldo_pendiente:,.0f}".replace(",", ".")
        mensaje = f"Hola {p.cliente_nombre}, tienes un saldo pendiente de ${saldo_formateado}. Puedes pagar aquí: {link_pago}"
        mensaje_codificado = quote(mensaje)

        # 🌟 CORRECCIÓN: Uso de '=' en lugar de ':' y limpieza de número telefónico
        telefono_limpio = ''.join(filter(str.isdigit, p.cliente_telefono or ""))
        if telefono_limpio and not telefono_limpio.startswith('57'):
            telefono_limpio = f"57{telefono_limpio}"
            
        whatsapp_link = f"https://wa.me/{telefono_limpio}?text={mensaje_codificado}"

        cartera.append({
            'pedido': p,
            'cliente': p.cliente,
            'telefono': p.cliente_telefono,
            'saldo': p.saldo_pendiente,
            'fecha_limite': p.fecha_limite,
            'dias_mora': dias_mora,
            'estado': estado_visual,
            'link_pago': link_pago,
            'whatsapp': whatsapp_link,
        })
        
    # 🌟 CORRECCIÓN: Sumar usando Decimal explícito para evitar fallos de tipos
    morosos_total = sum((c['saldo'] for c in cartera if c['estado'] == 'vencido'), Decimal('0.00'))
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
    
    # 🛡️ PROTECCIÓN CRÍTICA: Obtención segura del perfil
    usuario_pedido = pedido.usuario
    try:
        perfil = Perfil.objects.get(user=usuario_pedido)
    except Perfil.DoesNotExist:
        perfil = None

    empresa_nombre = perfil.nombre_tienda if perfil else "Mi Joyería"
    empresa_nit = perfil.nit if perfil else ""
    empresa_telefono = perfil.whatsapp if perfil else ""
    empresa_direccion = getattr(perfil, 'direccion', getattr(perfil, 'ciudad', '')) or ""
    empresa_email = perfil.email_empresa if perfil else ""
    
    empresa_logo = ""
    if perfil and hasattr(perfil, 'logo') and perfil.logo:
        try:
            empresa_logo = perfil.logo.url
        except Exception:
            empresa_logo = ""

    color_primario = perfil.color_primario if perfil else "#000000"
    color_secundario = perfil.color_secundario if perfil else "#333333"

    # 🛡️ PROTECCIÓN DE CÁLCULOS MATEMÁTICOS CONTRA VALORES NULL
    subtotal = sum(Decimal(str(item.subtotal or 0)) for item in items)
    iva_total = sum(Decimal(str(item.iva or 0)) for item in items)
    
    porcentaje_desc = getattr(pedido, 'porcentaje_descuento', 0) or 0
    descuento_total = subtotal * (Decimal(str(porcentaje_desc)) / Decimal('100')) if porcentaje_desc else Decimal('0')
    
    retefuente_total = sum(Decimal(str(item.retefuente or 0)) for item in items)
    envio = Decimal(str(getattr(pedido, 'costo_envio', 0) or 0))
    total_final = subtotal + iva_total + envio - descuento_total - retefuente_total

    # Motor de estados
    if pedido.estado == "pendiente":
        estado = "pendiente"
    elif pedido.saldo_pendiente is not None and pedido.saldo_pendiente <= 0:
        estado = "pagado"
    elif pedido.fecha_limite and pedido.fecha_limite < timezone.now().date():
        estado = "vencido"
    else:
        estado = pedido.estado

    # Integración de WhatsApp
    mensaje = f"Hola! Adjunto el comprobante de pago del pedido #{pedido.numero_orden} por valor de ${total_final:,.0f}".replace(",", ".")
    whatsapp_num = empresa_telefono if empresa_telefono != "-" else ""
    whatsapp_url = f"https://wa.me/{whatsapp_num}?text={urllib.parse.quote(mensaje)}"
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(whatsapp_url)}"

    # Control de fechas seguro para evitar 'NoneType' object has no attribute 'strftime'
    if getattr(pedido, 'fecha', None):
        fecha_str = pedido.fecha.strftime('%Y-%m-%d')
    elif getattr(pedido, 'fecha_creacion', None):
        fecha_str = pedido.fecha_creacion.strftime('%Y-%m-%d')
    else:
        fecha_str = timezone.now().strftime('%Y-%m-%d')

    # Extraemos de forma segura los datos del cliente, soportando si vienen por relación externa
    c_nombre = getattr(pedido, 'cliente_nombre', None) or (pedido.cliente.nombre if getattr(pedido, 'cliente', None) else 'Consumidor Final')
    c_email = getattr(pedido, 'cliente_email', None) or (pedido.cliente.email if getattr(pedido, 'cliente', None) else '')
    c_telefono = getattr(pedido, 'cliente_telefono', None) or (pedido.cliente.telefono if getattr(pedido, 'cliente', None) else '')
    c_direccion = getattr(pedido, 'cliente_direccion', None) or (pedido.cliente.direccion if getattr(pedido, 'cliente', None) else '')
    c_ciudad = getattr(pedido, 'cliente_ciudad', None) or (pedido.cliente.ciudad if getattr(pedido, 'cliente', None) else '')
    c_nit = getattr(pedido, 'cliente_nit', None) or (pedido.cliente.nit if getattr(pedido, 'cliente', None) else '')

    context = {
        'pedido': pedido,
        'items': items,
        'cliente_nombre': c_nombre,
        'cliente_email': c_email,
        'cliente_telefono': c_telefono,
        'cliente_direccion': c_direccion,
        'cliente_ciudad': c_ciudad,
        'cliente_nit': c_nit,
        'empresa_nombre': empresa_nombre,
        'empresa_nit': empresa_nit,
        'empresa_telefono': empresa_telefono,
        'empresa_direccion': empresa_direccion,
        'empresa_email': empresa_email,
        'empresa_logo': empresa_logo,
        'color_primario': color_primario,
        'color_secundario': color_secundario,
        'fecha_str': fecha_str,
        'subtotal': subtotal,
        'iva_total': iva_total,
        'total_final': total_final,
        'descuento_total': descuento_total,
        'retefuente_total': retefuente_total,
        'costo_envio': envio,   
        'qr_pago_url': qr_url,  
        'whatsapp_url': whatsapp_url,
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
    # 🎯 FILTROS CORREGIDOS (ANTI-ERROR 500)
    # =====================================
    if tipo == 'vip':
        # Clientes que han comprado igual o más de $500,000 COP
        clientes = clientes.filter(total_compras__gte=500000)

    elif tipo == 'nuevos':
        # Filtra de forma segura por la fecha de registro de hoy
        clientes = clientes.filter(fecha_creacion__date=hoy)

    elif tipo == 'dormidos':
        # 🔥 CORRECCIÓN CRÍTICA: Se usa doble guion bajo '__' para relacionar la tabla Pedido
        # y la propiedad '__lt' (less than) para evaluar la fecha correctamente en Django.
        clientes = clientes.filter(pedido__fecha_creacion__lt=hace_30).distinct()

    # Si se pasa un ID para excluir, lo sacamos de la lista para avanzar al siguiente
    if excluir_id:
        clientes = clientes.exclude(id=excluir_id)

    # Excluir de entrada registros sin números telefónicos o nulos
    clientes_validos = clientes.exclude(telefono__isnull=True).exclude(telefono='')

    # Traemos el primero disponible de la cola restante
    cliente_destino = clientes_validos.first()

    if not cliente_destino:
        messages.info(
            request,
            f"¡Felicidades! Has completado o no hay clientes en el segmento '{tipo}'."
        )
        return redirect('dashboard')

    # =====================================
    # ☎️ LIMPIAR TELÉFONO SEGURO
    # =====================================
    telefono = ''.join(filter(str.isdigit, cliente_destino.telefono or ""))

    if not telefono:
        # Si este registro estaba vacío, saltamos al siguiente de forma automática
        messages.warning(request, f"Cliente {cliente_destino.nombre} no tiene un formato de teléfono válido.")
        return redirect(f"{request.path}?tipo={tipo}&excluir_id={cliente_destino.id}")

    # Forzar prefijo de Colombia si aplica
    if not telefono.startswith('57') and len(telefono) == 10:
        telefono = f"57{telefono}"

    # =====================================
    # 🧠 PERSONALIZAR MENSAJE DINÁMICO (SaaS)
    # =====================================
    try:
        perfil = usuario.perfil
        nombre_tienda = perfil.nombre_tienda if perfil.nombre_tienda else "Nuestra Joyería"
    except AttributeError:
        nombre_tienda = "Nuestra Joyería"

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

    # Guardamos el link de avance seguro
    url_siguiente = f"{request.path}?tipo={tipo}&excluir_id={cliente_destino.id}"
    messages.success(
        request, 
        f"Abriendo WhatsApp para {cliente_destino.nombre}. "
        f"<a href='{url_siguiente}' style='font-weight:bold; color:#007bff; text-decoration:underline;'>¡Haga clic aquí para pasar al siguiente cliente de la lista!</a>",
        extra_tags='safe'
    )

    return redirect(url_whatsapp)

@login_required
def fabricacion(request):
    # Definimos las variables de costos (por ahora en 0 para que cargue limpio)
    context = {
        'material_total': 0.0,
        'total_mano_obra': 0.0,
        'costos_totales': 0.0,
        'mensaje': "⚙️ Control de Fabricación"
    }
    return render(request, 'productos/fabricacion.html', context)