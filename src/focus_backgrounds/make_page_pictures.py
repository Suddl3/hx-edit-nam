"""The Floor look page's pictures (Pillow only): python make_page_pictures.py

thumbs/<id>.jpg      each background alone (no gear, no labels): the rows Focus View shows (y 52..340), 320 px wide
category_gear/<category>.png   one piece of gear per kind of block (../helix_model_icons/128), drawn over the chosen
                     background on the page so changing a background changes only the background
"""
from pathlib import Path
import shutil

from PIL import Image

HERE = Path(__file__).resolve().parent
Y0, ROWS = 52, 288              # Focus View's area of the 800 x 480 screen (hx-unbound-floor bg_store.py)
WIDTH = 320
GEAR = {'default': 'gtr_essexa30', 'amp': 'gtr_britplexi_brt', 'preamp': 'pre_usdeluxe_nrm', 'distortion': 'dist_teemah',
        'cab': 'cabm_4x12_gb25', 'impulse response': 'ir_1024', 'dynamics': 'dyn_lastudio', 'eq': 'eq_parametric',
        'modulation': 'mod_70schorus', 'delay': 'delay_adriatic', 'reverb': 'rev_glitz', 'filter': 'filter_mutant',
        'pitchsynth': 'pitch_wham', 'wah': 'wah_uk846', 'volumepan': 'vol_volumepedal', 'input': 'io_input',
        'output': 'io_output', 'sendreturn': 'sr_fxloop1', 'split': 'route_split', 'merge': 'route_join',
        'looper': 'looper_main'}


def main():
    thumbs = HERE / 'thumbs'
    shutil.rmtree(thumbs, ignore_errors=True)
    thumbs.mkdir()
    for source in sorted((HERE / 'backgrounds').glob('*.png')):
        picture = Image.open(source).convert('RGB').crop((0, Y0, 800, Y0 + ROWS))
        picture.resize((WIDTH, round(WIDTH * ROWS / 800)), Image.LANCZOS).save(thumbs / f'{source.stem}.jpg', quality=85,
                                                                              optimize=True)
    gear = HERE / 'category_gear'
    shutil.rmtree(gear, ignore_errors=True)
    gear.mkdir()
    for category, render in GEAR.items():
        shutil.copyfile(HERE.parent / 'helix_model_icons' / '128' / f'{render}.png', gear / f'{category}.png')


if __name__ == '__main__':
    main()
