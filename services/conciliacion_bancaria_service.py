"""Conciliación bancaria: importar cartola Excel y cruzar extracto vs libro contable."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import and_, func, or_

from extensions import db
from models import BancoMovimiento, EmpresaBancoConexion, Movimiento


def _normalizar(texto) -> str:
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ''
    s = str(texto).strip()
    s = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


def _parse_fecha_cartola(valor) -> date | None:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        dt = pd.to_datetime(valor, dayfirst=True, errors='coerce')
        if pd.isna(dt):
            return None
        return dt.date()
    except Exception:
        return None


def _es_fecha_texto(valor) -> bool:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return False
    txt = str(valor).strip()
    return bool(re.match(r'^\d{1,2}/\d{1,2}/\d{4}$', txt))


def _celda_texto(valor) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ''
    return str(valor).strip()


def _fintoc_id_cartola(fecha: date, monto: float, descripcion: str, num_doc, idx: int) -> str:
    base = f'{fecha.isoformat()}|{abs(monto):.2f}|{descripcion}|{num_doc}|{idx}'
    digest = hashlib.sha1(base.encode('utf-8')).hexdigest()[:16]
    return f'cartola-{digest}'


def _parsear_cartola_excel(path: str) -> dict:
    """Lee cartola histórica chilena (Santander y similares) desde Excel."""
    df = pd.read_excel(path, sheet_name=0, header=None)
    meta = {
        'cuenta': '',
        'numero_cartola': None,
        'fecha_desde': None,
        'fecha_hasta': None,
        'saldo_inicial': None,
        'saldo_final': None,
    }
    movimientos = []

    for i in range(min(len(df), 20)):
        fila = df.iloc[i]
        txt = ' '.join(_celda_texto(v) for v in fila.values if _celda_texto(v))
        norm = _normalizar(txt)
        if 'cuenta corriente' in norm:
            m = re.search(r'(\d[\d\-]+)', txt)
            if m:
                meta['cuenta'] = m.group(1)
        if 'numero cartola' in norm or 'número cartola' in norm:
            m = re.search(r'(\d+)', txt)
            if m:
                meta['numero_cartola'] = int(m.group(1))
        if 'fecha desde' in norm or 'fecha hasta' in norm:
            fechas_txt = re.findall(r'(\d{1,2}/\d{1,2}/\d{4})', txt)
            if 'fecha desde' in norm and fechas_txt:
                meta['fecha_desde'] = _parse_fecha_cartola(fechas_txt[0])
            if 'fecha hasta' in norm and fechas_txt:
                meta['fecha_hasta'] = _parse_fecha_cartola(fechas_txt[-1])

    header_row = None
    for i in range(len(df)):
        c0 = _normalizar(df.iloc[i, 0])
        c1 = _normalizar(df.iloc[i, 1]) if df.shape[1] > 1 else ''
        if c0 == 'monto' and ('descripcion' in c1 or 'descripción' in c1):
            header_row = i
            break

    if header_row is None:
        raise ValueError('No se encontró la fila de encabezados «Detalle movimientos» en el Excel')

    for i in range(header_row - 5, header_row):
        if i < 0:
            continue
        fila = df.iloc[i]
        if _normalizar(fila.iloc[0]) == 'saldo inicial':
            meta['saldo_inicial'] = float(fila.iloc[6]) if pd.notna(fila.iloc[6]) else None

    idx_mov = 0
    for i in range(header_row + 1, len(df)):
        fila = df.iloc[i]
        if fila.isna().all():
            continue

        monto_raw = fila.iloc[0]
        if monto_raw is None or (isinstance(monto_raw, float) and pd.isna(monto_raw)):
            continue
        try:
            monto = float(monto_raw)
        except (TypeError, ValueError):
            continue

        desc = _celda_texto(fila.iloc[1]) if df.shape[1] > 1 else ''
        if not desc or _es_fecha_texto(desc):
            continue

        fecha = _parse_fecha_cartola(fila.iloc[3]) if df.shape[1] > 3 else None
        if not fecha and df.shape[1] > 1 and _es_fecha_texto(fila.iloc[1]):
            continue

        if not fecha:
            continue

        cargo_abono = _celda_texto(fila.iloc[7]).upper() if df.shape[1] > 7 else ''
        if cargo_abono == 'A':
            tipo = 'ingreso'
            monto_abs = abs(monto)
        elif cargo_abono == 'C':
            tipo = 'egreso'
            monto_abs = abs(monto)
        else:
            tipo = 'ingreso' if monto > 0 else 'egreso'
            monto_abs = abs(monto)

        if monto_abs <= 0:
            continue

        num_doc = fila.iloc[4] if df.shape[1] > 4 else None
        sucursal = _celda_texto(fila.iloc[5]) if df.shape[1] > 5 else ''
        descripcion = desc[:255]
        if sucursal and sucursal not in descripcion:
            descripcion = f'{descripcion} ({sucursal})'[:255]

        movimientos.append({
            'fintoc_id': _fintoc_id_cartola(fecha, monto_abs, descripcion, num_doc, idx_mov),
            'fecha': fecha,
            'descripcion': descripcion,
            'monto': monto_abs,
            'tipo': tipo,
            'num_documento': str(num_doc) if num_doc not in (None, '') and not pd.isna(num_doc) else '',
        })
        idx_mov += 1

    if not movimientos:
        raise ValueError('No se encontraron movimientos en la cartola')

    if meta['fecha_desde'] is None:
        meta['fecha_desde'] = min(m['fecha'] for m in movimientos)
    if meta['fecha_hasta'] is None:
        meta['fecha_hasta'] = max(m['fecha'] for m in movimientos)

    return {'meta': meta, 'movimientos': movimientos}


def importar_cartola_desde_excel(path: str, conexion_id: int, empresa_id: int) -> dict:
    conn = EmpresaBancoConexion.query.filter_by(id=conexion_id, empresa_id=empresa_id).first()
    if not conn:
        raise ValueError('Conexión bancaria no encontrada')

    if not conn.cuenta_contable_id:
        from common import _obtener_cuenta_banco_santander
        cuenta = _obtener_cuenta_banco_santander(empresa_id)
        conn.cuenta_contable_id = cuenta.id

    parsed = _parsear_cartola_excel(path)
    insertados = 0
    actualizados = 0
    omitidos = 0
    ahora = datetime.utcnow()

    for mov in parsed['movimientos']:
        existente = BancoMovimiento.query.filter_by(
            empresa_id=empresa_id,
            fintoc_id=mov['fintoc_id'],
        ).first()
        if existente:
            if existente.conexion_id != conn.id:
                existente.conexion_id = conn.id
            existente.fecha = mov['fecha']
            existente.descripcion = mov['descripcion']
            existente.monto = mov['monto']
            existente.tipo = mov['tipo']
            existente.synced_at = ahora
            actualizados += 1
            continue

        dup = BancoMovimiento.query.filter_by(
            empresa_id=empresa_id,
            conexion_id=conn.id,
            fecha=mov['fecha'],
            monto=mov['monto'],
            descripcion=mov['descripcion'],
        ).first()
        if dup:
            omitidos += 1
            continue

        db.session.add(BancoMovimiento(
            empresa_id=empresa_id,
            conexion_id=conn.id,
            fintoc_id=mov['fintoc_id'],
            fecha=mov['fecha'],
            descripcion=mov['descripcion'],
            monto=mov['monto'],
            tipo=mov['tipo'],
            moneda='CLP',
            estado_conciliacion='pendiente',
            synced_at=ahora,
        ))
        insertados += 1

    conn.ultima_sincronizacion = ahora
    db.session.flush()

    return {
        'insertados': insertados,
        'actualizados': actualizados,
        'omitidos': omitidos,
        'total_cartola': len(parsed['movimientos']),
        'meta': {
            'cuenta': parsed['meta']['cuenta'],
            'numero_cartola': parsed['meta']['numero_cartola'],
            'fecha_desde': parsed['meta']['fecha_desde'].isoformat() if parsed['meta']['fecha_desde'] else None,
            'fecha_hasta': parsed['meta']['fecha_hasta'].isoformat() if parsed['meta']['fecha_hasta'] else None,
            'saldo_inicial': parsed['meta']['saldo_inicial'],
            'saldo_final': parsed['meta']['saldo_final'],
        },
        'mensaje': f'Cartola importada: {insertados} nuevos, {actualizados} actualizados, {omitidos} omitidos',
    }


def _cuenta_banco_id(conn: EmpresaBancoConexion) -> int:
    if not conn.cuenta_contable_id:
        raise ValueError('La conexión bancaria no tiene cuenta contable asociada')
    return conn.cuenta_contable_id


def _movimientos_libro_en_periodo(
    empresa_id: int,
    cuenta_banco_id: int,
    fecha_desde: date | None,
    fecha_hasta: date | None,
    incluir_ep_cobros: bool = True,
) -> list[Movimiento]:
    """Movimientos del libro candidatos a conciliar con el extracto.

    Incluye:
    - Asientos que tocan la cuenta banco de la conexión
    - EP Facturado / Pagado / Cedida (ingresos a cruzar con depósitos), aunque
      el destino contable sea otra cuenta banco (p.ej. «Banco pesos» vs Santander)
    """
    status_ep = ('Facturado', 'Pagado', 'Cedida')
    filtros_cuenta = or_(
        Movimiento.cta_origen_id == cuenta_banco_id,
        Movimiento.cta_destino_id == cuenta_banco_id,
    )
    if incluir_ep_cobros:
        filtros_cuenta = or_(
            filtros_cuenta,
            and_(
                Movimiento.clase == 'estado_pago',
                Movimiento.status_pago.in_(status_ep),
                Movimiento.transaccion == 'Ingreso',
            ),
        )

    q = Movimiento.query.filter(
        Movimiento.empresa_id == empresa_id,
        Movimiento.estado == 'Activo',
        filtros_cuenta,
    )
    if fecha_desde or fecha_hasta:
        fecha_ref = func.coalesce(
            Movimiento.fecha_estado_pago,
            Movimiento.fecha_facturacion,
            Movimiento.fecha_movimiento,
        )
        if fecha_desde:
            q = q.filter(fecha_ref >= fecha_desde)
        if fecha_hasta:
            q = q.filter(or_(
                Movimiento.fecha_movimiento <= fecha_hasta,
                fecha_ref <= fecha_hasta,
            ))
    return q.order_by(Movimiento.fecha_movimiento, Movimiento.id).all()


def _fecha_referencia_mov(m: Movimiento) -> date:
    if m.clase == 'estado_pago':
        if m.status_pago in ('Pagado', 'Cedida'):
            return m.fecha_estado_pago or m.fecha_facturacion or m.fecha_movimiento
        return m.fecha_facturacion or m.fecha_estado_pago or m.fecha_movimiento
    return m.fecha_movimiento


def _monto_efectivo_movimiento(m: Movimiento, cuenta_banco_id: int) -> tuple[float, str]:
    """Retorna monto y tipo esperado (ingreso/egreso) desde la perspectiva del banco."""
    monto = float(m.monto_pesos or 0)
    if m.status_pago == 'Cedida' and m.monto_ingreso_cesion is not None:
        monto = float(m.monto_ingreso_cesion)

    # EP Facturado / Pagado / Cedida: siempre son ingresos esperados o cobrados en banco
    if m.clase == 'estado_pago' and m.transaccion == 'Ingreso':
        return abs(monto), 'ingreso'

    if m.cta_destino_id == cuenta_banco_id and m.cta_origen_id != cuenta_banco_id:
        return abs(monto), 'ingreso'
    if m.cta_origen_id == cuenta_banco_id and m.cta_destino_id != cuenta_banco_id:
        return abs(monto), 'egreso'
    if m.cta_origen_id == cuenta_banco_id and m.cta_destino_id == cuenta_banco_id:
        return abs(monto), 'egreso'
    return abs(monto), 'ingreso' if m.transaccion == 'Ingreso' else 'egreso'


def _ids_movimientos_vinculados(empresa_id: int, conexion_id: int | None = None) -> set[int]:
    q = BancoMovimiento.query.filter(
        BancoMovimiento.empresa_id == empresa_id,
        BancoMovimiento.movimiento_id.isnot(None),
        BancoMovimiento.estado_conciliacion == 'conciliado',
    )
    if conexion_id:
        q = q.filter(BancoMovimiento.conexion_id == conexion_id)
    return {row.movimiento_id for row in q.with_entities(BancoMovimiento.movimiento_id).all()}


def _es_ep_cobro_bancario(mov: Movimiento, banco: BancoMovimiento) -> bool:
    return (
        mov.clase == 'estado_pago'
        and mov.status_pago in ('Facturado', 'Pagado', 'Cedida')
        and banco.tipo == 'ingreso'
    )


def _buscar_candidatos(
    banco: BancoMovimiento,
    movimientos_libro: list[Movimiento],
    cuenta_banco_id: int,
    vinculados: set[int],
    tolerancia_dias: int = 5,
    tolerancia_monto: float = 1.0,
) -> list[dict]:
    candidatos = []
    for mov in movimientos_libro:
        if mov.id in vinculados:
            continue
        monto, tipo = _monto_efectivo_movimiento(mov, cuenta_banco_id)
        if banco.tipo != tipo:
            continue
        if abs(monto - banco.monto) > tolerancia_monto:
            continue

        fecha_ref = _fecha_referencia_mov(mov)
        es_ep_cobro = _es_ep_cobro_bancario(mov, banco)
        # Depósitos vs EP Facturado/Pagado/Cedida: cobro puede diferir de la factura
        if es_ep_cobro:
            dias_antes = (fecha_ref - banco.fecha).days
            dias_despues = (banco.fecha - fecha_ref).days
            if dias_antes > 5 or dias_despues > max(90, tolerancia_dias):
                continue
            diff_dias = abs((banco.fecha - fecha_ref).days)
            score = 120 - min(diff_dias, 60) - (0 if abs(monto - banco.monto) < 0.01 else 5)
            if mov.status_pago == 'Pagado':
                score += 10
            elif mov.status_pago == 'Cedida':
                score += 8
            if mov.numero_factura and str(mov.numero_factura) in (banco.descripcion or ''):
                score += 25
        else:
            diff_dias = abs((fecha_ref - banco.fecha).days)
            if diff_dias > tolerancia_dias:
                continue
            score = 100 - diff_dias * 10 - (0 if abs(monto - banco.monto) < 0.01 else 5)
            if mov.clase == 'estado_pago':
                score += 15

        label = mov.descripcion or ''
        if mov.clase == 'estado_pago':
            ep = f'EP#{mov.numero_ep}' if mov.numero_ep else 'EP'
            st = mov.status_pago or ''
            label = f'{ep} [{st}] {label}'.strip()

        candidatos.append({
            'movimiento_id': mov.id,
            'fecha': fecha_ref.isoformat() if fecha_ref else None,
            'monto': monto,
            'descripcion': label,
            'transaccion': mov.transaccion,
            'clase': mov.clase,
            'status_pago': mov.status_pago,
            'diff_dias': diff_dias,
            'score': score,
            'es_ep_facturado': mov.status_pago == 'Facturado' and es_ep_cobro,
            'es_ep_cobro': es_ep_cobro,
        })
    candidatos.sort(key=lambda c: (-c['score'], c['diff_dias']))
    return candidatos[:5]


def _actualizar_ep_al_conciliar(mov: Movimiento, fecha_banco: date) -> str | None:
    """Actualiza datos del EP al vincular con depósito. Retorna etiqueta de cambio o None."""
    if mov.clase != 'estado_pago':
        return None
    if mov.status_pago == 'Facturado':
        mov.status_pago = 'Pagado'
        mov.fecha_estado_pago = fecha_banco
        return 'Facturado → Pagado'
    if mov.status_pago == 'Pagado':
        if not mov.fecha_estado_pago:
            mov.fecha_estado_pago = fecha_banco
            return 'Pagado · fecha cobro actualizada'
        return 'Pagado · vinculado'
    if mov.status_pago == 'Cedida':
        if not mov.fecha_estado_pago:
            mov.fecha_estado_pago = fecha_banco
            return 'Cedida · fecha cobro actualizada'
        return 'Cedida · vinculado'
    return None


def ejecutar_conciliacion_automatica(
    conexion_id: int,
    empresa_id: int,
    tolerancia_dias: int = 5,
    solo_exactos: bool = True,
) -> dict:
    conn = EmpresaBancoConexion.query.filter_by(id=conexion_id, empresa_id=empresa_id).first()
    if not conn:
        raise ValueError('Conexión bancaria no encontrada')
    cuenta_banco_id = _cuenta_banco_id(conn)

    pendientes = BancoMovimiento.query.filter_by(
        empresa_id=empresa_id,
        conexion_id=conexion_id,
        estado_conciliacion='pendiente',
    ).all()
    if not pendientes:
        return {'conciliados': 0, 'sugerencias': 0, 'mensaje': 'No hay movimientos bancarios pendientes'}

    fechas = [b.fecha for b in pendientes]
    fecha_desde = min(fechas) - timedelta(days=max(tolerancia_dias, 5))
    fecha_hasta = max(fechas) + timedelta(days=max(tolerancia_dias, 5))
    libro = _movimientos_libro_en_periodo(
        empresa_id, cuenta_banco_id, fecha_desde - timedelta(days=90), fecha_hasta,
    )
    vinculados = _ids_movimientos_vinculados(empresa_id, conexion_id)

    conciliados = 0
    ep_actualizados = 0
    sugerencias = 0
    proyectos_a_recalcular = set()
    for banco in pendientes:
        candidatos = _buscar_candidatos(
            banco, libro, cuenta_banco_id, vinculados, tolerancia_dias=tolerancia_dias,
        )
        if not candidatos:
            continue
        mejor = candidatos[0]
        mov = next((m for m in libro if m.id == mejor['movimiento_id']), None)
        auto_ok = (
            len(candidatos) == 1
            or (mejor['diff_dias'] == 0 and mejor['score'] >= 95)
            or (mejor.get('es_ep_cobro') and mejor['score'] >= 110 and (
                len(candidatos) == 1 or candidatos[1]['score'] < mejor['score'] - 15
            ))
        )
        if auto_ok or (not solo_exactos and mejor['score'] >= 90):
            banco.movimiento_id = mejor['movimiento_id']
            banco.estado_conciliacion = 'conciliado'
            vinculados.add(mejor['movimiento_id'])
            if mov:
                cambio = _actualizar_ep_al_conciliar(mov, banco.fecha)
                if cambio:
                    ep_actualizados += 1
                    if mov.proyecto_id:
                        proyectos_a_recalcular.add(mov.proyecto_id)
            conciliados += 1
        else:
            sugerencias += 1

    if proyectos_a_recalcular:
        from common import _recalcular_proyectos
        _recalcular_proyectos(empresa_id, *proyectos_a_recalcular)

    partes = [f'{conciliados} vinculados', f'{sugerencias} con candidatos ambiguos']
    if ep_actualizados:
        partes.append(f'{ep_actualizados} EP actualizados')
    return {
        'conciliados': conciliados,
        'sugerencias': sugerencias,
        'ep_actualizados': ep_actualizados,
        'pendientes': len(pendientes) - conciliados,
        'mensaje': 'Conciliación automática: ' + ', '.join(partes),
    }


def vincular_movimiento(banco_mov_id: int, movimiento_id: int, empresa_id: int) -> dict:
    banco = BancoMovimiento.query.filter_by(id=banco_mov_id, empresa_id=empresa_id).first()
    if not banco:
        raise ValueError('Movimiento bancario no encontrado')
    mov = Movimiento.query.filter_by(id=movimiento_id, empresa_id=empresa_id).first()
    if not mov:
        raise ValueError('Movimiento contable no encontrado')

    previo = BancoMovimiento.query.filter(
        BancoMovimiento.empresa_id == empresa_id,
        BancoMovimiento.movimiento_id == movimiento_id,
        BancoMovimiento.id != banco_mov_id,
        BancoMovimiento.estado_conciliacion == 'conciliado',
    ).first()
    if previo:
        raise ValueError(f'El movimiento #{movimiento_id} ya está conciliado con otro extracto bancario')

    banco.movimiento_id = movimiento_id
    banco.estado_conciliacion = 'conciliado'
    cambio = _actualizar_ep_al_conciliar(mov, banco.fecha)
    msg = 'Movimientos vinculados'
    if cambio:
        msg += f' · {cambio}'
        if mov.proyecto_id:
            from common import _recalcular_proyectos
            _recalcular_proyectos(empresa_id, mov.proyecto_id)
    return {'mensaje': msg, 'banco_id': banco.id, 'movimiento_id': movimiento_id}


def desvincular_movimiento(banco_mov_id: int, empresa_id: int) -> dict:
    banco = BancoMovimiento.query.filter_by(id=banco_mov_id, empresa_id=empresa_id).first()
    if not banco:
        raise ValueError('Movimiento bancario no encontrado')
    banco.movimiento_id = None
    banco.estado_conciliacion = 'pendiente'
    return {'mensaje': 'Vínculo eliminado', 'banco_id': banco.id}


def ignorar_movimiento_banco(banco_mov_id: int, empresa_id: int, ignorar: bool = True) -> dict:
    banco = BancoMovimiento.query.filter_by(id=banco_mov_id, empresa_id=empresa_id).first()
    if not banco:
        raise ValueError('Movimiento bancario no encontrado')
    if ignorar:
        banco.estado_conciliacion = 'ignorado'
        banco.movimiento_id = None
    else:
        banco.estado_conciliacion = 'pendiente'
    return {'mensaje': 'Estado actualizado', 'banco_id': banco.id, 'estado': banco.estado_conciliacion}


def _marcador_extracto_banco(fintoc_id: str) -> str:
    return f'[banco:{fintoc_id}]'


def crear_movimiento_desde_linea_banco(
    banco_mov_id: int,
    empresa_id: int,
    contraparte_cuenta_id: int | None = None,
    descripcion: str | None = None,
    centro_costo: str = 'Administración',
    proyecto_id: int | None = None,
) -> dict:
    """Crea asiento contable desde línea bancaria pendiente y la concilia."""
    from common import (
        NOMBRE_CUENTA_CLIENTES,
        NOMBRE_CUENTA_GASTO_BANCO,
        _cuenta_por_nombre,
        _movimiento_a_dict,
        _recalcular_proyectos,
    )
    from contabilidad import calcular_transaccion
    from models import Cuenta, Proyecto

    banco = BancoMovimiento.query.filter_by(id=banco_mov_id, empresa_id=empresa_id).first()
    if not banco:
        raise ValueError('Movimiento bancario no encontrado')
    if banco.estado_conciliacion == 'conciliado' and banco.movimiento_id:
        raise ValueError('Este movimiento bancario ya está conciliado')

    conn = EmpresaBancoConexion.query.filter_by(id=banco.conexion_id, empresa_id=empresa_id).first()
    if not conn:
        raise ValueError('Conexión bancaria no encontrada')
    cuenta_banco_id = _cuenta_banco_id(conn)
    cuenta_banco = Cuenta.query.filter_by(id=cuenta_banco_id, empresa_id=empresa_id).first()
    if not cuenta_banco:
        raise ValueError('Cuenta bancaria no encontrada')

    marcador = _marcador_extracto_banco(banco.fintoc_id)
    if Movimiento.query.filter(
        Movimiento.empresa_id == empresa_id,
        Movimiento.descripcion.like(f'%{marcador}%'),
    ).first():
        raise ValueError('Ya existe un movimiento contable para esta línea bancaria')

    if contraparte_cuenta_id:
        contraparte = Cuenta.query.filter_by(id=contraparte_cuenta_id, empresa_id=empresa_id).first()
        if not contraparte:
            raise ValueError('Cuenta contraparte no encontrada')
        if contraparte.id == cuenta_banco.id:
            raise ValueError('La contraparte no puede ser la misma cuenta banco')
    elif banco.tipo == 'ingreso':
        contraparte = _cuenta_por_nombre(NOMBRE_CUENTA_CLIENTES, empresa_id)
    else:
        contraparte = _cuenta_por_nombre(NOMBRE_CUENTA_GASTO_BANCO, empresa_id)

    desc = (descripcion or banco.descripcion or 'Movimiento bancario').strip()
    if marcador not in desc:
        desc = f'{marcador} {desc}'
    desc = desc[:255]

    centro = (centro_costo or 'Administración').strip() or 'Administración'
    if proyecto_id:
        proyecto = Proyecto.query.filter_by(id=proyecto_id, empresa_id=empresa_id).first()
        if not proyecto:
            raise ValueError('Proyecto no encontrado')
        centro = proyecto.nombre

    if banco.tipo == 'ingreso':
        origen, destino = contraparte, cuenta_banco
    else:
        origen, destino = cuenta_banco, contraparte

    transaccion = calcular_transaccion(origen.categoria, destino.categoria)
    mov = Movimiento(
        empresa_id=empresa_id,
        fecha_movimiento=banco.fecha,
        monto_pesos=float(banco.monto),
        centro_costo=centro,
        estado='Activo',
        clase='general',
        cta_origen_id=origen.id,
        cta_destino_id=destino.id,
        transaccion=transaccion,
        descripcion=desc,
        proyecto_id=proyecto_id,
    )
    db.session.add(mov)
    db.session.flush()

    banco.movimiento_id = mov.id
    banco.estado_conciliacion = 'conciliado'

    _recalcular_proyectos(empresa_id, proyecto_id)

    return {
        'mensaje': 'Movimiento creado y conciliado',
        'banco_id': banco.id,
        'movimiento_id': mov.id,
        'movimiento': _movimiento_a_dict(mov),
    }


def obtener_estado_conciliacion(
    conexion_id: int,
    empresa_id: int,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    tolerancia_dias: int = 5,
) -> dict:
    from common import _banco_movimiento_a_dict, _movimiento_a_dict

    conn = EmpresaBancoConexion.query.filter_by(id=conexion_id, empresa_id=empresa_id).first()
    if not conn:
        raise ValueError('Conexión bancaria no encontrada')
    cuenta_banco_id = _cuenta_banco_id(conn)

    q_banco = BancoMovimiento.query.filter_by(empresa_id=empresa_id, conexion_id=conexion_id)
    if fecha_desde:
        q_banco = q_banco.filter(BancoMovimiento.fecha >= fecha_desde)
    if fecha_hasta:
        q_banco = q_banco.filter(BancoMovimiento.fecha <= fecha_hasta)
    banco_rows = q_banco.order_by(BancoMovimiento.fecha.desc(), BancoMovimiento.id.desc()).all()

    if not fecha_desde and banco_rows:
        fecha_desde = min(b.fecha for b in banco_rows)
    if not fecha_hasta and banco_rows:
        fecha_hasta = max(b.fecha for b in banco_rows)

    libro_rows = _movimientos_libro_en_periodo(
        empresa_id,
        cuenta_banco_id,
        (fecha_desde - timedelta(days=90)) if fecha_desde else None,
        fecha_hasta,
    )
    vinculados = _ids_movimientos_vinculados(empresa_id, conexion_id)

    banco_out = []
    conciliados = 0
    pendientes_banco = 0
    ignorados = 0
    monto_banco_pendiente = 0.0
    mov_por_id = {m.id: m for m in libro_rows}

    for b in banco_rows:
        item = _banco_movimiento_a_dict(b)
        if b.estado_conciliacion == 'conciliado' and b.movimiento_id:
            mov = mov_por_id.get(b.movimiento_id) or Movimiento.query.get(b.movimiento_id)
            item['movimiento'] = _movimiento_a_dict(mov) if mov else None
            item['candidatos'] = []
            conciliados += 1
        elif b.estado_conciliacion == 'ignorado':
            item['movimiento'] = None
            item['candidatos'] = []
            ignorados += 1
        else:
            candidatos = _buscar_candidatos(
                b, libro_rows, cuenta_banco_id, vinculados, tolerancia_dias=tolerancia_dias,
            )
            item['movimiento'] = None
            item['candidatos'] = candidatos
            pendientes_banco += 1
            signo = 1 if b.tipo == 'ingreso' else -1
            monto_banco_pendiente += signo * b.monto
        banco_out.append(item)

    libro_out = []
    pendientes_libro = 0
    monto_libro_pendiente = 0.0
    for mov in libro_rows:
        item = _movimiento_a_dict(mov)
        item['conciliado'] = mov.id in vinculados
        if not item['conciliado']:
            pendientes_libro += 1
            monto, tipo = _monto_efectivo_movimiento(mov, cuenta_banco_id)
            signo = 1 if tipo == 'ingreso' else -1
            monto_libro_pendiente += signo * monto
        libro_out.append(item)

    return {
        'conexion': {
            'id': conn.id,
            'nombre': conn.nombre,
            'cuenta_contable_id': cuenta_banco_id,
            'cuenta_contable_nombre': conn.cuenta_contable.nombre if conn.cuenta_contable else '',
        },
        'periodo': {
            'fecha_desde': fecha_desde.isoformat() if fecha_desde else None,
            'fecha_hasta': fecha_hasta.isoformat() if fecha_hasta else None,
        },
        'resumen': {
            'total_banco': len(banco_rows),
            'conciliados': conciliados,
            'pendientes_banco': pendientes_banco,
            'ignorados': ignorados,
            'pendientes_libro': pendientes_libro,
            'monto_banco_pendiente': round(monto_banco_pendiente, 0),
            'monto_libro_pendiente': round(monto_libro_pendiente, 0),
            'diferencia_neta': round(monto_banco_pendiente - monto_libro_pendiente, 0),
        },
        'banco': banco_out,
        'libro': libro_out,
    }
