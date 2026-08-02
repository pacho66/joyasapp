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
from ..models import Perfil

# =========================================================================
# 🔑 BLOQUE 2: AUTENTICACIÓN, CUENTAS Y PLANES SAAS
# =========================================================================

# Precios planes (Estructura de datos o constante que uses)

# =========================================================================
# ⚙️ CENTRO DE CONTROL DE TARIFAS DEL SAAS (Ubicación segura y limpia)
# =========================================================================
PRECIOS_PLANES = {
    'basico': {
        'nombre': 'Básico',
        'precio_mensual': Decimal('50000.00'),  # COP
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
    perfil = Perfil.objects.filter(activa=True).first()       

    return render(request, 'login.html', {'perfil': perfil})

@login_required
def cerrar_sesion(request):
    logout(request)
    return redirect('login')

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

@login_required
def renovar_manual(request):
    """Vista para forzar la renovación del plan SaaS desde el panel."""
    perfil = request.user.perfil
    renovar_plan(perfil)
    return redirect('dashboard')
