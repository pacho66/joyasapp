from urllib.parse import quote

def generar_link_whatsapp(request, pedido):

    mensaje = f"Pedido #{pedido.numero_orden} - Total ${pedido.total}"

    mensaje_url = quote(mensaje)

    telefono = pedido.cliente_telefono

    return f"https://wa.me/57{telefono}?text={mensaje_url}"


