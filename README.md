# SensorOS-Lite

Mini **sistema operativo cooperativo** para **ESP32** escrito en **MicroPython**, que
muestrea un sensor **DHT22** (GPIO4) y clasifica el **confort térmico** en vivo con un
modelo de **TinyML** ejecutado en la propia placa.

El kernel basa su multitarea en **corrutinas** (generadores de Python con `yield`):
cada proceso cede la CPU voluntariamente y el planificador decide a quién devolvérsela.

> Proyecto final de **Sistemas Operativos** (7.º semestre).

---

## Hardware

| Elemento | Conexión |
|---|---|
| ESP32 | USB (datos) al PC |
| DHT22 — VCC | 3.3 V |
| DHT22 — GND | GND |
| DHT22 — DATA | **GPIO4** (con resistencia pull-up de 10 kΩ a 3.3 V) |
| LED integrado | **GPIO2** |

---

## Módulos del sistema

| Módulo | Tema | Archivos |
|---|---|---|
| **M1** | Procesos y PCB (ciclo de vida) | [pcb.py](firmware/pcb.py), [procesos.py](firmware/procesos.py) |
| **M2** | Planificación (FCFS, RR, PRIO, SJF, HRRN, AUTO) | [planificador.py](firmware/planificador.py) |
| **M3** | Sincronización (semáforos, mutex, búfer circular) | [sync.py](firmware/sync.py) |
| **M4** | Memoria (cuotas, OOM-killer, GC) | [kernel.py](firmware/kernel.py) |
| **M5** | TinyML (regresión logística y árbol de decisión) | [modelo_lr.py](firmware/modelo_lr.py), [modelo_arbol.py](firmware/modelo_arbol.py) |
| **M6** | Métricas (CSV, tabla de estados, trazas) | [kernel.py](firmware/kernel.py) |

El **panel de control** con todos los parámetros (algoritmo, quantum, umbrales de
confort, cuotas de RAM, etc.) está en [firmware/config.py](firmware/config.py).

---

## Requisitos

- **Python 3** en el PC.
- Paquetes:
  ```powershell
  python -m pip install mpremote pyserial esptool matplotlib
  ```
- Un archivo de **firmware MicroPython para ESP32** (`.bin`). No se incluye en el
  repositorio; descárgalo desde <https://micropython.org/download/ESP32_GENERIC/>.

---

## Uso rápido

En todos los comandos cambia `COM3` por tu puerto real (`python pc/placa.py puertos`).

```powershell
# 1) Detectar el puerto
python pc/placa.py puertos

# 2) (Solo la 1.ª vez) Instalar MicroPython en la placa
python pc/placa.py flashear COM3 ESP32_GENERIC-20260824-v1.29.0.bin

# 3) Prueba de hardware (H0): parpadea el LED y lee el DHT22 ~20 s
python pc/placa.py h0 COM3

# 4) Subir TODO el firmware a la placa
python pc/placa.py subir COM3

# 5) Abrir la consola del kernel (Ctrl+D reinicia | Ctrl+C detiene | Ctrl+] sale)
python pc/placa.py consola COM3
```

[pc/placa.py](pc/placa.py) también permite subir un solo archivo
(`... subir COM3 config.py`) y bajar carpetas de la placa
(`... bajar COM3 resultados`).

---

## Consola del kernel (M2)

Con el sistema corriendo, escribe **una tecla + Enter**:

| Tecla | Acción |
|---|---|
| `f` | FCFS |
| `r <ms>` | Round Robin con quantum indicado (ej. `r 10`) |
| `p` | Prioridades con envejecimiento |
| `s` | SJF (ráfaga estimada) |
| `h` | HRRN (razón de respuesta) |
| `a` | **AUTO**: prueba todos por épocas y elige el de menos plazos incumplidos |
| `e` | Imprime la tabla de estados de los procesos |
| `t` | Prende/apaga la **traza** de transiciones LISTO ↔ EJECUCIÓN ↔ BLOQUEADO |
| `q` | Detiene el kernel (vuelve al REPL) |

El sistema arranca con el algoritmo definido por `ALGORITMO` en
[config.py](firmware/config.py) (por defecto `PRIO`).

---

## Demos de sincronización y memoria (M3 / M4)

Desde el REPL de la placa (`Ctrl+C` para detener el kernel):

```python
import demos
demos.carrera()        # condición de carrera: sin mutex vs con mutex
demos.interbloqueo()   # deadlock (orden opuesto) vs resuelto (orden global)
demos.oom()            # procesos glotones: el OOM-killer los mata y el sistema sigue
```

## Experimentos (E1–E4)

Desde el REPL de la placa:

```python
import experimentos
experimentos.e1()   # Planificación: FCFS, RR(q=2), RR(q=10), PRIO (3 repeticiones)
experimentos.e2()   # Escalamiento: 1→5 copias de P2 y P3
experimentos.e3()   # Memoria: A (objetos nuevos) vs B (búferes preasignados)
experimentos.e4()   # TinyML: exactitud, tiempo, RAM y tamaño de cada modelo
```

Los resultados quedan como CSV en `resultados/` (en la placa). Bájalos con:

```powershell
python pc/placa.py bajar COM3 resultados
```

---

## Flujo de TinyML en el PC (M5)

El sistema usa un **dataset público** (`weather_classification_data.csv`), con la
**misma fórmula de confort** y los **mismos umbrales** (`20` / `26`) que la placa.

```powershell
# 1) Convertir el dataset público a los CSV que espera el entrenamiento
python pc/dataset_publico.py

# 2) Entrenar y exportar los modelos a firmware/
python pc/entrenar.py --datos datos_publicos --reservada 2

# 3) Generar tablas.md y las gráficas
python pc/analizar.py
```

Si reentrenas, vuelve a subir los modelos a la placa: `python pc/placa.py subir COM3`.

Los modelos exportados son **inferencia en MicroPython puro** (sin librerías).

---

## Resultados (resumen)

**E1 · Planificación** — PRIO logra la mayor eficiencia (~23.6 %), pero concentra la
espera en los procesos de baja prioridad (trade-off / inanición).

**E2 · Escalamiento** — la CPU útil **satura** al aumentar las copias; la RAM libre
cae de forma **lineal** (~3.5 KB por copia), lo que indica ausencia de fugas.

**E3 · Memoria** — `memB` (búferes preasignados) usa algo menos de RAM que `memA`.

**E4 · TinyML en la placa** (sesión reservada):

| Modelo | Exactitud | Tiempo/ventana | RAM | Tamaño |
|---|---|---|---|---|
| Regresión Logística | 93.5 % | 1200 µs | 2032 B | 1500 B |
| **Árbol de Decisión** | **99.6 %** | **266 µs** | **0 B** | **628 B** |

Por eso [config.py](firmware/config.py) usa `MODELO = 'arbol'`.

> Todas las cifras provienen de los CSV en [resultados/](resultados/) y de
> [resultados/tablas.md](resultados/tablas.md) (generado por [pc/analizar.py](pc/analizar.py)).

---

## Estructura del proyecto

```
sensoros-lite/
├── firmware/            # Código MicroPython que corre en la ESP32
│   ├── boot.py          # apaga Wi-Fi/BT al arrancar
│   ├── main.py          # arranque del kernel y creación de los 6 procesos
│   ├── kernel.py        # núcleo: ciclo, estados, métricas, consola
│   ├── planificador.py  # FCFS, RR, PRIO, SJF, HRRN, AUTO
│   ├── procesos.py      # los 6 procesos (P1..P6)
│   ├── pcb.py           # PCB y ciclo de vida
│   ├── sync.py          # semáforos, mutex, búfer circular
│   ├── sensor.py        # DHT22 + fórmula de confort
│   ├── rasgos.py        # rasgos de la ventana (mismos en placa y PC)
│   ├── demos.py         # demos de M3/M4
│   ├── experimentos.py  # E1..E4
│   ├── modelo_lr.py     # modelo entrenado (exportado)
│   ├── modelo_arbol.py  # modelo entrenado (exportado)
│   └── config.py        # PANEL DE CONTROL (todos los parámetros)
├── h0/                  # Prueba de hardware (LED + DHT22)
├── pc/                  # Scripts del PC (placa, dataset, entrenamiento, análisis)
├── datos_publicos/      # CSVs de entrenamiento generados
├── resultados/          # CSVs, tablas.md y gráficas
└── weather_classification_data.csv
```

---

## Notas

- El sistema trabaja con **datos reales** del DHT22; no se usan datos simulados.
- El DHT22 solo entrega un valor nuevo cada **2 s**; entre lecturas se repite el
  último valor (comportamiento tenido en cuenta en el entrenamiento).
- El binario del firmware MicroPython (`.bin`) y los informes en PDF están
  excluidos por [.gitignore](.gitignore).
