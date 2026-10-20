"""Rebuilds web/ (the static Netlify site) from the desktop sources. Run: python3 make_web.py"""
import shutil, os
D = os.path.dirname(os.path.abspath(__file__))
shutil.copy(os.path.join(D, 'server.py'), os.path.join(D, 'web/py/server.py'))
shutil.copy(os.path.join(D, 'demo.py'), os.path.join(D, 'web/py/demo.py'))
h = open(os.path.join(D, 'index.html'), encoding='utf-8').read()
tags = '<script src="https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js"></script><script src="pos-web.js"></script>'
assert '</head><body>' in h and tags not in h
open(os.path.join(D, 'web/index.html'), 'w', encoding='utf-8').write(h.replace('</head><body>', '</head><body>' + tags, 1))
print('web/ rebuilt')
