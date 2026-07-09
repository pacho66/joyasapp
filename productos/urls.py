from django.urls import path
from . import views
from .views import whatsapp_segmento
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [

    # ===============================
    # 🔓 AUTENTICACIÓN
    # ===============================
    path('', views.inicio, name='inicio'),
    path('registro/', views.registro, name='registro'),
    path('login/', views.iniciar_sesion, name='login'),
    path('logout/', views.cerrar_sesion, name='logout'),

    # ===============================
    # 🛍️ CATÁLOGO / PRODUCTOS
    # ===============================
    path('buscar/', views.buscar_productos, name='buscar_productos'),
    path('producto/<int:id>/<slug:slug>/', views.detalle_producto, name='detalle_producto'),
    path('productos/nuevo/',views.crear_producto,name='crear_producto'),
    path('mis-productos/',views.mis_productos,name='mis_productos'),
    path('productos/eliminar/<int:id>/',views.eliminar_producto,name='eliminar_producto'),
    path('productos/editar/<int:id>/',views.editar_producto,name='editar_producto'),
    path('productos/<int:id>/eliminar-imagen/',views.eliminar_imagen_producto,name='eliminar_imagen_producto'),
    path('imagen-galeria/eliminar/<int:id>/',views.eliminar_imagen_galeria,name='eliminar_imagen_galeria'),
    path('categoria/<int:categoria_id>/', views.productos_por_categoria, name='productos_por_categoria'),
    path('categorias/nueva/',views.crear_categoria,name='crear_categoria'),
    path('top-productos/', views.productos_top, name='top_productos'),

    # ===============================
    # 🛒 CARRITO
    # ===============================
    path('carrito/', views.ver_carrito, name='ver_carrito'),
    path('carrito/agregar/<int:producto_id>/', views.agregar_al_carrito, name='agregar_al_carrito'),
    path('carrito/eliminar/<int:item_id>/', views.eliminar_del_carrito, name='eliminar_del_carrito'),
    path('carrito/aumentar/<int:item_id>/', views.aumentar_cantidad, name='aumentar_cantidad'),
    path('carrito/disminuir/<int:item_id>/', views.disminuir_cantidad, name='disminuir_cantidad'),

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
    path('cobrar/<int:cliente_id>/', views.cobrar_cliente, name='cobrar_cliente'),
    path('cobrar-whatsapp/<int:pedido_id>/', views.cobrar_whatsapp, name='cobrar_whatsapp'),
    path('cobrar-moroso/<int:cliente_id>/', views.cobrar_moroso, name='cobrar_moroso'),
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
    path('inventario/', views.inventario, name='inventario'),
    path('dashboard/exportar-excel/', views.exportar_excel_dashboard, name='exportar_excel_dashboard'),
    path('crm/', views.panel_crm, name='panel_crm'),

    # ===============================
    # 💸 GASTOS
    # ===============================
    path('gastos/', views.gastos, name='gastos'),
    path('lista-gastos/', views.lista_gastos, name='lista_gastos'),
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

