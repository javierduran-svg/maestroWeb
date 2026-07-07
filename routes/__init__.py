"""Flask blueprints for MaestroWeb API."""

from routes.configuracion import bp as configuracion_bp
from routes.contabilidad import bp as contabilidad_bp
from routes.finanzas import bp as finanzas_bp
from routes.gestion import bp as gestion_bp
from routes.proyectos import bp as proyectos_bp
from routes.rrhh import bp as rrhh_bp
from routes.time_tracker import bp as time_tracker_bp

ALL_BLUEPRINTS = (
    configuracion_bp,
    contabilidad_bp,
    finanzas_bp,
    gestion_bp,
    proyectos_bp,
    rrhh_bp,
    time_tracker_bp,
)

__all__ = [
    'ALL_BLUEPRINTS',
    'configuracion_bp',
    'contabilidad_bp',
    'finanzas_bp',
    'gestion_bp',
    'proyectos_bp',
    'rrhh_bp',
    'time_tracker_bp',
]
