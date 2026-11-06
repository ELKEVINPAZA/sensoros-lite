"""entrenar.py - M5: entrenamiento en el PC (Python 3 puro, sin librerias).

Lee los CSV capturados con SensorOS-Lite (modo captura) en datos/:
    datos/s<sesion>_<integrante>_<clase>.csv   con columnas temp,hum,confort (x10)
arma ventanas de 128 muestras con 50 % de solapamiento, calcula los rasgos con
el MISMO firmware/rasgos.py de la placa, entrena dos modelos y los evalua con
una sesion completa reservada (no con ventanas al azar):
    - regresion logistica multiclase (softmax)
    - arbol de decision (CART, Gini)
Exporta firmware/modelo_lr.py y firmware/modelo_arbol.py (inferencia en
MicroPython puro) y escribe en resultados/ las matrices de confusion y E4.

Uso:  python3 pc/entrenar.py [--datos datos] [--reservada 2]
"""
import argparse
import csv
import glob
import math
import os
import random
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, 'firmware'))
import rasgos  # noqa: E402

# Las 3 clases de ambiente (igual que en firmware/sensor.py y config.py).
CLASES = ('frio', 'normal', 'calido')
VENTANA, SALTO = 128, 64


# ------------------------------------------------------------------- datos
def leer_ventanas(carpeta):
    """Devuelve lista de (sesion, integrante, clase_idx, rasgos)."""
    filas = []
    archivos = sorted(glob.glob(os.path.join(carpeta, 's*_*_*.csv')))
    if not archivos:
        sys.exit('No hay archivos s<sesion>_<integrante>_<clase>.csv en ' + carpeta)
    for ruta in archivos:
        base = os.path.basename(ruta)[:-4]
        ses, integrante, clase = base.split('_', 2)
        if clase not in CLASES:
            print('  (omitido, clase desconocida)', base)
            continue
        with open(ruta) as f:
            lector = csv.reader(f)
            next(lector)   # cabecera
            muestras = []
            for fila in lector:
                muestras.extend(int(x) for x in fila[:3])
        n = len(muestras) // 3
        cuenta = 0
        for ini in range(0, n - VENTANA + 1, SALTO):
            v = muestras[3 * ini:3 * (ini + VENTANA)]
            filas.append((int(ses[1:]), integrante, CLASES.index(clase),
                          rasgos.calcular(v, VENTANA)))
            cuenta += 1
        print('  %-28s %5d muestras -> %3d ventanas' % (base, n, cuenta))
    return filas


# ------------------------------------------------------- regresion logistica
def normalizar(X):
    d = len(X[0])
    mu = [sum(x[j] for x in X) / len(X) for j in range(d)]
    # Si un rasgo es casi constante (desviacion ~0), dividir por el daria numeros
    # gigantes: en ese caso usamos 1.0 para no romper la regresion.
    sd = []
    for j in range(d):
        v = math.sqrt(sum((x[j] - mu[j]) ** 2 for x in X) / len(X))
        sd.append(v if v > 1e-6 else 1.0)
    return mu, sd


def entrenar_lr(X, y, k, epocas=600, tasa=0.3, l2=1e-3, semilla=1):
    random.seed(semilla)
    mu, sd = normalizar(X)
    Z = [[(x[j] - mu[j]) / sd[j] for j in range(len(x))] for x in X]
    d = len(Z[0])
    W = [[0.0] * d for _ in range(k)]
    b = [0.0] * k
    n = len(Z)
    for _ in range(epocas):
        gW = [[0.0] * d for _ in range(k)]
        gb = [0.0] * k
        for z, t in zip(Z, y):
            s = [sum(W[c][j] * z[j] for j in range(d)) + b[c] for c in range(k)]
            m = max(s)
            e = [math.exp(v - m) for v in s]
            tot = sum(e)
            for c in range(k):
                g = e[c] / tot - (1.0 if c == t else 0.0)
                gb[c] += g
                for j in range(d):
                    gW[c][j] += g * z[j]
        for c in range(k):
            b[c] -= tasa * gb[c] / n
            for j in range(d):
                W[c][j] -= tasa * (gW[c][j] / n + l2 * W[c][j])
    return {'mu': mu, 'sd': sd, 'W': W, 'b': b}


def predecir_lr(m, x):
    mejor, mejor_s = 0, -1e30
    for c in range(len(m['b'])):
        s = m['b'][c]
        for j in range(len(x)):
            s += m['W'][c][j] * (x[j] - m['mu'][j]) / m['sd'][j]
        if s > mejor_s:
            mejor, mejor_s = c, s
    return mejor


# ---------------------------------------------------------- arbol de decision
def gini(y, k):
    n = len(y)
    if n == 0:
        return 0.0
    return 1.0 - sum((y.count(c) / n) ** 2 for c in range(k))


def entrenar_arbol(X, y, k, prof_max=4, min_hoja=3, prof=0):
    mayoritaria = max(range(k), key=y.count)
    if prof >= prof_max or len(set(y)) == 1 or len(y) < 2 * min_hoja:
        return mayoritaria
    mejor = None
    g_padre = gini(y, k)
    n = len(y)
    for j in range(len(X[0])):
        # barrido ordenado: se mueven las muestras de derecha a izquierda una a una
        orden = sorted(range(n), key=lambda i: X[i][j])
        ci = [0] * k
        cd = [y.count(c) for c in range(k)]
        for pos in range(n - 1):
            t = y[orden[pos]]
            ci[t] += 1
            cd[t] -= 1
            a, b = X[orden[pos]][j], X[orden[pos + 1]][j]
            ni = pos + 1
            nd = n - ni
            if a == b or ni < min_hoja or nd < min_hoja:
                continue
            gi = 1.0 - sum((v / ni) ** 2 for v in ci)
            gd = 1.0 - sum((v / nd) ** 2 for v in cd)
            g = (ni * gi + nd * gd) / n
            if mejor is None or g < mejor[0]:
                mejor = (g, j, (a + b) / 2)
    if mejor is None or mejor[0] >= g_padre:
        return mayoritaria
    _, j, u = mejor
    izq = [(x, t) for x, t in zip(X, y) if x[j] <= u]
    der = [(x, t) for x, t in zip(X, y) if x[j] > u]
    return (j, u,
            entrenar_arbol([p[0] for p in izq], [p[1] for p in izq], k, prof_max, min_hoja, prof + 1),
            entrenar_arbol([p[0] for p in der], [p[1] for p in der], k, prof_max, min_hoja, prof + 1))


def predecir_arbol(nodo, x):
    while isinstance(nodo, tuple):
        nodo = nodo[2] if x[nodo[0]] <= nodo[1] else nodo[3]
    return nodo


def contar_nodos(nodo):
    if isinstance(nodo, tuple):
        return 1 + contar_nodos(nodo[2]) + contar_nodos(nodo[3])
    return 1


# ------------------------------------------------------------- exportacion
def exportar_lr(m, ruta, nota):
    with open(ruta, 'w') as f:
        f.write('# modelo_lr.py - Regresion logistica (softmax) generada por pc/entrenar.py\n')
        f.write('# %s\n' % nota)
        f.write('# Inferencia en MicroPython puro: z=(x-mu)/sd ; puntaje_c = b_c + W_c . z ; argmax\n')
        f.write('CLASES = %r\n' % (CLASES,))
        f.write('MU = %r\n' % (tuple(round(v, 6) for v in m['mu']),))
        f.write('INV_SD = %r\n' % (tuple(round(1 / v, 6) for v in m['sd']),))
        f.write('W = (\n')
        for fila in m['W']:
            f.write('    %r,\n' % (tuple(round(v, 6) for v in fila),))
        f.write(')\n')
        f.write('B = %r\n\n' % (tuple(round(v, 6) for v in m['b']),))
        f.write('''
def predecir(x):
    mejor = 0
    mejor_s = -1e30
    n = len(MU)
    for c in range(len(B)):
        w = W[c]
        s = B[c]
        for j in range(n):
            s += w[j] * (x[j] - MU[j]) * INV_SD[j]
        if s > mejor_s:
            mejor = c
            mejor_s = s
    return mejor
''')


def exportar_arbol(nodo, ruta, nota):
    # Se aplana el arbol en tuplas: (rasgo, umbral, hijo_izq, hijo_der); hoja = (-1, clase)
    nodos = []

    def aplanar(n):
        i = len(nodos)
        nodos.append(None)
        if isinstance(n, tuple):
            izq = aplanar(n[2])
            der = aplanar(n[3])
            nodos[i] = (n[0], round(n[1], 6), izq, der)
        else:
            nodos[i] = (-1, n, 0, 0)
        return i
    aplanar(nodo)
    with open(ruta, 'w') as f:
        f.write('# modelo_arbol.py - Arbol de decision (CART) generado por pc/entrenar.py\n')
        f.write('# %s\n' % nota)
        f.write('# Cada nodo: (rasgo, umbral, izquierdo, derecho); hoja: (-1, clase, 0, 0)\n')
        f.write('CLASES = %r\n' % (CLASES,))
        f.write('NODOS = (\n')
        for t in nodos:
            f.write('    %r,\n' % (t,))
        f.write(')\n\n')
        f.write('''
def predecir(x):
    i = 0
    while True:
        rasgo, umbral, izq, der = NODOS[i]
        if rasgo < 0:
            return umbral
        i = izq if x[rasgo] <= umbral else der
''')


# --------------------------------------------------------------- evaluacion
def matriz(pred, real, k):
    M = [[0] * k for _ in range(k)]
    for p, r in zip(pred, real):
        M[r][p] += 1
    return M


def imprimir_matriz(nombre, M):
    print('\n  Matriz de confusion - %s (filas = real, columnas = predicho)' % nombre)
    print('  %10s' % '' + ''.join('%10s' % c for c in CLASES))
    for c, fila in zip(CLASES, M):
        print('  %10s' % c + ''.join('%10d' % v for v in fila))
    exact = sum(M[i][i] for i in range(len(M))) / max(1, sum(map(sum, M)))
    print('  Exactitud: %.1f %%' % (100 * exact))
    return exact


def guardar_matriz(ruta, M):
    with open(ruta, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['real\\predicho'] + list(CLASES))
        for c, fila in zip(CLASES, M):
            w.writerow([c] + fila)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', default=os.path.join(RAIZ, 'datos'))
    ap.add_argument('--reservada', type=int, default=2, help='sesion usada solo para evaluar')
    ap.add_argument('--salida', default=os.path.join(RAIZ, 'firmware'))
    ap.add_argument('--resultados', default=os.path.join(RAIZ, 'resultados'))
    a = ap.parse_args()

    ruta_datos = os.path.abspath(a.datos)
    if 'public' in ruta_datos or 'dataset' in ruta_datos:
        origen = 'publicos'
        nota = ('ENTRENADO CON DATASET PUBLICO (weather_classification_data.csv), '
                'etiquetado por confort termico con los umbrales del sistema')
    else:
        origen = 'reales'
        nota = 'Entrenado con datos propios capturados con SensorOS-Lite'
    print('Leyendo', a.datos)
    filas = leer_ventanas(a.datos)
    entreno = [f for f in filas if f[0] != a.reservada]
    prueba = [f for f in filas if f[0] == a.reservada]
    print('\nVentanas por clase (entrenamiento / sesion reservada %d):' % a.reservada)
    for i, c in enumerate(CLASES):
        print('  %-8s %4d / %4d' % (c, sum(1 for f in entreno if f[2] == i),
                                    sum(1 for f in prueba if f[2] == i)))
    if not entreno or not prueba:
        sys.exit('Se necesitan ventanas en ambas sesiones.')
    Xe, ye = [f[3] for f in entreno], [f[2] for f in entreno]
    Xp, yp = [f[3] for f in prueba], [f[2] for f in prueba]
    k = len(CLASES)
    os.makedirs(a.resultados, exist_ok=True)

    resultados = []
    lr = entrenar_lr(Xe, ye, k)
    t0 = time.perf_counter()
    pred = [predecir_lr(lr, x) for x in Xp]
    t_lr = (time.perf_counter() - t0) / len(Xp) * 1e6
    M = matriz(pred, yp, k)
    ex = imprimir_matriz('regresion logistica', M)
    guardar_matriz(os.path.join(a.resultados, 'matriz_confusion_lr.csv'), M)
    ruta = os.path.join(a.salida, 'modelo_lr.py')
    exportar_lr(lr, ruta, nota)
    resultados.append(('lr', ex, k * (len(Xe[0]) + 1) + 2 * len(Xe[0]), os.path.getsize(ruta), t_lr))

    arbol = entrenar_arbol(Xe, ye, k)
    t0 = time.perf_counter()
    pred = [predecir_arbol(arbol, x) for x in Xp]
    t_ar = (time.perf_counter() - t0) / len(Xp) * 1e6
    M = matriz(pred, yp, k)
    ex = imprimir_matriz('arbol de decision', M)
    guardar_matriz(os.path.join(a.resultados, 'matriz_confusion_arbol.csv'), M)
    ruta = os.path.join(a.salida, 'modelo_arbol.py')
    exportar_arbol(arbol, ruta, nota)
    resultados.append(('arbol', ex, 4 * contar_nodos(arbol), os.path.getsize(ruta), t_ar))

    with open(os.path.join(a.resultados, 'E4_modelos_pc.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['modelo', 'exactitud_reservada', 'parametros', 'tamano_bytes', 'us_por_ventana_pc', 'datos'])
        for r in resultados:
            w.writerow([r[0], '%.4f' % r[1], r[2], r[3], '%.1f' % r[4], origen])
    # Rasgos de la sesion reservada: los usa experimentos.e4() para medir en la placa
    with open(os.path.join(a.salida, 'rasgos_reservada.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(list(rasgos.NOMBRES) + ['clase'])
        for x, t in zip(Xp, yp):
            w.writerow(['%.6f' % v for v in x] + [t])
    print('\nModelos exportados en', a.salida, '| resultados en', a.resultados)


if __name__ == '__main__':
    main()
