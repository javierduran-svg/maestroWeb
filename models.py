from datetime import datetime

from extensions import db
from security import EncryptedString


class Empresa(db.Model):
    __tablename__ = 'empresas'
    id = db.Column(db.Integer, primary_key=True)
    rut = db.Column(db.String(20), unique=True, nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    direccion = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    telefono = db.Column(db.String(30), nullable=True)
    giro = db.Column(db.String(150), nullable=True)
    activa = db.Column(db.Boolean, default=True, nullable=False)
    logo_filename = db.Column(db.String(100), nullable=True)
    plan_cuentas_template = db.Column(
        db.String(40), default='sociedad_profesionales', nullable=False,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Cliente(db.Model):
    __tablename__ = 'clientes'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    razon_social = db.Column(db.String(150), nullable=False)
    rut = db.Column(db.String(20), nullable=False)
    comentarios = db.Column(db.Text, nullable=True)
    proyectos = db.relationship('Proyecto', backref='cliente_rel', lazy=True)

    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'rut', name='uq_cliente_empresa_rut'),
    )


class Proyecto(db.Model):
    __tablename__ = 'proyectos'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    superficie = db.Column(db.Float, nullable=False)
    servicio = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='Activo')
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)
    monto_contrato = db.Column(db.Float, default=0.0)
    monto_pagado = db.Column(db.Float, default=0.0)
    monto_facturado = db.Column(db.Float, default=0.0)
    saldo_por_facturar = db.Column(db.Float, default=0.0)
    monto_gastos = db.Column(db.Float, default=0.0)
    movimientos = db.relationship('Movimiento', backref='proyecto_rel', lazy=True)
    entregas = db.relationship(
        'EntregaProgramada', backref='proyecto_rel', lazy=True, cascade='all, delete-orphan',
    )


class EntregaProgramada(db.Model):
    """Entrega programada de un proyecto (planificación, no contable)."""
    __tablename__ = 'entregas_programadas'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    proyecto_id = db.Column(db.Integer, db.ForeignKey('proyectos.id'), nullable=False)
    fecha_entrega = db.Column(db.Date, nullable=False)
    descripcion = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='Por Hacer', nullable=False)
    tareas = db.relationship(
        'TareaEntrega', backref='entrega_rel', lazy=True, cascade='all, delete-orphan',
    )


class TareaEntrega(db.Model):
    """Tarea asociada a una entrega programada."""
    __tablename__ = 'tareas_entrega'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    entrega_id = db.Column(db.Integer, db.ForeignKey('entregas_programadas.id'), nullable=False)
    descripcion = db.Column(db.String(500), nullable=True)
    asignado_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=True)
    fecha_limite = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), default='Pendiente', nullable=False)


class Propuesta(db.Model):
    __tablename__ = 'propuestas'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    numero = db.Column(db.Integer, nullable=False)
    nombre = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(30), default='No enviada')
    contacto_bgreen = db.Column(db.String(100), nullable=True)
    cliente_nombre = db.Column(db.String(150), nullable=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=True)
    contacto_cliente = db.Column(db.String(100), nullable=True)
    servicio = db.Column(db.String(100), nullable=True)
    detalle_servicio = db.Column(db.String(200), nullable=True)
    superficie_m2 = db.Column(db.Float, nullable=True)
    unidades = db.Column(db.Float, nullable=True)
    monto_uf = db.Column(db.Float, nullable=True)
    monto_pesos = db.Column(db.Float, default=0.0)
    fecha_envio = db.Column(db.Date, nullable=True)
    fecha_adjudicacion = db.Column(db.Date, nullable=True)

    cliente_rel = db.relationship('Cliente', backref='propuestas')

    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'numero', name='uq_propuesta_empresa_numero'),
    )


class Cuenta(db.Model):
    __tablename__ = 'cuentas'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    categoria = db.Column(db.String(50), nullable=False)
    moneda = db.Column(db.String(3), default='CLP', nullable=False)
    saldo_inicial = db.Column(db.Float, default=0.0, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'nombre', name='uq_cuenta_empresa_nombre'),
    )


class Movimiento(db.Model):
    __tablename__ = 'movimientos'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    fecha_movimiento = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    fecha_estado_pago = db.Column(db.Date, nullable=True)
    fecha_facturacion = db.Column(db.Date, nullable=True)
    monto_pesos = db.Column(db.Float, nullable=False)
    centro_costo = db.Column(db.String(50), nullable=False)
    estado = db.Column(db.String(20), default='Activo')
    clase = db.Column(db.String(20), default='general', nullable=False)
    cta_origen_id = db.Column(db.Integer, db.ForeignKey('cuentas.id'), nullable=False)
    cta_destino_id = db.Column(db.Integer, db.ForeignKey('cuentas.id'), nullable=False)
    transaccion = db.Column(db.String(20), nullable=False)
    descripcion = db.Column(db.String(255), nullable=True)
    numero_factura = db.Column(db.String(50), nullable=True)
    status_pago = db.Column(db.String(30), nullable=True)
    condicion_pago_dias = db.Column(db.Integer, default=30, nullable=True)
    proyecto_id = db.Column(db.Integer, db.ForeignKey('proyectos.id'), nullable=True)

    cta_origen = db.relationship('Cuenta', foreign_keys=[cta_origen_id])
    cta_destino = db.relationship('Cuenta', foreign_keys=[cta_destino_id])


class Trabajador(db.Model):
    __tablename__ = 'trabajadores'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    rut = db.Column(db.String(20), nullable=False)
    apellido_paterno = db.Column(db.String(100), nullable=False)
    apellido_materno = db.Column(db.String(100), nullable=True)
    nombres = db.Column(db.String(100), nullable=False)
    alias = db.Column(db.String(100), nullable=True)
    fecha_ingreso = db.Column(db.Date, nullable=False)
    tipo_contrato = db.Column(db.String(20), nullable=False)
    sueldo_base = db.Column(db.Float, nullable=False)
    sueldo_base_uf = db.Column(db.Float, default=0.0)
    banco = db.Column(db.String(100), nullable=True)
    cuenta_bancaria = db.Column(db.String(50), nullable=True)
    nombre_isapre = db.Column(db.String(100), nullable=True)
    nombre_plan_isapre = db.Column(db.String(100), nullable=True)
    afp = db.Column(db.String(50), nullable=False)
    sistema_salud = db.Column(db.String(20), nullable=False)
    valor_plan_isapre_uf = db.Column(db.Float, default=0.0)
    cuenta_gasto_id = db.Column(db.Integer, db.ForeignKey('cuentas.id'), nullable=False)
    email = db.Column(db.String(255), nullable=True, unique=True)
    password_hash = db.Column(db.String(255), nullable=True)
    rol = db.Column(db.String(20), default='trabajador', nullable=False)
    factor_overhead = db.Column(db.Float, default=1.0, nullable=False)
    costo_hh_manual = db.Column(db.Float, nullable=True)
    foto_path = db.Column(db.String(255), nullable=True)

    cuenta_gasto = db.relationship('Cuenta')
    liquidaciones = db.relationship('Liquidacion', backref='trabajador_rel', lazy=True)

    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'rut', name='uq_trabajador_empresa_rut'),
    )

    @property
    def costo_hh_real(self) -> float:
        if self.costo_hh_manual is not None:
            return float(self.costo_hh_manual)
        sueldo = float(self.sueldo_base or 0)
        if sueldo <= 0:
            return 0.0
        return (sueldo / 160) * float(self.factor_overhead or 1.0)


class ValorUF(db.Model):
    """Valor de la UF (Unidad de Fomento) asociado a una fecha calendario."""
    __tablename__ = 'valores_uf'
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, unique=True, nullable=False)
    valor = db.Column(db.Float, nullable=False)


class Liquidacion(db.Model):
    __tablename__ = 'liquidaciones'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False)
    mes = db.Column(db.Integer, nullable=False)
    anio = db.Column(db.Integer, nullable=False)
    dias_trabajados = db.Column(db.Integer, nullable=False)
    sueldo_base_proporcional = db.Column(db.Float, nullable=False)
    total_imponible = db.Column(db.Float, nullable=False)
    total_haberes = db.Column(db.Float, nullable=False)
    total_descuentos = db.Column(db.Float, nullable=False)
    alcance_liquido = db.Column(db.Float, nullable=False)
    estado = db.Column(db.String(20), default='Borrador')
    uf_valor = db.Column(db.Float, nullable=True)
    sueldo_base_uf = db.Column(db.Float, nullable=True)
    detalle_calculo = db.Column(db.Text, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('trabajador_id', 'mes', 'anio', name='uq_liquidacion_trabajador_periodo'),
    )


class EmpresaSIIConfig(db.Model):
    """Credenciales SII / SimpleAPI por empresa."""
    __tablename__ = 'empresa_sii_config'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False, unique=True)
    api_key = db.Column(EncryptedString(512), nullable=True)
    rut_emisor = db.Column(db.String(20), nullable=True)
    usuario = db.Column(db.String(20), nullable=True)
    password = db.Column(EncryptedString(512), nullable=True)
    certificado_path = db.Column(db.String(500), nullable=True)
    certificado_password = db.Column(EncryptedString(512), nullable=True)
    rut_certificado = db.Column(db.String(20), nullable=True)
    certificado_b64 = db.Column(db.Text, nullable=True)
    ambiente = db.Column(db.Integer, default=0, nullable=False)
    rcv_base_url = db.Column(db.String(255), nullable=True)
    api_base_url = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmpresaBancoConexion(db.Model):
    """Conexión bancaria Fintoc por empresa (puede haber varias)."""
    __tablename__ = 'empresa_banco_conexiones'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    fintoc_api_key = db.Column(EncryptedString(512), nullable=True)
    fintoc_link_token = db.Column(EncryptedString(512), nullable=True)
    fintoc_account_id = db.Column(db.String(100), nullable=True)
    cuenta_contable_id = db.Column(db.Integer, db.ForeignKey('cuentas.id'), nullable=True)
    activa = db.Column(db.Boolean, default=True, nullable=False)
    ultima_sincronizacion = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    cuenta_contable = db.relationship('Cuenta', foreign_keys=[cuenta_contable_id])


class CentroCosto(db.Model):
    """Centro de costo analítico por empresa."""
    __tablename__ = 'centros_costo'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    codigo = db.Column(db.String(50), nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'codigo', name='uq_centro_costo_empresa_codigo'),
    )


class CuentaContable(db.Model):
    """Plan de cuentas contable (partida doble), jerárquico por empresa."""
    __tablename__ = 'cuentas_contables'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    codigo = db.Column(db.String(50), nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    clasificacion_sii = db.Column(db.String(50), nullable=True)
    id_padre = db.Column(db.Integer, db.ForeignKey('cuentas_contables.id'), nullable=True)
    es_imputable = db.Column(db.Boolean, default=True, nullable=False)
    activa = db.Column(db.Boolean, default=True, nullable=False)

    cuenta_padre = db.relationship(
        'CuentaContable', remote_side=[id], backref='cuentas_hijas', lazy=True,
    )

    __table_args__ = (
        db.UniqueConstraint('empresa_id', 'codigo', name='uq_cuenta_contable_empresa_codigo'),
    )


class Comprobante(db.Model):
    """Comprobante contable de partida doble (ingreso, egreso o traspaso)."""
    __tablename__ = 'comprobantes'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    fecha = db.Column(db.Date, nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    numero = db.Column(db.Integer, nullable=False)
    numero_formateado = db.Column(db.String(20), nullable=False)
    anio = db.Column(db.Integer, nullable=False)
    glosa = db.Column(db.Text, nullable=False)
    estado = db.Column(db.String(20), default='Borrador', nullable=False)
    moneda_origen = db.Column(db.String(3), default='CLP', nullable=False)
    tipo_cambio = db.Column(db.Float, default=1.0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=True)

    lineas = db.relationship(
        'LineaComprobante', backref='comprobante_rel', lazy=True, cascade='all, delete-orphan',
    )

    __table_args__ = (
        db.UniqueConstraint(
            'empresa_id', 'tipo', 'anio', 'numero',
            name='uq_comprobante_empresa_tipo_anio_numero',
        ),
    )


class RegistroTiempo(db.Model):
    """Registro de tiempo trabajado en proyecto / entrega / tarea."""
    __tablename__ = 'registros_tiempo'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresas.id'), nullable=False)
    trabajador_id = db.Column(db.Integer, db.ForeignKey('trabajadores.id'), nullable=False)
    proyecto_id = db.Column(db.Integer, db.ForeignKey('proyectos.id'), nullable=False)
    entrega_id = db.Column(db.Integer, db.ForeignKey('entregas_programadas.id'), nullable=True)
    tarea_id = db.Column(db.Integer, db.ForeignKey('tareas_entrega.id'), nullable=True)
    inicio = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ultimo_inicio = db.Column(db.DateTime, nullable=True)
    fin = db.Column(db.DateTime, nullable=True)
    duracion_segundos = db.Column(db.Integer, default=0, nullable=False)
    estado = db.Column(db.String(20), default='activo', nullable=False)
    notas = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    trabajador = db.relationship('Trabajador', lazy=True)
    proyecto = db.relationship('Proyecto', lazy=True)
    entrega = db.relationship('EntregaProgramada', lazy=True)
    tarea = db.relationship('TareaEntrega', lazy=True)


class LineaComprobante(db.Model):
    """Línea de debe/haber en un comprobante contable (montos siempre en CLP)."""
    __tablename__ = 'lineas_comprobante'
    id = db.Column(db.Integer, primary_key=True)
    comprobante_id = db.Column(
        db.Integer, db.ForeignKey('comprobantes.id', ondelete='CASCADE'), nullable=False,
    )
    cuenta_contable_id = db.Column(db.Integer, db.ForeignKey('cuentas_contables.id'), nullable=False)
    debe = db.Column(db.Float, default=0.0, nullable=False)
    haber = db.Column(db.Float, default=0.0, nullable=False)
    glosa_linea = db.Column(db.String(255), nullable=True)
    centro_costo_id = db.Column(db.Integer, db.ForeignKey('centros_costo.id'), nullable=True)
    proyecto_id = db.Column(db.Integer, db.ForeignKey('proyectos.id'), nullable=True)
    rut_asociado = db.Column(db.String(20), nullable=True)

    cuenta_contable = db.relationship('CuentaContable', lazy=True)
    centro_costo = db.relationship('CentroCosto', lazy=True)
    proyecto = db.relationship('Proyecto', lazy=True)
