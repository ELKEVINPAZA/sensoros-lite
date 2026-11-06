# rasgos.py - M5: calculo de rasgos de una ventana de 128 muestras del DHT22.
# El mismo archivo se usa en la placa (P2) y en el PC (entrenamiento), asi los
# rasgos salen IDENTICOS en ambos lados.
#
# La ventana es un arreglo INTERCALADO de 3 canales por muestra:
#   temp0, hum0, conf0, temp1, hum1, conf1, ...
# y cada valor viene multiplicado por 10 (decimas). Aqui se divide entre 10 para
# trabajar en unidades reales (grados C, %, indice de confort).
#
# 15 rasgos (el enunciado pide de 8 a 24), calculados en 3 etapas para que P2
# pueda ceder la CPU (yield) entre una etapa y otra:
#   etapa 0, temperatura : media, desv, min, max, rango            -> 5
#   etapa 1, humedad     : media, desv, rango                      -> 3
#   etapa 2, confort     : media, desv, min, max, rango, y las
#                          tendencias (pendientes) de temp y conf  -> 7
import math


NOMBRES = (
    't_media', 't_desv', 't_min', 't_max', 't_rango',
    'h_media', 'h_desv', 'h_rango',
    'c_media', 'c_desv', 'c_min', 'c_max', 'c_rango', 't_tendencia', 'c_tendencia',
)
N_RASGOS = len(NOMBRES)     # 15
N_ETAPAS = 3                # el numero de etapas en que P2 parte el calculo


# off = 0 -> temperatura, 1 -> humedad, 2 -> confort (dentro de cada muestra).

def _resumen(v, off, n):
    """media, desviacion estandar, minimo, maximo y rango de un canal."""
    s = 0.0
    s2 = 0.0
    mn = 1e30
    mx = -1e30
    for k in range(n):
        x = v[3 * k + off] / 10.0      # pasamos de decimas a unidades reales
        s += x
        s2 += x * x
        if x < mn:
            mn = x
        if x > mx:
            mx = x
    media = s / n
    var = s2 / n - media * media       # varianza = E[x^2] - (E[x])^2
    if var < 0.0:                      # por errores de redondeo puede dar negativo
        var = 0.0
    return media, math.sqrt(var), mn, mx, mx - mn


def _tendencia(v, off, n):
    """Pendiente por minimos cuadrados: cuanto sube o baja el canal a lo largo de
    la ventana (unidades por muestra). Sirve para saber si el ambiente se esta
    calentando o enfriando."""
    sk = 0.0
    sx = 0.0
    skk = 0.0
    skx = 0.0
    for k in range(n):
        x = v[3 * k + off] / 10.0
        sk += k
        sx += x
        skk += k * k
        skx += k * x
    den = n * skk - sk * sk
    if den == 0.0:
        return 0.0
    return (n * skx - sk * sx) / den


def _temp(v, n):
    """Etapa 0: rasgos de la TEMPERATURA (5 rasgos)."""
    m, d, mn, mx, r = _resumen(v, 0, n)
    return m, d, mn, mx, r


def _hum(v, n):
    """Etapa 1: rasgos de la HUMEDAD (3 rasgos)."""
    m, d, mn, mx, r = _resumen(v, 1, n)
    return m, d, r


def _conf(v, n):
    """Etapa 2: rasgos del CONFORT + tendencias de temperatura y confort (7)."""
    m, d, mn, mx, r = _resumen(v, 2, n)
    return m, d, mn, mx, r, _tendencia(v, 0, n), _tendencia(v, 2, n)


_ETAPAS = (_temp, _hum, _conf)


def etapa(i, v, n):
    """Rasgos de la etapa i (tupla). P2 la llama por partes y hace yield entre
    una y otra, para que el planificador pueda expulsarlo."""
    return _ETAPAS[i](v, n)


def calcular(v, n, salida=None):
    """Todos los rasgos de una ventana (version sin yields, usada en el PC)."""
    if salida is None:
        salida = [0.0] * N_RASGOS
    j = 0
    for i in range(N_ETAPAS):
        for r in etapa(i, v, n):
            salida[j] = r
            j += 1
    return salida
