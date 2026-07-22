"""Integración con Fintoc API para movimientos bancarios."""

import os
from datetime import date, datetime

import requests
FINTOC_API_HOST = 'api.fintoc.com'


def mensaje_error_red_fintoc(exc: BaseException) -> str | None:
    """Mensaje claro en español para fallos DNS/conexión (sin exponer URLs ni credenciales)."""
    if isinstance(exc, (requests.Timeout, requests.ConnectTimeout, requests.ReadTimeout)):
        return (
            f'Sin respuesta de {FINTOC_API_HOST} (tiempo de espera agotado). '
            'Verifique su conexión a internet e intente de nuevo.'
        )

    causa = exc
    if isinstance(exc, requests.RequestException) and exc.__cause__ is not None:
        causa = exc.__cause__

    texto = str(exc).lower()
    es_dns = (
        isinstance(exc, requests.ConnectionError)
        or (
            isinstance(causa, OSError)
            and getattr(causa, 'errno', None) in (11001, -2, -3, 11002)
        )
        or any(
            frag in texto
            for frag in (
                'getaddrinfo failed',
                'failed to resolve',
                'nameresolutionerror',
                'nodename nor servname',
                'name or service not known',
                'temporary failure in name resolution',
            )
        )
    )
    if es_dns:
        return (
            f'Sin conexión a internet o no se puede resolver {FINTOC_API_HOST}. '
            'Verifique su red/DNS (por ejemplo 8.8.8.8), desactive VPN si aplica y revise firewall.'
        )

    if isinstance(exc, requests.ConnectionError):
        return (
            f'No se pudo conectar con {FINTOC_API_HOST}. '
            'Verifique su conexión a internet, firewall o proxy.'
        )

    if isinstance(exc, requests.RequestException):
        return (
            f'Error de red al consultar Fintoc ({FINTOC_API_HOST}). '
            'Verifique su conexión e intente de nuevo.'
        )

    return None


class BancoIntegrationError(Exception):
    """Error al comunicarse con la API de Fintoc."""


MOCK_MOVIMIENTOS = [
    {
        'id': 'mock_fintoc_001',
        'fecha': '2025-06-20',
        'descripción': 'Abono cliente proyecto Bosque Real',
        'monto': 2500000,
        'tipo': 'ingreso',
    },
    {
        'id': 'mock_fintoc_002',
        'fecha': '2025-06-18',
        'descripción': 'Pago proveedor insumos oficina',
        'monto': 185000,
        'tipo': 'egreso',
    },
    {
        'id': 'mock_fintoc_003',
        'fecha': '2025-06-12',
        'descripción': 'Comisión mantención cuenta corriente',
        'monto': 12500,
        'tipo': 'egreso',
    },
]


_FINTOC_ERROR_HINTS: dict[str, str] = {
    'invalid_api_key': (
        'Clave API inválida o ausente. Use su Secret Key (sk_test_… o sk_live_…) '
        'en el header Authorization, sin prefijo Bearer.'
    ),
    'invalid_link_token': (
        'Link token inválido o de un modo distinto al de la Secret Key. '
        'Obtenga el link_token al crear el Link en dashboard.fintoc.com; '
        'debe ser del mismo modo (test con sk_test_, live con sk_live_).'
    ),
    'missing_resource': (
        'Cuenta no encontrada o no pertenece al Link. '
        'Verifique FINTOC_ACCOUNT_ID (case-sensitive, empieza con acc_).'
    ),
    'payment_required': (
        'Fintoc requiere pago o el periodo de prueba expiró. '
        'Contacte soporte@fintoc.com o billing@fintoc.com.'
    ),
    'invalid_date': 'Fecha de filtro inválida; use formato ISO 8601 (YYYY-MM-DD).',
}


class FintocClient:
    """Cliente HTTP para consultar movimientos bancarios en Fintoc."""

    def __init__(
        self,
        api_key: str | None = None,
        account_id: str | None = None,
        link_token: str | None = None,
        timeout: int = 30,
        creds: dict | None = None,
    ):
        creds = creds or {}
        self.api_key = api_key or creds.get('fintoc_api_key') or os.environ.get('FINTOC_API_KEY', '')
        self.account_id = account_id or creds.get('fintoc_account_id') or os.environ.get('FINTOC_ACCOUNT_ID', '')
        self.link_token = link_token or creds.get('fintoc_link_token') or os.environ.get('FINTOC_LINK_TOKEN', '')
        self.base_url = 'https://api.fintoc.com/v1'
        self.timeout = timeout

    def tiene_credenciales(self) -> bool:
        # account_id es opcional: se puede resolver desde el Link (GET /accounts).
        return bool(self.api_key and self.link_token)

    def _mensaje_credenciales_faltantes(self) -> str:
        faltan = []
        if not self.api_key.strip():
            faltan.append('FINTOC_API_KEY (Secret Key sk_test_… o sk_live_…)')
        if not self.link_token.strip():
            faltan.append(
                'FINTOC_LINK_TOKEN (token del Link; se muestra una sola vez al crearlo en el dashboard)'
            )
        if faltan:
            return 'Modo simulación: faltan ' + ', '.join(faltan) + ' en la conexión bancaria'
        return (
            'Modo simulación: configure FINTOC_API_KEY y FINTOC_LINK_TOKEN '
            'en la conexión bancaria'
        )

    def _headers(self) -> dict[str, str]:
        return {'Authorization': self.api_key, 'Accept': 'application/json'}

    def listar_cuentas(self) -> list[dict]:
        """Lista cuentas del Link (GET /v1/accounts?link_token=…)."""
        if not self.api_key or not self.link_token:
            raise BancoIntegrationError(
                'Se requieren Secret Key y link_token para listar cuentas Fintoc.'
            )
        try:
            resp = requests.get(
                f'{self.base_url}/accounts',
                headers=self._headers(),
                params={'link_token': self.link_token},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            msg = mensaje_error_red_fintoc(e)
            raise BancoIntegrationError(
                msg or 'Error de red al listar cuentas Fintoc.'
            ) from e
        if not resp.ok:
            raise BancoIntegrationError(self._format_fintoc_error(resp))
        payload = resp.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return payload.get('data') or payload.get('accounts') or []
        return []

    def resolver_account_id(self, prefer_type: str = 'checking_account') -> str:
        """Elige account_id del Link: mantiene el configurado si existe; si no, la primera cuenta corriente."""
        cuentas = self.listar_cuentas()
        if not cuentas:
            raise BancoIntegrationError(
                'El Link de Fintoc no tiene cuentas asociadas. '
                'Cree o simule un Link en dashboard.fintoc.com.'
            )
        ids = {str(c.get('id') or '') for c in cuentas}
        if self.account_id and self.account_id in ids:
            return self.account_id
        preferidas = [
            c for c in cuentas
            if (c.get('type') or '').lower() == prefer_type
        ]
        elegida = preferidas[0] if preferidas else cuentas[0]
        account_id = str(elegida.get('id') or '')
        if not account_id:
            raise BancoIntegrationError('Fintoc devolvió cuentas sin id válido.')
        self.account_id = account_id
        return account_id

    @classmethod
    def _format_fintoc_error(cls, resp: requests.Response) -> str:
        status = resp.status_code
        try:
            payload = resp.json()
            error = payload.get('error') if isinstance(payload, dict) else None
            if isinstance(error, dict):
                code = error.get('code') or ''
                hint = _FINTOC_ERROR_HINTS.get(code, '')
                if status == 400 and error.get('param') == 'link_token' and not hint:
                    hint = (
                        'Falta link_token. Es obligatorio como parámetro de consulta '
                        'junto con la Secret Key.'
                    )
                message = error.get('message') or hint or 'Error desconocido'
                if hint and hint not in message:
                    return f'Error al consultar Fintoc ({status}): {message}. {hint}'
                return f'Error al consultar Fintoc ({status}): {message}'
        except (ValueError, TypeError):
            pass
        text = (resp.text or '').strip()
        if text:
            return f'Error al consultar Fintoc ({status}): {text[:500]}'
        return f'Error al consultar Fintoc ({status}): respuesta vacía'

    def obtener_movimientos(
        self,
        since: str | date | None = None,
    ) -> tuple[list[dict], bool, str]:
        """
        Devuelve (movimientos_normalizados, es_mock, mensaje).
        Sin credenciales usa datos de prueba; con credenciales consulta la API real.
        `since` (YYYY-MM-DD) limita por post_date según la API de Fintoc.
        """
        if not self.tiene_credenciales():
            return [dict(m) for m in MOCK_MOVIMIENTOS], True, self._mensaje_credenciales_faltantes()

        since_iso = self._normalize_since(since)
        try:
            if not self.account_id:
                self.resolver_account_id()
            try:
                items = self._fetch_all_movements(since=since_iso)
            except BancoIntegrationError as e:
                # Account id obsoleto / de otro Link → re-resolver desde el Link actual.
                if 'No such account' in str(e) or 'missing_resource' in str(e).lower() or '(404)' in str(e):
                    self.account_id = ''
                    self.resolver_account_id()
                    items = self._fetch_all_movements(since=since_iso)
                else:
                    raise
            extra = f' desde {since_iso}' if since_iso else ''
            return (
                [self._normalizar_movimiento(m) for m in items],
                False,
                f'Movimientos obtenidos desde Fintoc ({self.account_id}{extra})',
            )
        except BancoIntegrationError:
            raise
        except requests.RequestException as e:
            msg = mensaje_error_red_fintoc(e)
            raise BancoIntegrationError(
                msg or 'Error de red al consultar Fintoc. Verifique su conexión e intente de nuevo.'
            ) from e
        except (KeyError, TypeError, ValueError) as e:
            raise BancoIntegrationError(f'Error al procesar movimientos Fintoc: {e}') from e

    @staticmethod
    def _normalize_since(since: str | date | None) -> str | None:
        if since is None or since == '':
            return None
        if isinstance(since, date):
            return since.isoformat()
        texto = str(since).strip()
        return texto[:10] if texto else None

    def _fetch_all_movements(self, since: str | None = None) -> list[dict]:
        """Pagina GET /accounts/{id}/movements (máx. 300 por página según docs Fintoc)."""
        if not self.account_id:
            raise BancoIntegrationError('Falta account_id para consultar movimientos Fintoc.')
        url = f'{self.base_url}/accounts/{self.account_id}/movements'
        per_page = 300
        page = 1
        all_items: list[dict] = []

        while True:
            params = {
                'link_token': self.link_token,
                'per_page': str(per_page),
                'page': page,
            }
            if since:
                params['since'] = since
            resp = requests.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            if not resp.ok:
                raise BancoIntegrationError(self._format_fintoc_error(resp))
            payload = resp.json()
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                items = payload.get('data') or payload.get('movements') or []
            else:
                items = []
            all_items.extend(items)
            if len(items) < per_page:
                break
            page += 1

        return all_items

    def _normalizar_movimiento(self, raw: dict) -> dict:
        amount = raw.get('amount', raw.get('monto'))
        tipo_raw = (raw.get('tipo') or raw.get('type') or '').lower()

        if amount is not None:
            monto = abs(float(amount))
            if float(amount) < 0:
                tipo = 'egreso'
            elif tipo_raw in ('outbound', 'outgoing', 'withdrawal', 'egreso', 'charge'):
                tipo = 'egreso'
            elif tipo_raw in ('inbound', 'incoming', 'deposit', 'ingreso'):
                tipo = 'ingreso'
            else:
                tipo = 'ingreso' if float(amount) > 0 else 'egreso'
        else:
            monto = abs(float(raw.get('monto', 0)))
            tipo = 'ingreso' if tipo_raw == 'ingreso' else 'egreso'

        fecha_raw = (
            raw.get('post_date')
            or raw.get('transaction_date')
            or raw.get('fecha')
            or raw.get('created_at')
        )
        desc = (
            raw.get('description')
            or raw.get('descripción')
            or raw.get('descripcion')
            or 'Movimiento bancario'
        )

        return {
            'id': str(raw.get('id', '')),
            'fecha': self._parse_fecha(fecha_raw),
            'descripción': str(desc)[:255],
            'monto': monto,
            'tipo': tipo,
        }

    @staticmethod
    def _parse_fecha(valor) -> str:
        if not valor:
            return date.today().isoformat()
        if isinstance(valor, date):
            return valor.isoformat()
        texto = str(valor).strip()
        if 'T' in texto:
            texto = texto.split('T', 1)[0]
        if len(texto) >= 10 and texto[4] == '-' and texto[7] == '-':
            return texto[:10]
        try:
            return datetime.fromisoformat(texto.replace('Z', '+00:00')).date().isoformat()
        except ValueError:
            return date.today().isoformat()
