from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from .models import Gasto, Producto


class RegistroForm(forms.Form):
    username = forms.CharField(
        label='Usuario',
        max_length=150,
        min_length=4,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Usuario único'
        })
    )

    email = forms.EmailField(
        label='Correo',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'correo@ejemplo.com'
        })
    )

    password = forms.CharField(
        label='Contraseña',
        min_length=6,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Mínimo 6 caracteres'
        })
    )

    confirmar_password = forms.CharField(
        label='Confirmar contraseña',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Repite la contraseña'
        })
    )

    nombre_tienda = forms.CharField(
        label='Nombre de tienda',
        max_length=120,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: PG Joyas'
        })
    )

    whatsapp = forms.CharField(
        label='WhatsApp',
        max_length=20,
        validators=[
            RegexValidator(
                regex=r'^[0-9]+$',
                message='Solo números'
            )
        ],
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '573001112233'
        })
    )

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username=username).exists():
            raise ValidationError("Ese usuario ya existe")
        return username

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if User.objects.filter(email=email).exists():
            raise ValidationError("Ese correo ya está registrado")
        return email

    def clean_whatsapp(self):
        whatsapp = self.cleaned_data['whatsapp'].strip()
        if len(whatsapp) < 10:
            raise ValidationError("Número inválido")
        return whatsapp

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirmar = cleaned_data.get('confirmar_password')

        if password and confirmar and password != confirmar:
            raise ValidationError("Las contraseñas no coinciden")

        return cleaned_data

class ConfiguracionNegocioForm(forms.Form):
    nombre_tienda = forms.CharField(
        max_length=120,
        label='Nombre tienda',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Nombre de tu negocio'
        })
    )

    nit = forms.CharField(
        max_length=30,
        required=False,
        label='NIT',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '901234567-8'
        })
    )

    whatsapp = forms.CharField(
        max_length=20,
        label='WhatsApp',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '573001112233'
        })
    )

    correo_negocio = forms.EmailField(
        required=False,
        label='Correo negocio',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'ventas@mitienda.com'
        })
    )

    direccion = forms.CharField(
        required=False,
        label='Dirección',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Dirección'
        })
    )

    ciudad = forms.CharField(
        required=False,
        label='Ciudad',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ciudad'
        })
    )

    # ⚖️ REGLAS FISCALES Y FACTURACIÓN (NUEVOS CAMPOS)
    responsable_iva = forms.BooleanField(
        required=False,
        label='¿Es Responsable de IVA?',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )

    porcentaje_iva = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        initial=19.00,
        label='Porcentaje de IVA (%)',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '19.00'
        })
    )

    mostrar_iva_discriminado = forms.BooleanField(
        required=False,
        label='Mostrar IVA Discriminado',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )

    # ⚖️ REGLAS FISCALES (Añadir Retefuente)
    aplicar_retefuente = forms.BooleanField(
        required=False,
        label='¿Manejar Retención en la Fuente?',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    porcentaje_retefuente = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        initial=2.50,
        label='Porcentaje Retefuente (%)',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '2.50'})
    )

    prefijo_factura = forms.CharField(
        max_length=10,
        required=False,
        label='Prefijo Factura',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej: SETT'
        })
    )

    consecutivo_actual = forms.IntegerField(
        required=False,
        initial=1,
        label='Siguiente Consecutivo',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '1'
        })
    )

    resolucion_dian = forms.CharField(
        required=False,
        label='Resolución DIAN',
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Texto legal de la resolución DIAN...'
        })
    )

    integracion_siigo_activa = forms.BooleanField(
        required=False,
        label='Activar Siigo',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )

    # 🎁 CAMPAÑAS Y DESCUENTOS EN PROMO (NUEVOS CAMPOS)
    aplicar_descuentos = forms.BooleanField(
        required=False,
        label='Activar Descuentos en Promoción',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )

    porcentaje_descuento_promo = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        initial=0.00,
        label='Descuento de Campaña (%)',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '0.00'
        })
    )

    # 💳 PASARELAS DE PAGO
    wompi_public_key = forms.CharField(
        max_length=255,
        required=False,
        label='Wompi Public Key',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'pub_prod_XxxXxx...'
        })
    )

    mercadopago_access_token = forms.CharField(
        max_length=255,
        required=False,
        label='Mercado Pago Access Token',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'APP_USR-...'
        })
    )

    # 🚚 CONFIGURACIÓN DE ENVÍOS (AVANZADOS)
    cobrar_envio = forms.BooleanField(
        required=False,
        label='¿Cobrar Envío?',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )

    costo_envio_estandar = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        initial=0.00,
        label='Costo Envío Estándar',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '0.00'
        })
    )

    envio_gratis_desde = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        label='Envío Gratis Desde',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': 'Monto mínimo para envío gratis'
        })
    )

class GastoForm(forms.ModelForm):
    class Meta:
        model = Gasto
        fields = ['nombre', 'monto']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ej: Arriendo, Compra insumos'
            }),
            'monto': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Valor del gasto'
            }),
        }


class ProductoForm(forms.ModelForm):
    class Meta:
        model = Producto
        fields = [
            'nombre', 
            'referencia', 
            'descripcion', 
            'categoria', 
            'stock',
            'tipo_venta', 
            'peso_producto', 
            'precio_costo',
            'precio_detal', 
            'precio_semimayor', 
            'precio_mayor',
            'precio_por_gramo_detal', 
            'precio_por_gramo_semimayor', 
            'precio_por_gramo_mayor',  # Corregido aquí
            'imagen_principal', 
            'destacado'
        ]
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nombre de la joya'}),
            'referencia': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'SKU / Referencia'}),
            'descripcion': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Descripción del producto'}),
            'categoria': forms.Select(attrs={'class': 'form-control'}),
            'stock': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'tipo_venta': forms.Select(attrs={'class': 'form-control', 'id': 'id_tipo_venta'}),
            'peso_producto': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': 'Ej: 4.50'}),
            'precio_costo': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Costo total o por gramo'}),
            'precio_detal': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Precio final detal'}),
            'precio_semimayor': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Precio final semimayor'}),
            'precio_mayor': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Precio final mayor'}),
            'precio_por_gramo_detal': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Valor gramo detal'}),
            'precio_por_gramo_semimayor': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Valor gramo semimayor'}),
            'precio_por_gramo_mayor': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Valor gramo mayor'}),
            'imagen_principal': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'destacado': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
