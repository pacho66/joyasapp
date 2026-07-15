# motores/fiscal.py
from decimal import Decimal
from productos.models import ConfiguracionEmpresa

class MotorFiscal:
    """
    Motor de cálculo unificado y dinámico para JoyasApp.
    Determina impuestos, retenciones y logística de envíos de forma centralizada.
    """
    def __init__(self, config: ConfiguracionEmpresa):
        # Configuración Fiscal
        self.aplica_iva = config.responsable_iva
        self.porcentaje_iva = Decimal(str(config.porcentaje_iva or 19))
        self.mostrar_iva_disc = config.mostrar_iva_discriminado
        
        self.es_retenedor = config.es_retenedor
        self.porcentaje_retefuente = Decimal(str(config.porcentaje_retefuente or '2.50'))
        
        # Configuración Logística
        self.cobrar_envio = config.cobrar_envio
        self.costo_envio_estandar = Decimal(str(config.costo_envio_estandar or 0))
        self.envio_gratis_desde = Decimal(str(config.envio_gratis_desde or 0))

    def resolver_envio(self, tipo_envio: str, subtotal_con_descuento: Decimal, valor_personalizado: Decimal = None) -> Decimal:
        """
        Determina el costo de envío encapsulando las políticas del negocio.
        No requiere que la vista conozca las reglas internas de envío gratis.
        """
        if tipo_envio == 'recogida' or not self.cobrar_envio:
            return Decimal('0.00')
            
        if tipo_envio == 'personalizado':
            # Si es personalizado, se respeta el valor enviado o se asume 0 si es nulo
            return Decimal(str(valor_personalizado or 0))
            
        # Tipo 'estandar' (Aplica umbral de envío gratis si está configurado)
        if self.envio_gratis_desde > 0 and subtotal_con_descuento >= self.envio_gratis_desde:
            return Decimal('0.00')
            
        return self.costo_envio_estandar

    def calcular_totales_pedido(self, subtotal_base: Decimal, porcentaje_descuento: Decimal = Decimal('0'), tipo_envio: str = 'estandar', valor_envio_personalizado: Decimal = None) -> dict:
        """
        Calcula la cascada fiscal completa.
        """
        # 1. Aplicar Descuento Comercial
        descuento_total = subtotal_base * (porcentaje_descuento / Decimal('100'))
        subtotal_con_descuento = subtotal_base - descuento_total
        
        # 2. Desglose de Base Gravable e IVA
        if self.aplica_iva:
            if self.mostrar_iva_disc:
                # IVA Incluido: Desglose para extraer la base imponible real
                base_gravable = subtotal_con_descuento / (Decimal('1') + (self.porcentaje_iva / Decimal('100')))
                iva_total = subtotal_con_descuento - base_gravable
                subtotal_factura = base_gravable
                total_antes_de_envio = subtotal_con_descuento
            else:
                # IVA Adicional: Se calcula por encima
                base_gravable = subtotal_con_descuento
                iva_total = subtotal_con_descuento * (self.porcentaje_iva / Decimal('100'))
                subtotal_factura = subtotal_con_descuento
                total_antes_de_envio = subtotal_con_descuento + iva_total
        else:
            base_gravable = subtotal_con_descuento
            iva_total = Decimal('0.00')
            subtotal_factura = subtotal_con_descuento
            total_antes_de_envio = subtotal_con_descuento

        # 3. Retención en la Fuente (Dinámica y estrictamente sobre Base Gravable)
        if self.es_retenedor:
            factor_retencion = self.porcentaje_retefuente / Decimal('100')
            retefuente_total = base_gravable * factor_retencion
        else:
            retefuente_total = Decimal('0.00')

        # 4. Resolver Envío (Lógica interna del motor)
        costo_envio = self.resolver_envio(tipo_envio, subtotal_con_descuento, valor_envio_personalizado)

        # 5. Gran Total Neto
        total_final = max(Decimal('0.00'), total_antes_de_envio + costo_envio - retefuente_total)

        return {
            'subtotal': subtotal_factura,
            'iva_total': iva_total,
            'porcentaje_iva': self.porcentaje_iva,
            'descuento_total': descuento_total,
            'porcentaje_descuento': porcentaje_descuento,
            'retefuente_total': retefuente_total,
            'porcentaje_retefuente': self.porcentaje_retefuente,
            'costo_envio': costo_envio,
            'tipo_envio': tipo_envio,
            'total_final': total_final,
        }

    def calcular_valores_item(self, subtotal_item: Decimal) -> dict:
        """
        Cálculo fiscal por item individual para guardar en histórico de PedidoItem.
        """
        if self.aplica_iva:
            if self.mostrar_iva_disc:
                base_item = subtotal_item / (Decimal('1') + (self.porcentaje_iva / Decimal('100')))
                iva_item = subtotal_item - base_item
                total_item = subtotal_item
            else:
                base_item = subtotal_item
                iva_item = subtotal_item * (self.porcentaje_iva / Decimal('100'))
                total_item = subtotal_item + iva_item
        else:
            base_item = subtotal_item
            iva_item = Decimal('0.00')
            total_item = subtotal_item

        if self.es_retenedor:
            factor_retencion = self.porcentaje_retefuente / Decimal('100')
            retefuente_item = base_item * factor_retencion
        else:
            retefuente_item = Decimal('0.00')

        total_item = max(Decimal('0.00'), total_item - retefuente_item)

        return {
            'base_item': base_item,
            'iva_item': iva_item,
            'retefuente_item': retefuente_item,
            'total_item': total_item
        }