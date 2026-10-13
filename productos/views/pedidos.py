import traceback
from decimal import Decimal
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from ..models import Pedido
from ..forms import ConfiguracionNegocioForm
from .utilidades import (
    obtener_perfil_pedido,
    obtener_configuracion_negocio,
    generar_link_whatsapp)
from productos.services.motor_fiscal import calcular_totales_pedido, MotorFiscal

# =========================================================================
# 🧾 BLOQUE 9: ADMINISTRACIÓN DE PEDIDOS, CONTROL DE PAGOS Y FACTURACIÓN
# =========================================================================

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

@login_required
def lista_pedidos(request):
    # 🎯 CORREGIDO: Ajustamos el ordenamiento por el campo real de auditoría (ej. created_at o fecha_registro)
    # Cambia '-created_at' por tu campo exacto de fecha si es diferente.
    campo_orden = 'created_at' if hasattr(Pedido, 'created_at') else ('fecha_registro' if hasattr(Pedido, 'fecha_registro') else 'id')
    pedidos = Pedido.objects.filter(usuario=request.user).order_by(f'-{campo_orden}')
    
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

class PedidoWrapper:
    def __init__(self, pedido, totales):
        self._pedido = pedido
        self.subtotal_antes_de_descuento = totales.get('subtotal', Decimal('0.00'))
        self.subtotal_neto = totales.get('subtotal_neto', totales.get('subtotal', Decimal('0.00')))
        self.descuento_total = totales.get('descuento_total', Decimal('0.00'))
        self.porcentaje_descuento = totales.get('porcentaje_descuento', Decimal('0.00'))
        self.aplica_iva = bool(totales.get('iva_total', 0) > 0)
        self.iva = totales.get('iva_total', Decimal('0.00'))
        self.es_retenedor = bool(totales.get('retefuente_total', 0) > 0)
        self.retefuente = totales.get('retefuente_total', Decimal('0.00'))
        self.costo_envio = totales.get('envio', Decimal('0.00'))
        self.total = totales.get('total_final', Decimal('0.00'))

    def __getattr__(self, name):
        return getattr(self._pedido, name)

@login_required
def detalle_pedido(request, pedido_id):
    try:
        pedido = get_object_or_404(Pedido, id=pedido_id, usuario=request.user)

        if pedido.cliente:
            cliente_actual = pedido.cliente
            total_historico = Pedido.objects.filter(
                cliente=cliente_actual, usuario=request.user, estado='pagado'
            ).aggregate(Sum('total'))['total__sum'] or Decimal('0.00')

            total_cartera = Pedido.objects.filter(
                cliente=cliente_actual, usuario=request.user, saldo_pendiente__gt=0
            ).aggregate(Sum('saldo_pendiente'))['saldo_pendiente__sum'] or Decimal('0.00')

            cliente_actual.total_compras = total_historico
            cliente_actual.saldo_pendiente = total_cartera
            cliente_actual.save()

        items = pedido.items.select_related('producto', 'variante').all()

        perfil = obtener_perfil_pedido(pedido)
        config = obtener_configuracion_negocio(pedido)
        
        empresa_nombre = perfil.nombre_tienda if perfil else "Mi Joyería"
        empresa_nit = perfil.nit if perfil else "Sin NIT"
        empresa_telefono = perfil.whatsapp if perfil else "Sin teléfono"
        empresa_direccion = perfil.direccion if perfil else "Sin dirección"
        logo_path = perfil.logo.url if perfil and getattr(perfil, 'logo', None) else None

        try:
            totales = calcular_totales_pedido(pedido, config, items)
        except Exception:
            subtotal_base = sum(
                Decimal(str(getattr(item, 'subtotal', 0) or (float(item.precio or 0) * float(getattr(item, 'cantidad', 1) or 1))))
                for item in items
            )
            envio_val = Decimal(str(getattr(pedido, 'costo_envio', 0) or 0))
            desc_val = Decimal(str(getattr(pedido, 'descuento_total', 0) or 0))
            iva_val = Decimal(str(getattr(pedido, 'iva', 0) or 0))
            rete_val = Decimal(str(getattr(pedido, 'retefuente', 0) or 0))

            total_calculado = subtotal_base - desc_val + iva_val - rete_val + envio_val
            if total_calculado <= 0:
                total_calculado = subtotal_base + envio_val

            totales = {
                'subtotal': subtotal_base,
                'subtotal_neto': subtotal_base - desc_val,
                'iva_total': iva_val,
                'descuento_total': desc_val,
                'porcentaje_descuento': Decimal(str(getattr(pedido, 'porcentaje_descuento', 0) or 0)),
                'retefuente_total': rete_val,
                'envio': envio_val,
                'total_final': total_calculado
            }

        # Envolvemos el pedido con el wrapper seguro
        pedido_wrapp = PedidoWrapper(pedido, totales)

        link_pdf = request.build_absolute_uri(reverse('pedido_pdf', args=[pedido.id]))
        link_publico = request.build_absolute_uri(reverse('confirmar_pago_publico', args=[pedido.token_publico]))
        
        try:
            whatsapp_url = generar_link_whatsapp(request, pedido) if 'generar_link_whatsapp' in globals() else '#'
        except Exception:
            whatsapp_url = '#'

        hoy = timezone.now().date()
        estado_credito = None
        if getattr(pedido, 'tipo_pago', 'contado') == 'credito':
            if totales['total_final'] <= 0 or (pedido.saldo_pendiente is not None and pedido.saldo_pendiente <= 0):
                estado_credito = 'pagado'
            elif pedido.fecha_limite and pedido.fecha_limite < hoy:
                estado_credito = 'vencido'
            else:
                estado_credito = 'pendiente'

        abonos = pedido.abonos.all().order_by('-fecha') if hasattr(pedido, 'abonos') else []
        total_abonado = sum(a.monto for a in abonos) if abonos else 0

        context = {
            'pedido': pedido_wrapp,
            'items': items,
            'cliente': getattr(pedido, 'cliente', None),
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
            **totales  
        }
        
        return render(request, 'detalle_pedido.html', context)

    except Exception as e:
        print(f"💥 ERROR CRÍTICO EN DETALLE DE PEDIDO #{pedido_id}: {str(e)}")
        traceback.print_exc()
        return HttpResponse(f"Error al cargar el detalle del pedido: {str(e)}", status=500)
@login_required
@transaction.atomic
def confirmar_pago(request, pedido_id):
    """ El administrador aprueba el pago desde su panel privado """
    pedido = get_object_or_404(Pedido, id=pedido_id, usuario=request.user)
    
    # 🌟 Cambiamos el estado del pedido a pagado sin volver a restar stock
    if pedido.estado != "pagado":
        pedido.estado = "pagado"
        pedido.save()
        
    return render(request, 'pago_confirmado.html', {'pedido': pedido})

@login_required
def generar_factura(request, pedido_id):
    try:
        pedido = get_object_or_404(Pedido, id=pedido_id, usuario=request.user)
        items = pedido.items.select_related('producto', 'variante').all()

        perfil = getattr(request.user, 'perfil', None)
        nombre_tienda = getattr(perfil, 'nombre_tienda', 'Mi Joyería') or "Mi Joyería"
        nit_tienda = getattr(perfil, 'nit', '') or ""
        whatsapp_tienda = getattr(perfil, 'whatsapp', '') or ""
        ciudad_tienda = getattr(perfil, 'ciudad', '') or ""
        correo_empresa = getattr(perfil, 'email_empresa', None) or request.user.email or 'correo@empresa.com'

        try:
            totales = calcular_totales_pedido(pedido, perfil, items)
        except Exception:
            # 🛡️ Fallback inteligente: calcula desde precio y cantidad si el subtotal está en 0
            subtotal_base = sum(
                Decimal(str(getattr(item, 'subtotal', 0) or (float(item.precio or 0) * float(getattr(item, 'cantidad', 1) or 1))))
                for item in items
            )
            envio_val = Decimal(str(getattr(pedido, 'costo_envio', 0) or 0))
            desc_val = Decimal(str(getattr(pedido, 'descuento_total', 0) or 0))
            iva_val = Decimal(str(getattr(pedido, 'iva', 0) or 0))
            rete_val = Decimal(str(getattr(pedido, 'retefuente', 0) or 0))

            total_calculado = subtotal_base - desc_val + iva_val - rete_val + envio_val
            if total_calculado <= 0:
                total_calculado = subtotal_base + envio_val

            totales = {
                'subtotal': subtotal_base,
                'subtotal_neto': subtotal_base - desc_val,
                'iva_total': iva_val,
                'descuento_total': desc_val,
                'porcentaje_descuento': Decimal(str(getattr(pedido, 'porcentaje_descuento', 0) or 0)),
                'retefuente_total': rete_val,
                'envio': envio_val,
                'total_final': total_calculado
            }

            # 💾 Corrige el pedido en la base de datos si estaba en 0 para que no vuelva a fallar
            if not pedido.total or pedido.total <= 0:
                pedido.total = total_calculado
                pedido.save(update_fields=['total'])
        
        subtotal_general = totales['subtotal']
        iva_total = totales['iva_total']
        descuento_total = totales['descuento_total']
        porcentaje_desc = totales['porcentaje_descuento']
        retefuente_total = totales['retefuente_total']
        costo_envio_num = totales['envio']
        total_final = totales['total_final']

        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter)

        PAGE_WIDTH, PAGE_HEIGHT = letter
        LINE_HEIGHT = 18

        def draw_header():
            y = 760
            p.setFont("Helvetica-Bold", 16)
            p.drawString(50, y, nombre_tienda)

            p.setFont("Helvetica", 9)
            p.drawString(50, y - 15, f"NIT: {nit_tienda}")
            p.drawString(50, y - 28, f"Tel: {whatsapp_tienda}")
            p.drawString(50, y - 41, f"Email: {correo_empresa}")
            p.drawString(50, y - 54, f"{ciudad_tienda}")

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

        draw_header()
        y = draw_table_header(560)

        for item in items:
            if y < 100:
                draw_footer()
                y = nueva_pagina()

            nombre = item.producto.nombre[:28] if item.producto and item.producto.nombre else "Producto"

            gramos = getattr(item, 'total_gramos', None)
            if gramos and gramos > 0:
                cantidad = f"{gramos}g"
            else:
                cantidad = f"{int(item.cantidad or 1)} u"

            precio = Decimal(str(item.precio or 0))
            total_item = Decimal(str(item.total_final or item.subtotal or 0))

            p.setFont("Helvetica", 9)
            p.drawString(50, y, nombre)
            p.drawString(250, y, cantidad)
            p.drawRightString(390, y, f"${precio:,.0f}".replace(",", "."))
            p.drawRightString(540, y, f"${total_item:,.0f}".replace(",", "."))

            y -= LINE_HEIGHT

        y -= 20
        if y < 160:
            draw_footer()
            y = nueva_pagina()

        p.setFont("Helvetica", 10)
        p.drawString(340, y, "Subtotal:")
        p.drawRightString(540, y, f"${subtotal_general:,.0f}".replace(",", "."))

        if iva_total > 0:
            y -= 15
            p.drawString(340, y, "IVA:")
            p.drawRightString(540, y, f"${iva_total:,.0f}".replace(",", "."))

        if descuento_total > 0 or porcentaje_desc > 0:
            y -= 15
            p.drawString(340, y, f"Descuento ({porcentaje_desc}%):")
            p.drawRightString(540, y, f"-${descuento_total:,.0f}".replace(",", "."))

        if retefuente_total > 0:
            y -= 15
            p.drawString(340, y, "ReteFuente:")
            p.drawRightString(540, y, f"-${retefuente_total:,.0f}".replace(",", "."))

        y -= 15
        p.drawString(340, y, "Envío:")
        if costo_envio_num == 0:
            p.drawRightString(540, y, "GRATIS")
        else:
            p.drawRightString(540, y, f"${costo_envio_num:,.0f}".replace(",", "."))

        y -= 22
        p.setFont("Helvetica-Bold", 12)
        p.drawString(340, y, "TOTAL:")
        p.drawRightString(540, y, f"${total_final:,.0f}".replace(",", "."))

        draw_footer()
        p.save()
        pdf = buffer.getvalue()
        buffer.close()

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

    except Exception as e:
        print(f"💥 ERROR EN GENERAR FACTURA: {str(e)}")
        return HttpResponse(f"Error al generar la factura PDF: {str(e)}", status=500)