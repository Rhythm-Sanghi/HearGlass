import tkinter as tk

root = tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost', True)
root.configure(bg='#010101')
root.attributes('-transparentcolor', '#010101')
root.geometry('400x200+100+100')

frame = tk.Frame(root, bg='#1a1a2e', height=28)
frame.pack(fill='x')
label = tk.Label(frame, text='HANDLE', bg='#1a1a2e', fg='white')
label.pack()

def on_enter(e):
    frame.configure(height=28)
    label.pack()

def on_leave(e):
    # Hide after a short delay or immediately
    label.pack_forget()
    frame.configure(height=2)

frame.bind('<Enter>', on_enter)
frame.bind('<Leave>', on_leave)

root.after(3000, root.destroy)
root.mainloop()
