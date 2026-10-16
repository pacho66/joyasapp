import re
import urllib.parse
from decimal import Decimal
from datetime import timedelta
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from ..models import Gasto, Pedido, Abono, Cliente
from ..forms import GastoForm

# =========================================================================
# 💸 BLOQUE 10: CONTABILIDAD DE GASTOS, ABONOS Y COBRANZAS (CARTERA)
# =========================================================================

@login_required(login_url='/login/')
def gastos(request):
    usuario = request.user

    # 1. 📥 CREAR NUEVO GASTO (100% Dinámico)
    if request.method == 'POST':
        nombre = request.POST.get('nombre')
        monto = request.POST.get('monto')
        categoria = request.POST.get('categoria', 'fabricacion')
        observaciones = request.POST.get('observaciones', '')

        if nombre and monto:
            Gasto.objects.create(
                usuario=usuario,
                nombre=nombre,
                monto=monto,
                categoria=categoria,
                observaciones=observaciones
            )
            messages.success(request, f"Gasto '{nombre}' registrado correctamente.")
            return redirect('gastos')
        else:
            messages.error(request, "Por favor completa el concepto y el monto.")

    # 2. 🔍 CONSULTA BASE DE GASTOS DEL CLIENTE
    gastos_query = Gasto.objects.filter(usuario=usuario, activo=True)

    # 📊 CÁLCULO DE MÉTRICAS KPI
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

    porcentaje_fijo = (total_fijo / total_global * 100) if total_global > 0 else 0
    porcentaje_fab = (total_fab / total_global * 100) if total_global > 0 else 0
    porcentaje_mkt = (total_mkt / total_global * 100) if total_global > 0 else 0

    # 🔢 FILTROS DE BÚSQUEDA Y CATEGORÍA
    buscar = request.GET.get('buscar')
    categoria_filtrada = request.GET.get('categoria')

    if buscar:
        gastos_query = gastos_query.filter(nombre__icontains=buscar)
    if categoria_filtrada:
        gastos_query = gastos_query.filter(categoria__iexact=categoria_filtrada)

    gastos_filtrados = gastos_query.order_by('-fecha')

    return render(request, "gastos.html", {
        "gastos": gastos_filtrados,
        "categoria_filtrada": categoria_filtrada,
        "total_gastos": total_global,
        "total_fijo": total_fijo, "porcentaje_fijo": porcentaje_fijo,
        "total_fab": total_fab, "porcentaje_fab": porcentaje_fab,
        "total_mkt": total_mkt, "porcentaje_mkt": porcentaje_mkt,
    })

@login_required(login_url='/login/')
def editar_gasto(request, id):
    gasto = get_object_or_404(Gasto, id=id, usuario=request.user)
    if request.method == 'POST':
        gasto.nombre = request.POST.get('nombre', gasto.nombre)
        gasto.monto = request.POST.get('monto', gasto.monto)
        gasto.categoria = request.POST.get('categoria', gasto.categoria)
        gasto.observaciones = request.POST.get('observaciones', gasto.observaciones)
        gasto.save()
        messages.success(request, f"Gasto '{gasto.nombre}' actualizado correctamente.")
    return redirect('gastos')

@login_required(login_url='/login/')
def duplicar_gasto(request, id):
    gasto_original = get_object_or_404(Gasto, id=id, usuario=request.user, activo=True)
    Gasto.objects.create(
        usuario=request.user,
        nombre=f"{gasto_original.nombre} (Copia)",
        monto=gasto_original.monto,
        categoria=gasto_original.categoria,
        observaciones=gasto_original.observaciones
    )
    messages.success(request, f"Gasto duplicado correctamente.")
    return redirect('gastos')

@login_required(login_url='/login/')
def eliminar_gasto(request, id):
    gasto = get_object_or_404(Gasto, id=id, usuario=request.user)
    nombre = gasto.nombre
    gasto.delete()  # 👈 Eliminación real y definitiva de la base de datos
    messages.warning(request, f"Gasto '{nombre}' eliminado permanentemente.")
    return redirect('gastos')

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
        clientes = clientes.filter(pedido_fecha_creacion_lt=hace_30).distinct()

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