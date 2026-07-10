from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from django.utils.text import slugify
import uuid
from cloudinary.models import CloudinaryField
import random
from decimal import Decimal
from django.db import models, transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver

class Perfil(models.Model):
    PLANES = [
    ('basico', 'Básico'),
    ('pro', 'Pro'),
    ('premium', 'Premium'),
]
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='perfil'
    )
    # EMPRESA
    nombre_tienda = models.CharField(max_length=120)
    banner = models.ImageField(upload_to='banners/', blank=True, null=True)
    banner_texto = models.TextField(blank=True, default="")
    logo = models.ImageField(upload_to='logos/', blank=True, null=True)
    color_primario = models.CharField(max_length=7, default="#122216")
    color_secundario = models.CharField(max_length=7, default='#000000')

    # CONTACTO
    whatsapp = models.CharField(max_length=20, blank=True, null=True)
    email_empresa = models.EmailField(blank=True, null=True)

    # DIRECCIÓN
    direccion = models.CharField(max_length=200, blank=True, null=True)
    ciudad = models.CharField(max_length=100, blank=True, null=True)

    # REDES
    instagram = models.CharField(max_length=150, blank=True, null=True)
    facebook = models.CharField(max_length=150, blank=True, null=True)
    tiktok = models.CharField(max_length=150, blank=True, null=True)

    # FACTURACIÓN
    nit = models.CharField(max_length=50,blank=True,null=True,verbose_name="NIT")

    # PLAN SaaS
    plan = models.CharField(max_length=20,choices=PLANES,default='basico')

    activa = models.BooleanField(default=True)

    plan_vence = models.DateField(default=timezone.localdate)

    # DISEÑO
    color_principal = models.CharField(max_length=20,default="#000000",verbose_name="Color principal")

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    # 🔒 Identificador único y seguro para el Webhook del SaaS
    webhook_uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name="Webhook UUID")

    # 💳 PASARELAS DE PAGO INDEPENDIENTES (SAAS)
    wompi_public_key = models.CharField(max_length=255, blank=True, null=True, verbose_name="Wompi Public Key")
    mercadopago_access_token = models.CharField(max_length=255, blank=True, null=True, verbose_name="Mercado Pago Access Token")
    
    # 🚚 CONFIGURACIÓN DE ENVÍOS 
    costo_envio_estandar = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="Costo de Envío")

    # ⚖️ NUEVA CONFIGURACIÓN TRIBUTARIA (Indispensable para Facturación Legal)
    responsable_iva = models.BooleanField(default=False, verbose_name="¿Es Responsable de IVA?")
    porcentaje_iva = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('19.00'), verbose_name="Porcentaje de IVA (%)")
    mostrar_iva_discriminado = models.BooleanField(default=True, verbose_name="Mostrar IVA Discriminado en Factura")
    
    # Conexiones DIAN y Facturación Electrónica (Hitos para Siigo)
    prefijo_factura = models.CharField(max_length=10, blank=True, null=True, verbose_name="Prefijo de Facturación")
    consecutivo_actual = models.IntegerField(default=1, verbose_name="Siguiente Consecutivo")
    resolucion_dian = models.TextField(blank=True, null=True, verbose_name="Resolución DIAN")
    integracion_siigo_activa = models.BooleanField(default=False, verbose_name="Activar Sincronización con Siigo")

    # 🎁 CAMPAÑAS Y DESCUENTOS EN PROMO
    aplicar_descuentos = models.BooleanField(default=False, verbose_name="Activar Descuentos por Campaña / Temporada")
    porcentaje_descuento_promo = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Porcentaje Descuento Promoción (%)")

    # 🚚 CONTROL DE ENVÍOS AVANZADO
    cobrar_envio = models.BooleanField(default=False, verbose_name="¿Cobrar Envío?")
    envio_gratis_desde = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="Envío Gratis desde Monto")

    def __str__(self):
        return f"{self.user.username} - {self.nombre_tienda}"   

class Categoria(models.Model):
    nombre = models.CharField(max_length=100)
    usuario = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    nombre = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220,blank=True,null=True)
    referencia = models.CharField(max_length=50,blank=True,null=True,verbose_name="SKU")
    descripcion = models.TextField()
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE)
    destacado = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    stock = models.PositiveIntegerField(default=0)
    color = models.CharField(max_length=50, null=True, blank=True)
    talla = models.CharField(max_length=50, null=True, blank=True)
    colores_disponibles = models.JSONField(default=list, blank=True)
    tallas_disponibles = models.JSONField(default=list, blank=True)
    imagen_principal = CloudinaryField('imagen',blank=True,null=True)
    video = models.FileField(upload_to='productos/videos/', blank=True, null=True)
    certificado = models.FileField(upload_to='certificados/', blank=True, null=True)
    tipo_venta = models.CharField(max_length=20,choices=[('unidad', 'Por unidad'), ('gramo', 'Por gramos')],default='unidad')
    peso_producto = models.DecimalField(max_digits=6,decimal_places=2,null=True,blank=True,help_text="Peso del producto en gramos")
    precio_costo = models.DecimalField(max_digits=12,decimal_places=2,default=0,verbose_name="Precio de costo")
    @property
    def utilidad_detal(self):
        if self.precio_detal and self.precio_costo:
            return self.precio_detal - self.precio_costo
        return 0

    @property
    def utilidad_semimayor(self):
        if self.precio_semimayor and self.precio_costo:
            return self.precio_semimayor - self.precio_costo
        return 0

    @property
    def utilidad_mayor(self):
        if self.precio_mayor and self.precio_costo:
            return self.precio_mayor - self.precio_costo
        return 0
    
    @property
    def stock_total(self):

        if self.variantes.exists():
            return sum(
                v.stock
                for v in self.variantes.all()
        )

        return self.stock

    def __str__(self):
        return self.nombre
    
    precio_detal = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    precio_semimayor = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    precio_mayor = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cantidad_mayorista = models.IntegerField(default=6)
    # 🔥 PRECIOS POR GRAMO
    precio_por_gramo_detal = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    precio_por_gramo_semimayor = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    precio_por_gramo_mayor = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # 🔥 LÓGICA CENTRAL (LA IMPORTANTE)

    def precio_por_cantidad(self, cantidad):

    # 🔵 VENTA POR GRAMOS
        if self.tipo_venta == 'gramo':

            if not self.peso_producto:
                return 0  # seguridad

            total_gramos = cantidad * self.peso_producto

            if total_gramos >= 12:
                return self.precio_por_gramo_mayor or 0
            elif total_gramos >= 6:
                return self.precio_por_gramo_semimayor or 0
            else:
                return self.precio_por_gramo_detal or 0

    # 🟡 VENTA POR UNIDAD
        else:

            if cantidad >= 12:
                return self.precio_mayor
            elif cantidad >= 6:
                return self.precio_semimayor or self.precio_detal
            else:
                return self.precio_detal

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'referencia'],
                name='unique_referencia_por_usuario'
            ),
            models.UniqueConstraint(
                fields=['usuario', 'slug'],
                name='unique_slug_por_usuario'
            ),
        ]
    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.nombre)
            slug = base_slug
            contador = 1

            while Producto.objects.filter(
                slug=slug
             ).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{contador}"
                contador += 1

            self.slug = slug

        super().save(*args, **kwargs)

class ProductoImagen(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='imagenes')
    # 🚀 Cambiamos ImageField por CloudinaryField
    imagen = CloudinaryField('imagen', blank=True, null=True)

    def _str_(self):
        return f"Imagen de {self.producto.nombre}"

class ProductoVariante(models.Model):
    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name='variantes'
    )
    
    # Parámetros Base
    color = models.CharField(max_length=100, null=True, blank=True)
    talla = models.CharField(max_length=50, null=True, blank=True)
    stock = models.PositiveIntegerField(default=0)
    precio_venta = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, blank=True, null=True)
    
    # 💎 NUEVOS PARÁMETROS DE ESPECIFICACIÓN (JSVE™)
    peso = models.DecimalField(max_digits=6, decimal_places=2, default=0.00, blank=True, null=True) # En gramos (ej: 4.50)
    codigo = models.CharField(max_length=50, blank=True, null=True) # SKU único por variante
    activo = models.BooleanField(default=True)
    
    # Foto específica de la variante (Usando Cloudinary o models.ImageField)
    foto = CloudinaryField('imagen_variante', blank=True, null=True) 

    class Meta:
        unique_together = ('producto', 'color', 'talla')

    def __str__(self):
        return f"{self.producto.nombre} | {self.color or 'U'}-{self.talla or 'U'} | ${self.precio_venta} ({self.stock} unds)"


class CarritoItem(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE)
    variante = models.ForeignKey(ProductoVariante, null=True, blank=True, on_delete=models.CASCADE)
    cantidad = models.PositiveIntegerField(default=1)
    session_key = models.CharField(max_length=40)
    fecha_creacion = models.DateTimeField(default=timezone.now)
    color = models.CharField(max_length=50, null=True, blank=True)
    talla = models.CharField(max_length=50, null=True, blank=True)

    def precio_aplicado(self):
        """
        Retorna el precio unitario correcto (por gramo o por escala de unidad).
        """
        if self.producto.tipo_venta == 'gramo':
            # Usa el precio por gramo original configurado en el producto
            return Decimal(str(self.producto.precio_por_gramo_detal or 0))
        
        # Productos por unidad: usa tus escalas y rescata con precio_detal si falla o da None
        try:
            precio = self.producto.precio_por_cantidad(self.cantidad)
            if precio is None:
                precio = self.producto.precio_detal or 0
            return Decimal(str(precio))
        except Exception:
            return Decimal(str(self.producto.precio_detal or 0))

    def subtotal(self):
        """
        Calcula el subtotal real multiplicando por gramos o por unidades fijas.
        """
        precio = self.precio_aplicado()
        cantidad_val = Decimal(str(self.cantidad or 1))

        if self.producto.tipo_venta == 'gramo':
            peso_val = Decimal(str(self.producto.peso_producto or 0))
            total_gramos = cantidad_val * peso_val
            return total_gramos * precio

        return cantidad_val * precio

    def ahorro(self):
        """
        Calcula cuánto se está ahorrando el cliente según el tipo de venta.
        """
        precio_act = self.precio_aplicado()
        cantidad_val = Decimal(str(self.cantidad or 1))

        if self.producto.tipo_venta == 'gramo':
            peso_val = Decimal(str(self.producto.peso_producto or 0))
            total_gramos = cantidad_val * peso_val
            precio_or = Decimal(str(self.producto.precio_por_gramo_detal or 0))
            return (precio_or - precio_act) * total_gramos

        precio_or = Decimal(str(self.producto.precio_detal or 0))
        return (precio_or - precio_act) * cantidad_val

    def __str__(self):
        return f"{self.cantidad} x {self.producto.nombre}"

class Cliente(models.Model):
    # 🏢 AISLAMIENTO SAAS (Multi-tenant)
    usuario = models.ForeignKey(User, on_delete=models.CASCADE) # El dueño de la joyería
    
    # 👤 DATOS BÁSICOS e IDENTIFICACIÓN
    nombre = models.CharField(max_length=100)
    nit = models.CharField(max_length=50, blank=True, null=True)
    telefono = models.CharField(max_length=20, unique=True) # Llave maestra para WhatsApp
    email = models.EmailField(blank=True, null=True)
    ciudad = models.CharField(max_length=100, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)
    fecha_cumpleanos = models.DateField(blank=True, null=True)  # 🎂 Para campaña: Cumpleaños
    observaciones = models.TextField(blank=True)                # Notas internas del vendedor
    etiquetas = models.CharField(max_length=300, blank=True, help_text="Ej: Novias, Mayorista, Plata") # 🏷️ Tags
    
    # 🏢 CRM COLABORATIVO (Asignación interna)
    asesor = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="clientes_asignados"
    ) # Para joyerías con varios vendedores

    # 🔒 PRIVACIDAD Y SEGUIMIENTO
    acepta_whatsapp = models.BooleanField(default=True) # 📱 Control de bajas/Opt-in
    activo = models.BooleanField(default=True)

    # 💎 CLASIFICACIÓN Y DESCUENTOS ORIGINALES
    tipo_cliente = models.CharField(
        max_length=20,
        choices=[
            ('detalle', 'Detal'),
            ('vip', 'VIP'),
            ('mayorista', 'Mayorista'),
            ('distribuidor', 'Distribuidor'),
        ],
        default='detalle'
    )
    porcentaje_descuento = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # 📈 SEGMENTACIÓN CRM AVANZADA
    origen = models.CharField(
        max_length=30,
        default="WhatsApp",
        choices=[
            ("WhatsApp", "WhatsApp"),
            ("Tienda", "Tienda"),
            ("Instagram", "Instagram"),
            ("Facebook", "Facebook"),
            ("TikTok", "TikTok"),
            ("Página Web", "Página Web"),
        ]
    )
    
    estado = models.CharField(
        max_length=20,
        default="Nuevo",
        choices=[
            ("Nuevo", "Nuevo"),
            ("Activo", "Activo"),
            ("Frecuente", "Frecuente"),
            ("VIP", "VIP"),
            ("Inactivo", "Inactivo"),
        ]
    )
    
    nivel = models.CharField(
        max_length=20,
        default="Bronce",
        choices=[
            ("Bronce", "Bronce"),
            ("Plata", "Plata"),
            ("Oro", "Oro"),
            ("Diamante", "Diamante"),
        ]
    )

    # 📊 CONTROL Y MÉTRICAS FINANCIERAS
    total_compras = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    saldo_pendiente = models.DecimalField(max_digits=12, decimal_places=2, default=0.00) # 🪙 Control de apartados
    numero_pedidos = models.IntegerField(default=0)
    ultima_compra = models.DateTimeField(null=True, blank=True)
    
    # 🎯 PREFERENCIAS DE CONSUMO (Historial inteligente)
    categoria_favorita = models.CharField(max_length=100, blank=True, null=True)
    variante_favorita_resumen = models.CharField(max_length=150, blank=True, null=True)
    
    # 🔄 RETENCIÓN Y REACTIVACIÓN FUTURA
    ultima_categoria = models.CharField(max_length=100, blank=True, null=True)
    ultima_variante = models.CharField(max_length=150, blank=True, null=True)
    ultima_factura = models.CharField(max_length=40, blank=True, null=True)
    dias_sin_comprar = models.IntegerField(default=0) # 🎁 Base para el automatizador
    ultimo_contacto = models.DateTimeField(blank=True, null=True)
    proxima_tarea = models.DateTimeField(blank=True, null=True)

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def _str_(self):
        return f"[{self.nivel}] {self.nombre} - {self.telefono}"
        
    @property
    def ticket_promedio(self):
        """Calcula dinámicamente el valor promedio por pedido"""
        if self.numero_pedidos > 0:
            return self.total_compras / self.numero_pedidos
        return 0.00

    def actualizar_ciclo_y_rangos(self):
        """
        🚀 MÓDULO INTELIGENTE POST-VENTA
        Evalúa automáticamente la inactividad, promueve a VIP y gestiona los rangos del CRM.
        """
        # 1. Control de Días sin comprar (Reactivación)
        if self.ultima_compra:
            delta = timezone.now() - self.ultima_compra
            self.dias_sin_comprar = delta.days
            if self.dias_sin_comprar > 90:
                self.estado = "Inactivo"
        else:
            self.dias_sin_comprar = 0

        # 2. Asignación Inteligente de Niveles (Gamificación basada en tus metas de negocio)
        if self.total_compras >= 5000000 or self.numero_pedidos >= 20:
            self.nivel = "Diamante"
            self.estado = "VIP"
            self.tipo_cliente = "vip" # Sincroniza con tu campo original
        elif self.total_compras >= 3000000 or self.numero_pedidos >= 10:
            self.nivel = "Oro"
            if self.dias_sin_comprar <= 90: 
                self.estado = "Frecuente"
        elif self.total_compras >= 1000000 or self.numero_pedidos >= 5:
            self.nivel = "Plata"
            if self.dias_sin_comprar <= 90: 
                self.estado = "Activo"
        else:
            self.nivel = "Bronce"
            if self.numero_pedidos == 1: 
                self.estado = "Nuevo"

    # === 📱 MÓDULO DE AUTOMATIZACIÓN DE WHATSAPP (ENLACES DINÁMICOS MULTI-TENANT) ===
    
    @property
    def link_whatsapp_cumpleanos(self):
        """Mensaje de felicitación + gancho de fidelización (10% de descuento)"""
        import urllib.parse
        
        # 🏢 Intentamos traer el nombre de la tienda configurado en su perfil SaaS
        try:
            # Reemplaza 'perfil' por el related_name exacto de tu modelo de configuración
            nombre_negocio = self.usuario.perfil.nombre_tienda
        except AttributeError:
            nombre_negocio = "nuestra joyería"
            
        if not nombre_negocio: # Por si el campo está vacío en la base de datos
            nombre_negocio = "nuestra joyería"
        
        texto = (
            f"¡Hola, {self.nombre}! 🌟 De parte de todo el equipo de {nombre_negocio}, "
            f"te deseamos un espectacular feliz cumpleaños. 🎂 En tu día, queremos "
            f"celebrarte con un obsequio especial: un 10% de descuento en tu próxima "
            f"joya favorita. 💎 ¡Que pases un día maravilloso!"
        )
        return f"https://wa.me/{self.telefono}?text={urllib.parse.quote(texto)}"

    @property
    def link_whatsapp_saldo(self):
        """Recordatorio sutil y elegante de saldo pendiente"""
        import urllib.parse
        
        try:
            nombre_negocio = self.usuario.perfil.nombre_tienda
        except AttributeError:
            nombre_negocio = "nuestra joyería"
            
        if not nombre_negocio:
            nombre_negocio = "nuestra joyería"
        
        texto = (
            f"Hola, {self.nombre}. ✨ Esperamos que estés muy bien. Nos comunicamos de "
            f"{nombre_negocio} para actualizar tu estado de cuenta. Actualmente presentas "
            f"un saldo pendiente de ${self.saldo_pendiente:,.0f}. 📝 Si deseas realizar el pago "
            f"por transferencia o reportar tu comprobante, quedamos atentos por este medio. "
            f"¡Muchas gracias por tu confianza!"
        )
        return f"https://wa.me/{self.telefono}?text={urllib.parse.quote(texto)}"

    @property
    def link_whatsapp_inactivo(self):
        """Campaña de reactivación para clientes perdidos (+90 días)"""
        import urllib.parse
        
        try:
            nombre_negocio = self.usuario.perfil.nombre_tienda
        except AttributeError:
            nombre_negocio = "nuestra joyería"
            
        if not nombre_negocio:
            nombre_negocio = "nuestra joyería"
        
        texto = (
            f"¡Hola, {self.nombre}! 🥰 Hace tiempo que no sabemos de ti en {nombre_negocio} "
            f"y te extrañamos. Nos acaban de llegar unas piezas hermosas en nuestra última "
            f"colección que van perfectas con tu estilo. ✨ Te dejamos un envío gratis "
            f"válido por esta semana si deseas consentirte de nuevo. ¡Un abrazo!"
        )
        return f"https://wa.me/{self.telefono}?text={urllib.parse.quote(texto)}"
    
class Pedido(models.Model):

    # 🔹 IDENTIFICACIÓN
    usuario = models.ForeignKey(User,on_delete=models.CASCADE,null=True,blank=True)

    tienda = models.ForeignKey(Perfil, on_delete=models.CASCADE, null=True, blank=True)

    numero_orden = models.CharField(max_length=20, unique=True)

    token_publico = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    fecha = models.DateTimeField(auto_now_add=True)

    cliente = models.ForeignKey('Cliente',on_delete=models.SET_NULL,null=True,blank=True,related_name='pedidos')

    # 🔹 CLIENTE
    cliente_nombre = models.CharField(max_length=100, blank=True, null=True)
    cliente_nit = models.CharField(max_length=50, blank=True, null=True)
    cliente_direccion = models.CharField(max_length=200, blank=True, null=True)
    cliente_telefono = models.CharField(max_length=20, blank=True, null=True)
    cliente_email = models.EmailField(blank=True, null=True)
    cliente_ciudad = models.CharField(max_length=100, blank=True, null=True)

    # 🔹 ESTADO Y TIPO
    ESTADOS = [('pendiente', 'Pendiente'),('pagado', 'Pagado'),('enviado', 'Enviado'),('vencido', 'Vencido'),]
    estado = models.CharField(max_length=20,choices=ESTADOS,default='pendiente')
    es_credito = models.BooleanField(default=False)
    TIPO_PAGO = (('contado', 'Contado'),('credito', 'Crédito'),)
    tipo_pago = models.CharField(max_length=10,choices=TIPO_PAGO,default='contado')
    saldo_pendiente = models.DecimalField(max_digits=12,decimal_places=2,default=0)
    fecha_limite = models.DateField(null=True,blank=True)
    fecha_pago = models.DateField(null=True, blank=True)

    # 🔍 Auditoría y Soporte de Transacciones
    mercadopago_payment_id = models.CharField(max_length=100, blank=True, null=True, verbose_name="ID Pago Mercado Pago")
    wompi_transaction_id = models.CharField(max_length=100, blank=True, null=True, verbose_name="ID Transacción Wompi")

    # 🔹 IMPUESTOS Y DESCUENTOS
    aplica_iva = models.BooleanField(default=False)  # 👈 ESTE AGREGAS
    es_retenedor = models.BooleanField(default=False)
    descuento_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    porcentaje_descuento = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tipo_envio = models.CharField(max_length=20,choices=[('recogida', 'Recogida en tienda'),('domicilio', 'Domicilio'),('transportadora', 'Transportadora'),],default='recogida')
    costo_envio = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    direccion_envio = models.CharField(max_length=255, blank=True, null=True)
    ciudad_envio = models.CharField(max_length=100, blank=True, null=True)

     # 🔹 TOTAL
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # 🔹 COSTOS (🔥 PRO)
    costo_material = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    costo_mano_obra = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # Asegúrate de tener estos campos si usas créditos/descuentos en la sincronización:
    descuento_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    saldo_pendiente = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    @property
    def utilidad(self):
        return self.total - (self.costo_material + self.costo_mano_obra)
    
    @property
    def costo_total(self):
        return self.costo_material + self.costo_mano_obra
    
    @property
    def total_limpio(self):
        """Devuelve el total como un entero para usar fácilmente en HTML o pasarelas"""
        return int(self.total)

    def _str_(self):
        return self.numero_orden

    def sincronizar_pedido(self):
        """
        🔥 PUNTO ÚNICO DE VERDAD FINANCIERA
        Centraliza el cálculo matemático total de la orden de forma atómica.
        """
        with transaction.atomic():
            items_relacionados = self.items.select_related('producto').all()
            
            subtotal_items = sum(item.subtotal for item in items_relacionados)
            descuento_items = sum(item.descuento for item in items_relacionados)
            iva_items = sum(item.iva for item in items_relacionados)
            
            # Recalcular costo real de manufactura (precio_costo de cada producto)
            self.costo_material = sum(Decimal(str(item.producto.precio_costo or 0)) * item.cantidad for item in items_relacionados)
            
            # Sumar costos adicionales si tuvieras fletes en el modelo base (ej: costo_envio)
            envio = Decimal(str(getattr(self, 'costo_envio', 0) or 0))
            
            self.total = subtotal_items + iva_items + envio - descuento_items
            self.descuento_total = descuento_items

            # Sistema de cuentas por cobrar (Créditos / Apartados)
            if getattr(self, 'tipo_pago', 'contado') == 'credito' or getattr(self, 'es_credito', False):
                total_abonos = self.abonos.aggregate(suma=Sum('monto'))['suma'] or Decimal('0.00')
                self.saldo_pendiente = max(Decimal('0.00'), self.total - total_abonos)
                
                if getattr(self, 'cliente', None):
                    self.cliente.saldo_pendiente = self.saldo_pendiente
                    self.cliente.save(update_fields=['saldo_pendiente'])
            else:
                self.saldo_pendiente = Decimal('0.00')

            self.save(update_fields=['total', 'costo_material', 'descuento_total', 'saldo_pendiente'])


# ==========================================
# 💵 CONTROL DE INGRESOS: ABONOS
# ==========================================

class Abono(models.Model):
    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.CASCADE,
        related_name='abonos'
    )
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    fecha = models.DateField(auto_now_add=True)

    def _str_(self):
        return f"Abono {self.monto} - Pedido {self.pedido.numero_orden}"

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            # Sincroniza automáticamente el saldo pendiente del pedido al agregar/editar abonos
            self.pedido.sincronizar_pedido()

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            pedido_padre = self.pedido
            super().delete(*args, **kwargs)
            # Si se elimina un abono, recalculamos para subir el saldo pendiente de inmediato
            pedido_padre.sincronizar_pedido()


# ==========================================
# 📦 DETALLE DE FACTURA: PEDIDOITEM
# ==========================================

class PedidoItem(models.Model):
    usuario = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name='items')
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE)
    variante = models.ForeignKey(ProductoVariante, null=True, blank=True, on_delete=models.SET_NULL)
    cantidad = models.PositiveIntegerField()
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    descuento = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    iva = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    retefuente = models.DecimalField(max_digits=12, decimal_places=2, default=0)  
    total_final = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_gramos = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def calcular_subtotal(self):
        if self.producto.tipo_venta == 'gramo':
            peso_unitario = Decimal(str(self.producto.peso_producto or 0))
            self.total_gramos = Decimal(str(self.cantidad)) * peso_unitario
            return self.total_gramos * self.precio
        
        self.total_gramos = 0
        return self.cantidad * self.precio  

    def save(self, *args, **kwargs):
        self.subtotal = self.calcular_subtotal()
        
        if getattr(self.pedido, 'aplica_iva', False):
            self.iva = self.subtotal * Decimal('0.19')
        else:
            self.iva = 0

        self.total_final = self.subtotal + self.iva - self.descuento
        
        # Validación estricta antes de escribir en Base de Datos para evitar ventas sin stock
        if self.variante and self.variante.stock < self.cantidad:
            raise ValidationError(f"No hay stock suficiente. Disponibles: {self.variante.stock} unidades de esta variante.")
        elif not self.variante and self.producto.stock < self.cantidad:
            raise ValidationError(f"No hay stock suficiente. Disponibles: {self.producto.stock} unidades de este producto.")

        super().save(*args, **kwargs)


# ==========================================
# 📉 CONTROL DE EGRESOS: GASTOS
# ==========================================
class Gasto(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.CASCADE)
    nombre = models.CharField(max_length=150)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    categoria = models.CharField(max_length=50, null=True, blank=True) 
    observaciones = models.TextField(null=True, blank=True) # ✅ Añadido para auditoría
    activo = models.BooleanField(default=True)              # ✅ NUEVO: Borrado lógico contable
    fecha = models.DateTimeField(auto_now_add=True)

    def _str_(self):
        return f"{self.nombre} - {self.monto}"
 
# ==========================================
# 🛡️ ESCUDO DE SEÑALES (MÓDULO LOGÍSTICO)
# ==========================================

@receiver(pre_save, sender=PedidoItem)
def capturar_estado_anterior_inventario(sender, instance, **kwargs):
    if instance.pk:
        try:
            instance._estado_previo = PedidoItem.objects.get(pk=instance.pk)
        except PedidoItem.DoesNotExist:
            instance._estado_previo = None
    else:
        instance._estado_previo = None


@receiver(post_save, sender=PedidoItem)
def procesar_ajuste_inventario_dinamico(sender, instance, created, **kwargs):
    with transaction.atomic():
        # CASO A: Registro de una nueva venta
        if created:
            if instance.variante:
                if instance.variante.stock < instance.cantidad:
                    raise ValidationError("Stock insuficiente en la variante seleccionada.")
                instance.variante.stock -= instance.cantidad
                instance.variante.save(update_fields=['stock'])
            else:
                if instance.producto.stock < instance.cantidad:
                    raise ValidationError("Stock insuficiente en el inventario general.")
                instance.producto.stock -= instance.cantidad
                instance.producto.save(update_fields=['stock'])
                
        # CASO B: Corrección de factura (Edición de ítem)
        else:
            previo = getattr(instance, '_estado_previo', None)
            if not previo:
                return

            # Si el usuario cambió de variante/talla/color en la edición
            if previo.variante != instance.variante:
                # Devolución a la variante anterior
                if previo.variante:
                    previo.variante.stock += previo.cantidad
                    previo.variante.save(update_fields=['stock'])
                else:
                    previo.producto.stock += previo.cantidad
                    previo.producto.save(update_fields=['stock'])

                # Descuento a la nueva variante elegida
                if instance.variante:
                    if instance.variante.stock < instance.cantidad:
                        raise ValidationError("Stock insuficiente en la nueva variante.")
                    instance.variante.stock -= instance.cantidad
                    instance.variante.save(update_fields=['stock'])
                else:
                    if instance.producto.stock < instance.cantidad:
                        raise ValidationError("Stock insuficiente en el inventario general.")
                    instance.producto.stock -= instance.cantidad
                    instance.producto.save(update_fields=['stock'])
            
            # Si mantuvo la variante pero alteró las cantidades numéricas
            else:
                diferencia = instance.cantidad - previo.cantidad
                if instance.variante:
                    if instance.variante.stock < diferencia:
                        raise ValidationError("La cantidad solicitada supera las existencias de la variante.")
                    instance.variante.stock -= diferencia
                    instance.variante.save(update_fields=['stock'])
                else:
                    if instance.producto.stock < diferencia:
                        raise ValidationError("La cantidad solicitada supera las existencias del inventario general.")
                    instance.producto.stock -= diferencia
                    instance.producto.save(update_fields=['stock'])

        # Se lanza la sincronización financiera consolidada una única vez
        instance.pedido.sincronizar_pedido()


@receiver(post_delete, sender=PedidoItem)
def devolver_inventario_por_eliminacion(sender, instance, **kwargs):
    with transaction.atomic():
        if instance.variante:
            instance.variante.stock += instance.cantidad
            instance.variante.save(update_fields=['stock'])
        else:
            instance.producto.stock += instance.cantidad
            instance.producto.save(update_fields=['stock'])
            
        # Sincroniza los totales financieros tras la eliminación del ítem
        instance.pedido.sincronizar_pedido()
    