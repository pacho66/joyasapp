import requests
from django.conf import settings

def obtener_token_siigo():
    """Obtiene el token de acceso temporal desde la API de autenticación de Siigo."""
    url = "https://api.siigo.com/auth"
    payload = {
        "username": settings.SIIGO_USERNAME,
        "access_key": settings.SIIGO_ACCESS_KEY
    }
    headers = {"Content-Type": "application/json"}
    
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        return response.json().get("access_token")
    raise Exception(f"Error de autenticación en Siigo: {response.text}")

def enviar_factura_a_siigo(pedido):
    """Envía la información de un pedido confirmado para generar la factura electrónica en Siigo."""
    token = obtener_token_siigo()
    url = "https://api.siigo.com/v1/invoices"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "Partner-Id": getattr(settings, "SIIGO_PARTNER_ID", "")
    }
    
    # Construir los ítems del pedido a partir de tu base de datos
    items = []
    for item in pedido.items.all():
        items.append({
            "code": item.producto.referencia,
            "description": item.producto.nombre,
            "quantity": int(item.cantidad),
            "price": float(item.precio_unitario),
            "taxes": [{"id": 1}]  # Ajusta según el ID del impuesto configurado en tu Siigo
        })

    payload = {
        "document": {"id": int(settings.SIIGO_DOCUMENT_TYPE_ID)},  # ID del tipo de factura electrónica en Siigo
        "date": pedido.fecha_creacion.strftime("%Y-%m-%d"),
        "customer": {
            "identification": getattr(pedido.usuario, "documento", "222222222222"),  # NIT o cédula del cliente
            "branch_office": 0,
            "person_type": "Person",
            "name": [pedido.usuario.first_name, pedido.usuario.last_name],
            "address": {
                "address": getattr(pedido, "direccion_envio", "Calle principal"),
                "city": {"country_code": "Co", "state_code": "05", "city_code": "05266"}  # Ejemplo: Sabaneta / Antioquia
            }
        },
        "payments": [
            {
                "id": int(settings.SIIGO_PAYMENT_METHOD_ID),  # ID del medio de pago en Siigo (ej. Tarjeta, Efectivo)
                "value": float(pedido.total),
                "due_date": pedido.fecha_creacion.strftime("%Y-%m-%d")
            }
        ],
        "items": items,
        "observations": f"Pedido #{pedido.id} realizado en JoyasApp"
    }

    response = requests.post(url, json=payload, headers=headers)
    
    if response.status_code in [200, 201]:
        data = response.json()
        # Opcional: guardar el identificador de la factura de Siigo en tu modelo Pedido
        pedido.siigo_invoice_id = data.get("id")
        pedido.save()
        return data
    else:
        raise Exception(f"Error al generar factura en Siigo: {response.text}")