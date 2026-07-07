"""empresa plan_cuentas_template

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-30 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('empresas', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'plan_cuentas_template',
                sa.String(length=40),
                nullable=False,
                server_default='sociedad_profesionales',
            ),
        )


def downgrade():
    with op.batch_alter_table('empresas', schema=None) as batch_op:
        batch_op.drop_column('plan_cuentas_template')
