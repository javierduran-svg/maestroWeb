"""Motor contable: tipos de transacción, balances y métricas de proyectos."""

from datetime import date
from calendar import monthrange

PERIODOS_DASHBOARD = frozenset({'diario', 'mensual', 'trimestral', 'semestral'})
_MESES_ES = ('Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic')

STATUS_GASTO_PROGRAMADO = 'Gasto programado'

CATEGORIAS_ACTIVO = frozenset({'activo_banco', 'activo_cliente', 'patrimonio_socio', 'pasivo_factoring'})
CATEGORIAS_BANCO = frozenset({'activo_banco'})


def calcular_transaccion(categoria_origen: str, categoria_destino: str) -> str:
    """Infiere Ingreso / Egreso / Transferencia según categorías de cuentas."""
    if categoria_destino == 'gasto':
        return 'Egreso'
    if categoria_origen == 'activo_cliente' and categoria_destino in CATEGORIAS_BANCO:
        return 'Ingreso'
    if categoria_origen in CATEGORIAS_BANCO and categoria_destino == 'activo_cliente':
        return 'Ingreso'
    if categoria_origen in CATEGORIAS_BANCO and categoria_destino in CATEGORIAS_BANCO:
        return 'Transferencia'
    if categoria_origen in CATEGORIAS_ACTIVO and categoria_destino in CATEGORIAS_ACTIVO:
        return 'Transferencia'
    if categoria_origen in CATEGORIAS_BANCO:
        return 'Egreso'
    if categoria_destino in CATEGORIAS_BANCO:
        return 'Ingreso'
    return 'Transferencia'


def _es_gasto_programado(m) -> bool:
    """Gasto planificado: no afecta libro contable ni saldos reales."""
    return (
        m.estado == 'Activo'
        and m.clase == 'gasto'
        and getattr(m, 'status_pago', None) == STATUS_GASTO_PROGRAMADO
    )


def _movimiento_afecta_contabilidad(m) -> bool:
    """Movimientos que impactan saldos de cuentas y egresos reales."""
    if m.estado != 'Activo':
        return False
    if _es_gasto_programado(m):
        return False
    return True


def calcular_balance_cuenta(cuenta_id: int, movimientos, saldo_inicial: float = 0.0) -> float:
    """Saldo = saldo_inicial + entradas (como destino) − salidas (como origen), solo movimientos activos."""
    entradas = sum(
        m.monto_pesos for m in movimientos
        if m.cta_destino_id == cuenta_id and _movimiento_afecta_contabilidad(m)
    )
    salidas = sum(
        m.monto_pesos for m in movimientos
        if m.cta_origen_id == cuenta_id and _movimiento_afecta_contabilidad(m)
    )
    return (saldo_inicial or 0.0) + entradas - salidas


def calcular_flujo_financiero(cuentas, movimientos) -> list:
    """Lista de cuentas con saldo y moneda."""
    return [
        {
            'id': c.id,
            'nombre': c.nombre,
            'categoria': c.categoria,
            'moneda': c.moneda,
            'saldo': calcular_balance_cuenta(c.id, movimientos, c.saldo_inicial or 0.0),
        }
        for c in cuentas
    ]


def recalcular_proyecto(proyecto, movimientos) -> None:
    """Actualiza montos del proyecto a partir de sus movimientos activos."""
    movs = [m for m in movimientos if m.proyecto_id == proyecto.id and m.estado == 'Activo']
    estados = [m for m in movs if m.clase == 'estado_pago']
    gastos = [m for m in movs if m.clase == 'gasto']

    proyecto.monto_contrato = sum(m.monto_pesos for m in estados)
    proyecto.monto_pagado = sum(
        m.monto_pesos for m in estados
        if getattr(m, 'status_pago', None) in ('Pagado', 'Cedida') or m.fecha_estado_pago is not None
    )
    proyecto.monto_facturado = sum(
        m.monto_pesos for m in estados
        if getattr(m, 'status_pago', None) in ('Facturado', 'Pagado', 'Cedida') or m.fecha_facturacion is not None
    )
    proyecto.monto_gastos = sum(
        m.monto_pesos for m in gastos if not _es_gasto_programado(m)
    )
    proyecto.saldo_por_facturar = max(0.0, proyecto.monto_contrato - proyecto.monto_facturado)


def _es_movimiento_facturado(m) -> bool:
    """Facturado: estados de pago facturados o posteriores (misma regla que monto_facturado)."""
    if m.estado != 'Activo' or m.clase != 'estado_pago':
        return False
    status = getattr(m, 'status_pago', None)
    return status in ('Facturado', 'Pagado', 'Cedida') or m.fecha_facturacion is not None


def _fecha_movimiento_facturado(m) -> date:
    return m.fecha_facturacion or m.fecha_movimiento


def _es_movimiento_ingreso(m) -> bool:
    """Ingresos: transacciones Ingreso cobradas (status Pagado) o ingresos generales."""
    if m.estado != 'Activo' or m.transaccion != 'Ingreso':
        return False
    if m.clase == 'estado_pago':
        return getattr(m, 'status_pago', None) == 'Pagado'
    return True


def _fecha_movimiento_ingreso(m) -> date:
    if m.clase == 'estado_pago' and m.fecha_estado_pago:
        return m.fecha_estado_pago
    return m.fecha_movimiento


# Estados de pago aún no cobrados; se usan para proyección de ingresos futuros.
_STATUS_INGRESO_PROYECTABLE = frozenset({'Por enviar', 'Enviado', 'Facturado'})


def _es_ingreso_proyectado(m) -> bool:
    """Ingreso pendiente de cobro (no enviado / no pagado) para proyección del dashboard."""
    if m.estado != 'Activo' or m.clase != 'estado_pago' or m.transaccion != 'Ingreso':
        return False
    status = getattr(m, 'status_pago', None)
    if status in ('Pagado', 'Cedida'):
        return False
    return status in _STATUS_INGRESO_PROYECTABLE


def _fecha_ingreso_proyectado(m) -> date:
    """
    Fecha esperada de cobro para la proyección:
    - Por enviar / Enviado: fecha_movimiento (fecha planificada del estado de pago).
    - Facturado sin cobro: fecha_movimiento; si falta, fecha_facturacion.
    """
    status = getattr(m, 'status_pago', None)
    if status == 'Facturado':
        return m.fecha_movimiento or m.fecha_facturacion
    return m.fecha_movimiento


def _es_movimiento_gasto(m) -> bool:
    """Gastos reales: egresos activos excluyendo gastos programados."""
    return _movimiento_afecta_contabilidad(m) and m.transaccion == 'Egreso'


def _es_gasto_proyectado(m) -> bool:
    """Gasto programado para proyección del dashboard (flotación financiera)."""
    return _es_gasto_programado(m)


def calcular_kpis(cuentas, proyectos, movimientos) -> dict:
    """KPIs para el dashboard."""
    hoy = date.today()
    inicio_mes = date(hoy.year, hoy.month, 1)
    _, ultimo_dia = monthrange(hoy.year, hoy.month)
    fin_mes = date(hoy.year, hoy.month, ultimo_dia)

    cuenta_pesos = next(
        (c for c in cuentas if c.moneda == 'CLP' and c.categoria == 'activo_banco'),
        None,
    )
    disponible_pesos = (
        calcular_balance_cuenta(
            cuenta_pesos.id, movimientos, cuenta_pesos.saldo_inicial or 0.0,
        ) if cuenta_pesos else 0.0
    )

    proyectos_activos = [p for p in proyectos if p.status == 'Activo']
    por_facturar = sum(p.saldo_por_facturar for p in proyectos_activos)

    egresos_mes = sum(
        m.monto_pesos for m in movimientos
        if _es_movimiento_gasto(m)
        and inicio_mes <= m.fecha_movimiento <= fin_mes
    )

    ingresos_proyectados_mes = sum(
        m.monto_pesos for m in movimientos
        if _es_ingreso_proyectado(m)
        and m.fecha_movimiento
        and inicio_mes <= _fecha_ingreso_proyectado(m) <= fin_mes
    )
    flotacion_mes = sum(
        m.monto_pesos for m in movimientos
        if _es_gasto_programado(m)
        and m.fecha_movimiento
        and inicio_mes <= m.fecha_movimiento <= fin_mes
    )
    utilidad_proyectada = ingresos_proyectados_mes - flotacion_mes

    return {
        'disponible_pesos': disponible_pesos,
        'por_facturar': por_facturar,
        'proyectos_activos': len(proyectos_activos),
        'egresos_mes': egresos_mes,
        'flotacion_mes': flotacion_mes,
        'ingresos_proyectados_mes': ingresos_proyectados_mes,
        'utilidad_proyectada': utilidad_proyectada,
    }


def _acumular_serie(valores: list[float]) -> list[float]:
    """Convierte montos por bucket en sumas acumuladas."""
    total = 0.0
    acumulado = []
    for v in valores:
        total += v
        acumulado.append(round(total))
    return acumulado


def _clave_periodo(fecha: date, periodo: str) -> str:
    if periodo == 'diario':
        return fecha.isoformat()
    if periodo == 'mensual':
        return f'{fecha.year}-{fecha.month:02d}'
    if periodo == 'trimestral':
        return f'{fecha.year}-Q{(fecha.month - 1) // 3 + 1}'
    if periodo == 'semestral':
        return f'{fecha.year}-H{1 if fecha.month <= 6 else 2}'
    raise ValueError(f'Periodo no válido: {periodo}')


def _buckets_fijos_periodo(periodo: str, anio: int) -> list[str]:
    if periodo == 'mensual':
        return [f'{anio}-{mes:02d}' for mes in range(1, 13)]
    if periodo == 'trimestral':
        return [f'{anio}-Q{q}' for q in range(1, 5)]
    if periodo == 'semestral':
        return [f'{anio}-H{h}' for h in range(1, 3)]
    return []


def _etiqueta_periodo(clave: str, periodo: str) -> str:
    if periodo == 'diario':
        d = date.fromisoformat(clave)
        return f'{d.day:02d}/{d.month:02d}'
    if periodo == 'mensual':
        anio, mes = clave.split('-')
        return f'{_MESES_ES[int(mes) - 1]} {anio}'
    if periodo == 'trimestral':
        anio, q = clave.split('-')
        return f'{q} {anio}'
    if periodo == 'semestral':
        anio, h = clave.split('-')
        return f'{h} {anio}'
    return clave


def calcular_series_dashboard(
    movimientos,
    periodo: str = 'mensual',
    anio: int | None = None,
    acumulado: bool = False,
    proyeccion: bool = False,
) -> dict:
    """Series temporales de facturado, ingresos y gastos para el gráfico del dashboard."""
    if periodo not in PERIODOS_DASHBOARD:
        raise ValueError(f'Periodo no válido: {periodo}')

    anio = anio or date.today().year
    inicio_anio = date(anio, 1, 1)
    fin_anio = date(anio, 12, 31)

    totales: dict[str, dict[str, float]] = {}
    claves_diario: set[str] = set()

    def _bucket(k: str, campo: str, monto: float) -> None:
        totales.setdefault(k, {
            'facturado': 0.0,
            'ingresos': 0.0,
            'ingresos_proyectados': 0.0,
            'gastos': 0.0,
            'gastos_proyectados': 0.0,
        })
        totales[k][campo] += monto
        if periodo == 'diario':
            claves_diario.add(k)

    for m in movimientos:
        if _es_movimiento_facturado(m):
            f = _fecha_movimiento_facturado(m)
            if inicio_anio <= f <= fin_anio:
                _bucket(_clave_periodo(f, periodo), 'facturado', m.monto_pesos)

        if _es_movimiento_ingreso(m):
            f = _fecha_movimiento_ingreso(m)
            if inicio_anio <= f <= fin_anio:
                _bucket(_clave_periodo(f, periodo), 'ingresos', m.monto_pesos)

        # Proyección: estados Por enviar / Enviado / Facturado con fecha esperada de cobro.
        if proyeccion and _es_ingreso_proyectado(m):
            f = _fecha_ingreso_proyectado(m)
            if inicio_anio <= f <= fin_anio:
                _bucket(_clave_periodo(f, periodo), 'ingresos_proyectados', m.monto_pesos)

        if _es_movimiento_gasto(m):
            f = m.fecha_movimiento
            if inicio_anio <= f <= fin_anio:
                _bucket(_clave_periodo(f, periodo), 'gastos', m.monto_pesos)

        if proyeccion and _es_gasto_proyectado(m):
            f = m.fecha_movimiento
            if f and inicio_anio <= f <= fin_anio:
                _bucket(_clave_periodo(f, periodo), 'gastos_proyectados', m.monto_pesos)

    if periodo == 'diario':
        claves = sorted(claves_diario)
    else:
        claves = _buckets_fijos_periodo(periodo, anio)

    labels = [_etiqueta_periodo(k, periodo) for k in claves]
    facturado = [round(totales.get(k, {}).get('facturado', 0.0)) for k in claves]
    ingresos = [round(totales.get(k, {}).get('ingresos', 0.0)) for k in claves]
    ingresos_proyectados = [round(totales.get(k, {}).get('ingresos_proyectados', 0.0)) for k in claves]
    gastos = [round(totales.get(k, {}).get('gastos', 0.0)) for k in claves]
    gastos_proyectados = [round(totales.get(k, {}).get('gastos_proyectados', 0.0)) for k in claves]

    if acumulado:
        facturado = _acumular_serie(facturado)
        ingresos = _acumular_serie(ingresos)
        ingresos_proyectados = _acumular_serie(ingresos_proyectados)
        gastos = _acumular_serie(gastos)
        gastos_proyectados = _acumular_serie(gastos_proyectados)

    resultado = {
        'periodo': periodo,
        'anio': anio,
        'acumulado': acumulado,
        'proyeccion': proyeccion,
        'labels': labels,
        'facturado': facturado,
        'ingresos': ingresos,
        'gastos': gastos,
    }
    if proyeccion:
        resultado['ingresos_proyectados'] = ingresos_proyectados
        resultado['gastos_proyectados'] = gastos_proyectados
    return resultado


# Rutinas contables Chile — reglas estáticas (día del mes); configurable en el futuro.
RUTINAS_CONTABLES = [
    {
        'id': 'rcv',
        'nombre': 'RCV SII',
        'categoria': 'sii',
        'dia_inicio': 1,
        'dia_fin': 8,
        'color': '#0d6efd',
        'descripcion': 'Registro de compras y ventas del mes anterior (SII).',
    },
    {
        'id': 'previred',
        'nombre': 'Previred',
        'categoria': 'previred',
        'dia_inicio': 10,
        'dia_fin': 13,
        'color': '#008080',
        'descripcion': 'Envío planilla de remuneraciones y cotizaciones previsionales.',
    },
    {
        'id': 'f29',
        'nombre': 'F-29 IVA',
        'categoria': 'sii',
        'dia': 12,
        'color': '#dc3545',
        'descripcion': 'Declaración y pago formulario F-29 (IVA y retenciones del mes anterior).',
    },
    {
        'id': 'libro_remuneraciones',
        'nombre': 'Libro remuneraciones',
        'categoria': 'remuneraciones',
        'dia': 15,
        'color': '#6f42c1',
        'descripcion': 'Actualización libro de remuneraciones electrónico (DT).',
    },
    {
        'id': 'factura_compra',
        'nombre': 'Facturas compra',
        'categoria': 'sii',
        'dia_inicio': 1,
        'dia_fin': 5,
        'color': '#fd7e14',
        'descripcion': 'Recepción y registro de facturas de compra del mes anterior.',
    },
    {
        'id': 'pago_impuesto_renta',
        'nombre': 'PPM renta',
        'categoria': 'sii',
        'dia': 12,
        'color': '#b91c1c',
        'descripcion': 'Pago provisional mensual de impuesto a la renta (junto con F-29).',
    },
]

_RUTINAS_ANUALES = [
    {
        'id': 'f22',
        'nombre': 'F-22 renta anual',
        'categoria': 'sii',
        'mes': 4,
        'dia': 30,
        'color': '#991b1b',
        'descripcion': 'Declaración anual de renta (F-22) — plazo referencial abril.',
    },
    {
        'id': 'dj_1887',
        'nombre': 'DJ 1887',
        'categoria': 'sii',
        'mes': 3,
        'dia': 15,
        'color': '#1e40af',
        'descripcion': 'Declaración jurada 1887 — honorarios y retenciones.',
    },
]


def generar_eventos_calendario_contable(anio: int, mes: int) -> dict:
    """Genera eventos del calendario contable para un mes (reglas estáticas Chile)."""
    _, ultimo_dia = monthrange(anio, mes)
    eventos_por_dia: dict[int, list] = {d: [] for d in range(1, ultimo_dia + 1)}

    for rutina in RUTINAS_CONTABLES:
        if 'dia' in rutina:
            dias = [rutina['dia']] if rutina['dia'] <= ultimo_dia else []
        else:
            ini = max(1, rutina.get('dia_inicio', 1))
            fin = min(ultimo_dia, rutina.get('dia_fin', ultimo_dia))
            dias = list(range(ini, fin + 1)) if ini <= fin else []

        for d in dias:
            eventos_por_dia[d].append({
                'id': rutina['id'],
                'nombre': rutina['nombre'],
                'categoria': rutina['categoria'],
                'color': rutina['color'],
                'descripcion': rutina['descripcion'],
            })

    for rutina in _RUTINAS_ANUALES:
        if rutina.get('mes') == mes:
            d = rutina['dia']
            if d <= ultimo_dia:
                eventos_por_dia[d].append({
                    'id': rutina['id'],
                    'nombre': rutina['nombre'],
                    'categoria': rutina['categoria'],
                    'color': rutina['color'],
                    'descripcion': rutina['descripcion'],
                })

    dias = []
    for d in range(1, ultimo_dia + 1):
        evs = eventos_por_dia[d]
        dias.append({
            'dia': d,
            'eventos': evs,
            'tiene_eventos': bool(evs),
        })

    return {
        'anio': anio,
        'mes': mes,
        'nombre_mes': _MESES_ES[mes - 1],
        'dias': dias,
        'rutinas': RUTINAS_CONTABLES + _RUTINAS_ANUALES,
    }
