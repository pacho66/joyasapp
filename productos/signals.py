from django.db.models.signals import post_save
from django.contrib.auth.models import User
from django.dispatch import receiver
from django.utils import timezone
from .models import Perfil, Cliente  # Importamos los modelos de tu app productos
from .crm_services import registrar_venta_en_crm  
# 🔔 Importamos Pedido desde la app donde está guardado (usando la ruta correcta)
from .models import Pedido 

# ⚙️ SEÑAL 1: Creación automática del Perfil SaaS
@receiver(post_save, sender=User)
def crear_perfil(sender, instance, created, **kwargs):
    if created:
        Perfil.objects.create(
            user=instance,
            nombre_tienda='PG joyas gonzález',
            plan='gratis'
        )

# 🤖 SEÑAL 2: Disparador Automático del CRM Post-Venta (¡CONECTADO A PEDIDO!)
@receiver(post_save, sender=Pedido) # 🎯 Escucha cuando se crea un Pedido
def processar_venta_crm(sender, instance, created, **kwargs):
    """
    Cada vez que se cree un nuevo pedido en el sistema, extrae los datos 
    del cliente y los envía al acumulador inteligente del CRM.
    """
    if created: # Solo actúa cuando el pedido se crea por primera vez
        
        # Estructuramos los datos básicos del cliente desde el pedido
        datos_cliente = {
            'telefono': getattr(instance, 'cliente_telefono', ''), # Revisa si en Pedido se llama así
            'nombre_completo': getattr(instance, 'cliente_nombre', 'Cliente General'),
            'email': getattr(instance, 'cliente_email', None),
            'ciudad': getattr(instance, 'cliente_ciudad', None),
            'origen': getattr(instance, 'origen_venta', 'Tienda'),
        }
        
        # Estructuramos los datos financieros y de producto de la venta
        datos_factura = {
            'total_factura': getattr(instance, 'total', 0.00), # Cambia 'total' si tu campo se llama 'valor_total' o 'precio'
            'saldo_pendiente': getattr(instance, 'saldo_pendiente', 0.00),
            'categoria_producto': getattr(instance, 'categoria_principal', 'General'),
            'resumen_variante': getattr(instance, 'variante_resumen', 'Única'),
            'numero_factura': f"PED-{instance.id}"
        }
        
        # Invocamos el servicio pasando el usuario dueño de la tienda (tenant)
        registrar_venta_en_crm(
            usuario_tienda=instance.usuario, # El ForeignKey(User) que amarra el pedido a la joyería
            datos_cliente=datos_cliente,
            datos_factura=datos_factura
        )
        
