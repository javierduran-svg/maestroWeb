"""Integración con SimpleAPI (https://www.simpleapi.cl) vía REST."""

import base64
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import requests


class SIIIntegrationError(Exception):
    """Error al comunicarse con la API del proveedor SII."""


class SIIClient:
    """Cliente HTTP para consultar RCV y emitir DTEs con SimpleAPI."""

    _ERRORES_CONOCIDOS: dict[str, str] = {
        'rut certificado no v': (
            'El RUT del certificado digital no es válido. '
            'Verifique SII_RUT_CERTIFICADO (debe coincidir con el RUT del archivo .pfx).'
        ),
        'password de certificado no informado': (
            'Falta la clave del certificado digital. Configure SII_CERTIFICADO_PASSWORD en .env.'
        ),
        'falta certificado digital': (
            'Falta el archivo del certificado digital (.pfx). '
            'Configure SII_CERTIFICADO_PATH o SII_CERTIFICADO_B64 en .env.'
        ),
        'cannot find the requested object': (
            'El certificado digital no pudo leerse. Verifique que el archivo .pfx y su clave sean correctos.'
        ),
        'api calls quota exceeded': (
            'Límite de consultas a SimpleAPI alcanzado. Espere un momento e intente de nuevo.'
        ),
    }

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        rcv_base_url: str | None = None,
        rut_emisor: str | None = None,
        sii_usuario: str | None = None,
        sii_password: str | None = None,
        rut_certificado: str | None = None,
        certificado_b64: str | None = None,
        certificado_path: str | None = None,
        certificado_password: str | None = None,
        ambiente: int | None = None,
        timeout: int = 60,
        creds: dict | None = None,
    ):
        creds = creds or {}
        self.api_key = api_key or creds.get('api_key') or os.environ.get('SII_API_KEY', '')
        self.base_url = (
            base_url or creds.get('api_base_url') or os.environ.get('SII_API_BASE_URL', 'https://api.simpleapi.cl')
        ).rstrip('/')
        self.rcv_base_url = (
            rcv_base_url or creds.get('rcv_base_url') or os.environ.get('SII_RCV_BASE_URL', 'https://servicios.simpleapi.cl')
        ).rstrip('/')
        self.rut_emisor = rut_emisor or creds.get('rut_emisor') or os.environ.get('SII_RUT_EMISOR', '')
        self.sii_usuario = sii_usuario if sii_usuario is not None else creds.get('usuario', os.environ.get('SII_USUARIO', ''))
        self.sii_password = sii_password if sii_password is not None else creds.get('password', os.environ.get('SII_PASSWORD', ''))
        self.rut_certificado = (
            rut_certificado if rut_certificado is not None else creds.get('rut_certificado', os.environ.get('SII_RUT_CERTIFICADO', ''))
        )
        self.certificado_b64 = (
            certificado_b64 if certificado_b64 is not None else creds.get('certificado_b64', os.environ.get('SII_CERTIFICADO_B64', ''))
        )
        self.certificado_path = (
            certificado_path if certificado_path is not None else creds.get('certificado_path', os.environ.get('SII_CERTIFICADO_PATH', ''))
        )
        self.certificado_password = (
            certificado_password if certificado_password is not None
            else creds.get('certificado_password', os.environ.get('SII_CERTIFICADO_PASSWORD', ''))
        )
        ambiente_raw = ambiente if ambiente is not None else creds.get('ambiente', os.environ.get('SII_AMBIENTE', '0'))
        self.ambiente = int(ambiente_raw)
        self.timeout = timeout

        if not self.api_key:
            raise SIIIntegrationError(
                'Falta la API Key. Configure las credenciales SII de la empresa.'
            )

    def _headers(self) -> dict[str, str]:
        """SimpleAPI usa Authorization: {api-key} sin prefijo Bearer."""
        return {
            'Authorization': self.api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    @staticmethod
    def _normalizar_rut(rut: str) -> str:
        return rut.strip().replace('.', '')

    @classmethod
    def _humanizar_error(cls, mensaje: str) -> str:
        """Traduce errores técnicos de SimpleAPI a mensajes claros para el usuario."""
        texto = mensaje.strip()
        texto = re.sub(r'^Error HTTP \d+:\s*', '', texto, flags=re.IGNORECASE)
        if texto.startswith('"') and texto.endswith('"'):
            texto = texto[1:-1]
        clave = texto.lower()
        for patron, traduccion in cls._ERRORES_CONOCIDOS.items():
            if patron in clave:
                return traduccion
        return texto

    @staticmethod
    def _extraer_mensaje_error(response: requests.Response) -> str:
        try:
            body = response.json()
            if isinstance(body, dict):
                return (
                    body.get('mensaje')
                    or body.get('message')
                    or body.get('error')
                    or body.get('title')
                    or str(body)
                )
        except Exception:
            pass
        return response.text[:500] or f'HTTP {response.status_code}'

    def _obtener_certificado_b64(self) -> str:
        """Lee el certificado .pfx desde base64 en env o desde ruta de archivo."""
        if self.certificado_b64.strip():
            return self.certificado_b64.strip()
        if not self.certificado_path.strip():
            return ''
        path = Path(self.certificado_path.strip())
        if not path.is_file():
            raise SIIIntegrationError(
                f'No se encuentra el certificado digital en: {path}. '
                'Verifique SII_CERTIFICADO_PATH en .env.'
            )
        return base64.b64encode(path.read_bytes()).decode('ascii')

    def _validar_credenciales_rcv(self) -> None:
        """Valida que existan credenciales portal SII y certificado digital para RCV."""
        if not self.rut_emisor:
            raise SIIIntegrationError('Configure SII_RUT_EMISOR con el RUT de su empresa.')
        if not self.sii_usuario or not self.sii_password:
            raise SIIIntegrationError(
                'Para consultar el RCV configure SII_USUARIO y SII_PASSWORD (clave del portal sii.cl).'
            )
        if not self.rut_certificado:
            raise SIIIntegrationError(
                'Falta SII_RUT_CERTIFICADO: el RUT que figura en su certificado digital (.pfx).'
            )
        if not self.certificado_password:
            raise SIIIntegrationError(
                'Falta SII_CERTIFICADO_PASSWORD: la clave del archivo certificado .pfx.'
            )
        if not self.certificado_b64.strip() and not self.certificado_path.strip():
            raise SIIIntegrationError(
                'Falta el certificado digital. Configure SII_CERTIFICADO_PATH (ruta al .pfx) '
                'o SII_CERTIFICADO_B64 (contenido en base64) en .env.'
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        base_url: str | None = None,
        params: dict | None = None,
        json_body: dict | None = None,
        use_input_query: bool = False,
    ) -> Any:
        """Ejecuta una petición HTTP y devuelve el JSON de respuesta."""
        host = (base_url or self.base_url).rstrip('/')
        url = f'{host}/{path.lstrip("/")}'
        request_params = dict(params or {})
        if use_input_query and json_body is not None:
            request_params['input'] = json.dumps(json_body)
            json_body = None

        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(),
                params=request_params or None,
                json=json_body,
                timeout=self.timeout,
            )
            if response.status_code >= 400:
                detalle = self._extraer_mensaje_error(response)
                raise SIIIntegrationError(self._humanizar_error(detalle))
        except SIIIntegrationError:
            raise
        except requests.exceptions.Timeout as exc:
            raise SIIIntegrationError('Tiempo de espera agotado al contactar SimpleAPI.') from exc
        except requests.exceptions.ConnectionError as exc:
            raise SIIIntegrationError('No se pudo conectar con SimpleAPI.') from exc
        except requests.exceptions.RequestException as exc:
            raise SIIIntegrationError(f'Error en la petición: {exc}') from exc

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise SIIIntegrationError('SimpleAPI no devolvió JSON válido.') from exc

    def _request_rcv_multipart(self, path: str, payload: dict) -> Any:
        """POST RCV con multipart/form-data (campo input) para evitar límite de URL IIS."""
        url = f'{self.rcv_base_url}/{path.lstrip("/")}'
        headers = {
            'Authorization': self.api_key,
            'Accept': 'application/json',
        }
        try:
            response = requests.post(
                url,
                files={'input': (None, json.dumps(payload))},
                headers=headers,
                timeout=self.timeout,
            )
            if response.status_code >= 400:
                detalle = self._extraer_mensaje_error(response)
                raise SIIIntegrationError(self._humanizar_error(detalle))
        except SIIIntegrationError:
            raise
        except requests.exceptions.Timeout as exc:
            raise SIIIntegrationError('Tiempo de espera agotado al contactar SimpleAPI.') from exc
        except requests.exceptions.ConnectionError as exc:
            raise SIIIntegrationError('No se pudo conectar con SimpleAPI.') from exc
        except requests.exceptions.RequestException as exc:
            raise SIIIntegrationError(f'Error en la petición: {exc}') from exc

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise SIIIntegrationError('SimpleAPI no devolvió JSON válido.') from exc

    @staticmethod
    def _extraer_campo(data: dict, *claves, default=None):
        """Busca un valor probando varios nombres de campo habituales."""
        for clave in claves:
            if clave in data and data[clave] not in (None, ''):
                return data[clave]
        return default

    def _normalizar_dte(self, raw: dict) -> dict:
        """Convierte un registro RCV/SimpleAPI al formato simplificado de la app."""
        receptor = raw.get('Receptor') or raw.get('receptor') or {}
        if isinstance(receptor, dict):
            rut = self._extraer_campo(receptor, 'RUTRecep', 'rut', 'RutReceptor', 'rut_receptor', 'RutCliente')
            razon = self._extraer_campo(receptor, 'RznSocRecep', 'razon_social', 'RazonSocial', 'nombre', 'RznSoc')
        else:
            rut = self._extraer_campo(
                raw,
                'rut_cliente', 'RUTRecep', 'rut_receptor', 'RutCliente', 'Rut',
                'RUTDoc', 'RutDoc', 'RutReceptor',
            )
            razon = self._extraer_campo(
                raw,
                'razon_social', 'RznSocRecep', 'RznSoc', 'RazonSocial',
                'NombreDoc', 'RznSocRecep', 'RazonSocialDoc',
            )

        folio = self._extraer_campo(raw, 'Folio', 'folio', 'num_folio', 'NumFolio', 'FolioDoc', 'NroDoc')
        fecha = self._extraer_campo(
            raw, 'FchEmis', 'fecha', 'fecha_emision', 'FechaEmision', 'Fecha', 'FechaDoc',
        )
        monto = self._extraer_campo(
            raw, 'MntTotal', 'monto_total', 'MontoTotal', 'total', 'MntExe', 'Monto', 'MontoTotal', default=0,
        )

        return {
            'folio': folio,
            'fecha': str(fecha)[:10] if fecha else None,
            'rut_cliente': rut,
            'razon_social': razon,
            'monto_total': float(monto or 0),
            'tipo_dte': self._extraer_campo(raw, 'TipoDTE', 'tipo_dte', 'tipo_documento', 'TipoDoc', 'CodDoc'),
        }

    def _payload_rcv_ventas(self) -> dict:
        """Arma el cuerpo para consultar ventas en el Registro de Compras y Ventas."""
        self._validar_credenciales_rcv()
        certificado_b64 = self._obtener_certificado_b64()
        if not certificado_b64:
            raise SIIIntegrationError(
                'No se pudo cargar el certificado digital. Verifique SII_CERTIFICADO_PATH o SII_CERTIFICADO_B64.'
            )
        return {
            'RutUsuario': self._normalizar_rut(self.sii_usuario),
            'PasswordSII': self.sii_password,
            'RutEmpresa': self._normalizar_rut(self.rut_emisor),
            'RutCertificado': self._normalizar_rut(self.rut_certificado),
            'CertificadoB64': certificado_b64,
            'Password': self.certificado_password,
            'Ambiente': self.ambiente,
            'Detallado': False,
        }

    @staticmethod
    def _extraer_lista_rcv(data: Any) -> list:
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []
        for key in (
            'DetalleVentas', 'detalleVentas', 'Ventas', 'ventas',
            'data', 'documentos', 'Detalle', 'items',
        ):
            items = data.get(key)
            if isinstance(items, list):
                return items
        return []

    def obtener_dtes_emitidos(self, mes: int, anio: int) -> list[dict]:
        """
        Obtiene ventas emitidas del RCV del SII vía SimpleAPI RCV.
        Requiere credenciales portal SII y certificado digital (.pfx).
        """
        if not 1 <= mes <= 12:
            raise SIIIntegrationError('El mes debe estar entre 1 y 12.')
        if anio < 2000:
            raise SIIIntegrationError('Año inválido.')

        payload = self._payload_rcv_ventas()
        path = f'/api/rcv/ventas/{anio}/{mes}'
        data = self._request_rcv_multipart(path, payload)

        items = self._extraer_lista_rcv(data)
        return [self._normalizar_dte(item) for item in items]

    def _payload_generar_dte(
        self,
        rut_receptor: str,
        razon_social: str,
        detalle: str,
        monto: float,
        tipo_documento: int,
        cantidad: float,
    ) -> dict:
        """Construye el JSON esperado por POST /api/v1/DTE/generar."""
        hoy = date.today().isoformat()
        return {
            'Documento': {
                'Encabezado': {
                    'IdDoc': {
                        'TipoDTE': int(tipo_documento),
                        'Folio': 0,
                        'FchEmis': hoy,
                    },
                    'Emisor': {'RUTEmisor': self._normalizar_rut(self.rut_emisor)} if self.rut_emisor else {},
                    'Receptor': {
                        'RUTRecep': self._normalizar_rut(rut_receptor),
                        'RznSocRecep': razon_social.strip(),
                    },
                    'Totales': {
                        'MntNeto': int(monto),
                        'MntTotal': int(monto),
                    },
                },
                'Detalle': [
                    {
                        'NroLinDet': 1,
                        'NmbItem': detalle.strip(),
                        'DscItem': detalle.strip(),
                        'QtyItem': cantidad,
                        'PrcItem': int(monto),
                        'MontoItem': int(monto),
                    }
                ],
            }
        }

    def emitir_factura(
        self,
        rut_receptor: str,
        razon_social: str,
        detalle: str,
        monto: float,
        tipo_documento: int = 33,
        *,
        cantidad: float = 1,
        pagado: bool = False,
    ) -> dict:
        """
        Genera un DTE vía POST /api/v1/DTE/generar.
        Requiere certificado digital y CAF configurados en SimpleAPI.
        """
        if monto <= 0:
            raise SIIIntegrationError('El monto debe ser mayor a cero.')
        if not rut_receptor or not razon_social:
            raise SIIIntegrationError('RUT y razón social del receptor son obligatorios.')

        payload = self._payload_generar_dte(
            rut_receptor, razon_social, detalle, monto, tipo_documento, cantidad,
        )
        data = self._request(
            'POST',
            '/api/v1/DTE/generar',
            json_body=payload,
            use_input_query=True,
        )

        resultado = data.get('data') if isinstance(data.get('data'), dict) else data
        doc = resultado.get('Documento') or resultado
        enc = doc.get('Encabezado') or doc
        id_doc = enc.get('IdDoc') or enc.get('IdentificacionDTE') or {}

        folio = self._extraer_campo(id_doc, 'Folio', 'folio') or self._extraer_campo(resultado, 'Folio', 'folio')
        fecha = (
            self._extraer_campo(id_doc, 'FchEmis', 'fecha')
            or self._extraer_campo(resultado, 'FchEmis', 'fecha')
            or date.today().isoformat()
        )

        return {
            'folio': folio,
            'fecha': str(fecha)[:10],
            'tipo_dte': tipo_documento,
            'monto_total': float(monto),
            'rut_receptor': rut_receptor,
            'razon_social': razon_social,
            'pagado': pagado,
            'track_id': self._extraer_campo(resultado, 'track_id', 'TrackId', 'Token'),
            'pdf_url': self._extraer_campo(resultado, 'pdf_url', 'url_pdf', 'PdfUrl'),
            'raw': resultado,
        }
