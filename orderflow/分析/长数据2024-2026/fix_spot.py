import sys, traceback
sys.argv=['x','none']
exec(open('get_more.py').read().split('for x in')[0])
from concurrent.futures import ThreadPoolExecutor
def safe(c):
    for k in range(3):
        try: return spot(c)
        except Exception as e: err=repr(e)
    return c, '出错 '+err
todo=[c for c in FUT if not os.path.exists(f'spot/{c}.parquet')]
with ThreadPoolExecutor(4) as ex:
    for r in ex.map(safe,todo): print(*r,flush=True)
print('DONE')
