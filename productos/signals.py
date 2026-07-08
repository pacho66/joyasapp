from django.db.models.signals import post_save
from django.contrib.auth.models import User
from django.dispatch import receiver
from .models import Perfil, Factura  # 1. Asegúrate de importar tu modelo de Factura o Pedido
from .crm_services import registrar_venta_en_crm  # 2. Aquí va la importación del servicio que creamos

# ⚙️ SEÑAL 1: Creación automática del Perfil SaaS (Tu código actual intacto)
@receiver(post_save, sender=User)
def crear_perfil(sender, instance, created, **kwargs):
    if created:
        Perfil.objects.create(
            user=instance,
            nombre_tienda='PG joyas gonzález',
            plan='gratis'
        )

# 🤖 SEÑAL 2: Disparador Automático del CRM Post-Venta (Nuevo)
@receiver(post_save, sender=Factura) # Escucha cuando se guarda una factura
def procesar_venta_crm(sender, instance, created, **kwargs):
    """
    Cada vez que se cree una nueva factura en el sistema, extrae los datos 
    del cliente y los envía al acumulador inteligente del CRM.
    """
    if created: # Solo actúa cuando la factura se crea por primera vez
        
        # Estructuramos los datos básicos del cliente desde la factura
        datos_cliente = {
            'telefono': instance.cliente_telefono,  # Ajusta según cómo se llamen tus campos en Factura
            'nombre_completo': instance.cliente_nombre,
            'email': getattr(instance, 'cliente_email', None),
            'ciudad': getattr(instance, 'cliente_ciudad', None),
            'origen': getattr(instance, 'origen_venta', 'Tienda'),
        }
        
        # Estructuramos los datos financieros y de producto de la venta
        datos_factura = {
            'total_factura': instance.total,  # Ajusta según tus campos (ej: total, valor)
            'saldo_pendiente': getattr(instance, 'saldo_pendiente', 0.00),
            'categoria_producto': getattr(instance, 'categoria_principal', 'General'),
            'resumen_variante': getattr(instance, 'variante_resumen', 'Única'),
            'numero_factura': f"FAC-{instance.id}"
        }
        
        # Invocamos el servicio pasando el usuario dueño de la tienda (tenant)
        registrar_venta_en_crm(
            usuario_tienda=instance.usuario, # El ForeignKey(User) que amarra la factura a la joyería
            datos_cliente=datos_cliente,
            datos_factura=datos_factura
        )
