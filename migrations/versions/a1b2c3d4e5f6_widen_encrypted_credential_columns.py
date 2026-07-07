"""widen encrypted credential columns

Revision ID: a1b2c3d4e5f6
Revises: 9eed27e1d3de
Create Date: 2026-06-29 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '9eed27e1d3de'
branch_labels = None
depends_on = None

_ENCRYPTED_COLS = (
    ('empresa_sii_config', 'api_key'),
    ('empresa_sii_config', 'password'),
    ('empresa_sii_config', 'certificado_password'),
    ('empresa_banco_conexiones', 'fintoc_api_key'),
    ('empresa_banco_conexiones', 'fintoc_link_token'),
)


def upgrade():
    for table, column in _ENCRYPTED_COLS:
        op.alter_column(
            table,
            column,
            existing_type=sa.String(length=255),
            type_=sa.String(length=512),
            existing_nullable=True,
        )


def downgrade():
    for table, column in _ENCRYPTED_COLS:
        op.alter_column(
            table,
            column,
            existing_type=sa.String(length=512),
            type_=sa.String(length=255),
            existing_nullable=True,
        )
