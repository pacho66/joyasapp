from django import template

register = template.Library()

@register.filter
def moneda(value):
    try:
        value = float(value)
        return f"${value:,.0f}".replace(",", ".")
    except:
        return value