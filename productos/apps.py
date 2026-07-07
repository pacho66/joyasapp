from django.apps import AppConfig

class ProductosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'productos'

    def ready(self):
        # 🎯 Cambiamos el import para que use una ruta limpia y el editor lo reconozca
        from . import signals
