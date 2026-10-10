"""Parámetros de liquidación que calzan con el libro de remuneraciones.

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
Create Date: 2026-10-09 23:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 't0u1v2w3x4y5'
down_revision = 's9t0u1v2w3x4'
branch_labels = None
depends_on = None

_COLUMNAS = (
    ('paga_gratificacion', sa.Boolean(), sa.true()),
    ('afecto_cesantia', sa.Boolean(), sa.true()),
    ('sis_cargo_trabajador', sa.Boolean(), sa.false()),
)


def upgrade():
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if 'trabajadores' not in tables:
        return
    cols = {c['name'] for c in sa.inspect(bind).get_columns('trabajadores')}
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        for nombre, tipo, default in _COLUMNAS:
            if nombre not in cols:
                batch_op.add_column(sa.Column(
                    nombre, tipo, nullable=False, server_default=default,
                ))


def downgrade():
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        for nombre, _tipo, _default in reversed(_COLUMNAS):
            batch_op.drop_column(nombre)
