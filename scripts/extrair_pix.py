import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['DRY_RUN'] = 'true'
from dotenv import load_dotenv
load_dotenv()

# Tenta extrair codigo PIX de qualquer Chrome aberto via porta de debug
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
import glob, os

CACHE = os.path.join(os.environ.get('USERPROFILE','~'), r'.wdm\drivers\chromedriver\win64')
cd = None
for r,d,f in os.walk(CACHE):
    for fn in f:
        if fn=='chromedriver.exe': cd=os.path.join(r,fn)

codigos = []
for porta in range(9222, 9240):
    try:
        opts = webdriver.ChromeOptions()
        opts.add_experimental_option('debuggerAddress', f'127.0.0.1:{porta}')
        drv = webdriver.Chrome(service=Service(cd), options=opts)
        url = drv.current_url
        if 'buyticket' not in url: continue
        src = drv.page_source
        # JS scan para codigo PIX
        code = drv.execute_script("""
            var tags=['p','span','div','input'];
            for(var t of tags){
                var els=document.getElementsByTagName(t);
                for(var el of els){
                    var txt=(t==='input'?el.value:el.textContent||'').trim();
                    if(txt.startsWith('00020')&&txt.length>50){return txt;}
                }
            }return '';
        """)
        if code:
            print(f'PORTA {porta} | URL: {url[:60]}')
            print(f'PIX: {code}')
            print('---')
            codigos.append(code)
    except Exception as e:
        pass

if not codigos:
    print('Nenhum codigo PIX encontrado via debug port')
