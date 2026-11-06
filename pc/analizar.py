"""analizar.py - Convierte los CSV de los experimentos en tablas y graficas.

Lee resultados/E1_resumen.csv, E2_resumen.csv, E3_resumen.csv y
E4_modelos_placa.csv (copiados de la placa con mpremote), calcula la mediana y
el rango de las tres repeticiones y escribe:
    resultados/tablas.md          tablas listas para el informe
    resultados/graficas/*.png     graficas (si matplotlib esta instalado)
Toda cifra del informe sale de estos CSV, asi es rastreable.

Uso:  python3 pc/analizar.py [--resultados resultados]
"""
import argparse
import csv
import math
import os
from collections import OrderedDict

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def leer(ruta):
    if not os.path.exists(ruta):
        return []
    with open(ruta) as f:
        return list(csv.DictReader(f))


def mediana(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def agrupar(filas, campos):
    """{variante: {campo: (mediana, minimo, maximo)}} conservando el orden."""
    grupos = OrderedDict()
    for f in filas:
        grupos.setdefault(f['variante'], []).append(f)
    salida = OrderedDict()
    for var, fs in grupos.items():
        salida[var] = {}
        for c in campos:
            vals = [float(f[c]) for f in fs if f.get(c) not in (None, '')]
            if vals:
                salida[var][c] = (mediana(vals), min(vals), max(vals), len(vals))
    return salida


def fmt(t, escala=1, dec=0):
    m, lo, hi, n = t
    return '%.*f [%.*f–%.*f]' % (dec, m * escala, dec, lo * escala, dec, hi * escala)


def tabla(titulo, g, columnas):
    lineas = ['### ' + titulo, '', '| Variante | ' + ' | '.join(c[1] for c in columnas) + ' |',
              '|---|' + '---|' * len(columnas)]
    for var, d in g.items():
        celdas = []
        for campo, _, escala, dec in columnas:
            celdas.append(fmt(d[campo], escala, dec) if campo in d else '—')
        lineas.append('| %s | %s |' % (var, ' | '.join(celdas)))
    lineas += ['', 'Mediana [mínimo–máximo] de las repeticiones.', '']
    return '\n'.join(lineas)


def clasificar_curva(xs, ys):
    """Sugerencia para E2 (el equipo la confirma mirando la grafica)."""
    if len(ys) < 3:
        return 'insuficiente'
    inc = [b - a for a, b in zip(ys, ys[1:])]
    if all(abs(d) <= 0.05 * max(abs(y) for y in ys) for d in inc[-2:]) and inc[0] > 0:
        return 'meseta'
    # ajuste lineal vs exponencial por minimos cuadrados (R^2)
    def r2(x, y):
        n = len(x)
        mx, my = sum(x) / n, sum(y) / n
        sxx = sum((a - mx) ** 2 for a in x)
        sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
        if sxx == 0:
            return 0
        m = sxy / sxx
        b = my - m * mx
        ss_res = sum((yy - (m * xx + b)) ** 2 for xx, yy in zip(x, y))
        ss_tot = sum((yy - my) ** 2 for yy in y) or 1e-12
        return 1 - ss_res / ss_tot
    lin = r2(xs, ys)
    exp = r2(xs, [math.log(y) for y in ys]) if all(y > 0 for y in ys) else -1
    return 'exponencial' if exp > lin + 0.02 else 'lineal'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--resultados', default=os.path.join(RAIZ, 'resultados'))
    a = ap.parse_args()
    R = a.resultados
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        os.makedirs(os.path.join(R, 'graficas'), exist_ok=True)
    except ImportError:
        plt = None
        print('matplotlib no esta instalado: solo se generan tablas')

    partes = ['# Resultados de los experimentos', '',
              'Generado por `pc/analizar.py` a partir de los CSV de `%s`.' % os.path.basename(R), '']

    # --------------------------------------------------------------- E1
    e1 = leer(os.path.join(R, 'E1_resumen.csv'))
    if e1:
        procs = ['P%d' % i for i in range(1, 7)]
        campos = ['eficiencia', 'cpu_util', 'plazos_p1', 'jitter_desv_us', 'jitter_max_us',
                  'cambios_contexto'] + ['%s_%s' % (m, p) for m in ('espera', 'retorno', 'respuesta') for p in procs]
        g = agrupar(e1, campos)
        partes.append(tabla('E1 · Planificación: sistema', g, [
            ('eficiencia', 'Eficiencia %', 100, 1), ('cpu_util', 'CPU útil %', 100, 1),
            ('plazos_p1', 'Plazos incumplidos P1', 1, 0), ('jitter_desv_us', 'Jitter P1 desv (µs)', 1, 0),
            ('jitter_max_us', 'Jitter P1 máx (µs)', 1, 0), ('cambios_contexto', 'Cambios de contexto', 1, 0)]))
        for metrica in ('espera', 'retorno', 'respuesta'):
            partes.append(tabla('E1 · Tiempo de %s promedio por proceso (µs)' % metrica, g,
                                [('%s_%s' % (metrica, p), p, 1, 0) for p in procs]))
        if plt:
            fig, ejes = plt.subplots(1, 3, figsize=(13, 3.8))
            vars_ = list(g.keys())
            for eje, metrica in zip(ejes, ('espera', 'retorno', 'respuesta')):
                ancho = 0.8 / len(vars_)
                for i, var in enumerate(vars_):
                    ys = [g[var].get('%s_%s' % (metrica, p), (0,))[0] for p in procs]
                    eje.bar([j + i * ancho for j in range(6)], ys, ancho, label=var)
                eje.set_xticks([j + 0.4 - ancho / 2 for j in range(6)])
                eje.set_xticklabels(procs)
                eje.set_title('Tiempo de %s (µs, mediana)' % metrica)
            ejes[0].legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(os.path.join(R, 'graficas', 'E1_tiempos.png'), dpi=130)
            plt.close(fig)

    # --------------------------------------------------------------- E2
    e2 = leer(os.path.join(R, 'E2_resumen.csv'))
    if e2:
        campos = ['cpu_util', 'ram_libre_min', 'plazos_p1', 'eficiencia', 'us_por_ventana']
        g = agrupar(e2, campos)
        partes.append(tabla('E2 · Escalamiento de carga (copias de P2 y P3)', g, [
            ('cpu_util', 'CPU útil %', 100, 1), ('ram_libre_min', 'RAM libre mín (B)', 1, 0),
            ('plazos_p1', 'Plazos incumplidos P1', 1, 0), ('eficiencia', 'Eficiencia %', 100, 1),
            ('us_por_ventana', 'CPU por ventana (µs)', 1, 0)]))
        xs = [int(v.replace('copias', '')) for v in g]
        partes.append('Clasificación sugerida de las curvas (confirmar con la gráfica):\n')
        for campo, nombre in (('cpu_util', 'CPU'), ('ram_libre_min', 'RAM libre'), ('plazos_p1', 'Plazos P1')):
            ys = [g[v][campo][0] for v in g]
            if campo == 'ram_libre_min':
                ys = [ys[0] - y + 1 for y in ys]      # RAM usada adicional
            partes.append('- %s: **%s**' % (nombre, clasificar_curva(xs, ys)))
        partes.append('')
        if plt:
            fig, ejes = plt.subplots(1, 3, figsize=(13, 3.6))
            for eje, (campo, titulo, esc) in zip(ejes, (('cpu_util', 'CPU útil (%)', 100),
                                                        ('ram_libre_min', 'RAM libre mínima (B)', 1),
                                                        ('plazos_p1', 'Plazos incumplidos P1', 1))):
                med = [g[v][campo][0] * esc for v in g]
                lo = [g[v][campo][1] * esc for v in g]
                hi = [g[v][campo][2] * esc for v in g]
                eje.errorbar(xs, med, yerr=[[m - l for m, l in zip(med, lo)], [h - m for m, h in zip(med, hi)]],
                             marker='o', capsize=4)
                eje.set_xlabel('Copias de P2 y P3')
                eje.set_title(titulo)
                eje.set_xticks(xs)
            fig.tight_layout()
            fig.savefig(os.path.join(R, 'graficas', 'E2_escalamiento.png'), dpi=130)
            plt.close(fig)

    # --------------------------------------------------------------- E3
    e3 = leer(os.path.join(R, 'E3_resumen.csv'))
    if e3:
        g = agrupar(e3, ['ram_libre_min', 'gc_pausas', 'gc_us_max', 'us_por_ventana', 'plazos_p1'])
        partes.append(tabla('E3 · Memoria A (objetos nuevos) vs B (búferes preasignados)', g, [
            ('ram_libre_min', 'RAM libre mín (B)', 1, 0), ('gc_pausas', 'Pausas del GC', 1, 0),
            ('gc_us_max', 'Pausa GC máx (µs)', 1, 0), ('us_por_ventana', 'Ticks por ventana (µs)', 1, 0),
            ('plazos_p1', 'Plazos incumplidos P1', 1, 0)]))
        if plt:
            fig, ax = plt.subplots(figsize=(7, 3.5))
            for var in g:
                ruta = os.path.join(R, 'monitor_E3_%s_r1.csv' % var)
                filas = leer(ruta)
                if filas:
                    ax.plot([float(f['t_s']) for f in filas], [int(f['ram_libre']) for f in filas],
                            marker='o', label=var)
            ax.set_xlabel('t (s)')
            ax.set_ylabel('RAM libre (B)')
            ax.set_title('E3 · RAM libre en el tiempo (repetición 1)')
            ax.legend()
            fig.tight_layout()
            fig.savefig(os.path.join(R, 'graficas', 'E3_ram.png'), dpi=130)
            plt.close(fig)

    # --------------------------------------------------------------- E4
    e4 = leer(os.path.join(R, 'E4_modelos_placa.csv'))
    if e4:
        for f in e4:
            f['variante'] = f['modelo']
        g = agrupar(e4, ['exactitud', 'us_mediana', 'us_max', 'ram_bytes', 'tamano_bytes'])
        partes.append(tabla('E4 · Modelos TinyML en la placa (sesión reservada)', g, [
            ('exactitud', 'Exactitud %', 100, 1), ('us_mediana', 'Tiempo por ventana (µs)', 1, 0),
            ('us_max', 'Tiempo máx (µs)', 1, 0), ('ram_bytes', 'RAM (B)', 1, 0),
            ('tamano_bytes', 'Tamaño (B)', 1, 0)]))
    for nombre in ('lr', 'arbol'):
        m = leer(os.path.join(R, 'matriz_confusion_%s.csv' % nombre))
        if m:
            cols = list(m[0].keys())
            partes += ['### Matriz de confusión · %s (sesión reservada)' % nombre, '',
                       '| ' + ' | '.join(cols) + ' |', '|' + '---|' * len(cols)]
            partes += ['| ' + ' | '.join(f[c] for c in cols) + ' |' for f in m]
            partes.append('')

    with open(os.path.join(R, 'tablas.md'), 'w') as f:
        f.write('\n'.join(partes))
    print('Escrito', os.path.join(R, 'tablas.md'))


if __name__ == '__main__':
    main()
