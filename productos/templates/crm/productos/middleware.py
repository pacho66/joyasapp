from django.http import Http404
from .models import Perfil

class SubdomainTenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Extrae el host (ejemplo: "tuluna.joyasapp.com" o "tuluna.localhost:8000")
        host = request.get_host().split(':')[0]
        parts = host.split('.')
        
        subdomain = None

        # Si estamos en producción o entorno local con subdominio (ej: tuluna.joyasapp.com -> ['tuluna', 'joyasapp', 'com'])
        if len(parts) >= 3:
            potential_sub = parts[0].lower()
            # Ignoramos subdominios reservados del sistema
            if potential_sub not in ['www', 'app', 'admin', 'api']:
                subdomain = potential_sub

        request.subdomain = subdomain
        request.tenant_perfil = None

        # Si hay un subdominio detectado, buscamos el Perfil correspondiente
        if subdomain:
            try:
                request.tenant_perfil = Perfil.objects.get(subdominio__iexact=subdomain, activa=True)
            except Perfil.DoesNotExist:
                raise Http404("La tienda o joyería solicitada no existe o se encuentra inactiva.")

        response = self.get_response(request)
        return response