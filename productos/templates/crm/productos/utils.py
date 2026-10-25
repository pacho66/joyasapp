import os
from io import BytesIO
from decimal import Decimal
from urllib.parse import quote
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from django.http import HttpResponse
from django.core.mail import EmailMessage
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.template.loader import get_template
from xhtml2pdf import pisa
from .models import (
    Pedido,
)

def generar_pdf_pedido(pedido, perfil, usuario_backup=None):
    """
    Versión blindada con soporte de campos nulos (Fecha creación: None).
    """
    try:
        items = pedido.items.select_related('producto', 'variante').all()
        
        # Datos de la joyería seguros
        empresa_nombre = getattr(perfil, 'nombre_tienda', 'Mi Joyería') or "Mi Joyería"
        empresa_nit = getattr(perfil, 'nit', '') or ""
        empresa_telefono = getattr(perfil, 'whatsapp', '') or ""
        empresa_direccion = getattr(perfil, 'ciudad', '') or "" 
        email_user = usuario_backup.email if usuario_backup else 'correo@empresa.com'
        empresa_email = getattr(perfil, 'email_empresa', None) or email_user or 'correo@empresa.com'
        
        color_primario = getattr(perfil, 'color_primario', '#111111') or '#111111'
        empresa_logo = getattr(perfil, 'logo_url', None) 
        
        # Totales seguros usando conversión limpia
        subtotal = Decimal('0')
        iva_total = Decimal('0')
        descuento_total = Decimal('0')
        retefuente_total = Decimal('0')
        
        for item in items:
            subtotal += Decimal(str(getattr(item, 'subtotal', 0) or 0))
            iva_total += Decimal(str(getattr(item, 'iva', 0) or 0))
            descuento_total += Decimal(str(getattr(item, 'descuento_total', getattr(item, 'descuento', 0)) or 0))
            retefuente_total += Decimal(str(getattr(item, 'retefuente', 0) or 0))
            
        envio = Decimal(str(getattr(pedido, 'costo_envio', 0) or 0))
        total_final = Decimal(str(getattr(pedido, 'total', 0) or 0))
        
        # 🛡️ Control estricto del "Fecha creación: None" detectado en tus logs de Render
        if getattr(pedido, 'fecha_creacion', None):
            try:
                fecha_str = pedido.fecha_creacion.strftime('%Y-%m-%d')
            except Exception:
                fecha_str = str(pedido.fecha_creacion)[:10]
        elif getattr(pedido, 'fecha', None):
            try:
                fecha_str = pedido.fecha.strftime('%Y-%m-%d')
            except Exception:
                fecha_str = str(pedido.fecha)[:10]
        else:
            from django.utils import timezone
            fecha_str = timezone.now().strftime('%Y-%m-%d')

        contexto = {
            'pedido': pedido,
            'items': items,
            'fecha_str': fecha_str,
            'empresa_nombre': empresa_nombre,
            'empresa_nit': empresa_nit,
            'empresa_direccion': empresa_direccion,
            'empresa_telefono': empresa_telefono,
            'empresa_email': empresa_email,
            'empresa_logo': empresa_logo,
            'color_primario': color_primario,
            
            # Mapeo de clientes seguro
            'cliente_nombre': getattr(pedido, 'cliente_nombre', getattr(pedido, 'nombre_cliente', 'Consumidor Final')),
            'cliente_nit': getattr(pedido, 'cliente_nit', getattr(pedido, 'nit_cliente', '')),
            'cliente_direccion': getattr(pedido, 'cliente_direccion', getattr(pedido, 'direccion_entrega', '')),
            'cliente_ciudad': getattr(pedido, 'cliente_ciudad', getattr(pedido, 'ciudad_destino', '')),
            'cliente_telefono': getattr(pedido, 'cliente_telefono', getattr(pedido, 'telefono_contacto', '')),
            
            'subtotal': subtotal,
            'descuento_total': descuento_total,
            'iva_total': iva_total,
            'retefuente_total': retefuente_total,
            'envio': envio,
            'total_final': total_final,
        }

        template = get_template('pedido_pdf.html')  
        html = template.render(contexto)
        result = BytesIO()
        
        pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)
        
        if not pdf.err:
            pdf_binario = result.getvalue()
            result.close()
            return pdf_binario, empresa_email, empresa_nombre
            
        result.close()
        return None, empresa_email, empresa_nombre

    except Exception as e:
        print(f"💥 Error controlado en generar_pdf_pedido: {str(e)}")
        return None, 'correo@empresa.com', 'Mi Joyería'

@login_required
def generar_factura(request, pedido_id):
    """
    2. ESTA ES LA VISTA WEB. 
       Llama a la función de arriba para obtener el PDF y se lo muestra al usuario.
    """
    pedido = get_object_or_404(Pedido, id=pedido_id, usuario=request.user)
    perfil = getattr(request.user, 'perfil', None)
    
    # Llamamos a la función que procesa el HTML
    pdf, correo_empresa, nombre_tienda = generar_pdf_pedido(pedido, perfil, request.user)

    if not pdf:
        return HttpResponse("Error generando el PDF de la factura.", status=500)

    # Envío automático al cliente por correo si tiene uno registrado
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

    # Devuelve el PDF para que se abra en el navegador
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="factura_{pedido.numero_orden}.pdf"'
    return response


def generar_link_whatsapp(request, pedido):
    mensaje = f"Pedido #{pedido.numero_orden} - Total ${pedido.total}"
    mensaje_url = quote(mensaje)
    telefono = pedido.cliente_telefono
    return f"https://wa.me/57{telefono}?text={mensaje_url}"


def generar_numero_orden(usuario=None): 
    if usuario:
        queryset = Pedido.objects.filter(usuario=usuario)
    else:
        queryset = Pedido.objects.all()

    ultimo = queryset.only('numero_orden', 'id').order_by('-id').first()
    numero = 1

    if ultimo and ultimo.numero_orden:
        try:
            partes = ultimo.numero_orden.strip().split('-')
            consecutivo = partes[-1]
            if consecutivo.isdigit():
                numero = int(consecutivo) + 1
            else:
                raise ValueError
        except Exception:
            numero = queryset.count() + 1

    if numero < 1:
        numero = 1

    return f"FAC-{numero:06d}"

def calcular_totales_con_reglas_fiscales(pedido, perfil):
    """
    Lee la configuración actual guardada en el Perfil de la joyería 
    y aplica los cálculos matemáticos reales de IVA, descuentos, retefuente y envío.
    """
    if not perfil:
        return pedido

    # 🛒 1. CALCULAR SUBTOTAL DE LOS ÍTEMS
    subtotal = Decimal('0.00')
    for item in pedido.items.all():
        # Aseguramos que cada ítem tenga el precio real de su producto
        precio = Decimal(str(getattr(item.producto, 'precio', 0.00) or 0.00))
        cantidad = int(getattr(item, 'cantidad', 1) or 1)
        
        # Guardamos el subtotal del ítem en la BD
        item.subtotal = precio * cantidad
        item.save()
        subtotal += item.subtotal

    # 🎁 2. REGLA DE DESCUENTOS DE CAMPAÑA
    descuento = Decimal('0.00')
    if getattr(perfil, 'aplicar_descuentos', False) and getattr(perfil, 'porcentaje_descuento_promo', 0) > 0:
        porcentaje_desc = Decimal(str(perfil.porcentaje_descuento_promo)) / Decimal('100.00')
        descuento = subtotal * porcentaje_desc

    base_gravable = subtotal - descuento

    # ⚖️ 3. REGLA DE IVA
    iva = Decimal('0.00')
    if getattr(perfil, 'responsable_iva', False) and getattr(perfil, 'porcentaje_iva', 0) > 0:
        porcentaje_iva = Decimal(str(perfil.porcentaje_iva)) / Decimal('100.00')
        iva = base_gravable * porcentaje_iva

    # 🛑 4. REGLA DE RETEFUENTE
    retefuente = Decimal('0.00')
    if getattr(perfil, 'aplicar_retefuente', False) and getattr(perfil, 'porcentaje_retefuente', 0) > 0:
        porcentaje_rete = Decimal(str(perfil.porcentaje_retefuente)) / Decimal('100.00')
        retefuente = base_gravable * porcentaje_rete

    # 🚚 5. REGLA DE ENVÍOS
    envio = Decimal('0.00')
    if getattr(perfil, 'cobrar_envio', False):
        envio = Decimal(str(getattr(perfil, 'costo_envio_estandar', 0.00) or 0.00))
        # Validar si supera el tope de envío gratis
        tope_gratis = getattr(perfil, 'envio_gratis_desde', None)
        if tope_gratis and base_gravable >= Decimal(str(tope_gratis)):
            envio = Decimal('0.00')

    # 💾 6. GUARDAR MATEMÁTICAS EN EL PEDIDO
    pedido.descuento_total = descuento
    pedido.iva = iva
    pedido.retefuente = retefuente
    pedido.costo_envio = envio
    
    # Total Final = (Base + IVA + Envío) - Retefuente
    pedido.total = (base_gravable + iva + envio) - retefuente
    
    if hasattr(pedido, 'total_limpio'):
        pedido.total_limpio = pedido.total

    pedido.save()
    return pedido
