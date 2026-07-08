from django.utils import timezone
from .models import Cliente  

def registrar_venta_en_crm(usuario_tienda, datos_cliente, datos_factura):
    """
    🤖 DISPARADOR POST-VENTA AUTOMÁTICO
    Sincroniza los acumulados, calcula el comportamiento de compra
    y actualiza los rangos de fidelización del cliente en tiempo real.
    """
    # 1. Buscar o registrar al cliente usando el teléfono como llave única de la tienda
    cliente, creado = Cliente.objects.get_or_create(
        usuario=usuario_tienda,
        telefono=datos_cliente['telefono'],
        defaults={
            'nombre': datos_cliente['nombre_completo'],
            'email': datos_cliente.get('email'),
            'ciudad': datos_cliente.get('ciudad'),
            'origen': datos_cliente.get('origen', 'Tienda'),
            'fecha_cumpleanos': datos_cliente.get('fecha_cumpleanos'),
        }
    )
    
    # 2. Inyectar las métricas financieras acumuladas
    cliente.total_compras += datos_factura['total_factura']
    cliente.saldo_pendiente += datos_factura.get('saldo_pendiente', 0)
    cliente.numero_pedidos += 1
    cliente.ultima_compra = timezone.now()
    
    # 3. Mapear el rastro de consumo para segmentación de campañas
    cliente.ultima_categoria = datos_factura['categoria_producto']
    cliente.ultima_variante = datos_factura['resumen_variante']
    cliente.ultima_factura = datos_factura['numero_factura']
    
    # 4. Ejecutar el algoritmo de actualización de estados y niveles automáticos
    cliente.actualizar_ciclo_y_rangos()
    
    # 5. Consolidar en la base de datos
    cliente.save()
    return cliente