from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from .models import Gasto


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

    # ==================================
    # VALIDAR USUARIO
    # ==================================
    def clean_username(self):
        username = self.cleaned_data['username'].strip()

        if User.objects.filter(username=username).exists():
            raise ValidationError("Ese usuario ya existe")

        return username

    # ==================================
    # VALIDAR EMAIL
    # ==================================
    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()

        if User.objects.filter(email=email).exists():
            raise ValidationError("Ese correo ya está registrado")

        return email

    # ==================================
    # VALIDAR WHATSAPP
    # ==================================
    def clean_whatsapp(self):
        whatsapp = self.cleaned_data['whatsapp'].strip()

        if len(whatsapp) < 10:
            raise ValidationError("Número inválido")

        return whatsapp

    # ==================================
    # VALIDAR PASSWORDS
    # ==================================
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