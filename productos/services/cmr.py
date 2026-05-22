from django.utils.timezone import now
from datetime import timedelta
from urllib.parse import quote

from .models import Cliente


def clientes_dormidos():
    hace_30 = now() - timedelta(days=30)

    return Cliente.objects.filter(
        ultima_compra__lt=hace_30,
        acepta_marketing=True
    )


def mensaje_recompra(cliente):
    return f"""Hola {cliente.nombre} 😊

Hace poco compraste con nosotros 💎  
Tenemos nuevas joyas que te pueden encantar.

🎁 Tienes beneficio especial por ser cliente

¿Quieres ver lo nuevo?"""


def generar_link(cliente):
    mensaje = mensaje_recompra(cliente)
    return f"https://wa.me/57{cliente.telefono}?text={quote(mensaje)}"