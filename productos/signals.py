from django.db.models.signals import pre_save, post_save, pre_delete
from django.contrib.auth.models import User
from django.dispatch import receiver
from django.utils import timezone
from django.db import transaction
from .models import Perfil, Cliente, Pedido  # Agrupados en una sola línea limpia
from .crm_services import registrar_venta_en_crm 
from .siigo_services import enviar_factura_a_siigo 

# =======================================================================
# ⚙️ SEÑAL 1: Creación automática del Perfil SaaS (Existente)
# =======================================================================
@receiver(post_save, sender=User)
def crear_perfil(sender, instance, created, **kwargs):
    if created:
        Perfil.objects.create(
            user=instance,
            nombre_tienda='PG joyas gonzález',
            plan='gratis'
        )

# =======================================================================
# 🛡️ SEÑAL 2: Capturar estado del pedido antes de guardar (Nueva)
# =======================================================================
@receiver(pre_save, sender=Pedido)
def capturar_estado_anterior(sender, instance, **kwargs):
    """Guarda en memoria el estado viejo del pedido para saber si cambió"""
    if instance.pk:
        try:
            pedido_db = Pedido.objects.get(pk=instance.pk)
            instance._estado_anterior = pedido_db.estado
        except Pedido.DoesNotExist:
            instance._estado_anterior = None
    else:
        instance._estado_anterior = None

# =======================================================================
# 📦 SEÑAL 3: Control de Inventario y Facturación en Siigo (Modificada)
# =======================================================================
@receiver(post_save, sender=Pedido)
def procesar_cambio_stock_y_facturacion(sender, instance, created, **kwargs):
    """Descuenta stock y genera factura en Siigo al confirmarse el pago"""
    estado_anterior = getattr(instance, '_estado_anterior', None)
    nuevo_estado = instance.estado

    # CASO A: El pedido pasa a estar pagado/confirmado
    if nuevo_estado == 'pagado' and estado_anterior != 'pagado':
        with transaction.atomic():
            for item in instance.detalles.all(): 
                producto = item.producto
                variante = getattr(item, 'variante', None) 
                
                if variante:
                    variante.stock = max(0, variante.stock - item.cantidad)
                    variante.save()
                else:
                    producto.stock = max(0, producto.stock - item.cantidad)
                    producto.save()

        # Disparar la factura en Siigo (fuera de la transacción de stock por seguridad de red)
        try:
            enviar_factura_a_siigo(instance)
        except Exception as e:
            # Si Siigo falla, el stock ya se descontó correctamente en la BD, 
            # pero puedes registrar el error en los logs de Render para revisarlo.
            print(f"⚠️ Error al sincronizar la factura #{instance.id} con Siigo: {e}")

    # CASO B: El pedido ya estaba pagado y lo revirtieron (a cancelado o pendiente)
    elif estado_anterior == 'pagado' and nuevo_estado != 'pagado':
        with transaction.atomic():
            for item in instance.detalles.all():
                producto = item.producto
                variante = getattr(item, 'variante', None)
                
                if variante:
                    variante.stock += item.cantidad
                    variante.save()
                else:
                    producto.stock += item.cantidad
                    producto.save()
# =======================================================================
# 🤖 SEÑAL 4: Disparador Automático del CRM Post-Venta (Existente)
# =======================================================================
@receiver(post_save, sender=Pedido)
def processar_venta_crm(sender, instance, created, **kwargs):
    """Envía los datos financieros y del cliente al CRM al crearse el pedido"""
    if created: 
        datos_cliente = {
            'telefono': getattr(instance, 'cliente_telefono', ''), 
            'nombre_completo': getattr(instance, 'cliente_nombre', 'Cliente General'),
            'email': getattr(instance, 'cliente_email', None),
            'ciudad': getattr(instance, 'cliente_ciudad', None),
            'origen': getattr(instance, 'origen_venta', 'Tienda'),
        }
        
        datos_factura = {
            'total_factura': getattr(instance, 'total', 0.00), 
            'saldo_pendiente': getattr(instance, 'saldo_pendiente', 0.00),
            'categoria_producto': getattr(instance, 'categoria_principal', 'General'),
            'resumen_variante': getattr(instance, 'variante_resumen', 'Única'),
            'numero_factura': f"PED-{instance.id}"
        }
        
        registrar_venta_en_crm(
            usuario_tienda=instance.usuario, 
            datos_cliente=datos_cliente,
            datos_factura=datos_factura
        )

# =======================================================================
# 🗑️ SENEAL 5: Devolución de stock por eliminación física (Nueva)
# =======================================================================
@receiver(pre_delete, sender=Pedido)
def devolver_stock_por_eliminacion(sender, instance, **kwargs):
    """Si borran el pedido completo de la base de datos, rescata el stock primero"""
    if instance.estado == 'pagado':
        with transaction.atomic():
            for item in instance.detalles.all():
                producto = item.producto
                variante = getattr(item, 'variante', None)
                
                if variante:
                    variante.stock += item.cantidad
                    variante.save()
                else:
                    producto.stock += item.cantidad
                    producto.save()

        
