import logging
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify  # Herramienta nativa de Django para SEO

# Importaciones correctas basadas en la estructura real de tu app 'productos'
from productos.models import Producto, Categoria, ProductoVariante, ProductoImagen

logger = logging.getLogger('joyasapp.auditoria')

class ProductoService:

    @staticmethod
    def entero_seguro(valor, defecto=0):
        if valor is None: return defecto
        try: return int(valor)
        except (ValueError, TypeError): return defecto

    @staticmethod
    def decimal_seguro(valor, defecto=0.0):
        if valor is None: return defecto
        try:
            if isinstance(valor, str): valor = valor.replace(',', '.')
            return float(valor)
        except (ValueError, TypeError): return defecto

    @classmethod
    def generar_sku_inteligente(cls, producto, color, talla, indice):
        ref_base = str(producto.referencia or 'REF').strip().upper().replace(' ', '')
        color_clean = str(color or 'GN').strip().upper().replace(' ', '')
        color_prefijo = color_clean[:2] if len(color_clean) >= 2 else (color_clean + 'X')
        talla_clean = str(talla or '00').strip().upper().replace(' ', '')
        talla_prefijo = talla_clean[:2] if len(talla_clean) >= 2 else talla_clean
        if not talla_prefijo.isalnum(): talla_prefijo = 'UN'
        return f"{ref_base}-{color_prefijo}-{talla_prefijo}"

    @classmethod
    def guardar(cls, request, pk=None):
        """
        Orquestador de persistencia blindado con transacciones atómicas.
        Gestiona datos generales, multimedia, variantes y slugs de marketing.
        """
        if pk:
            producto = get_object_or_404(Producto, pk=pk)
        else:
            producto = Producto()

        data = request.POST
        files = request.FILES
        usuario = request.user

        with transaction.atomic():
            # 1. Datos Generales
            producto.nombre = data.get('nombre')
            producto.referencia = data.get('referencia')
            categoria_id = data.get('categoria')
            if categoria_id:
                producto.categoria = get_object_or_404(Categoria, id=categoria_id)
            
            producto.tipo_venta = data.get('tipo_venta', 'unidad')
            producto.stock = cls.entero_seguro(data.get('stock'))
            producto.descripcion = data.get('descripcion', '')
            producto.destacado = 'destacado' in data

            producto.precio_costo = cls.decimal_seguro(data.get('precio_costo'))
            producto.precio_detal = cls.decimal_seguro(data.get('precio_detal'))
            producto.precio_semimayor = cls.decimal_seguro(data.get('precio_semimayor'))
            producto.precio_mayor = cls.decimal_seguro(data.get('precio_mayor'))
            producto.peso_producto = cls.decimal_seguro(data.get('peso_producto'))
            
            producto.precio_por_gramo_detal = cls.decimal_seguro(data.get('precio_por_gramo_detal'))
            producto.precio_por_gramo_semimayor = cls.decimal_seguro(data.get('precio_por_gramo_semimayor'))
            producto.precio_por_gramo_mayor = cls.decimal_seguro(data.get('precio_por_gramo_mayor'))

            if not producto.pk:
                producto.usuario_creador = usuario
            producto.usuario_actualizacion = usuario
            producto.fecha_actualizacion = timezone.now()

            # 2. Generación Automatizada del Slug de Marketing (SEO)
            if producto.nombre:
                slug_propuesto = slugify(producto.nombre)
                slug_final = slug_propuesto
                contador = 1
                
                # Escudo anti-duplicados para Slugs
                while Producto.objects.filter(slug=slug_final).exclude(pk=producto.pk).exists():
                    slug_final = f"{slug_propuesto}-{contador}"
                    contador += 1
                
                producto.slug = slug_final

            # 3. Multimedia Estructural
            if 'imagen_principal' in files:
                producto.imagen_principal = files['imagen_principal']
            if data.get('eliminar_video') and producto.video:
                producto.video.delete()
            elif 'video' in files:
                producto.video = files['video']
            if data.get('eliminar_certificado') and producto.certificado:
                producto.certificado.delete()
            elif 'certificado' in files:
                producto.certificado = files['certificado']

            # Guardamos el producto base para asegurar el ID de las relaciones
            producto.save()

            # 4. Galería Secundaria (Escudo Anti-Duplicados)
            if 'galeria' in files:
                for f in files.getlist('galeria'):
                    if producto.imagen_principal and f.name == producto.imagen_principal.name:
                        continue
                    ProductoImagen.objects.create(producto=producto, imagen=f)

            # 5. Motor de Variantes SaaS
            colores = data.getlist('v_color[]')
            tallas = data.getlist('v_talla[]')
            pesos = data.getlist('v_peso[]')
            stocks = data.getlist('v_stock[]')
            precios = data.getlist('v_precio[]')
            codigos = data.getlist('v_codigo[]')
            fotos_variantes = files.getlist('v_foto[]')

            codigos_procesados = []

            for i in range(len(colores)):
                try:
                    v_color = colores[i] if i < len(colores) else ''
                    v_talla = tallas[i] if i < len(tallas) else ''
                    v_codigo = codigos[i].strip() if (i < len(codigos) and codigos[i]) else cls.generar_sku_inteligente(producto, v_color, v_talla, i+1)
                    codigos_procesados.append(v_codigo)

                    v_stock = cls.entero_seguro(stocks[i])
                    v_precio = cls.decimal_seguro(precios[i], defecto=producto.precio_detal)
                    v_peso = cls.decimal_seguro(pesos[i])

                    variante, created = ProductoVariante.objects.get_or_create(
                        producto=producto,
                        codigo=v_codigo,
                        defaults={
                            'color': v_color,
                            'talla': v_talla,
                            'stock': v_stock,
                            'precio_venta': v_precio,
                            'peso': v_peso,
                            'activo': True,
                            'usuario_creador': usuario
                        }
                    )

                    if not created:
                        cambios = []
                        if variante.stock != v_stock: cambios.append(f"Stock: {variante.stock} → {v_stock}")
                        if variante.precio_venta != v_precio: cambios.append(f"Precio: ${variante.precio_venta} → ${v_precio}")
                        if variante.peso != v_peso: cambios.append(f"Peso: {variante.peso}g → {v_peso}g")
                        
                        if cambios:
                            logger.info(f"Usuario {usuario.username} modificó variante {v_codigo}: {', '.join(cambios)} el {timezone.now().strftime('%d/%m/%Y')}")

                        variante.color = v_color
                        variante.talla = v_talla
                        variante.stock = v_stock
                        variante.precio_venta = v_precio
                        variante.peso = v_peso
                        variante.activo = True  

                    variante.usuario_actualizacion = usuario
                    variante.fecha_actualizacion = timezone.now()

                    if i < len(fotos_variantes):
                        foto_archivo = fotos_variantes[i]
                        if not (producto.imagen_principal and foto_archivo.name == producto.imagen_principal.name):
                            variante.foto = foto_archivo

                    variante.save()
                except IndexError:
                    continue

            # Soft Delete de variantes removidas en el frontend
            producto.variantes.exclude(codigo__in=codigos_procesados).update(
                activo=False,
                usuario_actualizacion=usuario,
                fecha_actualizacion=timezone.now()
            )

        return producto