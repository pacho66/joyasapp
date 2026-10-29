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
    calcular_totales_pedido,
)
from . import utilidades

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
    # 🛒 PASO 1: MODO DE ENTRADA (DIRECTO O CARRITO)
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
    # 📊 PASO 2: CONFIGURACIÓN Y MOTORES
    # =========================================================
    numero = generar_numero_orden(usuario)
    perfil, _ = Perfil.objects.get_or_create(user=usuario)
    config_fiscal, _ = ConfiguracionEmpresa.objects.get_or_create(perfil=perfil)
    
    motor = MotorFiscal(config_fiscal)

    porcentaje_descuento = Decimal('0')
    if config_fiscal.aplicar_descuentos:
        porcentaje_descuento = Decimal(str(config_fiscal.porcentaje_descuento_promo or 0))

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

    subtotal_general = sum(data['subtotal'] for data in resumen_items)

    if config_fiscal.cobrar_envio:
        tipo_envio = 'estandar'
        valor_envio_personalizado = None
    else:
        tipo_envio = 'personalizado'
        descuento_tentativo = subtotal_general * (porcentaje_descuento / Decimal('100'))
        subtotal_con_descuento = subtotal_general - descuento_tentativo
        valor_envio_personalizado = Decimal(str(calcular_envio(ciudad_destino, subtotal_con_descuento)))

    # 5. ⚡ EJECUTAMOS EL MOTOR FISCAL (Corregido usando utilidades)
    totales = utilidades.calcular_totales_pedido(
        perfil=perfil,
        items=resumen_items,
        porcentaje_descuento=porcentaje_descuento,
        tipo_envio=tipo_envio,
        costo_envio=valor_envio_personalizado
    )

    try:
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
            iva=float(totales['iva_total']),
            retefuente=float(totales['retefuente_total']),
            tipo_envio=totales['tipo_envio'],
            estado="pendiente"
        )

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

            if data['variante']:
                if data['variante'].stock < data['cantidad']:
                    raise Exception(f"Stock insuficiente para la variante. Disponibles: {data['variante'].stock}")
                data['variante'].stock -= data['cantidad']
                data['variante'].save()
            elif data['producto'].tipo_venta != "gramo":
                if data['producto'].stock < data['cantidad']:
                    raise Exception(f"Stock insuficiente para el producto. Disponibles: {data['producto'].stock}")
                data['producto'].stock -= data['cantidad']
                data['producto'].save()

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

        usuario = items.first().producto.usuario
        numero = generar_numero_orden(usuario)

        nombre = request.POST.get('nombre', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        ciudad = request.POST.get('ciudad', '').strip()
        direccion = request.POST.get('direccion', '').strip()
        nit = request.POST.get('nit', '').strip()
        
        tipo_envio_seleccionado = request.POST.get('tipo_envio', 'domicilio').strip()

        email_input = request.POST.get('email', '').strip()
        if email_input:
            cliente_email = email_input
        elif request.user.is_authenticated:
            cliente_email = request.user.email
        else:
            cliente_email = "cliente_pasarela@joyasapp.com"

        aplica_iva = request.POST.get('aplica_iva') == 'on' or request.POST.get('aplica_iva') == 'true'
        es_retenedor = request.POST.get('es_retenedor') == 'on' or request.POST.get('es_retenedor') == 'true'
        valor_descuento = request.POST.get('descuento', '0').strip()

        try:
            porcentaje_descuento = Decimal(str(valor_descuento or 0))
        except (ValueError, TypeError):
            porcentaje_descuento = Decimal('0')

        porcentaje_descuento = max(Decimal('0'), min(Decimal('100'), porcentaje_descuento))

        perfil, _ = Perfil.objects.get_or_create(user=usuario)
        config_fiscal, _ = ConfiguracionEmpresa.objects.get_or_create(perfil=perfil)
        
        motor = MotorFiscal(config_fiscal)
        motor.aplica_iva = aplica_iva
        motor.es_retenedor = es_retenedor

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

        if config_fiscal.cobrar_envio:
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
                valor_envio_personalizado = Decimal(str(calcular_envio(ciudad, subtotal_con_descuento)))

        totales = utilidades.calcular_totales_pedido(
            perfil=perfil,
            items=lineas_items,
            porcentaje_descuento=porcentaje_descuento,
            tipo_envio=tipo_envio,
            costo_envio=valor_envio_personalizado
        )

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
            tipo_envio=tipo_envio,
            aplica_iva=aplica_iva,             
            es_retenedor=es_retenedor,
            iva=float(totales['iva_total']),  
            retefuente=float(totales['retefuente_total']),  
            estado="pendiente" 
        )

        for linea in lineas_items:
            item = linea['item_carrito']
            
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

            if item.variante:
                if item.variante.stock < item.cantidad:
                    raise Exception(f"Stock insuficiente para la variante. Disponibles: {item.variante.stock}")
                item.variante.stock -= item.cantidad
                item.variante.save()
            elif item.producto.tipo_venta != "gramo":
                if item.producto.stock < item.cantidad:
                    raise Exception(f"Stock insuficiente para el producto. Disponibles: {item.producto.stock}")
                item.producto.stock -= item.cantidad
                item.producto.save()

        items.delete()

        return render(request, 'pago.html', {'pedido': pedido})

    except Exception as e:
        print("\n" + "🚨" * 30)
        print(f"💥 ERROR CRÍTICO EN /PAGAR/: {str(e)}")
        print("-" * 60)
        traceback.print_exc()
        print("🚨" * 30 + "\n")
        
        return HttpResponse(f"Error detectado en el proceso de pago: {str(e)}", status=500)

def pagar_wompi(request, token):
    pedido = get_object_or_404(Pedido, token_publico=token)
    return crear_checkout_wompi(request, pedido)

def crear_checkout_wompi(request, pedido):
    redirect_url = request.build_absolute_uri(f'/pago-exitoso/{pedido.token_publico}/')
    redirect_url_encoded = quote(redirect_url, safe='')

    perfil_dueno = pedido.usuario.perfil
    wompi_public_key = getattr(perfil_dueno, 'wompi_public_key', None)

    if not wompi_public_key or wompi_public_key.strip() == "":
        return HttpResponse(
            "<h3>Método de pago temporalmente no disponible</h3>"
            "<p>Esta tienda aún no ha terminado de configurar sus credenciales de pago en línea. "
            "Por favor, ponte en contacto con el administrador de la joyería para completar tu compra por WhatsApp.</p>", 
            status=400
        )

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
        data = json.loads(request.body)
        transaccion = data.get('data', {}).get('transaction', {})
        referencia_orden = transaccion.get('reference')
        estado_wompi = transaccion.get('status')
        
        if referencia_orden and estado_wompi == 'APPROVED':
            pedido = Pedido.objects.filter(numero_orden=referencia_orden).first()
            
            if pedido and pedido.estado != 'pagado':
                pedido.estado = 'pagado'
                pedido.saldo_pendiente = Decimal('0.00')
                pedido.save()
                
                from .utils import generar_pdf_pedido
                from django.core.mail import EmailMessage
                from .models import Perfil

                perfil = Perfil.objects.filter(user=pedido.usuario).first()

                if perfil:
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
                        except Exception as e:
                            print(f"Error en envío automático por Webhook Wompi: {str(e)}")
                
        return HttpResponse("Evento recibido", status=200)
        
    except Exception as e:
        print(f"💥 Error en Webhook Wompi: {str(e)}")
        return HttpResponse("Error interno procesado", status=200)

def pagar_con_mercadopago(request, token):
    try:
        pedido = get_object_or_404(Pedido, token_publico=token)
        
        perfil_tienda = getattr(pedido, 'perfil_joyeria', None) or getattr(pedido, 'perfil', None)
        
        if not perfil_tienda:
            primer_item = pedido.items.first()
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
        sdk = mercadopago.SDK(token_limpio)

        ruta_relativa = f"/webhooks/mercadopago/{perfil_tienda.webhook_uuid}/"
        url_webhook = request.build_absolute_uri(ruta_relativa).replace("http://", "https://")
        
        url_success = request.build_absolute_uri(f"/pago-exitoso/{pedido.token_publico}/").replace("http://", "https://")
        url_failure = request.build_absolute_uri(f"/pago-fallido/{pedido.token_publico}/").replace("http://", "https://")

        monto_total = float(pedido.total)

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

        if "localhost" not in url_webhook and "127.0.0.1" not in url_webhook:
            preference_data["notification_url"] = url_webhook

        preference_response = sdk.preference().create(preference_data)
        preference = preference_response.get("response") if preference_response else None
        
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
        
        return redirect(preference.get("init_point"))

    except Exception as e:
        print(f"💥 Error crítico detallado en Mercado Pago: {traceback.format_exc()}")
        return HttpResponse(f"Error interno al inicializar el pago: {str(e)}", status=200)

@csrf_exempt
def webhook_mercadopago(request, profile_uuid):
    if request.method == 'POST':
        try:
            perfil = get_object_or_404(Perfil, webhook_uuid=profile_uuid)

            topic = request.GET.get('topic') or request.POST.get('topic')
            id_recurso = request.GET.get('id') or request.POST.get('id')

            if not topic and request.body:
                data = json.loads(request.body)
                topic = data.get('type')
                if data.get('data'):
                    id_recurso = data.get('data').get('id')

            if topic == 'payment' and id_recurso:
                sdk = mercadopago.SDK(perfil.mercadopago_access_token)
                payment_info = sdk.payment().get(id_recurso)
                payment_data = payment_info["response"]

                status = payment_data.get("status")
                numero_orden = payment_data.get("external_reference")
                monto_recibido = payment_data.get("transaction_amount")

                pedido = get_object_or_404(Pedido, numero_orden=numero_orden)
                
                # Corregido a pedido.total y pedido.estado para consistencia
                if status == 'approved' and float(monto_recibido) >= float(pedido.total):
                    if pedido.estado != 'pagado':
                        pedido.estado = 'pagado'
                        pedido.mercadopago_payment_id = id_recurso
                        pedido.save()
                        
                        from .utils import generar_pdf_pedido
                        from django.core.mail import EmailMessage

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

            return HttpResponse("OK", status=200)
                
        except Exception as e:
            print(f"Error crítico en Webhook MP ({profile_uuid}): {str(e)}")
            return HttpResponse(status=500)
            
    return HttpResponse(status=400)

def pago_exitoso(request, token):
    """Vista de retorno cuando el pago es aprobado en Wompi o Mercado Pago"""
    try:
        pedido = get_object_or_404(Pedido, token_publico=token)

        status_mp = request.GET.get('status')
        if status_mp and status_mp not in ['approved', 'authorized']:
            return redirect('pago_fallido', token=token)

        # Actualizamos estado de forma segura
        pedido.estado = "pagado"
        pedido.saldo_pendiente = Decimal('0.00')
        pedido.save()

        return redirect('factura_publica', token=token)
    except Exception as e:
        print("\n" + "🚨" * 30)
        print(f"💥 ERROR EN PAGO EXITOSO: {str(e)}")
        print("-" * 60)
        traceback.print_exc()
        print("🚨" * 30 + "\n")
        # Si falla la redirección interna, mostramos un mensaje amigable o redirigimos al carrito/inicio
        messages.success(request, "¡Tu pago fue procesado con éxito!")
        return redirect('ver_carrito')

def pago_fallido(request, token):
    pedido = get_object_or_404(Pedido, token_publico=token) 
    
    context = {
        'pedido': pedido,
        'status': request.GET.get('status'),
    }
    return render(request, 'pago_fallido.html', context)

# =========================================================================
# 👑 PASARELA DE PAGOS WOMPI - SUSCRIPCIONES SAAS (JOYASAPP)
# =========================================================================

PLANES_SAAS = {
    'basico': {
        'nombre': 'Plan Básico - JoyasApp',
        'precio_centavos': 4990000, # $49.900 COP
    },
    'semi': {
        'nombre': 'Plan Semi - JoyasApp',
        'precio_centavos': 8990000, # $89.900 COP
    },
    'premium': {
        'nombre': 'Plan Premium - JoyasApp',
        'precio_centavos': 14990000, # $149.900 COP
    }
}

def iniciar_pago_suscripcion_wompi(request, plan_slug):
    """
    Inicia el pago del plan SaaS de JoyasApp mediante el widget de Wompi.
    Ruta sugerida: /suscripcion/pagar/<str:plan_slug>/
    """
    if not request.user.is_authenticated:
        return redirect('login')
        
    if plan_slug not in PLANES_SAAS:
        messages.error(request, "El plan seleccionado no es válido.")
        return redirect('dashboard') # O la vista de planes de tu SaaS

    plan_info = PLANES_SAAS[plan_slug]
    perfil, _ = Perfil.objects.get_or_create(user=request.user)
    
    # Generamos una referencia única para la suscripción
    import uuid
    referencia_suscripcion = f"SUB-{request.user.id}-{uuid.uuid4().hex[:8].upper()}"
    
    # Guardamos temporalmente en sesión o en una tabla de pagos de suscripción si lo deseas,
    # o usamos las credenciales globales de Wompi de la plataforma para recibir el pago del SaaS.
    wompi_public_key = getattr(settings, 'WOMPI_PUBLIC_KEY_PLATAFORMA', 'pub_prod_TU_LLAVE_PUBLICA_AQUI')
    
    redirect_url = request.build_absolute_uri(reverse('suscripcion_exitosa'))
    redirect_url_encoded = quote(redirect_url, safe='')

    checkout_url = (
        "https://checkout.wompi.co/p/"
        f"?public-key={wompi_public_key.strip()}"
        f"&currency=COP"
        f"&amount-in-cents={plan_info['precio_centavos']}"
        f"&reference={referencia_suscripcion}"
        f"&redirect-url={redirect_url_encoded}"
        f"&collect-customer-email=true"
    )
    
    # Opcional: puedes guardar la referencia pendiente en el perfil o un modelo de TransaccionSuscripcion
    perfil.ultima_referencia_pago = referencia_suscripcion
    perfil.plan_pendiente = plan_slug
    perfil.save()

    return redirect(checkout_url)


@csrf_exempt
def webhook_wompi_suscripciones(request):
    """
    Webhook exclusivo para recibir la confirmación de pago de las suscripciones de JoyasApp.
    Actualiza la vigencia del perfil por 30 días adicionales de forma automática.
    """
    if request.method != 'POST':
        return HttpResponse("Método no permitido", status=405)
        
    try:
        data = json.loads(request.body)
        transaccion = data.get('data', {}).get('transaction', {})
        referencia_orden = transaccion.get('reference', '')
        estado_wompi = transaccion.get('status')
        
        # Validamos que sea una referencia de suscripción de nuestro SaaS
        if referencia_orden.startswith("SUB-") and estado_wompi == 'APPROVED':
            # Extraemos el ID de usuario desde la referencia (ej: SUB-5-A1B2C3D4 -> ID 5)
            partes = referencia_orden.split('-')
            if len(partes) >= 2:
                user_id = partes[1]
                from django.contrib.auth.models import User
                from django.utils import timezone
                from datetime import timedelta
                
                usuario = User.objects.filter(id=user_id).first()
                if usuario:
                    perfil, _ = Perfil.objects.get_or_create(user=usuario)
                    
                    # Determinamos qué plan pagó basándonos en el monto o en la referencia guardada
                    monto_en_centavos = transaccion.get('amount_in_cents', 0)
                    
                    plan_asignado = 'basico'
                    if monto_en_centavos == 8990000:
                        plan_asignado = 'semi'
                    elif monto_en_centavos == 14990000:
                        plan_asignado = 'premium'
                        
                    # Actualizamos la vigencia sumando 30 días
                    hoy = timezone.now()
                    if perfil.fecha_expiracion_suscripcion and perfil.fecha_expiracion_suscripcion > hoy:
                        perfil.fecha_expiracion_suscripcion += timedelta(days=30)
                    else:
                        perfil.fecha_expiracion_suscripcion = hoy + timedelta(days=30)
                        
                    perfil.plan_activo = plan_asignado
                    perfil.suscripcion_activa = True
                    perfil.save()
                    
        return HttpResponse("Webhook suscripción recibido", status=200)
        
    except Exception as e:
        print(f"💥 Error en Webhook Wompi Suscripciones: {str(e)}")
        return HttpResponse("Error interno procesado", status=200)


def suscripcion_exitosa(request):
    """Vista de retorno cuando el dueño de la joyería paga su plan de JoyasApp con éxito"""
    messages.success(request, "¡Felicidades! Tu suscripción ha sido activada/renovada exitosamente por 30 días.")
    return redirect('dashboard') # Ajusta a la vista principal del panel de administración del SaaS
