"""MaestroWeb Flask application factory."""

import os

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

from bootstrap import configure_app, get_database_url
from common import AUTH_EXEMPT_PREFIXES, _migrar_schema
from routes import ALL_BLUEPRINTS


def create_app(config_name=None):
    """Create and configure the Flask application."""
    app = Flask(__name__, template_folder='.')
    configure_app(app)
    if os.environ.get('BEHIND_PROXY', '').strip().lower() in ('1', 'true', 'yes'):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    db_url = get_database_url()
    if db_url.startswith('postgresql'):
        app.logger.info('Base de datos: PostgreSQL (%s)', db_url.rsplit('@', 1)[-1])
    else:
        app.logger.warning('Base de datos: SQLite (%s) — configure DATABASE_URL para Postgres', db_url)

    for blueprint in ALL_BLUEPRINTS:
        app.register_blueprint(blueprint)

    @app.before_request
    def _requiere_autenticacion():
        if request.method == 'OPTIONS':
            return None
        path = request.path
        if not path.startswith('/api/'):
            return None
        for prefix in AUTH_EXEMPT_PREFIXES:
            if path == prefix or path.startswith(prefix + '/'):
                return None
        if not session.get('trabajador_id'):
            return jsonify({'error': 'No autenticado'}), 401
        return None

    return app


app = create_app()


if __name__ == '__main__':
    with app.app_context():
        _migrar_schema()
    app.run(debug=True, port=5000)
