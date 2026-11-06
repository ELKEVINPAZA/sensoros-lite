"""placa.py - Atajos para manejar la ESP32 desde la terminal.

Uso (cambiar COM3 por su puerto):
    python pc/placa.py puertos                  lista los puertos donde hay una placa
    python pc/placa.py flashear COM3 firmware.bin   borra la flash e instala MicroPython
    python pc/placa.py h0 COM3                  corre h0/main.py (LED + WHO_AM_I) sin guardarlo
    python pc/placa.py h0-guardar COM3          guarda h0/main.py como main.py en la placa
    python pc/placa.py subir COM3               sube todo firmware/ a la placa
    python pc/placa.py subir COM3 config.py     sube solo ese archivo de firmware/
    python pc/placa.py consola COM3             consola interactiva del kernel (salir con Ctrl+])
    python pc/placa.py bajar COM3 datos         baja la carpeta datos/ de la placa
    python pc/placa.py bajar COM3 resultados    baja la carpeta resultados/ de la placa
"""
import glob
import os
import subprocess
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRMWARE = os.path.join(RAIZ, 'firmware')


def correr(args, intentos=1):
    print('>', ' '.join(args))
    for i in range(intentos):
        try:
            r = subprocess.call(args)
        except KeyboardInterrupt:
            return
        if r == 0:
            return
        if i + 1 < intentos:
            # suele pasar cuando el kernel estaba corriendo: mpremote lo detiene y se reintenta
            print('Reintentando (%d/%d)...' % (i + 2, intentos))
            time.sleep(1.5)
    sys.exit('Fallo (codigo %d). Cierren cualquier otra consola abierta en ese puerto (Ctrl+] en la otra '
             'terminal) y vuelvan a intentar.' % r)


def mpremote(puerto, *args):
    intentos = 1 if args[0] == 'repl' else 3
    correr([sys.executable, '-m', 'mpremote', 'connect', puerto] + list(args), intentos)


def terminal(puerto):
    """Consola interactiva con el kernel (equivalente al monitor serie).

    El kernel de SensorOS-Lite lee el teclado directamente del puerto serie
    (sys.stdin), asi que aqui NO usamos "mpremote repl": ese comando reinicia la
    placa y, como main.py deja el kernel corriendo, se queda colgado esperando el
    REPL que nunca aparece. En su lugar abrimos el puerto tal cual: lo que la
    placa imprime lo mostramos, y lo que se teclea lo enviamos a la placa.
    """
    import msvcrt                    # teclado crudo en Windows (getch, sin Enter)
    import serial                    # pyserial: hablar con el puerto serie
    try:
        s = serial.Serial(puerto, 115200, timeout=0)   # abrimos a 115200 baudios
    except Exception as e:
        sys.exit('No pude abrir %s: %s' % (puerto, e))
    print('Consola de SensorOS-Lite en %s.' % puerto)
    print('Ctrl+D reinicia (arranca el kernel) | Ctrl+C detiene el kernel | Ctrl+] sale')
    s.write(b'\r\n')                 # un Enter para que la placa repinte el prompt
    try:
        while True:
            n = s.in_waiting         # bytes que la placa ya envio y no leimos
            if n:
                sys.stdout.write(s.read(n).decode('utf-8', 'replace'))   # mostrarlos
                sys.stdout.flush()
            if msvcrt.kbhit():       # si se pulso una tecla en la PC
                c = msvcrt.getch()   # la leemos SIN esperar a Enter
                if c == b'\x1d':     # Ctrl+] cierra la consola
                    break
                if c == b'\x03':     # Ctrl+C -> orden de interrupcion a la placa
                    s.write(b'\x03')
                elif c in (b'\r', b'\n'):   # Enter -> mandamos CRLF
                    s.write(b'\r\n')
                    sys.stdout.write('\r\n')
                else:                # cualquier otra tecla va tal cual
                    s.write(c)
                    sys.stdout.write(c.decode('utf-8', 'replace'))
                sys.stdout.flush()
            time.sleep(0.005)        # pausa corta para no gastar toda la CPU
    except KeyboardInterrupt:        # por si aprietan Ctrl+C en esta ventana
        pass
    finally:
        s.close()                    # cerramos el puerto al salir


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    orden = sys.argv[1]
    if orden == 'puertos':
        correr([sys.executable, '-m', 'mpremote', 'connect', 'list'])
        return
    if len(sys.argv) < 3:
        sys.exit('Falta el puerto, por ejemplo: python pc/placa.py %s COM3' % orden)
    puerto = sys.argv[2]
    extra = sys.argv[3:]
    if orden == 'flashear':
        if not extra:
            sys.exit('Falta el archivo .bin del firmware')
        correr([sys.executable, '-m', 'esptool', '--port', puerto, 'erase_flash'])
        correr([sys.executable, '-m', 'esptool', '--port', puerto, '--baud', '460800',
                'write_flash', '0x1000', extra[0]])
    elif orden == 'h0':
        mpremote(puerto, 'run', os.path.join(RAIZ, 'h0', 'main.py'))
    elif orden == 'h0-guardar':
        mpremote(puerto, 'cp', os.path.join(RAIZ, 'h0', 'main.py'), ':main.py')
    elif orden == 'subir':
        if extra:
            archivos = [os.path.join(FIRMWARE, a) for a in extra]
        else:
            archivos = sorted(glob.glob(os.path.join(FIRMWARE, '*.py')) +
                              glob.glob(os.path.join(FIRMWARE, '*.csv')))
        mpremote(puerto, 'cp', *archivos, ':')
        print('Listo. Abran la consola y presionen Ctrl+D para reiniciar SensorOS-Lite.')
    elif orden == 'consola':
        terminal(puerto)
    elif orden == 'bajar':
        carpeta = extra[0] if extra else 'datos'
        os.chdir(RAIZ)
        mpremote(puerto, 'cp', '-r', ':' + carpeta, '.')
        print('Copiado en', os.path.join(RAIZ, carpeta))
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
