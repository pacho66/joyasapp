import urllib.parse
import traceback

from django.shortcuts import get_object_or_404, redirect
from django.http import HttpResponse
from django.template.loader import get_template
from django.utils import timezone

from xhtml2pdf import pisa

from ..models import Pedido, Perfil
from .utilidades import link_callback

# =========================================================================
# 📄 BLOQUE 6: PORTAL PÚBLICO DEL CLIENTE (FACTURA Y PDF)
# =========================================================================

def factura_publica(request, token):
    """
    Vista que renderiza el recibo público para el cliente mediante el token UUID.
    """
    pedido = get_object_or_404(Pedido, token_publico=token)
    items = pedido.items.select_related('producto', 'variante').all()
    
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
        try: empresa_logo = perfil.logo.url
        except Exception: pass

    color_primario = perfil.color_primario if perfil else "#000000"
    color_secundario = perfil.color_secundario if perfil else "#333333"

    # ⚡ LLAMADA AL MOTOR FISCAL ÚNICO
    totales = calcular_totales_pedido(pedido, perfil, items)

    # Motor de estados
    if pedido.estado == "pendiente":
        estado = "pendiente"
    elif pedido.saldo_pendiente is not None and pedido.saldo_pendiente <= 0:
        estado = "pagado"
    elif pedido.fecha_limite and pedido.fecha_limite < timezone.now().date():
        estado = "vencido"
    else:
        estado = pedido.estado

    # Integración de WhatsApp usando el total final calculado
    mensaje = f"Hola! Adjunto el comprobante de pago del pedido #{pedido.numero_orden} por valor de ${totales['total_final']:,.0f}".replace(",", ".")
    whatsapp_num = empresa_telefono if empresa_telefono != "-" else ""
    whatsapp_url = f"https://wa.me/{whatsapp_num}?text={urllib.parse.quote(mensaje)}"
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(whatsapp_url)}"

    if getattr(pedido, 'fecha', None):
        fecha_str = pedido.fecha.strftime('%Y-%m-%d')
    elif getattr(pedido, 'fecha_creacion', None):
        fecha_str = pedido.fecha_creacion.strftime('%Y-%m-%d')
    else:
        fecha_str = timezone.now().strftime('%Y-%m-%d')

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
        'qr_pago_url': qr_url,  
        'whatsapp_url': whatsapp_url,
        'estado': estado,
    }
    # Inyectamos los totales calculados directo al contexto
    context.update(totales)
    
    return render(request, 'factura_publica.html', context)

def confirmar_pago_publico(request, token):
    """ El cliente avisa de forma anónima que ya transfirió """
    pedido = get_object_or_404(Pedido, token_publico=token)
    pedido.estado = "confirmacion_pago"
    pedido.save()
    return redirect('factura_publica', token=token)

# ────────────────────────────────────────────────────────────────
# 📄 VISTA PRINCIPAL: GENERACIÓN DE PDF
# ────────────────────────────────────────────────────────────────
def pedido_pdf(request, pedido_id):
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
    color_primario = perfil.color_primario if perfil else "#111111"
    color_secundario = perfil.color_secundario if perfil else "#333333"

    # ⚡ LLAMADA AL MOTOR FISCAL ÚNICO
    totales = calcular_totales_pedido(pedido, perfil, items)

    # 🕵️‍♂️ Buscamos de forma segura el campo de fecha real que tiene tu modelo Pedido
    fecha_obj = getattr(
        pedido, 'created_at', 
        getattr(pedido, 'fecha', 
        getattr(pedido, 'fecha_registro', None))
    )
    
    # Si encuentra el campo, lo formatea. Si no, pone "S/F" de forma segura
    if fecha_obj:
        try:
            fecha_str = fecha_obj.strftime('%Y-%m-%d')
        except AttributeError:
            fecha_str = str(fecha_obj)
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
        'color_primario': color_primario,
        'color_secundario': color_secundario,
        'fecha_str': fecha_str,
    }
    # Inyectamos exactamente los mismos totales
    context.update(totales)

    try:
        template = get_template('pedido_pdf.html')
        html = template.render(context)
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="pedido_{pedido.numero_orden}.pdf"'
        
        # 🎯 CAMBIO CLAVE: Agregamos el parámetro link_callback aquí abajo
        pdf = pisa.CreatePDF(html, dest=response, link_callback=link_callback)
        
        if pdf.err:
            return HttpResponse(f"Error generando PDF: {pdf.err}", status=500)
        return response
    except Exception as e:
        # Añadido un print para depurar rápido en la consola de Render si algo más falla
        print(f"💥 ERROR EN GENERACIÓN PDF: {str(e)}")
        traceback.print_exc()
        return HttpResponse(f"Error interno: {str(e)}", status=500)
