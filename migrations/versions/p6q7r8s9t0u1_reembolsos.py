"""Tabla reembolsos (gastos reembolsables)

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-08-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'p6q7r8s9t0u1'
down_revision = 'o5p6q7r8s9t0'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'reembolsos' in tables:
        return
    op.create_table(
        'reembolsos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('trabajador_id', sa.Integer(), nullable=False),
        sa.Column('proyecto_id', sa.Integer(), nullable=True),
        sa.Column('fecha_gasto', sa.Date(), nullable=False),
        sa.Column('monto', sa.Float(), nullable=False),
        sa.Column('descripcion', sa.String(length=500), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('adjunto_path', sa.String(length=255), nullable=True),
        sa.Column('adjunto_nombre', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('reembolsado_at', sa.DateTime(), nullable=True),
        sa.Column('reembolsado_por_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id']),
        sa.ForeignKeyConstraint(['trabajador_id'], ['trabajadores.id']),
        sa.ForeignKeyConstraint(['proyecto_id'], ['proyectos.id']),
        sa.ForeignKeyConstraint(['reembolsado_por_id'], ['trabajadores.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('reembolsos', schema=None) as batch_op:
        batch_op.create_index('ix_reembolsos_empresa_id', ['empresa_id'])
        batch_op.create_index('ix_reembolsos_trabajador_id', ['trabajador_id'])
        batch_op.create_index('ix_reembolsos_status', ['status'])


def downgrade():
    op.drop_table('reembolsos')
