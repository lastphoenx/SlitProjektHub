import shutil
import os

src = 'streamlit_app.py'
dst = os.path.join('app', 'streamlit_app.py')

if os.path.exists(src):
    shutil.copy(src, dst)
    os.remove(src)
    print(f"Moved to {dst}")
else:
    print(f"Source {src} not found")
    
print(f"Exists: {os.path.exists(dst)}")
