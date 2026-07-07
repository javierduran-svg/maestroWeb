"""contabilidad partida doble

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-06-29 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c8d9e0f1a2'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'centros_costo',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('codigo', sa.String(length=50), nullable=False),
        sa.Column('nombre', sa.String(length=150), nullable=False),
        sa.Column('activo', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('empresa_id', 'codigo', name='uq_centro_costo_empresa_codigo'),
    )
    op.create_table(
        'cuentas_contables',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('codigo', sa.String(length=50), nullable=False),
        sa.Column('nombre', sa.String(length=150), nullable=False),
        sa.Column('tipo', sa.String(length=20), nullable=False),
        sa.Column('clasificacion_sii', sa.String(length=50), nullable=True),
        sa.Column('id_padre', sa.Integer(), nullable=True),
        sa.Column('es_imputable', sa.Boolean(), nullable=False),
        sa.Column('activa', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id']),
        sa.ForeignKeyConstraint(['id_padre'], ['cuentas_contables.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('empresa_id', 'codigo', name='uq_cuenta_contable_empresa_codigo'),
    )
    op.create_table(
        'comprobantes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('tipo', sa.String(length=20), nullable=False),
        sa.Column('numero', sa.Integer(), nullable=False),
        sa.Column('numero_formateado', sa.String(length=20), nullable=False),
        sa.Column('anio', sa.Integer(), nullable=False),
        sa.Column('glosa', sa.Text(), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('moneda_origen', sa.String(length=3), nullable=False),
        sa.Column('tipo_cambio', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'empresa_id', 'tipo', 'anio', 'numero',
            name='uq_comprobante_empresa_tipo_anio_numero',
        ),
    )
    op.create_table(
        'lineas_comprobante',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('comprobante_id', sa.Integer(), nullable=False),
        sa.Column('cuenta_contable_id', sa.Integer(), nullable=False),
        sa.Column('debe', sa.Float(), nullable=False),
        sa.Column('haber', sa.Float(), nullable=False),
        sa.Column('glosa_linea', sa.String(length=255), nullable=True),
        sa.Column('centro_costo_id', sa.Integer(), nullable=True),
        sa.Column('proyecto_id', sa.Integer(), nullable=True),
        sa.Column('rut_asociado', sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(['centro_costo_id'], ['centros_costo.id']),
        sa.ForeignKeyConstraint(['comprobante_id'], ['comprobantes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['cuenta_contable_id'], ['cuentas_contables.id']),
        sa.ForeignKeyConstraint(['proyecto_id'], ['proyectos.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('lineas_comprobante')
    op.drop_table('comprobantes')
    op.drop_table('cuentas_contables')
    op.drop_table('centros_costo')
