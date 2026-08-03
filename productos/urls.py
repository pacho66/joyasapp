from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from .views import auth
from .views import catalogo
from .views import admin_catalogo
from .views import carrito
from . import views

urlpatterns = [


    # ===============================
    # 🔓 AUTENTICACIÓN
    # ===============================
    path('', catalogo.inicio, name='inicio'),
    path('registro/', auth.registro, name='registro'),
    path('login/', auth.iniciar_sesion, name='login'),
    path('logout/', auth.cerrar_sesion, name='logout'),
    # ===============================
    # 🛍️ CATÁLOGO / PRODUCTOS
    # ===============================
    path('buscar/', catalogo.buscar_productos, name='buscar_productos'),
    path('producto/<int:id>/<slug:slug>/', catalogo.detalle_producto, name='detalle_producto'),
    #path('productos/nuevo/',views.crear_producto,name='crear_producto'),
    path('productos/mis-productos/', admin_catalogo.mis_productos, name='mis_productos'),
    path('productos/eliminar/<int:id>/',admin_catalogo.eliminar_producto,name='eliminar_producto'),
    #path('productos/editar/<int:id>/',views.editar_producto,name='editar_producto'),
    path('productos/<int:id>/eliminar-imagen/',admin_catalogo.eliminar_imagen_producto,name='eliminar_imagen_producto'),
    path('imagen-galeria/eliminar/<int:id>/',admin_catalogo.eliminar_imagen_galeria,name='eliminar_imagen_galeria'),
    path('categoria/<int:categoria_id>/', views.productos_por_categoria, name='productos_por_categoria'),
    path('categorias/nueva/',admin_catalogo.crear_categoria,name='crear_categoria'),
    path('top-productos/', catalogo.productos_top, name='top_productos'),
    # Ruta para crear un producto nuevo
    path('productos/nuevo/', admin_catalogo.guardar_producto_view, name='crear_producto'),
    
    # Ruta para editar un producto existente (recibe la clave primaria 'pk')
    path('productos/<int:pk>/editar/', views.guardar_producto_view, name='editar_producto'),
    
    # ===============================
    # 🛒 CARRITO
    # ===============================
    path('carrito/', carrito.ver_carrito, name='ver_carrito'),
    path('carrito/agregar/<int:producto_id>/', carrito.agregar_al_carrito, name='agregar_al_carrito'),
    path('carrito/eliminar/<int:item_id>/', carrito.eliminar_del_carrito, name='eliminar_del_carrito'),
    path('carrito/aumentar/<int:item_id>/', carrito.aumentar_cantidad, name='aumentar_cantidad'),
    path('carrito/disminuir/<int:item_id>/', carrito.disminuir_cantidad, name='disminuir_cantidad'),

    # ===============================
    # 💳 COMPRAS / PAGOS UNIFICADOS
    # ===============================
    path('comprar-whatsapp/', views.comprar_whatsapp, name='comprar_whatsapp'),
    path('comprar-directo/<int:producto_id>/', views.comprar_whatsapp, name='comprar_directo'),
    path('pagar/', views.pagar_pedido, name='pagar_pedido'),
    path('pagar/mercadopago/<uuid:token>/', views.pagar_con_mercadopago, name='pagar_con_mercadopago'),
    path('pagar/wompi/<uuid:token>/', views.pagar_wompi, name='pagar_wompi'), # <-- Corregido a uuid y renombrado para claridad
    path('pago-exitoso/<uuid:token>/', views.pago_exitoso),
    path('confirmar-pago/<uuid:token>/', views.confirmar_pago_publico, name='confirmar_pago_publico'),
    path('confirmar-pago/<int:pedido_id>/', views.confirmar_pago, name='confirmar_pago'),
    path('pago-fallido/<uuid:token>/', views.pago_fallido, name='pago_fallido'),

    # ===============================
    # 💳 WEBHOOKS AUTOMÁTICOS
    # ===============================
    path('webhooks/mercadopago/<uuid:profile_uuid>/', views.webhook_mercadopago, name='webhook_mercadopago'),
    path('webhooks/wompi/', views.webhook_wompi, name='webhook_wompi'),
    path('webhooks/mercadopago/', views.webhook_mercadopago, name='webhook_mercadopago'), 
    
    # ===============================
    # 📦 PEDIDOS
    # ===============================
    path('pedidos/', views.lista_pedidos, name='lista_pedidos'),
    path('pedido/<int:pedido_id>/', views.detalle_pedido, name='detalle_pedido'),
    path('pedido/<int:pedido_id>/pdf/', views.pedido_pdf, name='pedido_pdf'),
    path('pedido/<int:pedido_id>/abono/', views.registrar_abono, name='registrar_abono'),

    # ===============================
    # 💰 FACTURACIÓN
    # ===============================
    path('factura/<int:pedido_id>/', views.generar_factura, name='generar_factura'),
    path('factura/<uuid:token>/', views.factura_publica, name='factura_publica'),

    # ===============================
    # 💳 COBROS (CORREGIDO)
    # ===============================
    path('cobrar-whatsapp/<int:pedido_id>/', views.cobrar_whatsapp, name='cobrar_whatsapp'),
    path('cobrar/<int:cliente_id>/', views.cobrar_moroso, name='cobrar_cliente'),
    # ===============================
    # 📊 PANEL / SaaS
    # ===============================
    path('dashboard/', views.dashboard, name='dashboard'),
    path('fabricacion/', views.fabricacion, name='fabricacion'),
    path('renovar/', views.renovar_manual, name='renovar_manual'),
    path('modificar-banner/', views.modificar_banner, name='modificar_banner'),
    path('estadisticas/', views.estadisticas, name='estadisticas'),
    path('configuracion-negocio/', views.configurar_negocio, name='configurar_negocio'),
    path('ganancias/', views.ganancias, name='ganancias'),
    path('inventario/', admin_catalogo.inventario, name='inventario'),
    path('dashboard/exportar-excel/', views.exportar_excel_dashboard, name='exportar_excel_dashboard'),
    path('crm/', views.panel_crm, name='panel_crm'),

    # ===============================
    # 💸 GASTOS
    # ===============================
    # 🏦 Rutas de control financiero unificadas
    path('gastos/', views.gastos, name='gastos'),
    path('lista-gastos/editar/<int:id>/', views.editar_gasto, name='editar_gasto'),
    path('lista-gastos/duplicar/<int:id>/', views.duplicar_gasto, name='duplicar_gasto'),
    path('lista-gastos/eliminar/<int:id>/', views.eliminar_gasto, name='eliminar_gasto'),
   
    # ===============================
    # 📲 MARKETING
    # ===============================
    path('whatsapp-segmento/', whatsapp_segmento, name='whatsapp_segmento'),

    # ===============================
    # 💼 CARTERA
    # ===============================
    path('cartera/', views.cartera_clientes, name='cartera_clientes'),
]
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])

