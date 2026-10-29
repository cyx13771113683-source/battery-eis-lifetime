"""Host-only rasterization of Noto Sans SC Medium at approved 20/24/32px."""
import sys
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ps'))
from panel import LABELS
font_path=Path(sys.argv[1] if len(sys.argv)>1 else 'C:/Windows/Fonts/NotoSansSC-VF.ttf')
output_path=Path(sys.argv[2]) if len(sys.argv)>2 else ROOT/'assets/glyphs.npz'
ui_source=''.join((ROOT/'ps'/name).read_text(encoding='utf-8') for name in ('panel.py','ui_records.py'))
chars=sorted(set(LABELS+''.join(chr(i) for i in range(32,127))+''.join(c for c in ui_source if '\u4e00'<=c<='\u9fff')))
atlas={}
for size in (20,24,32):
    font=ImageFont.truetype(str(font_path),size)
    font.set_variation_by_axes([500])
    for char in chars:
        width=max(1,round(font.getlength(char)))
        im=Image.new('L',(width,size+8))
        # One baseline for the entire line; reserve room for descenders (e.g. |).
        ImageDraw.Draw(im).text((0,size-1),char,font=font,fill=255,anchor='ls')
        atlas['%d_%d'%(size,ord(char))]=np.array(im)
np.savez_compressed(output_path,**atlas)
print('GLYPHS',len(chars),'sizes=20,24,32 weight=500')
