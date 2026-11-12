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
from ..models import Perfil, Producto, ConfiguracionSEO, ConfiguracionPromocion


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
    host = request.get_host().split(':')[0].lower()
    dominios_saas = ['joyasapp.com', 'www.joyasapp.com', '127.0.0.1', 'localhost']
    
    if host not in dominios_saas and '.joyasapp.com' in host:
        subdominio_actual = host.split('.')[0]
        perfil_tienda = Perfil.objects.filter(subdominio=subdominio_actual, activa=True).first()
        if perfil_tienda:
            productos = Producto.objects.filter(usuario=perfil_tienda.user, activo=True)
            # 💡 Cambia 'index.html' por el nombre de la plantilla de tu catálogo (ej. 'catalogo.html')
            return render(request, 'catalogo.html', {'perfil': perfil_tienda, 'productos': productos})
    
    seo_config, _ = ConfiguracionSEO.objects.get_or_create(id=1)
    
    config_promo, _ = ConfiguracionPromocion.objects.update_or_create(
        pk=1,
        defaults={
            'en_promocion': True,
            'precio_basico_actual': 49900,
            'precio_basico_tachado': 59900,
            'precio_semi_actual': 89900,
            'precio_semi_tachado': 109900,
            'precio_premium_actual': 149900,
            'precio_premium_tachado': 179900,
        }
    )
    
    return render(request, 'landing_saas.html', {
        'seo': seo_config,
        'en_promocion': config_promo.en_promocion,
        'plan_basico_actual': config_promo.precio_basico_actual,
        'plan_basico_tachado': config_promo.precio_basico_tachado,
        'plan_semi_actual': config_promo.precio_semi_actual,
        'plan_semi_tachado': config_promo.precio_semi_tachado,
        'plan_premium_actual': config_promo.precio_premium_actual,
        'plan_premium_tachado': config_promo.precio_premium_tachado,
        'precios_planes': PRECIOS_PLANES,
    })

def registro(request):
    plan_seleccionado = request.GET.get('plan', 'basico')
    if plan_seleccionado not in ['basico', 'semi', 'premium']:
        plan_seleccionado = 'basico'

    if request.method == 'POST':
        form = RegistroForm(request.POST)

        if form.is_valid():
            username = form.cleaned_data['username']
            email = form.cleaned_data['email']
            password = form.cleaned_data['password']
            first_name = form.cleaned_data['first_name']
            last_name = form.cleaned_data['last_name']
            
            prefijo = form.cleaned_data['prefijo']
            whatsapp_num = form.cleaned_data['whatsapp']
            telefono_completo = f"{prefijo} {whatsapp_num}"
            
            ciudad = form.cleaned_data['ciudad']
            direccion = form.cleaned_data['direccion']
            nombre_tienda = form.cleaned_data['nombre_tienda']

            plan_elegido = request.POST.get('plan', plan_seleccionado)
            if plan_elegido not in ['basico', 'semi', 'premium']:
                plan_elegido = 'basico'

            # 🌐 Generar subdominio único automáticamente
            base_subdominio = slugify(nombre_tienda)
            subdominio = base_subdominio
            contador = 1
            while Perfil.objects.filter(subdominio=subdominio).exists():
                subdominio = f"{base_subdominio}{contador}"
                contador += 1

            with transaction.atomic():
                # 1. Crear el usuario de Django con nombres reales y seudónimo
                user, created = User.objects.get_or_create(
                    username=username,
                    defaults={
                        'email': email,
                        'first_name': first_name,
                        'last_name': last_name
                    }
                )
                if not created:
                    user.set_password(password)
                    user.email = email
                    user.first_name = first_name
                    user.last_name = last_name
                    user.save()
                else:
                    user.set_password(password)
                    user.save()

                # 2. Guardar o actualizar perfil completo
                Perfil.objects.update_or_create(
                    user=user,
                    defaults={
                        'nombre_tienda': nombre_tienda,
                        'whatsapp': telefono_completo,
                        'ciudad': ciudad,
                        'direccion': direccion,
                        'subdominio': subdominio,
                        'plan': plan_elegido,
                        'activa': True,
                        'plan_vence': timezone.localdate() + timedelta(days=15)
                    }
                )

            # 3. Autenticar y enviar al dashboard de forma segura
            backend = 'django.contrib.auth.backends.ModelBackend'
            login(request, user, backend=backend)
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

# =========================================================================
# 📖 MANUAL DE USUARIO DEL SAAS
# =========================================================================

def manual_usuario(request):
    """Vista dedicada para el manual completo de usuario de JoyasApp."""
    context = {
        'precios_planes': PRECIOS_PLANES,
    }
    return render(request, 'manual_usuario.html', context)