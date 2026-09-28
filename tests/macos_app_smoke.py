"""Open and close only the Mac launcher's own window, without opening a browser."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from linkexpand.macos_app import run,tk
from linkexpand.server import App,Server

def main():
    if sys.platform!='darwin':raise RuntimeError('Run this integration on macOS.')
    original=tk.Tk
    def window():
        root=original()
        def close():
            assert root.winfo_width()>300 and root.winfo_height()>200
            root.tk.call(root.protocol('WM_DELETE_WINDOW'))
        root.after(1200,close)
        return root
    app=App();server=Server(('127.0.0.1',0),app)
    try:
        with patch('linkexpand.macos_app.tk.Tk',side_effect=window):run(server,f'http://127.0.0.1:{server.server_port}')
    finally:app.close();server.server_close()
    print('PASS: native macOS launcher window, server startup and clean quit; browser remained closed.')

if __name__=='__main__':main()
