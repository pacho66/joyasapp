from decimal import Decimal
from productos.models import Pedido, PedidoItem
from productos.services.precios import calcular_precio_producto
from productos.services.envios import calcular_envio
from productos.models import Cliente

# =========================================
# 🔥 CREAR PEDIDO DESDE CARRITO
# =========================================
def crear_pedido_desde_carrito(items, datos, numero):

    total_pedido = Decimal('0')
    subtotal_general = Decimal('0')

    # =========================
    # 👥 CLIENTE
    # =========================
    cliente, creado = Cliente.objects.get_or_create(
        telefono=datos.get('telefono'),
        defaults={
            'nombre': datos.get('nombre'),
            'email': datos.get('email'),
            'ciudad': datos.get('ciudad'),
            'direccion': datos.get('direccion'),
        }
    )

    # =========================
    # 🔥 CREAR PEDIDO (UNA SOLA VEZ)
    # =========================
    pedido = Pedido.objects.create(
        numero_orden=numero,
        cliente=cliente,
        total=0,

        aplica_iva=datos.get('aplica_iva', False),
        es_retenedor=datos.get('es_retenedor', False),

        cliente_nombre=datos.get('nombre'),
        cliente_nit=datos.get('nit'),
        cliente_direccion=datos.get('direccion'),
        cliente_telefono=datos.get('telefono'),
        cliente_email=datos.get('email'),
        cliente_ciudad=datos.get('ciudad'),
    )

    # =========================
    # 🔥 ITEMS
    # =========================
    for item in items:

        precio, gramos = calcular_precio_producto(
            item.producto,
            item.cantidad
        )

        cantidad = Decimal(item.cantidad)
        precio = Decimal(precio)

        if gramos:
            subtotal = Decimal(gramos) * precio
        else:
            subtotal = cantidad * precio

        subtotal_general += subtotal

        # 🔥 DESCUENTO
        descuento = Decimal('0')
        base = subtotal - descuento

        # 🔥 IVA
        iva = Decimal('0')
        if pedido.aplica_iva:
            iva = base * Decimal('0.19')

        # 🔥 RETEFUENTE
        retefuente = Decimal('0')
        if pedido.es_retenedor:
            retefuente = subtotal * Decimal('0.025')

        total_item = base + iva - retefuente
        total_pedido += total_item

        # 🔥 GUARDAR ITEM (DENTRO DEL FOR)
        PedidoItem.objects.create(
            pedido=pedido,
            producto=item.producto,
            variante=item.variante,
            cantidad=item.cantidad,
            precio=precio,
            subtotal=subtotal,
            descuento=descuento,
            iva=iva,
            retefuente=retefuente,
            total_final=total_item
        )

    # =========================
    # 🚚 ENVÍO (FUERA DEL FOR)
    # =========================
    costo_envio = calcular_envio(
        datos.get('ciudad'),
        subtotal_general  # 🔥 CORRECTO
    )

    # =========================
    # 🔥 TOTAL FINAL
    # =========================
    pedido.costo_envio = costo_envio
    pedido.total = total_pedido + costo_envio
    pedido.save()

    # =========================
    # 👥 ACTUALIZAR CLIENTE
    # =========================
    cliente.total_compras += pedido.total
    cliente.numero_pedidos += 1

    # 🔥 CLASIFICACIÓN CORRECTA
    if cliente.total_compras > 3000000:
        cliente.tipo_cliente = 'mayorista'
    elif cliente.total_compras > 1000000:
        cliente.tipo_cliente = 'vip'

    cliente.save()

    return pedido
    
# =========================================
# 🔁 RECALCULAR PEDIDO (ADMIN)
# =========================================
def recalcular_pedido(pedido):

    total_pedido = Decimal('0')

    for item in pedido.items.all():

        subtotal = Decimal(item.subtotal or 0)

        # =========================
        # 🔥 DESCUENTO MANUAL (ADMIN)
        # =========================
        descuento = Decimal(item.descuento or 0)

        # 🔥 VALIDACIÓN (PRO)
        if descuento > subtotal:
            descuento = subtotal

        base = subtotal - descuento

        # =========================
        # 🔥 IVA
        # =========================
        iva = Decimal('0')
        if pedido.aplica_iva:
            iva = base * Decimal('0.19')

        # =========================
        # 🔥 RETEFUENTE
        # =========================
        retefuente = Decimal('0')
        if pedido.es_retenedor:
            retefuente = subtotal * Decimal('0.025')

        # =========================
        # 🔥 TOTAL ITEM
        # =========================
        total_item = base + iva - retefuente

        # =========================
        # 🔥 GUARDAR
        # =========================
        item.descuento = descuento
        item.iva = iva
        item.retefuente = retefuente
        item.total_final = total_item
        item.save()

        total_pedido += total_item

    # =========================
    # 🔥 TOTAL PEDIDO
    # =========================
    pedido.total = total_pedido
    pedido.save()

    return pedido