#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,html,json,re,time,random,sys
from pathlib import Path
from urllib.parse import urljoin,urlparse
import requests
from bs4 import BeautifulSoup,Tag

GALLERY='https://allthingsagentichackathon.devpost.com/project-gallery'
TOTAL=1841
PAGES=77
PAGE_SIZE=24
LAST_SIZE=17
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36'

def norm(s): return re.sub(r'\s+',' ',html.unescape(s or '')).strip()

def project_url(href, base='https://devpost.com'):
    if not href: return None
    a=urljoin(base,href)
    p=urlparse(a)
    m=re.search(r'/software/([^/?#]+)',p.path,re.I)
    if not m: return None
    return 'https://devpost.com/software/'+m.group(1)

class Client:
    def __init__(self,delay=1.5,timeout=45,attempts=8):
        self.delay=delay; self.timeout=timeout; self.attempts=attempts; self.last=0
        self.s=requests.Session(); self.s.headers.update({'User-Agent':UA,'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8','Accept-Language':'en-US,en;q=0.9,ja;q=0.6','Cache-Control':'no-cache'})
    def get(self,url):
        last=None
        for i in range(1,self.attempts+1):
            w=self.delay-(time.monotonic()-self.last)
            if w>0: time.sleep(w+random.uniform(0,.25))
            try:
                r=self.s.get(url,timeout=self.timeout,allow_redirects=True); self.last=time.monotonic(); last=r
                if r.status_code==200 and len(r.text)>1000: return r
                if r.status_code==202:
                    time.sleep(min(90,5*(2**(i-1))+random.uniform(0,2))); continue
                if r.status_code in (408,425,429,500,502,503,504):
                    time.sleep(min(60,2**i+random.uniform(0,2))); continue
                break
            except requests.RequestException:
                self.last=time.monotonic(); time.sleep(min(60,2**i+random.uniform(0,2)))
        return last

def card_data(soup, page_url):
    items=[]; seen=set()
    for a in soup.find_all('a', href=True):
        u=project_url(a.get('href'),page_url)
        if not u or u in seen: continue
        seen.add(u)
        title=norm(a.get_text(' ',strip=True))
        container=None
        for parent in a.parents:
            if not isinstance(parent,Tag): continue
            cls=' '.join(parent.get('class',[]))
            if parent.name in ('article','li') or re.search(r'gallery|project|submission|software',cls,re.I): container=parent; break
        tagline=''
        if container:
            h=container.find(['h1','h2','h3','h4','h5','h6'])
            if isinstance(h,Tag): title=norm(h.get_text(' ',strip=True)) or title
            candidates=[]
            for node in container.find_all(['p','div','span'],limit=30):
                t=norm(node.get_text(' ',strip=True))
                if t and t!=title and 8<=len(t)<=600: candidates.append(t)
            if candidates: tagline=min(candidates,key=lambda x:(0 if 25<=len(x)<=240 else 1,abs(len(x)-110)))
        items.append({'url':u,'title_from_gallery':title,'tagline_from_gallery':tagline})
    return items

def parse_gallery(page,text):
    soup=BeautifulSoup(text,'lxml'); return card_data(soup,f'{GALLERY}?page={page}')

def meta(soup,key,value):
    n=soup.find('meta',attrs={key:value}); return norm(n.get('content','')) if isinstance(n,Tag) else ''

def sections(soup):
    aliases={'inspiration':['inspiration'],'what_it_does':['what it does','what does it do','overview','solution'],'how_built':['how we built it','how i built it','how it works','architecture'],'challenges':['challenges','challenges we ran into','challenges i ran into'],'accomplishments':['accomplishments','accomplishments that we are proud of',"accomplishments that we're proud of"],'next':["what's next",'whats next','what is next'],'built_with':['built with']}
    out={}
    for h in soup.find_all(['h2','h3']):
        label=norm(h.get_text(' ',strip=True)).lower().strip('#: ')
        key=next((k for k,vals in aliases.items() if any(label==v or label.startswith(v+' ') for v in vals)),None)
        if not key or key in out: continue
        chunks=[]; total=0
        for sib in h.next_siblings:
            if isinstance(sib,Tag) and sib.name in ('h2','h3'): break
            t=norm(sib.get_text(' ',strip=True) if isinstance(sib,Tag) else str(sib))
            if t: chunks.append(t); total+=len(t)
            if total>10000: break
        out[key]=norm(' '.join(chunks))
    return out

def parse_project(u,text,row):
    soup=BeautifulSoup(text,'lxml'); sec=sections(soup)
    h=soup.find('h1'); title=norm(h.get_text(' ',strip=True)) if isinstance(h,Tag) else ''
    if not title: title=meta(soup,'property','og:title')
    title=re.sub(r'\s*\|\s*Devpost\s*$','',title,flags=re.I).strip()
    desc=meta(soup,'property','og:description') or meta(soup,'name','description')
    main=soup.find('main')
    return {'url':u,'source_gallery_page':row.get('source_gallery_page'),'title':title or row.get('title_from_gallery',''),'tagline':row.get('tagline_from_gallery','') or desc,'description_meta':desc,'what_it_does':sec.get('what_it_does',''),'inspiration':sec.get('inspiration',''),'how_built':sec.get('how_built',''),'challenges':sec.get('challenges',''),'accomplishments':sec.get('accomplishments',''),'next':sec.get('next',''),'built_with':sec.get('built_with',''),'full_text':norm((main or soup).get_text(' ',strip=True))[:40000]}

def cmd_gallery(a):
    c=Client(a.delay,a.timeout,a.attempts); rows=[]; failures=[]
    for p in range(a.start_page,a.end_page+1):
        expected=LAST_SIZE if p==PAGES else PAGE_SIZE; best=[]; status=None
        for attempt in range(1,5):
            r=c.get(f'{GALLERY}?page={p}'); status=getattr(r,'status_code',None)
            if r is not None and status==200:
                best=parse_gallery(p,r.text)
                if len(best)>=expected: break
            time.sleep(3*attempt)
        if len(best)>expected: best=best[:expected]
        if len(best)!=expected: failures.append({'page':p,'status':status,'count':len(best),'expected':expected})
        for x in best: rows.append({'source_gallery_page':p,**x})
        print(f'gallery page {p}: {len(best)}/{expected} status={status}',flush=True)
    Path(a.out).parent.mkdir(parents=True,exist_ok=True)
    with open(a.out,'w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['source_gallery_page','url','title_from_gallery','tagline_from_gallery']); w.writeheader(); w.writerows(rows)
    Path(a.out+'.failures.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if not failures else 2

def load_index(path):
    with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def cmd_project(a):
    allrows=load_index(a.index); rows=[r for i,r in enumerate(allrows) if i%a.shard_count==a.shard_index]
    c=Client(a.delay,a.timeout,a.attempts); out=[]; failures=[]
    for n,row in enumerate(rows,1):
        u=row['url']; r=c.get(u); status=getattr(r,'status_code',None)
        if r is not None and status==200:
            rec=parse_project(u,r.text,row); rec['http_status']=200; rec['fetch_error']=''; out.append(rec)
            print(f'project {n}/{len(rows)} ok {rec["title"][:80]}',flush=True)
        else:
            rec={'url':u,'source_gallery_page':row.get('source_gallery_page'),'title':row.get('title_from_gallery',''),'tagline':row.get('tagline_from_gallery',''),'description_meta':'','what_it_does':'','inspiration':'','how_built':'','challenges':'','accomplishments':'','next':'','built_with':'','full_text':'','http_status':status or '', 'fetch_error':f'HTTP {status}' if status else 'request failed'}
            out.append(rec); failures.append(rec)
            print(f'project {n}/{len(rows)} FAILED status={status} {rec["title"][:80]}',flush=True)
    Path(a.out).parent.mkdir(parents=True,exist_ok=True)
    with open(a.out,'w',encoding='utf-8',newline='') as f:
        fields=['url','source_gallery_page','title','tagline','description_meta','what_it_does','inspiration','how_built','challenges','accomplishments','next','built_with','full_text','http_status','fetch_error']
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)
    Path(a.out+'.failures.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if not failures else 2

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    g=sub.add_parser('gallery'); g.add_argument('--start-page',type=int,required=True);g.add_argument('--end-page',type=int,required=True);g.add_argument('--out',required=True);g.add_argument('--delay',type=float,default=1.5);g.add_argument('--timeout',type=float,default=45);g.add_argument('--attempts',type=int,default=8)
    p=sub.add_parser('project');p.add_argument('--index',required=True);p.add_argument('--shard-index',type=int,required=True);p.add_argument('--shard-count',type=int,required=True);p.add_argument('--out',required=True);p.add_argument('--delay',type=float,default=1.5);p.add_argument('--timeout',type=float,default=45);p.add_argument('--attempts',type=int,default=8)
    a=ap.parse_args();sys.exit(cmd_gallery(a) if a.cmd=='gallery' else cmd_project(a))
if __name__=='__main__': main()
