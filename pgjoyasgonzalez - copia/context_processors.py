from productos.models import Categoria

def categorias_menu(request):

    if request.user.is_authenticated:
        categorias = Categoria.objects.filter(usuario=request.user)
    else:
        categorias = Categoria.objects.none()

    return {
        'categorias_menu': categorias
    }