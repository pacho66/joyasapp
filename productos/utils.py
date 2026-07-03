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

def generar_pdf_pedido(pedido, perfil, usuario_backup=None):
    """
    1. ESTA FUNCIÓN SE ENCARGA SÓLO DE LEER TU 'pedido_pdf.html' 
       Y TRANSFORMARLO EN UN ARCHIVO PDF (BINARIO).
    """
    items = pedido.items.select_related('producto', 'variante').all()
    
    # Datos de la joyería (Escudo anti error 500)
    empresa_nombre = getattr(perfil, 'nombre_tienda', 'Mi Joyería') or "Mi Joyería"
    empresa_nit = getattr(perfil, 'nit', '') or ""
    empresa_telefono = getattr(perfil, 'whatsapp', '') or ""
    empresa_direccion = getattr(perfil, 'ciudad', '') or "" 
    email_user = usuario_backup.email if usuario_backup else 'correo@empresa.com'
    empresa_email = getattr(perfil, 'email_empresa', None) or email_user or 'correo@empresa.com'
    
    color_primario = getattr(perfil, 'color_primario', '#111111') or '#111111'
    empresa_logo = getattr(perfil, 'logo_url', None) 
    
    # Cálculos matemáticos de los totales
    subtotal = Decimal('0')
    iva_total = Decimal('0')
    descuento_total = Decimal('0')
    retefuente_total = Decimal('0')
    
    for item in items:
        subtotal += Decimal(str(item.subtotal or 0))
        iva_total += Decimal(str(item.iva or 0))
        descuento_total += Decimal(str(item.descuento_total or item.descuento or 0))
        retefuente_total += Decimal(str(item.retefuente or 0))
        
    envio = Decimal(str(getattr(pedido, 'costo_envio', 0) or 0))
    total_final = Decimal(str(getattr(pedido, 'total', 0) or 0))
    
    fecha_str = pedido.fecha_creacion.strftime('%Y-%m-%d') if pedido.fecha_creacion else ''

    # Aquí armamos el diccionario con los mismos nombres que usas en tu HTML
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
        'cliente_nombre': getattr(pedido, 'cliente_nombre', '') or '',
        'cliente_nit': getattr(pedido, 'cliente_nit', '') or '',
        'cliente_direccion': getattr(pedido, 'cliente_direccion', '') or '',
        'cliente_ciudad': getattr(pedido, 'cliente_ciudad', '') or '',
        'cliente_telefono': getattr(pedido, 'cliente_telefono', '') or '',
        'subtotal': subtotal,
        'descuento_total': descuento_total,
        'iva_total': iva_total,
        'retefuente_total': retefuente_total,
        'envio': envio,
        'total_final': total_final,
    }

    # Carga tu archivo HTML, le inyecta los datos y xhtml2pdf hace la magia
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


@login_required
def generar_factura(request, pedido_id):
    """
    2. ESTA ES LA VISTA WEB. 
       Llama a la función de arriba para obtener el PDF y se lo muestra al usuario.
    """
    from .models import Pedido
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
    from .models import Pedido  
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


