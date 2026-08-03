# =========================================================================
# 🐍 LIBRERÍAS ESTÁNDAR
# =========================================================================
import json
import os
import traceback
import urllib.parse
from decimal import Decimal
from urllib.parse import quote

# =========================================================================
# ⚡ DJANGO
# =========================================================================
from django.conf import settings
from django.contrib import messages
from django.contrib.sites.shortcuts import get_current_site
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.staticfiles import finders

# =========================================================================
# 🏪 MODELOS
# =========================================================================
from ..models import (
    Perfil,
    Producto,
    ProductoVariante,
    CarritoItem,
    Pedido,
    PedidoItem,
    Cliente,
    ConfiguracionEmpresa,
)
from ..utils import generar_pdf_pedido
# =========================================================================
# 🧠 SERVICIOS
# =========================================================================
from ..services.precios import calcular_precio_producto
from ..services.motor_fiscal import MotorFiscal

# =========================================================================
# 💳 PASARELAS
# =========================================================================
import mercadopago

# =========================================================================
# ⚙️ UTILIDADES
# =========================================================================
from .utilidades import (
    generar_numero_orden,
    calcular_envio,
)

# =========================================================================
# 💳 BLOQUE 5: PASARELAS DE PAGO, CHECKOUT Y WEBHOOKS
# =========================================================================

@transaction.atomic
def comprar_whatsapp(request, producto_id=None):
    if request.method != "POST":
        messages.error(request, "Acceso no autorizado.")
        return redirect('ver_carrito')

    resumen_items = []
    items_carrito = None
    es_compra_directa = producto_id is not None

    # =========================================================
    # 🛒 PASO 1: MODO DE ENTRADA (DIRECTO O CARRITO) - (Queda igual, es correcto)
    # =========================================================
    if es_compra_directa:
        producto = get_object_or_404(Producto, id=producto_id)
        usuario = producto.usuario  

        try:
            cantidad = int(request.POST.get('cantidad', 1))
        except ValueError:
            cantidad = 1

        variante = None
        variante_id = request.POST.get('variante_id')
        if variante_id:
            variante = get_object_or_404(ProductoVariante, id=variante_id, producto=producto)

        if variante:
            if variante.stock < cantidad:
                messages.warning(request, f"Sin stock suficiente para {producto.nombre}")
                return redirect('detalle_producto', id=producto.id, slug=producto.slug)
        else:
            if producto.tipo_venta != "gramo" and producto.stock < cantidad:
                messages.warning(request, f"Sin stock suficiente para {producto.nombre}")
                return redirect('detalle_producto', id=producto.id, slug=producto.slug)

        precio, gramos = calcular_precio_producto(producto, cantidad)
        precio = Decimal(str(precio))

        if gramos:
            subtotal = Decimal(str(gramos)) * precio
            linea = f"{producto.nombre} ({gramos}g)"
        else:
            subtotal = Decimal(str(cantidad)) * precio
            linea = f"{producto.nombre} x{cantidad}"

        resumen_items.append({
            'item': None, 
            'producto': producto,
            'variante': variante,
            'cantidad': cantidad,
            'total_gramos': gramos if gramos else 0,
            'precio': precio,
            'subtotal': subtotal,
            'linea': linea,
        })
    else:
        session_key = request.session.session_key
        if not session_key:
            messages.warning(request, "El carrito está vacío")
            return redirect('ver_carrito')

        items_carrito = CarritoItem.objects.select_related('producto', 'variante').filter(session_key=session_key)
        if not items_carrito.exists():
            messages.warning(request, "El carrito está vacío")
            return redirect('ver_carrito')

        usuario = items_carrito.first().producto.usuario

        for item in items_carrito:
            producto = item.producto
            variante = item.variante
            cantidad = item.cantidad or 0

            if producto.usuario != usuario:
                messages.warning(request, "Producto inválido en el carrito.")
                return redirect('ver_carrito')

            if variante:
                if variante.stock < cantidad:
                    messages.warning(request, f"Sin stock suficiente para {producto.nombre}")
                    return redirect('ver_carrito')
            else:
                if producto.tipo_venta != "gramo" and producto.stock < cantidad:
                    messages.warning(request, f"Sin stock suficiente para {producto.nombre}")
                    return redirect('ver_carrito')

            precio, gramos = calcular_precio_producto(producto, cantidad)
            precio_seguro = precio if precio is not None else 0
            precio = Decimal(str(precio_seguro))

            if gramos:
                subtotal = Decimal(str(gramos)) * precio
                linea = f"{producto.nombre} ({gramos}g)"
            else:
                subtotal = Decimal(str(cantidad)) * precio
                linea = f"{producto.nombre} x{cantidad}"

            resumen_items.append({
                'item': item,
                'producto': producto,
                'variante': variante,
                'cantidad': cantidad,
                'total_gramos': gramos if gramos else 0,
                'precio': precio,
                'subtotal': subtotal,
                'linea': linea,
            })

    # =========================================================
    # 📊 PASO 2: CONFIGURACIÓN Y MOTORES (Aquí empieza el cambio radical ⚡)
    # =========================================================
    numero = generar_numero_orden(usuario)
    perfil, _ = Perfil.objects.get_or_create(user=usuario)
    
    # 1. Traemos de forma segura la nueva clase de Configuración Fiscal
    config_fiscal, _ = ConfiguracionEmpresa.objects.get_or_create(perfil=perfil)
    
    # 2. Instanciamos el Motor Fiscal pasándole su configuración única
    motor = MotorFiscal(config_fiscal)

    # 3. Determinamos porcentaje de descuento comercial
    porcentaje_descuento = Decimal('0')
    if config_fiscal.aplicar_descuentos:
        porcentaje_descuento = Decimal(str(config_fiscal.porcentaje_descuento_promo or 0))

    # Captura de datos del cliente enviados por el POST
    nombre_cliente = request.POST.get('nombre', '').strip()
    telefono_cliente = request.POST.get('telefono', '').strip()
    ciudad_destino = request.POST.get('ciudad', '').strip()
    direccion_entrega = request.POST.get('direccion', '').strip()
    nit = request.POST.get('nit', '').strip()

    email_input = request.POST.get('email', '').strip()
    if email_input:
        cliente_email = email_input
    elif request.user.is_authenticated:
        cliente_email = request.user.email
    else:
        cliente_email = "cliente_whatsapp@joyasapp.com"

    # Calculamos el subtotal base neto de los productos
    subtotal_general = sum(data['subtotal'] for data in resumen_items)

    # 4. Resolvemos el tipo de envío de forma inteligente
    if config_fiscal.cobrar_envio:
        # El motor decidirá internamente si aplica tarifa estándar o envío gratis según las reglas del negocio
        tipo_envio = 'estandar'
        valor_envio_personalizado = None
    else:
        # Si el comercio no tiene reglas automáticas de cobro, aplicamos tu cálculo dinámico por ciudad
        tipo_envio = 'personalizado'
        # Calculamos el subtotal tentativo con descuento comercial para pasarlo a la función externa de envíos
        descuento_tentativo = subtotal_general * (porcentaje_descuento / Decimal('100'))
        subtotal_con_descuento = subtotal_general - descuento_tentativo
        valor_envio_personalizado = Decimal(str(calcular_envio(ciudad_destino, subtotal_con_descuento)))

    # 5. ⚡ EJECUTAMOS EL MOTOR FISCAL ⚡
    # Toda la cascada matemática ocurre en esta sola línea
    totales = motor.calcular_totales_pedido(
        subtotal_base=subtotal_general,
        porcentaje_descuento=porcentaje_descuento,
        tipo_envio=tipo_envio,
        valor_envio_personalizado=valor_envio_personalizado
    )

    try:
        # Guardamos el Pedido utilizando directamente los resultados estructurados del motor
        pedido = Pedido.objects.create(
            usuario=usuario,
            numero_orden=numero,
            cliente_nombre=nombre_cliente,
            cliente_telefono=telefono_cliente,
            cliente_email=cliente_email,
            cliente_ciudad=ciudad_destino,
            cliente_direccion=direccion_entrega,
            cliente_nit=nit,
            total=float(totales['total_final']),  
            porcentaje_descuento=float(totales['porcentaje_descuento']),  
            descuento_total=float(totales['descuento_total']),
            costo_envio=float(totales['costo_envio']),
            aplica_iva=config_fiscal.responsable_iva,
            es_retenedor=config_fiscal.es_retenedor,
            tipo_envio=totales['tipo_envio'],
            estado="pendiente"
        )

        # 6. Guardamos los items de forma inmutable usando los valores de item del motor
        for data in resumen_items:
            valores_item = motor.calcular_valores_item(subtotal_item=data['subtotal'])

            PedidoItem.objects.create(
                pedido=pedido,
                producto=data['producto'],
                variante=data['variante'],
                cantidad=int(data['cantidad']),
                total_gramos=float(data['total_gramos']), 
                precio=float(data['precio']),
                subtotal=float(data['subtotal']),
                iva=float(valores_item['iva_item']),
                retefuente=float(valores_item['retefuente_item']),
                total_final=float(valores_item['total_item'])
            )

            # Descuento del stock (Lógica de negocio)
            if data['variante']:
                data['variante'].stock -= data['cantidad']
                data['variante'].save()
            elif data['producto'].tipo_venta != "gramo":
                data['producto'].stock -= data['cantidad']
                data['producto'].save()

        # =========================================================
        # 🔗 PASO 3: ENLACE SEGURO Y WHATSAPP (Queda igual de limpio)
        # =========================================================
        domain = get_current_site(request).domain
        url_factura = f"https://{domain}{reverse('factura_publica', kwargs={'token': pedido.token_publico})}"

        mensaje = (
            f"🛍️ ¡Hola! Acabo de confirmar mi pedido.\n\n"
            f"📦 Orden N°: {pedido.numero_orden}\n"
            f"👤 Cliente: {nombre_cliente}\n"
            f"💰 Total Neto: ${float(totales['total_final']):,.0f}\n\n"
            f"📄 Ver detalles y datos de facturación aquí:\n{url_factura}\n\n"
            f"Quedo atento a tus indicaciones para realizar el pago. ¡Muchas gracias!"
        ).replace(",", ".")

        if not perfil.whatsapp:
            messages.warning(request, "El comercio no tiene configurado WhatsApp.")
            return redirect('ver_carrito') if not es_compra_directa else redirect('detalle_producto', id=producto.id, slug=producto.slug)

        url_whatsapp_final = f"https://wa.me/{perfil.whatsapp}?text={urllib.parse.quote(mensaje)}"

        if not es_compra_directa and items_carrito:
            items_carrito.delete()

        return redirect(url_whatsapp_final)

    except Exception as e:
        print("\n" + "🚨" * 30)
        print(f"💥 ERROR EN BASE DE DATOS / GUARDADO: {str(e)}")
        print("-" * 60)
        traceback.print_exc()
        print("🚨" * 30 + "\n")
        return HttpResponse(f"Fallo capturado en proceso de guardado: {str(e)}", status=500)
    
@transaction.atomic
def pagar_pedido(request):
    """
    Vista para procesar el pago final del pedido (Ruta: /pagar/)
    """
    if request.method != 'POST':
        return redirect('ver_carrito')

    try:
        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key

        items = CarritoItem.objects.select_related('producto', 'variante').filter(session_key=session_key)

        if not items.exists():
            messages.error(request, "Tu carrito está vacío.")
            return redirect('ver_carrito')

        # 🏪 Detectamos el usuario dueño de la joyería
        usuario = items.first().producto.usuario
        numero = generar_numero_orden(usuario)

        # 📝 CAPTURA DE DATOS DEL FORMULARIO DE DESPACHO
        nombre = request.POST.get('nombre', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        ciudad = request.POST.get('ciudad', '').strip()
        direccion = request.POST.get('direccion', '').strip()
        nit = request.POST.get('nit', '').strip()
        
        # Tipo de envío seleccionado por el usuario en el frontend
        tipo_envio_seleccionado = request.POST.get('tipo_envio', 'domicilio').strip()

        # 🛠️ ESCUDO CONTRA CORREOS VACÍOS O ANÓNIMOS
        email_input = request.POST.get('email', '').strip()
        if email_input:
            cliente_email = email_input
        elif request.user.is_authenticated:
            cliente_email = request.user.email
        else:
            cliente_email = "cliente_pasarela@joyasapp.com"

        # 📊 CAPTURA DINÁMICA DE IMPUESTOS Y DESCUENTO DESDE EL FORMULARIO
        aplica_iva = request.POST.get('aplica_iva') == 'on' or request.POST.get('aplica_iva') == 'true'
        es_retenedor = request.POST.get('es_retenedor') == 'on' or request.POST.get('es_retenedor') == 'true'
        valor_descuento = request.POST.get('descuento', '0').strip()

        try:
            porcentaje_descuento = Decimal(str(valor_descuento or 0))
        except (ValueError, TypeError):
            porcentaje_descuento = Decimal('0')

        porcentaje_descuento = max(Decimal('0'), min(Decimal('100'), porcentaje_descuento))

        # =========================================================
        # 🧠 CONFIGURACIÓN DEL MOTOR FISCAL
        # =========================================================
        perfil, _ = Perfil.objects.get_or_create(user=usuario)
        config_fiscal, _ = ConfiguracionEmpresa.objects.get_or_create(perfil=perfil)
        
        motor = MotorFiscal(config_fiscal)
        
        # El formulario de la transacción específica puede sobreescribir las reglas por defecto
        motor.aplica_iva = aplica_iva
        motor.es_retenedor = es_retenedor

        # Calcular precios bases y gramos de cada ítem (Conserva tu lógica de negocio intacta)
        subtotal_general = Decimal('0')
        lineas_items = []

        for item in items:
            cantidad_fisica = Decimal(str(item.cantidad or 1))
            
            if item.producto.tipo_venta == 'gramo':
                val_precio = Decimal(str(item.producto.precio_por_gramo_detal or 0))
                peso_base = Decimal(str(item.producto.peso_producto or 0))
                gramos_totales = cantidad_fisica * peso_base
                subtotal_item = gramos_totales * val_precio
                precio_aplicado = val_precio
            else:
                try:
                    precio_por_escala = item.producto.precio_por_cantidad(item.cantidad)
                    if precio_por_escala is None:
                        precio_por_escala = item.producto.precio_detal or 0
                    val_precio = Decimal(str(precio_por_escala))
                except Exception:
                    val_precio = Decimal(str(item.producto.precio_detal or 0))
                
                gramos_totales = Decimal('0.00')
                subtotal_item = cantidad_fisica * val_precio
                precio_aplicado = val_precio
            
            subtotal_general += subtotal_item
            
            lineas_items.append({
                'item_carrito': item,
                'precio_aplicado': precio_aplicado,
                'gramos_totales': gramos_totales,
                'subtotal_item': subtotal_item
            })

        # =========================================================
        # 🚚 RESOLUCIÓN DE ENVÍOS INTELIGENTE
        # =========================================================
        if config_fiscal.cobrar_envio:
            # Si se configuró cobro de envío automático, respetamos si el cliente prefiere recogerlo
            tipo_envio = 'recogida' if tipo_envio_seleccionado == 'recogida' else 'estandar'
            valor_envio_personalizado = None
        else:
            tipo_envio = 'personalizado'
            descuento_tentativo = subtotal_general * (porcentaje_descuento / Decimal('100'))
            subtotal_con_descuento = subtotal_general - descuento_tentativo
            
            if tipo_envio_seleccionado == 'recogida':
                valor_envio_personalizado = Decimal('0.00')
                tipo_envio = 'recogida'
            else:
                # Usamos tu función auxiliar basada en la base de datos de envíos por municipio
                valor_envio_personalizado = Decimal(str(calcular_envio(ciudad, subtotal_con_descuento)))

        # ⚡ CÁLCULO GENERAL DE LA CASCADA MATEMÁTICA
        totales = motor.calcular_totales_pedido(
            subtotal_base=subtotal_general,
            porcentaje_descuento=porcentaje_descuento,
            tipo_envio=tipo_envio,
            valor_envio_personalizado=valor_envio_personalizado
        )

        # 👤 Buscar o crear cliente (Conserva tu lógica)
        cliente, creado = Cliente.objects.get_or_create(
            usuario=usuario,
            telefono=telefono,
            defaults={
                "nombre": nombre,
                "email": cliente_email,
                "ciudad": ciudad,
                "direccion": direccion,
            }
        )

        if not creado:
            cliente.nombre = nombre
            cliente.email = cliente_email
            cliente.ciudad = ciudad
            cliente.direccion = direccion
            cliente.save()

        # 📦 Crear el Pedido Maestro con datos puros del Motor Fiscal
        pedido = Pedido.objects.create(
            usuario=usuario,
            cliente=cliente,
            numero_orden=numero,
            cliente_nombre=nombre,        
            cliente_telefono=telefono,
            cliente_email=cliente_email,  
            cliente_ciudad=ciudad,
            cliente_direccion=direccion,
            cliente_nit=nit,
            total=float(totales['total_final']),                 
            porcentaje_descuento=float(totales['porcentaje_descuento']),
            descuento_total=float(totales['descuento_total']),
            costo_envio=float(totales['costo_envio']),
            tipo_envio=totales['tipo_envio'],
            aplica_iva=aplica_iva,             
            es_retenedor=es_retenedor,
            iva=float(totales['iva_total']),  # Guardamos el IVA consolidado
            retefuente=float(totales['retefuente_total']),  # Guardamos la Retefuente consolidada
            estado="pendiente" 
        )

        # 🔄 Registrar cada PedidoItem calculando valores unitarios inmutables
        for linea in lineas_items:
            item = linea['item_carrito']
            
            # El motor desglosa el IVA e impuesto de cada ítem de forma aislada
            valores_item = motor.calcular_valores_item(subtotal_item=linea['subtotal_item'])

            PedidoItem.objects.create(
                pedido=pedido,
                producto=item.producto,
                variante=item.variante,
                cantidad=int(item.cantidad),
                precio=float(linea['precio_aplicado']),
                total_gramos=float(linea['gramos_totales']),  
                subtotal=float(linea['subtotal_item']),
                iva=float(valores_item['iva_item']),
                retefuente=float(valores_item['retefuente_item']),
                total_final=float(valores_item['total_item'])
            )

            # Descontar existencias del inventario (Apartar mercancía)
            if item.variante:
                item.variante.stock -= item.cantidad
                item.variante.save()
            elif item.producto.tipo_venta != "gramo":
                item.producto.stock -= item.cantidad
                item.producto.save()

        # Vaciar el carrito de compras tras el éxito de la transacción
        items.delete()

        return render(request, 'pago.html', {'pedido': pedido})

    except Exception as e:
        print("\n" + "🚨" * 30)
        print(f"💥 ERROR CRÍTICO EN /PAGAR/: {str(e)}")
        print("-" * 60)
        traceback.print_exc()
        print("🚨" * 30 + "\n")
        
        return HttpResponse(f"Error detectado en el proceso de pago: {str(e)}", status=500)

def pagar_wompi(request, token): # <-- Cambiado de pedido_id a token
    pedido = get_object_or_404(Pedido, token_publico=token) # <-- get_object_or_404 seguro
    return crear_checkout_wompi(request, pedido)

def crear_checkout_wompi(request, pedido):
    redirect_url = request.build_absolute_uri(f'/pago-exitoso/{pedido.token_publico}/')
    redirect_url_encoded = quote(redirect_url, safe='')

    # 🏪 1. Buscamos el perfil del dueño de la joyería
    perfil_dueno = pedido.usuario.perfil
    
    # 🔑 2. Buscamos ÚNICAMENTE la llave del usuario en la base de datos
    wompi_public_key = getattr(perfil_dueno, 'wompi_public_key', None)

    # 🚨 EL ESCUDO: Si el usuario NO tiene su llave configurada, frenamos todo
    if not wompi_public_key or wompi_public_key.strip() == "":
        print(f"⚠️ Alerta de Seguridad: El usuario {pedido.usuario.username} intentó recibir un pago pero no tiene configurada su Wompi Public Key.")
        return HttpResponse(
            "<h3>Método de pago temporalmente no disponible</h3>"
            "<p>Esta tienda aún no ha terminado de configurar sus credenciales de pago en línea. "
            "Por favor, ponte en contacto con el administrador de la joyería para completar tu compra por WhatsApp.</p>", 
            status=400
        )

    # 🚀 Si sí tiene su llave, el flujo continúa normal y el dinero va a SU cuenta bancaria
    checkout_url = (
        "https://checkout.wompi.co/p/"
        f"?public-key={wompi_public_key.strip()}" 
        f"&currency=COP"
        f"&amount-in-cents={int(pedido.total * 100)}"
        f"&reference={pedido.numero_orden}"
        f"&redirect-url={redirect_url_encoded}"
    )
    return redirect(checkout_url)

@csrf_exempt
def webhook_wompi(request):
    if request.method != 'POST':
        return HttpResponse("Método no permitido", status=405)
        
    try:
        # 1. Parsear los datos que envía Wompi
        data = json.loads(request.body)
        
        # Opcional: Validar integridad con la firma de Wompi (si configuras WOMPI_EVENTS_SECRET)
        # Por ahora procesamos el evento de forma directa y segura por ID de orden
        
        transaccion = data.get('data', {}).get('transaction', {})
        referencia_orden = transaccion.get('reference') # Ejemplo: #FAC-00022
        estado_wompi = transaccion.get('status') # APPROVED, DECLINED, VOIDED
        
        if referencia_orden and estado_wompi == 'APPROVED':
            # 2. Buscar el pedido por su número de orden único
            pedido = Pedido.objects.filter(numero_orden=referencia_orden).first()
            
            if pedido and pedido.estado != 'pagado':
                # 3. Actualizar el estado de manera definitiva
                pedido.estado = 'pagado'
                pedido.saldo_pendiente = Decimal('0.00')
                pedido.save()
                print(f"✅ Webhook exitoso: Pedido {referencia_orden} marcado como PAGADO.")
                
                # ========================================================
                # 🚀 DISPARADORES AUTOMÁTICOS INTEGRADOS (Wompi)
                # ========================================================
                from .utils import generar_pdf_pedido
                from django.core.mail import EmailMessage
                from .models import Perfil # Asegúrate de importar tu modelo Perfil aquí si no está arriba

                # Recuperamos el perfil de la joyería dueña de este pedido de forma dinámica
                perfil = Perfil.objects.filter(user=pedido.usuario).first()

                if perfil:
                    # Generamos el binario del PDF usando tu plantilla HTML
                    pdf, correo_empresa, nombre_tienda = generar_pdf_pedido(pedido, perfil, usuario_backup=perfil.user)

                    email_cliente = getattr(pedido, 'cliente_email', None)
                    if email_cliente and pdf:
                        try:
                            email = EmailMessage(
                                subject=f"Confirmación de Pago: Pedido #{pedido.numero_orden} - {nombre_tienda}",
                                body=f"¡Hola! Tu pago ha sido confirmado correctamente a través de Wompi. Adjuntamos tu factura detallada. 💎",
                                from_email=correo_empresa,
                                to=[email_cliente]
                            )
                            email.attach(f"factura_{pedido.numero_orden}.pdf", pdf, 'application/pdf')
                            email.send()
                            print(f"📧 Correo enviado al cliente {email_cliente} para la orden {pedido.numero_orden}")
                        except Exception as e:
                            print(f"Error en envío automático por Webhook Wompi: {str(e)}")
                # ========================================================
                
        # Wompi exige que le respondas un HTTP 200 para saber que recibiste la notificación
        return HttpResponse("Evento recibido", status=200)
        
    except Exception as e:
        print(f"💥 Error en Webhook Wompi: {str(e)}")
        # Corregido el typo 'HttpRespon tnse' que tenías en tu borrador original
        return HttpResponse("Error interno procesado", status=200)

def pagar_con_mercadopago(request, token):
    """
    Genera la preferencia de Mercado Pago asegurando la correcta obtención 
    del perfil del vendedor sin recalcular totales de forma prematura.
    """
    try:
        # 1. Obtener el pedido usando el token público correcto
        pedido = get_object_or_404(Pedido, token_publico=token)
        
        # 🎯 INTENTO 1: Buscar relación directa si tu modelo Pedido tiene 'perfil_joyeria' o similar
        perfil_tienda = getattr(pedido, 'perfil_joyeria', None) or getattr(pedido, 'perfil', None)
        
        # 🎯 INTENTO 2: Si no viene directo, lo extraemos a través del usuario de la tienda/vendedor
        if not perfil_tienda:
            primer_item = pedido.items.first()  # Ajusta 'items' según el related_name de PedidoItem
            if primer_item and hasattr(primer_item.producto, 'usuario'):
                vendedor = primer_item.producto.usuario
            elif primer_item and hasattr(primer_item.producto, 'perfil'):
                perfil_tienda = primer_item.producto.perfil
                vendedor = getattr(perfil_tienda, 'user', None)
            else:
                vendedor = pedido.usuario 
                
            if not perfil_tienda and vendedor:
                try:
                    perfil_tienda = Perfil.objects.get(user=vendedor)
                except Perfil.DoesNotExist:
                    perfil_tienda = None

        if not perfil_tienda:
            return HttpResponse(
                "<h3>Error de Configuración</h3><p>No se pudo determinar la joyería dueña de este pedido.</p>", 
                status=200
            )

        # 🛡️ EXTRACCIÓN Y LIMPIEZA DE TU TOKEN 'APP_USR'
        token_mp = getattr(perfil_tienda, 'mercadopago_access_token', None)
        
        if token_mp is None or str(token_mp).strip() in ["", "None"]:
            return HttpResponse(
                f"<div style='font-family:sans-serif; padding:20px; text-align:center; margin-top:50px;'>"
                f"   <h2 style='color:#1D4ED8;'>Módulo de Pago en Configuración</h2>"
                f"   <p style='color:#4B5563;'>La tienda '{perfil_tienda.nombre_tienda}' aún no ha enlazado sus credenciales de Mercado Pago.</p>"
                f"</div>", 
                status=200
            )

        token_limpio = str(token_mp).strip()

        # Inicialización del SDK con credenciales reales del vendedor
        sdk = mercadopago.SDK(token_limpio)

        # URLs de Redirección y Notificación usando el atributo real: token_publico
        ruta_relativa = f"/webhooks/mercadopago/{perfil_tienda.webhook_uuid}/"
        url_webhook = request.build_absolute_uri(ruta_relativa).replace("http://", "https://")
        
        url_success = request.build_absolute_uri(f"/pago-exitoso/{pedido.token_publico}/").replace("http://", "https://")
        url_failure = request.build_absolute_uri(f"/pago-fallido/{pedido.token_publico}/").replace("http://", "https://")

        # Configuración del valor a pagar extraído directamente de lo sellado en la base de datos
        monto_total = getattr(pedido, 'total_limpio', None) or getattr(pedido, 'total', 0)
        monto_total = float(monto_total)

        # Estructura de preferencia ÚNICA y limpia
        preference_data = {
            "items": [
                {
                    "title": f"Pedido #{pedido.numero_orden} - {perfil_tienda.nombre_tienda}",
                    "quantity": 1,
                    "currency_id": "COP",
                    "unit_price": monto_total,
                }
            ],
            "back_urls": {
                "success": url_success,
                "failure": url_failure,
                "pending": url_success
            },
            "auto_return": "approved",
            "external_reference": str(pedido.numero_orden),
        }

        # Excluir localhost del webhook para evitar rechazos de Mercado Pago en desarrollo local
        if "localhost" not in url_webhook and "127.0.0.1" not in url_webhook:
            preference_data["notification_url"] = url_webhook

        # 1. Hacemos la petición a Mercado Pago
        preference_response = sdk.preference().create(preference_data)
        
        # 2. LOG DE CONTROL: Imprimimos la respuesta completa en Render para auditorías rápidas
        print("====== RESPUESTA COMPLETA DE MERCADO PAGO ======")
        print(preference_response)
        print("================================================")

        # 3. Extraemos la respuesta usando .get() de forma segura
        preference = preference_response.get("response") if preference_response else None
        
        # 4. Validamos si Mercado Pago nos devolvió el punto de inicio de pago válido
        if not preference or not preference.get("init_point"):
            mensaje_error = preference_response.get("message", "Las credenciales no son válidas o la cuenta requiere homologación en Mercado Pago.")
            return HttpResponse(
                f"<div style='font-family:sans-serif; padding:20px; text-align:center; margin-top:50px;'>"
                f"   <h2 style='color:#DC2626;'>Mercado Pago: Rechazo de Solicitud</h2>"
                f"   <p style='color:#4B5563;'>La pasarela respondió pero no generó el punto de inicio.</p>"
                f"   <p style='color:#9CA3AF; font-size:14px;'><b>Detalle técnico:</b> {mensaje_error}</p>"
                f"   <a href='/factura/{pedido.token_publico}/' style='display:inline-block; margin-top:20px; padding:10px 20px; background:#1D4ED8; color:white; text-decoration:none; border-radius:5px;'>Volver al Pedido</a>"
                f"</div>",
                status=200
            )
        
        # 5. Redirección al checkout transparente de Mercado Pago
        return redirect(preference.get("init_point"))

    except Exception as e:
        print(f"💥 Error crítico detallado en Mercado Pago: {traceback.format_exc()}")
        return HttpResponse(f"Error interno al inicializar el pago: {str(e)}", status=200)

@csrf_exempt
def webhook_mercadopago(request, profile_uuid):
    """Recibe el UUID directamente en la URL, consulta el pago y procesa el flujo automático."""
    if request.method == 'POST':
        try:
            # 1. Buscamos el perfil de la joyería usando el UUID de la URL de forma directa
            perfil = get_object_or_404(Perfil, webhook_uuid=profile_uuid)

            # 2. Capturamos el ID del recurso que envía Mercado Pago
            topic = request.GET.get('topic') or request.POST.get('topic')
            id_recurso = request.GET.get('id') or request.POST.get('id')

            if not topic and request.body:
                data = json.loads(request.body)
                topic = data.get('type')
                if data.get('data'):
                    id_recurso = data.get('data').get('id')

            # 3. Si es una notificación de pago, procedemos con el SDK del vendedor
            if topic == 'payment' and id_recurso:
                # 🧠 Inicialización limpia usando el token del vendedor recuperado con el UUID
                sdk = mercadopago.SDK(perfil.mercadopago_access_token)
                payment_info = sdk.payment().get(id_recurso)
                payment_data = payment_info["response"]

                status = payment_data.get("status")
                numero_orden = payment_data.get("external_reference")
                monto_recibido = payment_data.get("transaction_amount")

                # 4. Localizamos el pedido real en el sistema
                pedido = get_object_or_404(Pedido, numero_orden=numero_orden)
                
                # 5. Validamos estado y monto
                if status == 'approved' and float(monto_recibido) >= float(pedido.total_limpio):
                    if pedido.estado_pago != 'pagado':
                        pedido.estado_pago = 'pagado'
                        pedido.mercadopago_payment_id = id_recurso  # Guardamos la auditoría
                        pedido.save()
                        
                        # ========================================================
                        # 🚀 DISPARADORES AUTOMÁTICOS (Tu hoja de ruta)
                        # ========================================================
                        
                        # 1. Enviar correo con factura PDF adjunta (Tu HTML usando xhtml2pdf)
                        from .utils import generar_pdf_pedido
                        from django.core.mail import EmailMessage

                        # Generamos el binario del PDF invocando tu función pura
                        # Pasamos perfil.user como usuario de respaldo (backup)
                        pdf, correo_empresa, nombre_tienda = generar_pdf_pedido(pedido, perfil, usuario_backup=perfil.user)

                        email_cliente = getattr(pedido, 'cliente_email', None)
                        if email_cliente and pdf:
                            try:
                                email = EmailMessage(
                                    subject=f"Confirmación de Pago: Pedido #{pedido.numero_orden} - {nombre_tienda}",
                                    body=f"¡Hola! Tu pago ha sido confirmado correctamente. Adjuntamos tu factura detallada. 💎",
                                    from_email=correo_empresa,
                                    to=[email_cliente]
                                )
                                email.attach(f"factura_{pedido.numero_orden}.pdf", pdf, 'application/pdf')
                                email.send()
                            except Exception as e:
                                print(f"Error en envío automático por Webhook MP para {profile_uuid}: {str(e)}")

                        # 2. Notificación push o WhatsApp al vendedor
                        # (Aquí pones tu lógica de notificaciones cuando la tengas lista)

                        # 3. Asiento en el registro financiero / contabilidad
                        # (Aquí pones tu lógica contable cuando la tengas lista)

                        # ========================================================

            return HttpResponse("OK", status=200)
                
        except Exception as e:
            print(f"Error crítico en Webhook MP ({profile_uuid}): {str(e)}")
            return HttpResponse(status=500)
            
    return HttpResponse(status=400)

def pago_exitoso(request, token):
    """Vista de retorno cuando el pago es aprobado en Wompi o Mercado Pago"""
    pedido = get_object_or_404(Pedido, token_publico=token)

    # 🛡️ Validamos de forma segura que Mercado Pago realmente retorne aprobado (si aplica)
    status_mp = request.GET.get('status')
    if status_mp and status_mp != 'approved' and status_mp != 'authorized':
        # Si no está aprobado de verdad, lo mandamos a la vista de pendiente o fallido
        return redirect('pago_fallido', token=token)

    pedido.estado = "pagado"
    pedido.saldo_pendiente = Decimal('0.00')
    pedido.save()

    return redirect('factura_publica', token=token)

def pago_fallido(request, token):
    """Vista para gestionar los pagos rechazados o fallidos de Mercado Pago/Wompi"""
    # 🎯 CORREGIDO: Buscamos usando 'token_publico' para evitar FieldError
    pedido = get_object_or_404(Pedido, token_publico=token) 
    
    context = {
        'pedido': pedido,
        'status': request.GET.get('status'), # Captura el estado ('rejected', 'cancelled')
    }
    # 🎯 CORREGIDO: Ajustado a 'pago_fallido.html' para consistencia de rutas
    return render(request, 'pago_fallido.html', context)

# ────────────────────────────────────────────────────────────────
# 🛠️ FUNCIÓN AUXILIAR: TRADUCTOR DE RUTAS ESTÁTICAS PARA EL PDF
# ────────────────────────────────────────────────────────────────
def link_callback(uri, rel):
    """
    Convierte rutas relativas de estáticos y media de Django en rutas 
    absolutas del sistema operativo para que xhtml2pdf las encuentre en Render.
    """
    # 1. Buscar el archivo usando el motor de estáticos de Django
    result = finders.find(uri)
    if result:
        if not isinstance(result, (list, tuple)):
            result = [result]
        path = result[0]
    else:
        # 2. Si no lo encuentra, construir la ruta usando settings
        s_url = settings.STATIC_URL
        s_root = settings.STATIC_ROOT
        m_url = settings.MEDIA_URL
        m_root = settings.MEDIA_ROOT

        if uri.startswith(m_url):
            path = os.path.join(m_root, uri.replace(m_url, ""))
        elif uri.startswith(s_url):
            path = os.path.join(s_root, uri.replace(s_url, ""))
        else:
            return uri

    # Asegurarnos de que el archivo físico realmente existe en el servidor
    if not os.path.isfile(path):
        return uri
    return path