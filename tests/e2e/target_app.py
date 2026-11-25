"""Fake 'game' for end-to-end tests: a window, values in memory that change, and network traffic.

Writes the absolute addresses of its values to the JSON file given as argv[1].
"""
import ctypes
import json
import socket
import sys
import threading
import time
import tkinter as tk


class Player(ctypes.Structure):
    _fields_ = [("hp", ctypes.c_int32), ("pad", ctypes.c_int32), ("x", ctypes.c_float), ("y", ctypes.c_float)]


player = Player(100, 0, 0.0, 0.0)
player_ptr = ctypes.pointer(player)                 # heap pointer -> Player
ptr_holder = ctypes.c_void_p(ctypes.addressof(player))  # a pointer variable (for chain test)
counter = ctypes.c_int32(0)

info = {
    "pid": __import__("os").getpid(),
    "counter": ctypes.addressof(counter),
    "player_x": ctypes.addressof(player) + Player.x.offset,
    "ptr_holder": ctypes.addressof(ptr_holder),
    "x_offset": Player.x.offset,
    "hp": ctypes.addressof(player) + Player.hp.offset,
}
with open(sys.argv[1], "w") as f:
    json.dump(info, f)


def tick():
    while True:
        counter.value += 1
        player.x += 0.5
        player.y -= 0.25
        if counter.value % 20 == 0:
            player.hp -= 1
        time.sleep(0.05)


def net():
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.connect(("1.1.1.1", 53))  # a real interface route; payload is junk, replies don't matter
    while True:
        try:
            u.send(f"pos {player.x:.1f} {player.y:.1f}".encode())
        except OSError:
            pass
        time.sleep(0.2)


threading.Thread(target=tick, daemon=True).start()
threading.Thread(target=net, daemon=True).start()

root = tk.Tk()
root.title("E2E Target Game")
root.geometry("640x360+50+50")
label = tk.Label(root, font=("Consolas", 24))
label.pack(expand=True, fill="both")


def redraw():
    label.config(text=f"counter={counter.value}\nx={player.x:.1f} y={player.y:.1f} hp={player.hp}")
    root.after(50, redraw)


redraw()
root.after(120_000, root.destroy)  # safety
root.mainloop()
