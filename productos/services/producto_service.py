import logging
from decimal import Decimal
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

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
    def guardar(cls, data, files, pk=None, user=None, *args, **kwargs):
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
            
            stock_raw = data.get('stock')
            if stock_raw not in [None, '']:
                producto.stock = cls.entero_seguro(stock_raw)
            elif not producto.pk:
                producto.stock = 0

            producto.descripcion = data.get('descripcion', '')
            producto.destacado = 'destacado' in data
            producto.mostrar_en_catalogo = 'mostrar_en_catalogo' in data  # 👈 Guardado del interruptor de catálogo

            # 🛡️ Precios blindados: Si vienen vacíos, mantienen el valor actual en lugar de borrarlo
            def obtener_decimal_o_mantener(valor_raw, valor_actual):
                if valor_raw not in [None, '']:
                    return cls.decimal_seguro(valor_raw)
                return valor_actual if valor_actual is not None else 0.0

            producto.precio_costo = obtener_decimal_o_mantener(data.get('precio_costo'), producto.precio_costo)
            producto.precio_detal = obtener_decimal_o_mantener(data.get('precio_detal'), producto.precio_detal)
            producto.precio_semimayor = obtener_decimal_o_mantener(data.get('precio_semimayor'), producto.precio_semimayor)
            producto.precio_mayor = obtener_decimal_o_mantener(data.get('precio_mayor'), producto.precio_mayor)
            producto.peso_producto = obtener_decimal_o_mantener(data.get('peso_producto'), producto.peso_producto)
            
            producto.precio_por_gramo_detal = obtener_decimal_o_mantener(data.get('precio_por_gramo_detal'), producto.precio_por_gramo_detal)
            producto.precio_por_gramo_semimayor = obtener_decimal_o_mantener(data.get('precio_por_gramo_semimayor'), producto.precio_por_gramo_semimayor)
            producto.precio_por_gramo_mayor = obtener_decimal_o_mantener(data.get('precio_por_gramo_mayor'), producto.precio_por_gramo_mayor)

            if not producto.pk:
                producto.usuario_creador = usuario
            producto.usuario_actualizacion = usuario
            producto.fecha_actualizacion = timezone.now()

            # 2. Slug SEO
            if producto.nombre:
                slug_propuesto = slugify(producto.nombre)
                slug_final = slug_propuesto
                contador = 1
                while Producto.objects.filter(slug=slug_final).exclude(pk=producto.pk).exists():
                    slug_final = f"{slug_propuesto}-{contador}"
                    contador += 1
                producto.slug = slug_final

            # 3. Multimedia
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

            producto.save()

            # 4. Galería
            if 'galeria' in files:
                for f in files.getlist('galeria'):
                    if producto.imagen_principal and f.name == producto.imagen_principal.name:
                        continue
                    ProductoImagen.objects.create(producto=producto, imagen=f)

            # 5. MOTOR DE VARIANTES BLINDADO Y SEGURO
            indices = data.getlist('v_index[]')
            colores = data.getlist('v_color[]')
            tallas = data.getlist('v_talla[]')
            pesos = data.getlist('v_peso[]')
            stocks = data.getlist('v_stock[]')
            precios = data.getlist('v_precio[]')
            codigos = data.getlist('v_codigo[]')
            ids_variantes = data.getlist('v_id[]') if 'v_id[]' in data else []

            codigos_procesados = []

            for i, index_js in enumerate(indices):
                try:
                     # Extracción segura con validación de tamaño para evitar IndexError
                    v_color = colores[i] if i < len(colores) else ''
                    v_talla = tallas[i] if i < len(tallas) else ''
        
                    raw_codigo = codigos[i] if i < len(codigos) else ''
                    v_codigo = raw_codigo.strip() if raw_codigo else cls.generar_sku_inteligente(producto, v_color, v_talla, i+1)
                    codigos_procesados.append(v_codigo)

                    raw_stock = stocks[i] if i < len(stocks) else '0'
                    v_stock = cls.entero_seguro(raw_stock)

                    raw_peso = pesos[i] if i < len(pesos) else '0'
                    v_peso = cls.decimal_seguro(raw_peso)

                    v_id = ids_variantes[i] if i < len(ids_variantes) and ids_variantes[i] else None

                    if producto.tipo_venta == 'gramo':
                        precio_defecto = producto.precio_por_gramo_detal
                    else:
                        precio_defecto = producto.precio_detal
        
                    raw_precio = precios[i] if i < len(precios) else None
                    v_precio = cls.decimal_seguro(raw_precio, defecto=precio_defecto)

                    # Búsqueda ultra segura de la variante
                    variante = None
                    if v_id:
                        variante = ProductoVariante.objects.filter(pk=v_id, producto=producto).first()
        
                    if not variante and v_codigo:
                        # Si no hay ID, buscar por código dentro de este producto
                        variante = ProductoVariante.objects.filter(producto=producto, codigo=v_codigo).first()

                    if not variante:
                        variante = ProductoVariante(producto=producto)

                    # Asignación de datos
                    variante.codigo = v_codigo
                    variante.color = v_color
                    variante.talla = v_talla
                    variante.stock = v_stock
                    variante.precio_venta = v_precio
                    variante.peso = v_peso
                    variante.activo = True  

                    # Captura correcta y segura del archivo de foto de la variante
                    if files:
                        foto_archivo = files.get(f'v_foto_var_row_{index_js}') or files.get(f'v_foto_{index_js}')
                    if foto_archivo:
                        variante.foto = foto_archivo

                    variante.save()
                except Exception as var_error:
                    # exc_info=True imprimirá el rastro exacto en los logs de Render si algo falla
                    logger.error(f"❌ Error crítico procesando variante indexada {index_js} (ítem {i}): {str(var_error)}", exc_info=True)
                    continue

            # Soft Delete limpio de variantes inactivas
            producto.variantes.exclude(codigo__in=codigos_procesados).update(activo=False)

        return producto