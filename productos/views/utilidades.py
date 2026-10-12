from decimal import Decimal

from ..models import (
    Producto,
    ProductoVariante,
    Perfil,
    Pedido,
)
import os
from django.conf import settings
from django.contrib.staticfiles import finders
import urllib.parse

# =========================================================================
# ⚙️ BLOQUE 1: UTILIDADES, CONVERSORES Y HELPERS MATEMÁTICOS/FISCALES
# (Siempre van al inicio porque las vistas de abajo los llaman)
# =========================================================================

def safe_int(valor, default=1):
    try:
        return int(valor)
    except (ValueError, TypeError):
        return default

def safe_float(val):
    if not val:
        return None
    try:
        # Esto limpia el número por si el celular envía comas en vez de puntos
        return float(str(val).replace(',', '.'))
    except ValueError:
        return None

def obtener_variante(producto, color, talla):
    return ProductoVariante.objects.filter(
        producto=producto,
        color=color,
        talla=talla
    ).first()

def obtener_perfil_pedido(pedido):
    """Helper para obtener el Perfil/Tenant (Logo, Redes, Nombre de tienda)"""
    try:
        return Perfil.objects.get(user=pedido.usuario)
    except Perfil.DoesNotExist:
        return None

def obtener_configuracion_negocio(pedido):
    """Helper para obtener la parametrización fiscal, envíos e integraciones"""
    try:
        # 🚀 Cambiamos ConfiguracionNegocio por Perfil, que sí existe y maneja tus valores
        return Perfil.objects.filter(usuario=pedido.usuario).first()
    except Exception:
        return None

def generar_numero_orden(usuario=None):
    """
    Genera un consecutivo global inmune a choques de concurrencia (SaaS seguro).
    Garantiza que el número sea único en toda la base de datos.
    """
    from django.db.models import Max

    # 1. Buscamos SIEMPRE a nivel global (evita choques entre joyerías)
    # Excluimos strings vacíos o nulos por seguridad
    ultimo_pedido = Pedido.objects.exclude(numero_orden="").order_by('-id').first()

    numero = 1

    if ultimo_pedido and ultimo_pedido.numero_orden:
        try:
            # Extrae la parte numérica después del guion (ej: "FAC-000001" -> "000001")
            partes = ultimo_pedido.numero_orden.strip().split('-')
            consecutivo = partes[-1]

            if consecutivo.isdigit():
                numero = int(consecutivo) + 1
            else:
                raise ValueError
        except Exception:
            # Si el formato se rompió por alguna razón, usamos el conteo global total como respaldo + 1
            numero = Pedido.objects.count() + 1
    else:
        # Si la tabla está absolutamente vacía
        numero = 1

    # 2. ESCUDO DE CONCURRENCIA: Doble verificación antes de retornar
    # Si por alguna razón el número calculado ya existe, lo autoincrementamos en bucle hasta que esté libre
    while Pedido.objects.filter(numero_orden=f"FAC-{numero:06d}").exists():
        numero += 1

    return f"FAC-{numero:06d}"

def calcular_envio(ciudad=None, subtotal=0):
    """
    JoyasApp

    El costo del envío NO se calcula automáticamente.

    Cada joyero define posteriormente el valor del envío
    según la transportadora, ciudad o país.

    El pedido inicia con envío = 0.
    """
    return Decimal("0")

def calcular_precio_producto(producto, cantidad):
    cantidad = Decimal(str(cantidad))

    # Venta por gramos
    if producto.tipo_venta == "gramo":
        peso = Decimal(str(producto.peso_producto or 0))
        total_gramos = cantidad * peso

        if total_gramos >= 12:
            precio = producto.precio_por_gramo_mayor or 0
        elif total_gramos >= 6:
            precio = producto.precio_por_gramo_semimayor or 0
        else:
            precio = producto.precio_por_gramo_detal or 0

        return Decimal(str(precio)), total_gramos

    # Venta por unidades
    if cantidad >= 12:
        precio = producto.precio_mayor or producto.precio_detal or 0
    elif cantidad >= 6:
        precio = producto.precio_semimayor or producto.precio_detal or 0
    else:
        precio = producto.precio_detal or 0

    return Decimal(str(precio)), None

def calcular_totales_con_reglas_fiscales(pedido):
    """
    Única función centralizada en views.py para procesar el pedido.
    Recibe el objeto 'pedido' directamente, lo que facilita su uso en cualquier vista.
    """
    perfil = pedido.usuario.perfil  # Accedemos al centro de mando del joyero
    subtotal_productos = Decimal('0.00')
    
    # 1. Sumamos el valor base de los ítems del pedido
    for item in pedido.items.all():
        subtotal_productos += Decimal(str(item.subtotal or 0.00))

    # 🎁 REGLA DE CAMPAÑA: ¿El joyero encendió el interruptor de promociones?
    descuento_aplicado = Decimal('0.00')
    if perfil.aplicar_descuentos and perfil.porcentaje_descuento_promo > 0:
        porcentaje_desc = Decimal(str(perfil.porcentaje_descuento_promo)) / Decimal('100.00')
        descuento_aplicado = subtotal_productos * porcentaje_desc
    
    # Base sobre la que se calcula el IVA tras el descuento de campaña
    base_gravable = subtotal_productos - descuento_aplicado

    # ⚖️ REGLA FISCAL: ¿Es responsable de IVA?
    iva_calculado = Decimal('0.00')
    if perfil.responsable_iva and perfil.porcentaje_iva > 0:
        porcentaje_iva = Decimal(str(perfil.porcentaje_iva)) / Decimal('100.00')
        iva_calculado = base_gravable * porcentaje_iva

    retefuente_calculada = Decimal('0.00')
    if hasattr(perfil, 'aplicar_retefuente') and perfil.aplicar_retefuente:
        # Nota: Aquí puedes validar si el cliente del pedido es retenedor si tienes ese campo
        porcentaje_rete = Decimal(str(perfil.porcentaje_retefuente or 2.50)) / Decimal('100.00')
        retefuente_calculada = base_gravable * porcentaje_rete

    # 🚚 REGLA DE ENVÍOS: Control de costos y umbral gratis
    costo_envio = Decimal('0.00')
    if perfil.cobrar_envio:
        costo_envio = Decimal(str(perfil.costo_envio_estandar or 0.00))
        
        # Si el negocio configuró un tope de envío gratis y la base lo supera, queda en 0
        if perfil.envio_gratis_desde and base_gravable >= Decimal(str(perfil.envio_gratis_desde)):
            costo_envio = Decimal('0.00')

    # 💾 Guardamos de forma limpia los resultados en el Pedido
    pedido.descuento_total = descuento_aplicado
    pedido.aplica_iva = perfil.responsable_iva  # Sincronizamos el estado del IVA
    pedido.iva = iva_calculado
    pedido.costo_envio = costo_envio
    pedido.total = base_gravable + iva_calculado + costo_envio
    pedido.save()
    
    return pedido

def calcular_totales_pedido(pedido, perfil, items):
    """
    Motor fiscal único para JoyasApp. Centraliza las matemáticas de IVA,
    descuentos, envíos y retenciones para evitar duplicidad.
    """
    subtotal_base = sum(Decimal(str(item.subtotal or 0)) for item in items)
    
    # 1. Descuentos promocionales del negocio o del pedido
    porcentaje_desc = getattr(pedido, 'porcentaje_descuento', 0) or 0
    if not porcentaje_desc and perfil and getattr(perfil, 'aplicar_descuentos', False):
        porcentaje_desc = getattr(perfil, 'porcentaje_descuento_promo', 0) or 0
    descuento_total = subtotal_base * (Decimal(str(porcentaje_desc)) / Decimal('100')) if porcentaje_desc else Decimal('0')

    # =========================================================================
    # 🎯 2. LOGÍSTICA Y ENVÍOS (CORREGIDO CON PRIORIDAD DE RECOGIDA)
    # =========================================================================
    tipo_envio = getattr(pedido, 'tipo_envio', '').lower() if pedido.tipo_envio else ''
    
    if tipo_envio == 'recogida':
        # Prioridad Absoluta: Si recoge en tienda, el envío es $0, sin importar el monto de compra
        envio = Decimal('0')
    else:
        # Si es domicilio/transportadora, evaluamos las reglas dinámicas del negocio
        envio = Decimal(str(getattr(pedido, 'costo_envio', 0) or 0))
        if envio == 0 and perfil and getattr(perfil, 'cobrar_envio', False):
            limite_gratis = Decimal(str(getattr(perfil, 'envio_gratis_desde', 0) or 0)) # Ej: 300000
            
            if subtotal_base < limite_gratis:
                # Si no llega al mínimo, se cobra la tarifa estándar (Ej: 15000)
                envio = Decimal(str(getattr(perfil, 'costo_envio_estandar', 0) or 0))
            else:
                # Si supera el mínimo, el envío se fuerza a $0 (Envío Gratis)
                envio = Decimal('0')
    # =========================================================================

    # 3. IVA (Cálculo limpio y determinista desde las reglas del perfil)
    es_responsable_iva = perfil and getattr(perfil, 'responsable_iva', False)
    mostrar_iva_disc = perfil and getattr(perfil, 'mostrar_iva_discriminado', False)
    porcentaje_iva = Decimal(str(getattr(perfil, 'porcentaje_iva', 19) or 19))
    
    if es_responsable_iva:
        if mostrar_iva_disc:
            # IVA Incluido: Se desglosa matemáticamente del precio base
            iva_total = subtotal_base - (subtotal_base / (Decimal('1') + (porcentaje_iva / Decimal('100'))))
            subtotal_render = subtotal_base - iva_total
            total_final = subtotal_base + envio - descuento_total
        else:
            # IVA Adicional: Se calcula por encima del subtotal
            iva_total = subtotal_base * (porcentaje_iva / Decimal('100'))
            subtotal_render = subtotal_base
            total_final = subtotal_base + iva_total + envio - descuento_total
    else:
        # Si la empresa no es responsable de IVA, el impuesto se apaga ($0)
        iva_total = Decimal('0')
        subtotal_render = subtotal_base
        total_final = subtotal_base + envio - descuento_total

    # 4. Retenciones (Se restan del total final si disminuyen el pago exigible)
    retefuente_total = sum(Decimal(str(item.retefuente or 0)) for item in items)
    total_final = max(Decimal('0'), total_final - retefuente_total)

    return {
        'subtotal': subtotal_render,
        'iva_total': iva_total,
        'porcentaje_iva': porcentaje_iva,
        'es_responsable_iva': es_responsable_iva,
        'mostrar_iva_discriminado': mostrar_iva_disc,
        'descuento_total': descuento_total,
        'porcentaje_descuento': porcentaje_desc,
        'retefuente_total': retefuente_total,
        'envio': envio,
        'costo_envio': envio, # Duplicado por compatibilidad con tus templates antiguos
        'total_final': total_final,
    }

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

# ────────────────────────────────────────────────────────────────
# 📱 ENLACE DE WHATSAPP
# ────────────────────────────────────────────────────────────────
def generar_link_whatsapp(telefono, mensaje=""):
    """Genera una URL codificada para enviar un mensaje directo por WhatsApp."""
    if not telefono:
        return ""

    # Extrae solo los números del teléfono
    numero_limpio = "".join(filter(str.isdigit, str(telefono)))

    # Codifica espacios y caracteres especiales del mensaje
    mensaje_codificado = urllib.parse.quote(mensaje)

    return f"https://wa.me/{numero_limpio}?text={mensaje_codificado}"

