from decimal import Decimal


class MotorFiscal:
    """
    Servicio encargado de liquidar impuestos (IVA, Retefuente),
    descuentos promocionales y costos de envío según las reglas fijadas en Perfil.
    """

    def __init__(self, perfil_empresa=None, *args, **kwargs):
        self.perfil = perfil_empresa

    def calcular_descuento(self, subtotal: Decimal) -> Decimal:
        """Calcula el descuento promocional si la campaña está activa."""
        if (
            self.perfil
            and getattr(self.perfil, 'aplicar_descuentos', False)
            and getattr(self.perfil, 'porcentaje_descuento_promo', 0) > 0
        ):
            porcentaje = Decimal(
                str(self.perfil.porcentaje_descuento_promo)
            ) / Decimal('100.00')
            return (subtotal * porcentaje).quantize(Decimal('0.01'))
        return Decimal('0.00')

    def calcular_envio(
        self, subtotal_neto: Decimal, tipo_envio: str = 'envio'
    ) -> Decimal:
        """Calcula el costo de envío aplicando reglas de envío gratis o tarifa estándar."""
        if (
            not self.perfil
            or tipo_envio == 'recogida'
            or not getattr(self.perfil, 'cobrar_envio', False)
        ):
            return Decimal('0.00')

        envio_gratis_desde = getattr(self.perfil, 'envio_gratis_desde', None)
        if envio_gratis_desde and subtotal_neto >= envio_gratis_desde:
            return Decimal('0.00')

        return Decimal(str(getattr(self.perfil, 'costo_envio_estandar', 0) or 0))

    def calcular_impuestos(self, subtotal_neto: Decimal):
        """Devuelve una tupla (IVA, Retefuente) según la responsabilidad fiscal del comercio."""
        iva = Decimal('0.00')
        retefuente = Decimal('0.00')

        if not self.perfil:
            return iva, retefuente

        # 1. Cálculo de IVA
        if getattr(self.perfil, 'responsable_iva', False) and getattr(
            self.perfil, 'porcentaje_iva', 0
        ) > 0:
            porcentaje_iva = Decimal(
                str(self.perfil.porcentaje_iva)
            ) / Decimal('100.00')
            iva = (subtotal_neto * porcentaje_iva).quantize(Decimal('0.01'))

        # 2. Cálculo de Retefuente
        config_fiscal = getattr(self.perfil, 'configuracion_fiscal', None)
        if (
            config_fiscal
            and getattr(config_fiscal, 'es_retenedor', False)
            and getattr(config_fiscal, 'porcentaje_retefuente', 0) > 0
        ):
            porcentaje_rete = Decimal(
                str(config_fiscal.porcentaje_retefuente)
            ) / Decimal('100.00')
            retefuente = (subtotal_neto * porcentaje_rete).quantize(
                Decimal('0.01')
            )

        return iva, retefuente

    def calcular_valores_item(self, subtotal_item: Decimal) -> dict:
        """Calcula el IVA y retenciones específicas para un ítem dado."""
        subtotal = Decimal(str(subtotal_item or '0.00'))
        iva, retefuente = self.calcular_impuestos(subtotal)
        total_item = subtotal + iva - retefuente
        return {
            'iva_item': iva,
            'retefuente_item': retefuente,
            'total_item': max(Decimal('0.00'), total_item)
        }

    def liquidar_pedido(
        self, subtotal_base: Decimal, tipo_envio: str = 'envio'
    ) -> dict:
        """
        Calcula de principio a fin el resumen financiero para un carrito o pedido.
        """
        descuento = self.calcular_descuento(subtotal_base)
        subtotal_neto = max(Decimal('0.00'), subtotal_base - descuento)

        costo_envio = self.calcular_envio(subtotal_neto, tipo_envio)
        iva, retefuente = self.calcular_impuestos(subtotal_neto)

        total_final = subtotal_neto + iva + costo_envio - retefuente

        porcentaje_desc = (
            Decimal(str(getattr(self.perfil, 'porcentaje_descuento_promo', 0) or 0))
            if getattr(self.perfil, 'aplicar_descuentos', False)
            else Decimal('0.00')
        )

        return {
            'subtotal': subtotal_base,
            'subtotal_base': subtotal_base,
            'subtotal_neto': subtotal_neto,
            'descuento_total': descuento,
            'descuento': descuento,
            'porcentaje_descuento': porcentaje_desc,
            'envio': costo_envio,
            'costo_envio': costo_envio,
            'iva_total': iva,
            'iva': iva,
            'retefuente_total': retefuente,
            'retefuente': retefuente,
            'total_final': max(Decimal('0.00'), total_final),
            'total': max(Decimal('0.00'), total_final),
        }


# =========================================================================
# ⚙️ WRAPPER DE COMPATIBILIDAD (DEFINIDO FUERA DE LA CLASE)
# =========================================================================
def calcular_totales_pedido(pedido, config=None, items=None):
    """
    Función helper de compatibilidad para ser usada por vistas y PDFs.
    Instancia MotorFiscal y ejecuta la liquidación completa.
    """
    perfil_empresa = (
        config
        or getattr(pedido, 'perfil', None)
        or getattr(getattr(pedido, 'usuario', None), 'perfil', None)
    )
    motor = MotorFiscal(perfil_empresa)

    lista_items = items if items is not None else getattr(pedido, 'items', None) and pedido.items.all() or []
    if lista_items:
        subtotal_base = sum(
            Decimal(
                str(
                    getattr(
                        item,
                        'total_final',
                        getattr(
                            item,
                            'subtotal',
                            (getattr(item, 'precio', 0) * getattr(item, 'cantidad', 1)),
                        ),
                    )
                    or 0
                )
            )
            for item in lista_items
        )
    else:
        subtotal_base = getattr(pedido, 'subtotal', Decimal('0.00'))

    tipo_envio = getattr(pedido, 'tipo_envio', 'envio')
    return motor.liquidar_pedido(subtotal_base, tipo_envio=tipo_envio)