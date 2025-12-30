import asyncio
import threading
from queue import Queue
import tkinter as tk
from tkinter import ttk
import tempfile
import os

from bleak import BleakScanner
from pybricksdev.connections.pybricks import PybricksHubBLE

# ---------------------------------------------------------
# 1) CREAR PROGRAMA PYBRICKS
# ---------------------------------------------------------
def create_program(drive_cmd: str) -> str:
    drive_commands = {
        'izquierda': "motorC.run_angle(-250, -60)",
        'derecha': "motorC.run_angle(250, -45)",
        'empujar': "motorF.run(500)",
        'tirar': "motorF.run(-500)",
        'leer_color': "",
        'inicio': "motorC.run_target(250, 0)"
    }

    drive_code = drive_commands.get(drive_cmd, "")

    program = f"""
from pybricks.hubs import PrimeHub
from pybricks.pupdevices import Motor, ColorSensor
from pybricks.parameters import Port
from pybricks.tools import wait

hub = PrimeHub()

motorC = Motor(Port.C)
motorF = Motor(Port.F)
sensor = ColorSensor(Port.E)

{drive_code}

color = sensor.color()
print("COLOR:", color)

wait(300)
motorC.stop()
motorF.stop()
"""
    return program

# ---------------------------------------------------------
# 2) EJECUTAR PROGRAMA EN EL HUB
# ---------------------------------------------------------
async def execute_command(hub, drive_cmd, logger):
    program = create_program(drive_cmd)
    path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as f:
            f.write(program.encode("utf-8"))
            path = f.name

        logger("📤 Enviando comando al robot...")
        output = await hub.run(path)

        if output:
            for line in output.splitlines():
                if "COLOR:" in line:
                    logger(f"Color detectado → {line.replace('COLOR:', '').strip()}")

    except Exception as e:
        logger(f"❌ Error: {e}")

    finally:
        if path and os.path.exists(path):
            os.remove(path)

# ---------------------------------------------------------
# 3) BLE WORKER
# ---------------------------------------------------------
class BLEWorker:
    def __init__(self, log_queue: Queue):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._thread_main, daemon=True)
        self.queue = asyncio.Queue()
        self.hub = None
        self.device = None
        self.running = False
        self.log_queue = log_queue

    def log(self, msg):
        self.log_queue.put(msg)

    def _thread_main(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def connect(self):
        try:
            self.hub = PybricksHubBLE(self.device)
            await self.hub.connect()
            self.running = True
            self.log("🔗 Conectado al hub.")

            while self.running:
                cmd = await self.queue.get()
                await execute_command(self.hub, cmd, self.log)

        except Exception as e:
            self.log(f"❌ Error BLE: {e}")

    async def disconnect(self):
        self.running = False
        if self.hub:
            await self.hub.disconnect()
            self.hub = None
            self.log("🔌 Hub desconectado.")

    def start(self):
        if not self.thread.is_alive():
            self.thread.start()

    def set_device(self, device):
        self.device = device
        self.loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self.connect())
        )

    def stop(self):
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self.disconnect())
            )

    def send_command(self, cmd):
        if self.running:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, cmd)

# ---------------------------------------------------------
# 4) VENTANA SELECCIÓN DE HUB (SIN CTK)
# ---------------------------------------------------------
class DeviceSelectWindow(tk.Toplevel):
    def __init__(self, parent, on_select_callback):
        super().__init__(parent)
        self.on_select_callback = on_select_callback

        self.title("Buscador de Hubs")
        self.geometry("400x450")
        self.attributes("-topmost", True)
        self.grab_set()

        ttk.Label(
            self,
            text="Buscando dispositivos...",
            font=("Arial", 14, "bold")
        ).pack(pady=10)

        self.listbox = tk.Listbox(self)
        self.listbox.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Button(self, text="Escanear", command=self.start_scan).pack(pady=5)
        ttk.Button(self, text="Conectar", command=self.select_device).pack(pady=5)

        self.devices = []
        self.start_scan()

    def start_scan(self):
        self.listbox.delete(0, tk.END)
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        loop = asyncio.new_event_loop()
        devices = loop.run_until_complete(BleakScanner.discover(timeout=3.0))
        loop.close()
        self.after(0, lambda: self._update_list(devices))

    def _update_list(self, devices):
        self.devices = [d for d in devices if d.name]
        for dev in self.devices:
            self.listbox.insert(tk.END, dev.name)

    def select_device(self):
        sel = self.listbox.curselection()
        if sel:
            self.on_select_callback(self.devices[sel[0]])
            self.destroy()

# ---------------------------------------------------------
# 5) INTERFAZ GRÁFICA (ALINEAR BOTONES EN LA MISMA FILA)
# ---------------------------------------------------------
def main_gui():
    root = tk.Tk()
    root.title("Control del Robot Separador de Bloques")
    root.geometry("480x580")

    log_queue = Queue()
    worker = BLEWorker(log_queue)
    worker.start()

    def enviar_con_mensaje(cmd, mensaje):
        worker.log(mensaje)
        worker.send_command(cmd)

    color_var = tk.StringVar(value="Color: ---")
    ttk.Label(root, textvariable=color_var,
              font=("Arial", 16, "bold")).pack(pady=10)

    # Colocamos los botones "Buscar Hub" y "Desconectar" arriba
    frame_buttons_top = ttk.Frame(root)
    frame_buttons_top.pack(fill="both", padx=10, pady=10)

    # Botones con las etiquetas "Buscar Hub" y "Desconectar"
    ttk.Button(frame_buttons_top, text="Conectar Hub", command=lambda: DeviceSelectWindow(root, worker.set_device)).grid(row=0, column=0, padx=5)
    ttk.Button(frame_buttons_top, text="Desconectar Hub", command=lambda: worker.stop()).grid(row=0, column=1, padx=5)

    # Ahora el área de logs
    frame_log = ttk.LabelFrame(root, text="Estado")
    frame_log.pack(fill="both", padx=10, pady=10)

    txt = tk.Text(frame_log, height=10, state="disabled")
    txt.pack(fill="both", padx=5, pady=5)

    def update_logs():
        while not log_queue.empty():
            msg = log_queue.get()
            if "Color detectado" in msg:
                color_var.set("Color: " + msg.split("→")[1].strip())
            txt.config(state="normal")
            txt.insert("end", msg + "\n")
            txt.see("end")
            txt.config(state="disabled")
        root.after(100, update_logs)

    update_logs()

    # **Nuevo LabelFrame para los botones de "Empujar" y "Tirar bloque"**
    frame_right = ttk.LabelFrame(root, text="Manipular Bloques")
    frame_right.pack(fill="both", padx=10, pady=10)

    # Uso de grid para alinear los botones de "Empujar" y "Tirar bloque" en la misma fila
    ttk.Button(frame_right, text="Empujar Bloque", command=lambda: enviar_con_mensaje("empujar", "🧱 Empujar el bloque")).grid(row=0, column=0, padx=5)
    ttk.Button(frame_right, text="Tirar Bloque", command=lambda: enviar_con_mensaje("tirar", "🧱 Tirar el bloque")).grid(row=0, column=1, padx=5)

    # Los botones de movimiento
    frame_left = ttk.LabelFrame(root, text="Movimiento Izquierda/Derecha")
    frame_left.pack(fill="both", padx=10, pady=10)

    ttk.Button(frame_left, text="⬅ Izquierda", command=lambda: enviar_con_mensaje("izquierda", "⬅ Botón presionado: Izquierda")).grid(row=0, column=0, padx=5)
    ttk.Button(frame_left, text="➡ Derecha", command=lambda: enviar_con_mensaje("derecha", "➡ Botón presionado: Derecha")).grid(row=0, column=2, padx=5)
    ttk.Button(frame_left, text="🔄 Volver al inicio", command=lambda: enviar_con_mensaje("inicio", "🔄 Botón presionado: Volver al inicio")).grid(row=0, column=1, padx=5)

    root.protocol("WM_DELETE_WINDOW", lambda: (worker.stop(), root.destroy()))
    root.mainloop()

# ---------------------------------------------------------
# EJECUCIÓN
# --------------------------------------------------------
if __name__ == "__main__":
    main_gui()