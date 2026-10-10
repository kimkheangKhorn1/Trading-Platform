"""Extract literal Pine input metadata without executing Pine. Source stays authoritative."""
import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Indicator SMC & ICT & Indicators + Scalping Master for TradingView.pine'
COLORS = {'white':'#FFFFFF','red':'#FF5252','green':'#4CAF50','blue':'#2196F3','lime':'#00E676','gray':'#787B86','orange':'#FF9800','purple':'#9C27B0'}

def split(text):
    out, start, depth, quote, escape = [], 0, 0, None, False
    for i,c in enumerate(text):
        if quote:
            if escape: escape=False
            elif c=='\\': escape=True
            elif c==quote: quote=None
        elif c in "\"'": quote=c
        elif c in '([': depth+=1
        elif c in ')]': depth-=1
        elif c==',' and depth==0: out.append(text[start:i].strip()); start=i+1
    out.append(text[start:].strip())
    return out

def value(s, constants):
    s=s.strip()
    if s in constants: return constants[s]
    if s=='true': return True
    if s=='false': return False
    if s.startswith('alert.freq_'): return s.removeprefix('alert.freq_')
    if s.startswith('color.new'):
        col,t = [value(x,constants) for x in split(s[s.index('(')+1:-1])]
        return col[:7]+f'{round(255*(1-t/100)):02X}'
    if s.startswith('color.rgb'):
        vals=[value(x,constants) for x in split(s[s.index('(')+1:-1])]
        return '#'+''.join(f'{int(x):02X}' for x in vals[:3])+f'{round(255*(1-(vals[3] if len(vals)>3 else 0)/100)):02X}'
    if s.startswith('color.'): return COLORS[s[6:]]+'FF'
    if s.startswith('#'): return s.upper()+('FF' if len(s)==7 else '')
    if s.startswith('['): return [value(x,constants) for x in split(s[1:-1])]
    node=ast.parse('('+s+')',mode='eval').body
    def literal(n):
        if isinstance(n,ast.BinOp) and isinstance(n.op,ast.Add): return literal(n.left)+literal(n.right)
        if isinstance(n,ast.Name): return constants[n.id]
        return ast.literal_eval(n)
    return literal(node)

def extract():
    source=SOURCE.read_text(); constants=dict(re.findall(r'^(\w+) = position\.(\w+)',source,re.M))
    for line in source.splitlines():
        m=re.match(r'\s*(?:string\s+)?(\w+)\s*=\s*([\'"].*)$',line)
        if m:
            try: constants[m[1]]=value(m[2],constants)
            except (ValueError,SyntaxError): pass
    out=[]
    for m in re.finditer(r'^\s*(\w+)\s*=\s*input(?:\.(\w+))?\s*\(',source,re.M):
        start=m.end(); depth=1; quote=None; escape=False; i=start
        while depth:
            c=source[i]; i+=1
            if quote:
                if escape: escape=False
                elif c=='\\': escape=True
                elif c==quote: quote=None
            elif c in "\"'": quote=c
            elif c=='(': depth+=1
            elif c==')': depth-=1
        args=split(source[start:i-1]); pos=[]; kw={}
        for a in args:
            nm=re.match(r'^(\w+)\s*=\s*(.*)$',a,re.S)
            if nm: kw[nm[1]]=nm[2]
            else: pos.append(a)
        default=value(kw.get('defval',pos[0] if pos else ''),constants)
        typ=m[2] or ('bool' if isinstance(default,bool) else 'color' if isinstance(default,str) and default.startswith('#') else 'string' if isinstance(default,str) else 'float' if isinstance(default,float) else 'int')
        item={'id':m[1],'type':typ,'title':value(kw['title'],constants) if 'title' in kw else value(pos[1],constants) if len(pos)>1 else m[1], 'default':default}
        for key in ['minval','maxval','step','options','group','inline','tooltip']:
            if key in kw: item[key]=value(kw[key],constants)
        out.append(item)
    target=ROOT/'backend/app/scripts/smc_master/inputs.json'
    target.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n')
    print(f'{len(out)} inputs; SHA256 {hashlib.sha256(SOURCE.read_bytes()).hexdigest()}')

if __name__=='__main__': extract()
