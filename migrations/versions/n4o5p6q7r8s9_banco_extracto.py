"""Extracto bancario Fintoc: banco_movimientos + saldos en conexion

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-07-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'n4o5p6q7r8s9'
down_revision = 'm3n4o5p6q7r8'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols_conn = {c['name'] for c in insp.get_columns('empresa_banco_conexiones')}
    with op.batch_alter_table('empresa_banco_conexiones', schema=None) as batch_op:
        if 'saldo_disponible' not in cols_conn:
            batch_op.add_column(sa.Column('saldo_disponible', sa.Float(), nullable=True))
        if 'saldo_contable' not in cols_conn:
            batch_op.add_column(sa.Column('saldo_contable', sa.Float(), nullable=True))
        if 'saldo_limite' not in cols_conn:
            batch_op.add_column(sa.Column('saldo_limite', sa.Float(), nullable=True))
        if 'saldo_actualizado_at' not in cols_conn:
            batch_op.add_column(sa.Column('saldo_actualizado_at', sa.DateTime(), nullable=True))

    if not insp.has_table('banco_movimientos'):
        op.create_table(
            'banco_movimientos',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('empresa_id', sa.Integer(), nullable=False),
            sa.Column('conexion_id', sa.Integer(), nullable=False),
            sa.Column('fintoc_id', sa.String(length=100), nullable=False),
            sa.Column('fecha', sa.Date(), nullable=False),
            sa.Column('descripcion', sa.String(length=255), nullable=False),
            sa.Column('monto', sa.Float(), nullable=False),
            sa.Column('tipo', sa.String(length=20), nullable=False),
            sa.Column('moneda', sa.String(length=3), nullable=False),
            sa.Column('estado_conciliacion', sa.String(length=20), nullable=False),
            sa.Column('movimiento_id', sa.Integer(), nullable=True),
            sa.Column('synced_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['conexion_id'], ['empresa_banco_conexiones.id']),
            sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id']),
            sa.ForeignKeyConstraint(['movimiento_id'], ['movimientos.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('empresa_id', 'fintoc_id', name='uq_banco_movimiento_empresa_fintoc'),
        )


def downgrade():
    op.drop_table('banco_movimientos')
    with op.batch_alter_table('empresa_banco_conexiones', schema=None) as batch_op:
        batch_op.drop_column('saldo_actualizado_at')
        batch_op.drop_column('saldo_limite')
        batch_op.drop_column('saldo_contable')
        batch_op.drop_column('saldo_disponible')
