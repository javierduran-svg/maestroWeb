"""estado de pago documento: UF, plantilla, template_html

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-07-15 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'l2m3n4o5p6q7'
down_revision = 'k1l2m3n4o5p6'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    cols = {c['name'] for c in sa.inspect(bind).get_columns('movimientos')}
    with op.batch_alter_table('movimientos', schema=None) as batch_op:
        if 'monto_uf' not in cols:
            batch_op.add_column(sa.Column('monto_uf', sa.Float(), nullable=True))
        if 'valor_uf' not in cols:
            batch_op.add_column(sa.Column('valor_uf', sa.Float(), nullable=True))
        if 'numero_ep' not in cols:
            batch_op.add_column(sa.Column('numero_ep', sa.Integer(), nullable=True))
        if 'atencion_de' not in cols:
            batch_op.add_column(sa.Column('atencion_de', sa.String(length=150), nullable=True))
        if 'notas_ep' not in cols:
            batch_op.add_column(sa.Column('notas_ep', sa.Text(), nullable=True))
        if 'incluir_iva' not in cols:
            batch_op.add_column(sa.Column('incluir_iva', sa.Boolean(), nullable=True))
        if 'template_html' not in cols:
            batch_op.add_column(sa.Column('template_html', sa.Text(), nullable=True))

    tables = set(sa.inspect(bind).get_table_names())
    if 'plantillas_estado_pago' not in tables:
        op.create_table(
            'plantillas_estado_pago',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('empresa_id', sa.Integer(), nullable=False),
            sa.Column('contenido_html', sa.Text(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('empresa_id', name='uq_plantilla_ep_empresa'),
        )


def downgrade():
    op.drop_table('plantillas_estado_pago')
    with op.batch_alter_table('movimientos', schema=None) as batch_op:
        batch_op.drop_column('template_html')
        batch_op.drop_column('incluir_iva')
        batch_op.drop_column('notas_ep')
        batch_op.drop_column('atencion_de')
        batch_op.drop_column('numero_ep')
        batch_op.drop_column('valor_uf')
        batch_op.drop_column('monto_uf')
