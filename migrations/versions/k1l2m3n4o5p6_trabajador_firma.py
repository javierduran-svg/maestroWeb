"""trabajador firma_path

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-07-13 20:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'k1l2m3n4o5p6'
down_revision = 'j0k1l2m3n4o5'
branch_labels = None
depends_on = None


def upgrade():
    # Idempotente: si la columna ya existe (p.ej. añadida por el guard de
    # arranque para SQLite), no se vuelve a agregar para evitar un error.
    bind = op.get_bind()
    cols = {c['name'] for c in sa.inspect(bind).get_columns('trabajadores')}
    if 'firma_path' not in cols:
        with op.batch_alter_table('trabajadores', schema=None) as batch_op:
            batch_op.add_column(sa.Column('firma_path', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('trabajadores', schema=None) as batch_op:
        batch_op.drop_column('firma_path')
