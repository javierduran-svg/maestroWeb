"""Datos de facturación del cliente (mandante)

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-10-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 's9t0u1v2w3x4'
down_revision = 'r8s9t0u1v2w3'
branch_labels = None
depends_on = None

_COLUMNAS = (
    ('giro', sa.String(length=150)),
    ('direccion', sa.String(length=255)),
    ('comuna', sa.String(length=80)),
    ('ciudad', sa.String(length=80)),
    ('email', sa.String(length=120)),
)


def upgrade():
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'clientes' not in tables:
        return
    cols = {c['name'] for c in sa.inspect(bind).get_columns('clientes')}
    with op.batch_alter_table('clientes', schema=None) as batch_op:
        for nombre, tipo in _COLUMNAS:
            if nombre not in cols:
                batch_op.add_column(sa.Column(nombre, tipo, nullable=True))


def downgrade():
    with op.batch_alter_table('clientes', schema=None) as batch_op:
        for nombre, _tipo in reversed(_COLUMNAS):
            batch_op.drop_column(nombre)
