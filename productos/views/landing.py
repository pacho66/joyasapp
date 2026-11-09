from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
# Asegúrate de ajustar la ruta de los puntos .. si tu models.py está un nivel más arriba
from ..models import EventoEmbudo 

@csrf_exempt
def registrar_evento_embudo(request):
    if request.method == 'POST':
        paso = request.POST.get('paso')
        if paso:
            ip = request.META.get('REMOTE_ADDR')
            if not request.session.session_key:
                request.session.save()
            
            EventoEmbudo.objects.create(
                paso=paso,
                ip_address=ip,
                session_key=request.session.session_key
            )
            return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': 'error'}, status=400)