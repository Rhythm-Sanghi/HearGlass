import tkinter as tk
import ctypes

root = tk.Tk()
root.title('Test Overlay')
root.overrideredirect(True)
root.attributes('-topmost', True)
root.configure(bg='#010101')
root.attributes('-transparentcolor', '#010101')
root.geometry('400x200+100+100')

frame = tk.Frame(root, bg='#222222', height=30)
frame.pack(fill='x')
tk.Label(frame, text='Drag me (close after 3s)', bg='#222222', fg='white').pack()

canvas = tk.Canvas(root, bg='#010101', highlightthickness=0)
canvas.pack(fill='both', expand=True)
canvas.create_text(200, 50, text='TRANSPARENT AREA', fill='red', font=('Arial', 20))

root.after(3000, root.destroy)
root.mainloop()
