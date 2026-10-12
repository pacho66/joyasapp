# =========================================================================
# 🛠️ BLOQUE 11: OPERACIONES DE TALLER / MANUFACTURA
# =========================================================================

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def fabricacion(request):
    # Definimos las variables de costos (por ahora en 0 para que cargue limpio)
    context = {
        'material_total': 0.0,
        'total_mano_obra': 0.0,
        'costos_totales': 0.0,
        'mensaje': "⚙️ Control de Fabricación"
    }
    return render(request, 'productos/fabricacion.html', context)