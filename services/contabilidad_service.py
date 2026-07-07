"""Motor de validación y registro de comprobantes contables (partida doble)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func

from extensions import db
from models import Comprobante, CuentaContable, Empresa, LineaComprobante

TOLERANCIA_DESCUADRE = 0.01
TIPOS_COMPROBANTE = frozenset({'Ingreso', 'Egreso', 'Traspaso'})
SIGLAS_TIPO = {'Ingreso': 'I', 'Egreso': 'E', 'Traspaso': 'T'}


class DescuadreContableError(Exception):
    """El comprobante no cuadra: suma del debe distinta a la del haber."""

    def __init__(self, total_debe: float, total_haber: float, diferencia: float | None = None):
        self.total_debe = round(float(total_debe), 2)
        self.total_haber = round(float(total_haber), 2)
        self.diferencia = (
            round(float(diferencia), 2)
            if diferencia is not None
            else round(self.total_debe - self.total_haber, 2)
        )
        super().__init__(
            f'Descuadre contable: debe={self.total_debe}, haber={self.total_haber}, '
            f'diferencia={self.diferencia}'
        )


def validar_partida_doble(lineas: list[dict]) -> tuple[float, float]:
    """Suma debe y haber de las líneas (redondeo a 2 decimales)."""
    total_debe = round(sum(_monto_linea(linea, 'debe') for linea in lineas), 2)
    total_haber = round(sum(_monto_linea(linea, 'haber') for linea in lineas), 2)
    return total_debe, total_haber


def _sigla_tipo_comprobante(tipo: str) -> str:
    sigla = SIGLAS_TIPO.get(tipo)
    if not sigla:
        raise ValueError(f'Tipo de comprobante no válido: {tipo!r}')
    return sigla


def _siguiente_numero_comprobante(empresa_id: int, tipo: str, anio: int) -> int:
    maximo = (
        db.session.query(func.max(Comprobante.numero))
        .filter_by(empresa_id=empresa_id, tipo=tipo, anio=anio)
        .scalar()
    )
    return int(maximo or 0) + 1


def _monto_linea(linea: dict, campo: str) -> float:
    valor = linea.get(campo, 0)
    if valor is None:
        return 0.0
    return float(valor)


def _parse_fecha(valor) -> date:
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, str):
        return date.fromisoformat(valor[:10])
    raise ValueError(f'Fecha no válida: {valor!r}')


def _validar_empresa(empresa_id: int) -> Empresa:
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None:
        raise ValueError(f'Empresa no encontrada: id={empresa_id}')
    return empresa


def _validar_lineas(empresa_id: int, lineas: list[dict]) -> None:
    if len(lineas) < 2:
        raise ValueError('El comprobante debe tener al menos 2 líneas')

    for idx, linea in enumerate(lineas, start=1):
        cuenta_id = linea.get('cuenta_contable_id')
        if not cuenta_id:
            raise ValueError(f'Línea {idx}: cuenta_contable_id es obligatorio')

        cuenta = db.session.get(CuentaContable, cuenta_id)
        if cuenta is None:
            raise ValueError(f'Línea {idx}: cuenta contable no encontrada (id={cuenta_id})')
        if cuenta.empresa_id != empresa_id:
            raise ValueError(
                f'Línea {idx}: la cuenta {cuenta.codigo} no pertenece a la empresa',
            )
        if not cuenta.es_imputable:
            raise ValueError(
                f'Línea {idx}: la cuenta {cuenta.codigo} no es imputable',
            )

        debe = _monto_linea(linea, 'debe')
        haber = _monto_linea(linea, 'haber')
        if debe < 0 or haber < 0:
            raise ValueError(f'Línea {idx}: debe y haber no pueden ser negativos')
        if debe > 0 and haber > 0:
            raise ValueError(f'Línea {idx}: no puede tener debe y haber simultáneos')
        if debe <= 0 and haber <= 0:
            raise ValueError(f'Línea {idx}: debe indicar un monto en debe o en haber')


def _asegurar_partida_cuadrada(lineas: list[dict]) -> tuple[float, float]:
    total_debe, total_haber = validar_partida_doble(lineas)
    if abs(total_debe - total_haber) > TOLERANCIA_DESCUADRE:
        raise DescuadreContableError(total_debe, total_haber)
    return total_debe, total_haber


def registrar_comprobante(
    empresa_id: int,
    encabezado: dict,
    lineas: list[dict],
    *,
    contabilizar: bool = False,
) -> dict:
    """
    Registra un comprobante contable con validación estricta de partida doble.

    Retorna {'id': int, 'numero_formateado': str}.
    """
    _validar_empresa(empresa_id)

    tipo = encabezado.get('tipo')
    if tipo not in TIPOS_COMPROBANTE:
        raise ValueError(f'Tipo de comprobante no válido: {tipo!r}')

    glosa = (encabezado.get('glosa') or '').strip()
    if not glosa:
        raise ValueError('La glosa del comprobante es obligatoria')

    fecha = _parse_fecha(encabezado['fecha'])
    anio = fecha.year
    estado = encabezado.get('estado') or ('Contabilizado' if contabilizar else 'Borrador')
    if contabilizar:
        estado = 'Contabilizado'
    moneda_origen = encabezado.get('moneda_origen') or 'CLP'
    tipo_cambio = float(encabezado.get('tipo_cambio') or 1.0)

    _validar_lineas(empresa_id, lineas)

    try:
        total_debe, total_haber = _asegurar_partida_cuadrada(lineas)

        numero = _siguiente_numero_comprobante(empresa_id, tipo, anio)
        sigla = _sigla_tipo_comprobante(tipo)
        numero_formateado = f'{anio}-{sigla}-{numero:04d}'

        comprobante = Comprobante(
            empresa_id=empresa_id,
            fecha=fecha,
            tipo=tipo,
            numero=numero,
            numero_formateado=numero_formateado,
            anio=anio,
            glosa=glosa,
            estado=estado,
            moneda_origen=moneda_origen,
            tipo_cambio=tipo_cambio,
        )
        db.session.add(comprobante)
        db.session.flush()

        for linea in lineas:
            db.session.add(LineaComprobante(
                comprobante_id=comprobante.id,
                cuenta_contable_id=linea['cuenta_contable_id'],
                debe=_monto_linea(linea, 'debe'),
                haber=_monto_linea(linea, 'haber'),
                glosa_linea=linea.get('glosa_linea'),
                centro_costo_id=linea.get('centro_costo_id'),
                proyecto_id=linea.get('proyecto_id'),
                rut_asociado=linea.get('rut_asociado'),
            ))

        total_debe_db, total_haber_db = validar_partida_doble([
            {'debe': l.debe, 'haber': l.haber} for l in comprobante.lineas
        ])
        if abs(total_debe_db - total_haber_db) > TOLERANCIA_DESCUADRE:
            raise DescuadreContableError(total_debe_db, total_haber_db)

        db.session.commit()
        return {'id': comprobante.id, 'numero_formateado': numero_formateado}
    except DescuadreContableError:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise
