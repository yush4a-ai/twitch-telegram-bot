"""Read-only source inventory; no bot, database, tokens or external requests."""
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
paths=sorted((ROOT/'bot/handlers').glob('*.py'))+sorted((ROOT/'bot').glob('*.py'))
paths+=sorted((ROOT/'bot/mini_app_ui').glob('*.js'))
records=[];files={}
def browser_literals(source):
    """Skip comments instead of treating apostrophes in prose as JS quotes."""
    i=0
    while i<len(source):
        if source.startswith('//',i):
            end=source.find('\n',i);i=len(source) if end<0 else end+1;continue
        if source.startswith('/*',i):
            end=source.find('*/',i+2);i=len(source) if end<0 else end+2;continue
        if source[i] not in "'\"`":i+=1;continue
        start=i;delimiter=source[i];i+=1
        while i<len(source):
            if source[i]=='\\':i+=2;continue
            if source[i]==delimiter:break
            i+=1
        yield start,source[start+1:i]
        i+=1
for path in paths:
    source=path.read_text(encoding='utf-8-sig');name=path.relative_to(ROOT).as_posix()
    files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    if path.suffix=='.py':
        tree=ast.parse(source);parents={child:parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node,ast.JoinedStr):
                text=''.join(part.value if isinstance(part,ast.Constant) and isinstance(part.value,str) else '{value}' for part in node.values)
            elif isinstance(node,ast.Constant) and isinstance(node.value,str) and not isinstance(parents.get(node),ast.JoinedStr):
                text=node.value
            else:continue
            if not re.search('[А-Яа-яЁё]',text):continue
            owner=node
            while owner in parents and not isinstance(owner,(ast.FunctionDef,ast.AsyncFunctionDef)):owner=parents[owner]
            parent=parents.get(node)
            if isinstance(parent,ast.Expr) and getattr(parents.get(parent),'body',[None])[0] is parent:continue
            caller=node
            while caller in parents and not isinstance(caller,ast.Call):caller=parents[caller]
            if isinstance(caller,ast.Call) and isinstance(caller.func,ast.Attribute) and isinstance(caller.func.value,ast.Name) and caller.func.value.id in {'logger','logging'}:continue
            function=getattr(owner,'name','module')
            records.append(dict(file=name,line=node.lineno,owner=function,chars=len(text),paragraphs=text.count('\n\n')+1,
                                html_heading='<b>' in text,quote='<blockquote' in text,preview=text[:160]))
    else:
        for start,text in browser_literals(source):
            if not re.search('[А-Яа-яЁё]',text):continue
            records.append(dict(file=name,line=source[:start].count('\n')+1,owner='browser literal',chars=len(text),preview=text[:160]))
result={'kind':'source inventory, not an automatic UI quality verdict','files':files,'records':records}
(OUT/'INVENTORY.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'files':len(files),'russian_literals':len(records),'long_literals':sum(r['chars']>160 for r in records)},ensure_ascii=False))
