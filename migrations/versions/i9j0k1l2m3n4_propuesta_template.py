"""propuesta template_html calculadora_json plantillas_propuesta

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-07-10 20:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'i9j0k1l2m3n4'
down_revision = 'h8i9j0k1l2m3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('propuestas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('template_html', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('calculadora_json', sa.Text(), nullable=True))

    op.create_table(
        'plantillas_propuesta',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('servicio', sa.String(length=100), nullable=False),
        sa.Column('contenido_html', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('empresa_id', 'servicio', name='uq_plantilla_empresa_servicio'),
    )


def downgrade():
    op.drop_table('plantillas_propuesta')
    with op.batch_alter_table('propuestas', schema=None) as batch_op:
        batch_op.drop_column('calculadora_json')
        batch_op.drop_column('template_html')
