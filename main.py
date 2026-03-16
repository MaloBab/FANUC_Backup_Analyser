import tkinter as tk
from ui.app import App
from config.settings import Settings


def main() -> None:
    settings = Settings.load()

    root = tk.Tk()
    App(root, settings)
    root.mainloop()


if __name__ == "__main__":
    main()