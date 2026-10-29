from decimal import Decimal
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.text import slugify

from ..forms import RegistroForm
from ..models import Perfil,Producto

# =========================================================================
# 🔑 BLOQUE 2: AUTENTICACIÓN, CUENTAS Y PLANES SAAS
# =========================================================================

# Precios planes (Estructura de datos o constante que uses)

# =========================================================================
# ⚙️ CENTRO DE CONTROL DE TARIFAS DEL SAAS
# =========================================================================
PRECIOS_PLANES = {
    'basico': {
        'nombre': 'Básico',
        'precio_mensual': Decimal('49900.00'),  # COP
        'descripcion': 'Todo lo esencial para comenzar.',
        'link_pago': 'https://checkout.wompi.co/l/TU_LINK_BASICO'
    },
    'semi': {
        'nombre': 'Semi',
        'precio_mensual': Decimal('89900.00'),  # COP
        'descripcion': 'Control avanzado y facturación Siigo.',
        'link_pago': 'https://checkout.wompi.co/l/TU_LINK_SEMI'
    },
    'premium': {
        'nombre': 'Premium',
        'precio_mensual': Decimal('149900.00'),  # COP
        'descripcion': 'Subdominio personalizado exclusivo + CRM completo, automatizaciones de WhatsApp y soporte prioritario 24/7.',
        'link_pago': 'https://checkout.wompi.co/l/TU_LINK_PREMIUM'
    }
}

def index_saas(request):
    # 1. Obtener el dominio/host desde el que entra el cliente
    host = request.get_host().split(':')[0].lower()
    
    # 2. Dominios que pertenecen a la sala principal del SaaS
    dominios_saas = ['joyasapp.com', 'www.joyasapp.com', '127.0.0.1', 'localhost']
    
    # 3. SI VIENE DE UN SUBDOMINIO (ejemplo: pgjoyas.joyasapp.com)
    if host not in dominios_saas and '.joyasapp.com' in host:
        subdominio_actual = host.split('.')[0]
        
        # Buscamos la tienda asignada a ese subdominio
        perfil_tienda = Perfil.objects.filter(subdominio=subdominio_actual, activa=True).first()
        
        if perfil_tienda:
            # Renderiza la tienda del cliente con sus productos
            productos = Producto.objects.filter(usuario=perfil_tienda.user, activo=True)
            return render(request, 'index.html', {
                'perfil': perfil_tienda,
                'productos': productos
            })
    
    # 4. SI VIENE DE LA RAÍZ PURA (joyasapp.com)
    # Muestra únicamente la sala principal del SaaS (Landing Page limpia)
    return render(request, 'landing_saas.html')

def registro(request):
    # 1. Capturar el plan desde los parámetros GET (ej: ?plan=semi), por defecto 'basico'
    plan_seleccionado = request.GET.get('plan', 'basico')
    if plan_seleccionado not in ['basico', 'semi', 'premium']:
        plan_seleccionado = 'basico'

    if request.method == 'POST':
        form = RegistroForm(request.POST)

        if form.is_valid():
            username = form.cleaned_data['username']
            email = form.cleaned_data['email']
            password = form.cleaned_data['password']
            nombre_tienda = form.cleaned_data['nombre_tienda']
            whatsapp = form.cleaned_data['whatsapp']

            # Capturar el plan enviado por POST (o mantener el de la URL)
            plan_elegido = request.POST.get('plan', plan_seleccionado)
            if plan_elegido not in ['basico', 'semi', 'premium']:
                plan_elegido = 'basico'

            # ⚙️ Mapeo exacto de límites y permisos según el plan elegido
            config_planes = {
                'basico': {'usuarios': 1, 'wompi': False, 'siigo': False},
                'semi': {'usuarios': 3, 'wompi': True, 'siigo': True},
                'premium': {'usuarios': 999999, 'wompi': True, 'siigo': True}
            }
            conf = config_planes.get(plan_elegido, config_planes['basico'])

            # 🌐 Generar subdominio único automáticamente
            base_subdominio = slugify(nombre_tienda)
            subdominio = base_subdominio
            contador = 1
            while Perfil.objects.filter(subdominio=subdominio).exists():
                subdominio = f"{base_subdominio}{contador}"
                contador += 1

            with transaction.atomic():
                # 1. Crear el usuario de Django
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password
                )

                # 2. Crear el Perfil con el plan correcto y sus restricciones lógicas
                Perfil.objects.create(
                    user=user,
                    nombre_tienda=nombre_tienda,
                    whatsapp=whatsapp,
                    subdominio=subdominio,
                    plan=plan_elegido,
                    usuarios_permitidos=conf['usuarios'],
                    permite_wompi=conf['wompi'],
                    permite_siigo=conf['siigo'],
                    activa=True,
                    plan_vence=timezone.localdate() + timedelta(days=15)
                )

            # 3. Autenticar y enviar al dashboard
            login(request, user)
            return redirect('dashboard')

    else:
        form = RegistroForm()

    return render(request, 'registro.html', {
        'form': form,
        'plan_seleccionado': plan_seleccionado
    })

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
            next_url = request.GET.get('next') or request.POST.get('next') or 'dashboard'
            return redirect(next_url)
        else:
            messages.error(
                request,
                'Usuario o contraseña incorrectos'
            )
            
    perfil = Perfil.objects.filter(activa=True).first()       
    return render(request, 'login.html', {'perfil': perfil})

@login_required
def cerrar_sesion(request):
    logout(request)
    return redirect('login')

def renovar_plan(perfil):
    """Lógica centralizada para extender la vigencia del plan 30 días."""
    hoy = timezone.localdate()

    if perfil.plan_vence and perfil.plan_vence > hoy:
        perfil.plan_vence = perfil.plan_vence + timedelta(days=30)
    else:
        perfil.plan_vence = hoy + timedelta(days=30)

    perfil.activa = True
    perfil.save()

@login_required
def renovar_manual(request):
    """Vista para forzar la renovación del plan SaaS desde el panel."""
    perfil = request.user.perfil
    renovar_plan(perfil)
    return redirect('dashboard')

@login_required
def gestionar_planes(request):
    """Vista interna para ver el plan actual, días restantes y links de pago Wompi."""
    perfil = request.user.perfil
    
    # Calcular días restantes de prueba o vigencia del plan
    hoy = timezone.localdate()
    dias_restantes = (perfil.plan_vence - hoy).days if perfil.plan_vence else 0
    dias_restantes = max(0, dias_restantes)  # Evitar números negativos

    context = {
        'perfil': perfil,
        'precios_planes': PRECIOS_PLANES,
        'dias_restantes': dias_restantes,
    }
    return render(request, 'autenticacion/planes.html', context)