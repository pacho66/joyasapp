import traceback
import time
from decimal import Decimal
from datetime import timedelta

import openpyxl

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Avg, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.utils import timezone

from ..models import (
    Perfil,
    Pedido,
    Cliente,
    Producto,
    Gasto,
    PedidoItem,
)

from .contabilidad import _asegurar_gastos_basicos

# =========================================================================
# 📊 BLOQUE 7: DASHBOARD ADMINISTRATIVO, CRM Y ESTADÍSTICAS
# =========================================================================
@login_required(login_url='/login/')
def dashboard(request):
    try:
        usuario = request.user
        hoy = timezone.localdate()

        # ✅ Perfil multiusuario del SaaS seguro
        perfil, creado = Perfil.objects.get_or_create(
            user=usuario,
            defaults={
                'nombre_tienda': 'JoyasApp',
                'plan': 'gratis'
            }
        )

        # 🛡️ Protección de Gastos Activa (Protegida si no existe la función)
        try:
            if 'I' in globals() or '_asegurar_gastos_basicos' in globals():
                _asegurar_gastos_basicos(usuario)
        except Exception:
            pass

        # 🔒 Validar vencimiento del plan de forma segura
        if hasattr(perfil, 'plan_vence') and perfil.plan_vence and perfil.plan_vence < hoy:
            return render(request, 'plan_vencido.html', {'perfil': perfil})
        
        ayer = hoy - timedelta(days=1)
        hace_30 = timezone.now() - timedelta(days=30)
        tipo = request.GET.get('tipo')

        pedidos = Pedido.objects.filter(usuario=usuario)

        # 📊 GRÁFICAS (ÚLTIMOS 7 DÍAS)
        ventas_por_dia = (
            pedidos
            .annotate(fecha_dia=TruncDate('fecha'))
            .values('fecha_dia')
            .annotate(total=Sum('total'))
            .order_by('fecha_dia')
        )

        fechas = [v['fecha_dia'].strftime('%d/%m') for v in ventas_por_dia if v['fecha_dia']]
        totales = [float(v['total']) if v['total'] is not None else 0.0 for v in ventas_por_dia]
        
        clientes = Cliente.objects.filter(usuario=usuario)
        productos = Producto.objects.filter(usuario=usuario)

        # 🔴 CARTERA Y MOROSOS (Protegido por campos opcionales)
        try:
            pedidos_en_mora = pedidos.filter(tipo_pago='credito', saldo_pendiente_gt=0, fecha_limite_lt=hoy)
            cartera_agg = pedidos_en_mora.aggregate(suma_mora=Sum('saldo_pendiente'))['suma_mora']
            morosos_total = float(cartera_agg) if cartera_agg is not None else 0.0
            clientes_morosos_ids = pedidos_en_mora.values_list('cliente_id', flat=True).distinct()
            morosos_count = Cliente.objects.filter(id__in=clientes_morosos_ids).count()
        except Exception:
            morosos_total = 0.0
            morosos_count = 0

        # FILTROS CLIENTES
        clientes_filtrados = clientes
        if tipo == 'vip':
            clientes_filtrados = clientes.filter(total_compras__gte=500000)
        elif tipo == 'nuevos':
            clientes_filtrados = clientes.filter(fecha_creacion__date=hoy)
        elif tipo == 'dormidos':
            clientes_filtrados = clientes.filter(ultima_compra__lt=hace_30).distinct()
        elif tipo == 'morosos':
            try:
                ids_morosos = pedidos.filter(tipo_pago='credito', saldo_pendiente__gt=0, fecha_limite__lt=hoy).values_list('cliente_id', flat=True).distinct()
                clientes_filtrados = clientes.filter(id__in=ids_morosos)
            except Exception:
                pass
        
        mensajes = {
            'vip': "💎 Clientes VIP",
            'nuevos': "✨ Clientes nuevos",
            'dormidos': "😴 Clientes inactivos",
            'morosos': "🔴 Clientes en mora"
        }
        mensaje = mensajes.get(tipo, "📊 Panel General")

        for clie in clientes_filtrados:
            try:
                total_real = Pedido.objects.filter(usuario=usuario, cliente_nombre=clie.nombre, estado__iexact='pagado').aggregate(suma=Sum('total'))['suma'] or 0
                clie.total_compras = total_real
            except Exception:
                pass

        total_hoy = pedidos.filter(fecha__date=hoy).aggregate(total=Sum('total'))['total'] or 0
        total_ayer = pedidos.filter(fecha__date=ayer).aggregate(total=Sum('total'))['total'] or 0
        total_general = pedidos.aggregate(total=Sum('total'))['total'] or 0
        ganancias_mes = pedidos.filter(fecha__month=hoy.month, fecha__year=hoy.year).aggregate(total=Sum('total'))['total'] or 0

        total_ingresos = float(total_general or 0)

        try:
            costos_pedidos = pedidos.aggregate(mat=Sum('costo_material'), mo=Sum('costo_mano_obra'))
            total_material = float(costos_pedidos['mat'] or 0)
            total_mano_obra = float(costos_pedidos['mo'] or 0)
        except Exception:
            total_material = 0.0
            total_mano_obra = 0.0

        total_costos = total_material + total_mano_obra

        try:
            from ..models import Gasto
            total_gastos = float(Gasto.objects.filter(usuario=usuario).aggregate(total=Sum('monto'))['total'] or 0)
        except Exception:
            total_gastos = 0.0

        utilidad_bruta = total_ingresos - total_costos
        utilidad_neta = utilidad_bruta - total_gastos

        margen_bruto = (utilidad_bruta / total_ingresos * 100) if total_ingresos > 0 else 0
        margen_neto = (utilidad_neta / total_ingresos * 100) if total_ingresos > 0 else 0

        # VALORIZACIÓN DE INVENTARIO
        inv_valorizado_costo = 0.0
        inv_valorizado_venta = 0.0
        for prod in productos.prefetch_related('variantes'):
            p_costo = float(getattr(prod, 'precio_costo', 0) or 0)
            p_detal = float(getattr(prod, 'precio_detal', 0) or 0)
            if hasattr(prod, 'variantes') and prod.variantes.exists():
                cant = sum(int(v.stock or 0) for v in prod.variantes.all())
            else:
                cant = int(prod.stock or 0)
            inv_valorizado_costo += cant * p_costo
            inv_valorizado_venta += cant * p_detal

        utilidad_potencial_inv = inv_valorizado_venta - inv_valorizado_costo
        
        pedidos_hoy = pedidos.filter(fecha__date=hoy).count()
        total_pedidos = pedidos.count()
        pedidos_recientes = pedidos.order_by('-fecha')[:5]
        pedidos_pendientes = pedidos.filter(estado='pendiente').count()
        pedidos_pagados = pedidos.filter(estado='pagado').count()

        total_clientes = clientes.count()
        total_productos = productos.count()
        productos_sin_stock = productos.filter(stock=0).count()
        productos_bajo_stock = productos.filter(stock__gt=0, stock__lte=5).count()

        ticket_promedio = total_general / total_pedidos if total_pedidos > 0 else 0
        crecimiento = total_hoy - total_ayer

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
            'total_ingresos': total_ingresos,
            'total_material': total_material,
            'total_mano_obra': total_mano_obra,
            'total_costos': total_costos,
            'total_gastos': total_gastos,
            'utilidad': utilidad_bruta,
            'utilidad_bruta': utilidad_bruta,
            'utilidad_neta': utilidad_neta,
            'margen_bruto': margen_bruto,
            'margen_neto': margen_neto,
            'inv_valorizado_costo': inv_valorizado_costo,
            'inv_valorizado_venta': inv_valorizado_venta,
            'utilidad_potencial_inv': utilidad_potencial_inv,
            'pedidos_hoy': pedidos_hoy,
            'total_pedidos': total_pedidos,
            'pedidos_recientes': pedidos_recientes,
            'pedidos_pendientes': pedidos_pendientes,
            'pedidos_pagados': pedidos_pagados,
            'total_clientes': total_clientes,
            'total_productos': total_productos,
            'productos_sin_stock': productos_sin_stock,
            'productos_bajo_stock': productos_bajo_stock,
            'ticket_promedio': ticket_promedio,
            'morosos_count': morosos_count,
            'morosos_total': morosos_total,
            'clientes_filtrados': clientes_filtrados,
        }

        return render(request, 'dashboard.html', context)

    except Exception as e:
        print("\n" + "🚨" * 30)
        print(f"💥 ERROR CRÍTICO EN /DASHBOARD/: {str(e)}")
        print("-" * 60)
        traceback.print_exc()
        print("🚨" * 30 + "\n")
        return HttpResponse(f"Error interno en dashboard: {str(e)}", status=500)

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
    usuario = request.user
    hoy = timezone.now().date()
    hace_30 = hoy - timedelta(days=30)
    
    # 1. Filtro base de pedidos del usuario
    pedidos_usuario = Pedido.objects.filter(usuario=usuario)

    # 2. Métricas de ventas
    ventas_hoy = pedidos_usuario.filter(fecha__date=hoy).aggregate(total=Sum('total'))['total'] or 0
    ventas_mes = pedidos_usuario.filter(fecha__date__gte=hace_30).aggregate(total=Sum('total'))['total'] or 0
    ventas_total = pedidos_usuario.aggregate(total=Sum('total'))['total'] or 0
    
    # 3. Ticket Promedio real
    ticket_promedio = pedidos_usuario.aggregate(promedio=Avg('total'))['promedio'] or 0

    # =========================================================================
    # 💰 INTEGRACIÓN REAL DE GASTOS DEL CRM (Últimos 30 días)
    # =========================================================================
    # Sumamos los montos de todos los gastos activos del usuario en los últimos 30 días
    gastos_mes = Gasto.objects.filter(
        usuario=usuario,
        activo=True,
        fecha__date__gte=hace_30
    ).aggregate(total=Sum('monto'))['total'] or 0

    # Convertimos ambos valores a Decimal de forma segura para evitar fallos de precisión float
    ventas_mes_dec = Decimal(str(ventas_mes))
    gastos_mes_dec = Decimal(str(gastos_mes))

    # Ganancia Real (Ventas del mes menos gastos reales del mes)
    # max(0, ...) asegura que si en un mes muy malo los gastos superan las ventas, no muestre ganancias en negativo
    ganancia_mes = max(Decimal('0.00'), ventas_mes_dec - gastos_mes_dec)
    # =========================================================================

    # 4. Listado de últimos pedidos para la tabla
    pedidos = pedidos_usuario.order_by('-fecha')[:20]

    context = {
        'ventas_hoy': ventas_hoy,
        'ventas_mes': ventas_mes,  
        'ventas_total': ventas_total,
        'ticket_promedio': ticket_promedio,  
        'ganancia_mes': ganancia_mes,  # 🚀 Tu ganancia real calculada con datos del modelo Gasto
        'pedidos': pedidos,
    }
    return render(request, 'ganancias.html', context)

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