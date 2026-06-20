from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from django.utils.text import slugify
import uuid
from cloudinary.models import CloudinaryField
import random
from decimal import Decimal

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
    imagen = models.ImageField(upload_to='productos/galeria/')

    def __str__(self):
        return f"Imagen de {self.producto.nombre}"


class ProductoVariante(models.Model):
    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name='variantes'
    )

    color = models.CharField(
        max_length=100,
        null=True,
        blank=True
    )

    talla = models.CharField(
        max_length=50,
        null=True,
        blank=True
    )

    stock = models.PositiveIntegerField(
        default=0
    )

    class Meta:
        unique_together = (
            'producto',
            'color',
            'talla'
        )

    def _str_(self):
        return f"{self.producto.nombre} - {self.color or ''} {self.talla or ''}"

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

    def _str_(self):
        return f"{self.cantidad} x {self.producto.nombre}"

class Cliente(models.Model):

    usuario = models.ForeignKey(User, on_delete=models.CASCADE)
    nombre = models.CharField(max_length=100)
    telefono = models.CharField(max_length=20, unique=True)
    email = models.EmailField(blank=True, null=True)

    ciudad = models.CharField(max_length=100, blank=True, null=True)
    direccion = models.CharField(max_length=255, blank=True, null=True)

    # 💎 CLASIFICACIÓN
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

    # 💰 DESCUENTO AUTOMÁTICO
    porcentaje_descuento = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0
    )

    # 📊 CONTROL
    total_compras = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    numero_pedidos = models.IntegerField(default=0)

    ultima_compra = models.DateTimeField(null=True, blank=True)

    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nombre} - {self.telefono}"
    
class Pedido(models.Model):

    # 🔹 IDENTIFICACIÓN
    usuario = models.ForeignKey(User,on_delete=models.CASCADE,null=True,blank=True)

    tienda = models.ForeignKey(Perfil, on_delete=models.CASCADE, null=True, blank=True)

    numero_orden = models.CharField(max_length=20, unique=True)

    token_publico = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    fecha = models.DateTimeField(auto_now_add=True)

    cliente = models.ForeignKey(
    'Cliente',
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name='pedidos'
)

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
    total = models.DecimalField(max_digits=10, decimal_places=2)

    # 🔹 COSTOS (🔥 PRO)
    costo_material = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    costo_mano_obra = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    @property
    def utilidad(self):
        return self.total - (self.costo_material + self.costo_mano_obra)
    
    @property
    def costo_total(self):
        return self.costo_material + self.costo_mano_obra

    def __str__(self):
        return self.numero_orden

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

class PedidoItem(models.Model):
    usuario = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name='items')
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE)
    variante = models.ForeignKey(ProductoVariante, null=True, blank=True, on_delete=models.CASCADE)
    cantidad = models.PositiveIntegerField()
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    descuento = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    iva = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    retefuente = models.DecimalField(max_digits=12, decimal_places=2, default=0)  
    total_final = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_gramos = models.DecimalField(max_digits=10,decimal_places=2,default=0
)

    def calcular_subtotal(self):
        return self.cantidad * self.precio  

class Gasto(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.CASCADE)

    nombre = models.CharField(max_length=150)
    monto = models.DecimalField(max_digits=10, decimal_places=2)

    fecha = models.DateTimeField(auto_now_add=True)

    def _str_(self):
        return f"{self.nombre} - {self.monto}"