from decimal import Decimal
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import render, redirect
from django.utils import timezone

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