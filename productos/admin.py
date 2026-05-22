from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from .models import Producto, Categoria, ProductoImagen, CarritoItem, ProductoVariante
from .models import Pedido, PedidoItem, Cliente
from django.utils.html import format_html
from django.urls import reverse
from urllib.parse import quote
from django.contrib.admin import SimpleListFilter
from productos.services.pedidos import recalcular_pedido
from django.contrib import messages
from django.urls import path
from django.shortcuts import redirect

import openpyxl
from django.http import HttpResponse
from .models import Perfil

    # =====================================
# 🔥 1. FUNCIÓN MASIVA (AQUÍ VA)
# =====================================
def enviar_whatsapp_masivo(modeladmin, request, queryset):
    for cliente in queryset:
        mensaje = f"Hola {cliente.nombre} 😊 tenemos novedades 💎"
        link = f"https://wa.me/57{cliente.telefono}?text={quote(mensaje)}"

        print(link)  # luego lo mejoramos

enviar_whatsapp_masivo.short_description = "Enviar WhatsApp masivo"

@admin.register(Perfil)
class PerfilAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Empresa', {
            'fields': (
                'user',
                'nombre_tienda',
                'logo',
            )
        }),

        ('Contacto', {
            'fields': (
                'whatsapp',
                'email_empresa',
                'direccion',
                'ciudad',
            )
        }),

        ('Redes', {
            'fields': (
                'instagram',
                'facebook',
                'tiktok',
            )
        }),

        ('Facturación', {
            'fields': (
                'nit',
            )
        }),

        ('Plan', {
            'fields': (
                'plan',
                'activa',
                'plan_vence',
            )
        }),

        ('Diseño', {
            'fields': (
                'color_principal',
            )
        }),
    )

    list_display = (
        'user',
        'nombre_tienda',
        'whatsapp',
        'plan',
        'activa',
        'plan_vence',
    )

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):

    list_display = (
        'nombre',
        'telefono',
        'tipo_cliente',
        'porcentaje_descuento',
        'total_compras',
        'numero_pedidos',
        'whatsapp_cliente', 
    )

    search_fields = ('nombre', 'telefono')
    actions = [enviar_whatsapp_masivo]

    # =========================
    # 📲 BOTÓN WHATSAPP CLIENTE
    # =========================
    def whatsapp_cliente(self, obj):
        mensaje = f"Hola {obj.nombre}, tenemos novedades 💎"
        url = f"https://wa.me/57{obj.telefono}?text={quote(mensaje)}"

        return format_html('<a href="{}" target="_blank">Enviar</a>', url)

    whatsapp_cliente.short_description = "WhatsApp"

class TieneDescuentoFilter(SimpleListFilter):
    title = 'Con descuento'
    parameter_name = 'descuento'

    def lookups(self, request, model_admin):
        return (
            ('si', 'Sí'),
            ('no', 'No'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'si':
            return queryset.filter(descuento_total__gt=0)
        if self.value() == 'no':
            return queryset.filter(descuento_total=0)
        return queryset

class ProductoImagenInline(admin.TabularInline):
    model = ProductoImagen
    extra = 3

class ProductoVarianteInline(admin.TabularInline):
    model = ProductoVariante
    extra = 1

class PedidoItemInline(admin.TabularInline):
    model = PedidoItem
    extra = 0

    fields = (
        'producto',
        'cantidad',
        'precio',
        'subtotal',
        'porcentaje_descuento', 
        'descuento',
        'iva',
        'retefuente',
        'total_final'
    )

    readonly_fields = (
        'subtotal',
        'iva',
        'retefuente',
        'total_final'
    )
    
class ProductoAdmin(admin.ModelAdmin):
    list_display = (
        'nombre',
        'slug',
        'referencia',
        'get_categoria',
        'precio_detal',
        'precio_mayor'
    )

    list_filter = ('categoria',)

    search_fields = ('nombre', 'descripcion', 'slug')

    prepopulated_fields = {
        'slug': ('nombre',)
    }

    exclude = (
        'usuario',
        'color',
        'talla',
        'stock',
        'colores_disponibles',
        'tallas_disponibles'
    )

    inlines = [ProductoImagenInline, ProductoVarianteInline]

    def get_categoria(self, obj):
        return obj.categoria.nombre
    get_categoria.short_description = 'Categoría'

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        if request.user.is_superuser:
            return qs

        return qs.filter(usuario=request.user)

    def save_model(self, request, obj, form, change):
        if not obj.usuario:
            obj.usuario = request.user
        super().save_model(request, obj, form, change)

@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = (
        'numero_orden',
        'total',
        'envio_info',
        'iva_activo',
        'descuento_tipo',
        'estado_color',
        'fecha',
        'ver_en_web',
        'enviar_whatsapp',
        'ver_factura',
        'ver_dashboard',
        'whatsapp_pedido', 
    )

    list_filter = ('tipo_cliente',)

    search_fields = ('nombre', 'telefono')

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        if request.user.is_superuser:
            return qs

        return qs.filter(usuario=request.user)


    # 🔥 GUARDAR USUARIO AUTOMÁTICO
    def save_model(self, request, obj, form, change):
        if not obj.usuario:
            obj.usuario = request.user

        super().save_model(request, obj, form, change)

    # =========================
    # 📲 WHATSAPP PEDIDO
    # =========================
    def whatsapp_pedido(self, obj):
        mensaje = f"Hola {obj.cliente_nombre}, te confirmo tu pedido 💎\n\n"
        mensaje += f"Orden: {obj.numero_orden}\n\n"

        for item in obj.items.all():
            mensaje += f"{item.producto.nombre} x{item.cantidad} - ${item.total_final:,.0f}\n".replace(",", ".")

        mensaje += f"\nTotal: ${obj.total:,.0f}".replace(",", ".")

        url = f"https://wa.me/57{obj.cliente_telefono}?text={quote(mensaje)}"

        return format_html('<a href="{}" target="_blank">📲 WhatsApp</a>', url)

    whatsapp_pedido.short_description = "WhatsApp"

    # =========================
    # 🚚 ENVÍO BONITO
    # =========================
    def envio_info(self, obj):
        if obj.costo_envio == 0:
            return f"Gratis - {obj.tipo_envio}"
        return f"${obj.costo_envio:,.0f} - {obj.tipo_envio}".replace(",", ".")

    envio_info.short_description = "Envío"

    ordering = ('-fecha',)

    actions = ['marcar_enviado', 'exportar_excel', 'recalcular_pedidos']

    change_form_template = "admin/pedido_change_form.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:pedido_id>/recalcular/',
                self.admin_site.admin_view(self.recalcular_view),
                name='recalcular-pedido',
            ),
        ]
        return custom_urls + urls

    def recalcular_view(self, request, pedido_id):
        pedido = Pedido.objects.get(id=pedido_id)
        recalcular_pedido(pedido)

        self.message_user(request, "Pedido recalculado ✅", messages.SUCCESS)
        return redirect(f'../../{pedido_id}/change/')

    def recalcular_pedidos(self, request, queryset):
        for pedido in queryset:
            recalcular_pedido(pedido)

        self.message_user(
            request,
            "Pedidos recalculados correctamente ✅",
            level=messages.SUCCESS
        )

    recalcular_pedidos.short_description = "🔄 Recalcular pedido(s)"

    def iva_activo(self, obj):
        if obj.aplica_iva:
            return format_html(
                '<span style="color:white; background:{}; padding:4px 8px; border-radius:5px;">{}</span>',
                "green",
                "✔ Sí"
        )
        else:
            return format_html(
                '<span style="color:white; background:{}; padding:4px 8px; border-radius:5px;">{}</span>',
                "red",
                "✖ No"
        )

    iva_activo.short_description = "IVA"

    def descuento_tipo(self, obj):
        if obj.porcentaje_descuento and obj.porcentaje_descuento > 0:
            return f"{obj.porcentaje_descuento}%"
        elif obj.descuento_total and obj.descuento_total > 0:
            return f"${obj.descuento_total:,.0f}"
        return "-"
    
    descuento_tipo.short_description = "Descuento"

        # =========================
    # 🔁 RECÁLCULO AUTOMÁTICO
    # =========================

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)

        pedido = form.instance
        recalcular_pedido(pedido)

    list_filter = (
        'estado',
        'fecha',
        'aplica_iva',          # 🔥 NUEVO
        TieneDescuentoFilter,  # 🔥 NUEVO (personalizado)
)

    search_fields = (
        'numero_orden',
        'cliente_nombre',      # 🔥 recomendado
        'cliente_nit',         # 🔥 recomendado
)

    inlines = [PedidoItemInline]


    # 🔥 BOTÓN VER LISTA
    def ver_en_web(self, obj):
        url = reverse('lista_pedidos')
        return format_html('<a href="{}" target="_blank">📦 Lista</a>', url)

    ver_en_web.short_description = "Pedidos"

    # 🔥 BOTÓN WHATSAPP
    def enviar_whatsapp(self, obj):
        mensaje = f"Hola, te confirmo tu pedido:\n\nOrden: {obj.numero_orden}\n"

        for item in obj.items.all():
            mensaje += f"{item.producto.nombre} x{item.cantidad} - ${item.precio}\n"

        mensaje += f"\nTotal: ${obj.total}"

        url = f"https://wa.me/573215280610?text={quote(mensaje)}"

        return format_html('<a href="{}" target="_blank">📲 WhatsApp</a>', url)

    enviar_whatsapp.short_description = "WhatsApp"

    # 🔥 ACCIÓN MARCAR ENVIADO
    def marcar_enviado(self, request, queryset):
        queryset.update(estado="enviado")

    marcar_enviado.short_description = "📦 Marcar como enviado"
    def ver_factura(self, obj):
        url = reverse('generar_factura', args=[obj.id])
        return format_html('<a href="{}" target="_blank">🧾 PDF</a>', url)

    ver_factura.short_description = "Factura"

    def estado_color(self, obj):
        colores = {
            "pendiente": "#ffc107",       # amarillo
            "pendiente_pago": "#fd7e14",  # naranja
            "pagado": "#28a745",          # verde
            "enviado": "#007bff",         # azul
            "entregado": "#6f42c1"        # morado
    }

        estado = obj.estado or "sin estado"
        color = colores.get(estado, "#6c757d")

        return format_html(
            '<span style="color:white; background:{}; padding:4px 8px; border-radius:6px; font-weight:bold;">{}</span>',
            color,
            estado.upper()
    )

    estado_color.short_description = "Estado"

    def ver_dashboard(self, obj):
        url = reverse('dashboard')
        return format_html('<a href="{}" target="_blank">📊 Dashboard</a>', url)

    ver_dashboard.short_description = "Ventas"

    def exportar_excel(self, request, queryset):
        import openpyxl
        from django.http import HttpResponse
        from decimal import Decimal

        wb = openpyxl.Workbook()

        # =========================================
        # 📄 HOJA 1: RESUMEN PEDIDOS
        # =========================================
        ws_resumen = wb.active
        ws_resumen.title = "Resumen"

        ws_resumen.append([
            "Orden",
            "Fecha",
            "Cliente",
            "Subtotal",
            "IVA",
            "ReteFuente",
            "Descuento",
            "Total"
        ])

        # =========================================
        # 📄 HOJA 2: DETALLE PRODUCTOS
        # =========================================
        ws_detalle = wb.create_sheet(title="Detalle")

        ws_detalle.append([
            "Orden",
            "Producto",
            "Cantidad",
            "Gramos",
            "Precio Unitario",
            "Subtotal",
            "Descuento",
            "IVA",
            "Total"
        ])

        # =========================================
        # 🔁 RECORRER PEDIDOS
        # =========================================
        for pedido in queryset:

            subtotal_pedido = Decimal('0')
            iva_total = Decimal('0')
            rete_total = Decimal('0')
            descuento_total = Decimal('0')

        for item in pedido.items.all():

            cantidad = Decimal(item.cantidad)
            precio = Decimal(item.precio)

            # 🔹 GRAMOS
            gramos = ''
            if item.producto.tipo_venta == 'gramo':
                peso = item.producto.peso_producto or 1
                gramos = cantidad * Decimal(peso)
                base = gramos * precio
            else:
                base = cantidad * precio

            subtotal = Decimal(item.subtotal or base)
            descuento = Decimal(item.descuento or 0)
            iva = Decimal(item.iva or 0)
            rete = Decimal(item.retefuente or 0)
            total = Decimal(item.total_final or 0)

            subtotal_pedido += subtotal
            iva_total += iva
            rete_total += rete
            descuento_total += descuento

            # 🔹 HOJA DETALLE
            ws_detalle.append([
                pedido.numero_orden,
                item.producto.nombre,
                float(cantidad),
                float(gramos) if gramos else '',
                float(precio),
                float(subtotal),
                float(descuento),
                float(iva),
                float(total),
            ])

        # 🔹 HOJA RESUMEN
        ws_resumen.append([
            pedido.numero_orden,
            pedido.fecha.strftime('%Y-%m-%d') if pedido.fecha else '',
            pedido.cliente_nombre or '',
            float(subtotal_pedido),
            float(iva_total),
            float(rete_total),
            float(descuento_total),
            float(pedido.total),
        ])

    # =========================================
    # 📏 AJUSTE COLUMNAS AUTOMÁTICO
    # =========================================
        for ws in [ws_resumen, ws_detalle]:
            for col in ws.columns:
                max_length = 0
                col_letter = col[0].column_letter

                for cell in col:
                    try:
                        if cell.value:
                            max_length = max(max_length, len(str(cell.value)))
                    except:
                            pass

        ws.column_dimensions[col_letter].width = max_length + 2

        # =========================================
        # 📤 RESPUESTA
        # =========================================
        response = HttpResponse(
    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
        response['Content-Disposition'] = 'attachment; filename=ventas_contable.xlsx'

        wb.save(response)
        return response

    exportar_excel.short_description = "📊 Excel contable (resumen + detalle)"

admin.site.register(Producto, ProductoAdmin)

try:
    admin.site.unregister(Categoria)
except:
    pass

@admin.action(description="Eliminar categorías seleccionadas")
def eliminar_categorias(modeladmin, request, queryset):
    queryset.delete()

class CategoriaAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre', 'usuario')
    actions = [eliminar_categorias]  # 👈 IMPORTANTE

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(usuario=request.user)

    def save_model(self, request, obj, form, change):
        if not obj.usuario:
            obj.usuario = request.user
        super().save_model(request, obj, form, change)


admin.site.register(Categoria, CategoriaAdmin)

admin.site.register(CarritoItem)