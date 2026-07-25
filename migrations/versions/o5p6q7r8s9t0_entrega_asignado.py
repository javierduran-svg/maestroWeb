"""Responsable asignado a entregas programadas

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-07-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'o5p6q7r8s9t0'
down_revision = 'n4o5p6q7r8s9'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    cols = {c['name'] for c in sa.inspect(bind).get_columns('entregas_programadas')}
    if 'asignado_id' not in cols:
        with op.batch_alter_table('entregas_programadas', schema=None) as batch_op:
            batch_op.add_column(sa.Column('asignado_id', sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                'fk_entregas_programadas_asignado_id_trabajadores',
                'trabajadores',
                ['asignado_id'],
                ['id'],
            )


def downgrade():
    with op.batch_alter_table('entregas_programadas', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_entregas_programadas_asignado_id_trabajadores',
            type_='foreignkey',
        )
        batch_op.drop_column('asignado_id')
