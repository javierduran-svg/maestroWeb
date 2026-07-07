"""registro tiempo

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-06-30 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'b7c8d9e0f1a2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'registros_tiempo',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('trabajador_id', sa.Integer(), nullable=False),
        sa.Column('proyecto_id', sa.Integer(), nullable=False),
        sa.Column('entrega_id', sa.Integer(), nullable=True),
        sa.Column('tarea_id', sa.Integer(), nullable=True),
        sa.Column('inicio', sa.DateTime(), nullable=False),
        sa.Column('ultimo_inicio', sa.DateTime(), nullable=True),
        sa.Column('fin', sa.DateTime(), nullable=True),
        sa.Column('duracion_segundos', sa.Integer(), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('notas', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id']),
        sa.ForeignKeyConstraint(['entrega_id'], ['entregas_programadas.id']),
        sa.ForeignKeyConstraint(['proyecto_id'], ['proyectos.id']),
        sa.ForeignKeyConstraint(['tarea_id'], ['tareas_entrega.id']),
        sa.ForeignKeyConstraint(['trabajador_id'], ['trabajadores.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('registros_tiempo')
