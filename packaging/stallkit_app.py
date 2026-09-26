"""Entry point PyInstaller freezes into the downloadable app.

No arguments opens the window; any arguments run the command line tool.
"""

from stallkit.desktop import main

if __name__ == "__main__":
    main()
