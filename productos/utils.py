from urllib.parse import quote

def generar_link_whatsapp(request, pedido):
    """
    Genera un enlace directo al WhatsApp del cliente con un resumen básico.
    """
    mensaje = f"Pedido #{pedido.numero_orden} - Total ${pedido.total}"
    mensaje_url = quote(mensaje)
    telefono = pedido.cliente_telefono
    
    # Mantiene el prefijo 57 para Colombia
    return f"https://wa.me/57{telefono}?text={mensaje_url}"


def generar_numero_orden(usuario=None):
    """
    Genera consecutivo de factura/orden:
    - Por usuario (SaaS independiente)
    - Global (WhatsApp / sin usuario)
    """
    # Importación interna para blindar el código contra errores circulares
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

