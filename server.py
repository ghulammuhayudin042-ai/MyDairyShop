import sqlite3,json,os,csv,io,sys,time,hashlib,secrets,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
DB=os.environ.get('POS_DB','dairy.db');D=os.path.dirname(os.path.abspath(__file__))
R=lambda x:round(float(x or 0)+1e-9,2)
SCHEMA='''
create table if not exists products(id integer primary key,name text not null,sku text unique,barcode text unique,category text,unit text not null,decimals int default 2,cost real default 0,price real not null,min_stock real default 0,active int default 1);
create table if not exists parties(id integer primary key,kind text,name text not null,phone text,address text,active int default 1);
create table if not exists docs(id integer primary key,kind text,ref text unique,party_id int,ts text default(datetime('now','localtime')),total real,discount real default 0,tax real default 0,paid real default 0,method text,status text default 'done',note text,src int,key text unique);
create table if not exists items(id integer primary key,doc_id int,product_id int,qty real,rate real,cost real,amount real);
create table if not exists stock(id integer primary key,product_id int,type text,ref text,qty real,ts text default(datetime('now','localtime')),note text);
create table if not exists ledger(id integer primary key,party_id int,type text,ref text,amount real,ts text default(datetime('now','localtime')),note text);
create table if not exists cash(id integer primary key,type text,ref text,amount real,ts text default(datetime('now','localtime')),note text,category text);
create table if not exists audit(id integer primary key,ts text default(datetime('now','localtime')),action text,detail text);
create table if not exists days(id integer primary key,opened text default(datetime('now','localtime')),opening real,mark int,closed text,expected real,counted real,diff real,note text);
create table if not exists settings(k text primary key,v text);
insert or ignore into settings values('shop','My Dairy Shop'),('phone',''),('currency','PKR'),('footer','Thank you!'),('prefix','INV'),('pin','1234'),('allow_negative','0'),('max_disc_pct','10'),('tax_pct','0'),('max_credit','0');
create table if not exists conv(base text,alt text,factor real,primary key(base,alt));insert or ignore into conv values('liter','ml',0.001),('kg','g',0.001),('liter','half-liter',0.5);
create table if not exists users(id integer primary key,username text unique,salt text,hash text,role text,active int default 1);
'''
class Err(Exception):pass
CUR=threading.local();SESS={}
ADMIN={'GET users','POST users','POST conv','POST backup'}
def hp(pw,salt):return hashlib.pbkdf2_hmac('sha256',pw.encode(),bytes.fromhex(salt),120000).hex()
ROUTES={}
def route(name,tx=False):
    def w(f):ROUTES[name]=(f,tx);return f
    return w
def Q(c,s,*a):return [dict(r) for r in c.execute(s,a)]
def one(c,s,*a):
    r=c.execute(s,a).fetchone();return r[0] if r and r[0] is not None else 0
def S(c,k):return one(c,'select v from settings where k=?',k)
def stk(c,p):return one(c,'select sum(qty) from stock where product_id=?',p)
def mv(c,p,t,ref,q,n=''):c.execute('insert into stock(product_id,type,ref,qty,note)values(?,?,?,?,?)',(p,t,ref,q,n))
def led(c,p,t,ref,a,n=''):
    if p and abs(a)>0.004:c.execute('insert into ledger(party_id,type,ref,amount,note)values(?,?,?,?,?)',(p,t,ref,R(a),n))
def csh(c,t,ref,a,n='',cat=''):
    if abs(a)>0.004:c.execute('insert into cash(type,ref,amount,note,category)values(?,?,?,?,?)',(t,ref,R(a),n,cat))
def aud(c,a,d):c.execute('insert into audit(action,detail)values(?,?)',(a,'[%s] %s'%(getattr(CUR,'user','system'),d)))
def pin(c,d):
    if getattr(CUR,'role','')=='admin' or str(d.get('pin',''))==S(c,'pin'):return
    raise Err('Admin authorization required: ask an admin or enter the supervisor PIN')
def num(v,name,pos=True):
    try:v=float(v)
    except:raise Err(name+' must be a number')
    if pos and v<=0:raise Err(name+' must be greater than zero')
    return v
def nref(c,kind,pre):return '%s-%06d'%(pre,one(c,'select count(*) from docs where kind=?',kind)+1)
@route('GET settings')
def _(c,q,d):return {r['k']:r['v'] for r in Q(c,'select * from settings') if r['k']!='pin'}
@route('POST settings',True)
def _(c,q,d):
    pin(c,d)
    for k,v in d['values'].items():c.execute('insert or replace into settings values(?,?)',(k,str(v)))
    aud(c,'settings',json.dumps(d['values']));return {}
@route('GET products')
def _(c,q,d):
    s='%'+q.get('q','')+'%'
    return Q(c,'select p.*,coalesce((select sum(qty) from stock where product_id=p.id),0) stock from products p where (name like ? or sku like ? or barcode like ?)'+('' if q.get('all') else ' and active=1')+' and (length(?)=0 or category=?) order by name',s,s,s,q.get('cat',''),q.get('cat',''))
@route('POST products',True)
def _(c,q,d):
    n=d.get('name','').strip();
    if not n or not d.get('unit'):raise Err('Name and unit are required')
    price=num(d.get('price'),'Sale price',False);cost=num(d.get('cost',0),'Cost',False)
    if price<0 or cost<0:raise Err('Prices cannot be negative')
    v=(n,d.get('sku') or None,d.get('barcode') or None,d.get('category'),d['unit'],int(d.get('decimals',2)),cost,price,num(d.get('min_stock',0),'Min stock',False),int(d.get('active',1)))
    try:
        if d.get('id'):
            old=Q(c,'select price,cost from products where id=?',d['id'])[0]
            if old['price']!=price or old['cost']!=cost:aud(c,'price_change','#%s %s->%s cost %s->%s'%(d['id'],old['price'],price,old['cost'],cost))
            c.execute('update products set name=?,sku=?,barcode=?,category=?,unit=?,decimals=?,cost=?,price=?,min_stock=?,active=? where id=?',v+(d['id'],));pid=d['id']
        else:
            pid=c.execute('insert into products(name,sku,barcode,category,unit,decimals,cost,price,min_stock,active)values(?,?,?,?,?,?,?,?,?,?)',v).lastrowid
            o=num(d.get('opening',0),'Opening stock',False)
            if o:mv(c,pid,'opening','OPEN-%d'%pid,o)
    except sqlite3.IntegrityError:raise Err('Duplicate SKU or barcode')
    c.execute('update products set brand=?,notes=? where id=?',(d.get('brand'),d.get('notes'),pid))
    return {'id':pid}
@route('GET categories')
def _(c,q,d):return Q(c,'select distinct category from products where category is not null and length(category)>0 order by 1')
@route('GET costhistory')
def _(c,q,d):return Q(c,'select d.ts,d.ref,p.name supplier,i.qty,i.rate from items i join docs d on d.id=i.doc_id left join parties p on p.id=d.party_id where d.kind="purchase" and d.status="done" and i.product_id=? order by d.id desc limit 50',q['product_id'])
@route('GET parties')
def _(c,q,d):
    s='%'+q.get('q','')+'%'
    return Q(c,'select p.*,coalesce((select sum(amount) from ledger where party_id=p.id),0) balance from parties p where kind=? and (name like ? or phone like ?) order by name',q['kind'],s,s)
@route('POST parties',True)
def _(c,q,d):
    if not d.get('name','').strip():raise Err('Name is required')
    if d.get('id'):c.execute('update parties set name=?,phone=?,address=?,active=?,notes=? where id=?',(d['name'],d.get('phone'),d.get('address'),int(d.get('active',1)),d.get('notes'),d['id']));return {'id':d['id']}
    i=c.execute('insert into parties(kind,name,phone,address,notes)values(?,?,?,?,?)',(d['kind'],d['name'],d.get('phone'),d.get('address'),d.get('notes'))).lastrowid
    ob=num(d.get('opening',0),'Opening balance',False)
    if ob:led(c,i,'opening','OPEN-P%d'%i,ob,'Opening balance')
    return {'id':i}
def lines(c,d,kind):
    out=[]
    for i in d.get('items') or []:
        p=Q(c,'select * from products where id=?',i['product_id'])
        if not p or not p[0]['active']:raise Err('Product unavailable')
        qty=num(i['qty'],'Quantity');u=i.get('unit')
        if u and u!=p[0]['unit']:
            f=one(c,'select factor from conv where base=? and alt=?',p[0]['unit'],u)
            if not f:raise Err('No conversion configured from %s to %s'%(u,p[0]['unit']))
            qty*=f
        qty=round(qty,p[0]['decimals'])
        if qty<=0:raise Err('Quantity too small for unit precision')
        out.append((p[0],qty))
    if not out:raise Err('No items added')
    return out
@route('POST sale',True)
def sale(c,q,d):
    if d.get('key'):
        x=Q(c,'select id,ref from docs where key=?',d['key'])
        if x:return x[0]
    cust=int(d.get('party_id') or 0);rows=[];sub=0
    for p,qty in lines(c,d,'sale'):
        if stk(c,p['id'])<qty and S(c,'allow_negative')!='1':raise Err('Insufficient stock for %s (have %s)'%(p['name'],stk(c,p['id'])))
        it=[i for i in d['items'] if i['product_id']==p['id']][0];rate=num(it.get('rate',p['price']),'Rate',False);a=R(qty*rate);sub+=a;rows.append((p,qty,rate,a))
    disc=R(d.get('discount',0));sub=R(sub)
    if disc<0 or disc>sub:raise Err('Invalid discount')
    if disc>0:
        if not d.get('reason'):raise Err('Discount reason required')
        if disc>sub*float(S(c,'max_disc_pct'))/100:pin(c,d)
    tax=R((sub-disc)*float(S(c,'tax_pct'))/100);total=R(sub-disc+tax);pm=d.get('payments');paid=R(sum(float(x['amount']) for x in pm)) if pm else R(d.get('paid',total))
    if pm:d['method']='split' if len({x['method'] for x in pm})>1 else pm[0]['method']
    if paid<0 or paid>total:raise Err('Paid amount must be between 0 and total')
    if total-paid>0.004 and not cust:raise Err('Select a customer for credit/partial sales')
    mc=float(S(c,'max_credit'))
    if cust and total-paid>0.004 and mc>0 and one(c,'select sum(amount) from ledger where party_id=?',cust)+total-paid>mc:pin(c,d)
    ref=nref(c,'sale',S(c,'prefix'))
    i=c.execute('insert into docs(kind,ref,party_id,total,discount,paid,method,note,key,tax)values(?,?,?,?,?,?,?,?,?,?)',('sale',ref,cust or None,total,disc,paid,d.get('method','cash'),(d.get('reason') or '')+' '+(d.get('note') or ''),d.get('key'),tax)).lastrowid
    for p,qty,rate,a in rows:
        c.execute('insert into items(doc_id,product_id,qty,rate,cost,amount)values(?,?,?,?,?,?)',(i,p['id'],qty,rate,p['cost'],a));mv(c,p['id'],'sale',ref,-qty)
    led(c,cust,'sale',ref,total);led(c,cust,'payment',ref,-paid)
    csh(c,'sale',ref,R(sum(float(x['amount']) for x in pm if x['method']=='cash')) if pm else (paid if d.get('method','cash')=='cash' else 0))
    return {'id':i,'ref':ref}
@route('POST purchase',True)
def _(c,q,d):
    if d.get('key') and Q(c,'select 1 from docs where key=?',d['key']):raise Err('Purchase already posted')
    sup=int(d.get('party_id') or 0)
    if not sup:raise Err('Select a supplier')
    rows=[];tot=0
    for p,qty in lines(c,d,'purchase'):
        it=[i for i in d['items'] if i['product_id']==p['id']][0];cost=num(it.get('cost'),'Unit cost',False);a=R(qty*cost);tot+=a;rows.append((p,qty,cost,a))
    disc=R(d.get('discount',0));total=R(tot-disc);paid=R(d.get('paid',0))
    if disc<0 or total<0 or paid<0 or paid>total:raise Err('Invalid discount or paid amount')
    ref=nref(c,'purchase','PUR')
    i=c.execute('insert into docs(kind,ref,party_id,total,discount,paid,method,note,key)values(?,?,?,?,?,?,?,?,?)',('purchase',ref,sup,total,disc,paid,d.get('method','cash'),d.get('note'),d.get('key'))).lastrowid
    for p,qty,cost,a in rows:
        c.execute('insert into items(doc_id,product_id,qty,rate,cost,amount,expiry)values(?,?,?,?,?,?,?)',(i,p['id'],qty,cost,cost,a,next((x.get('expiry') for x in d['items'] if x['product_id']==p['id']),None)));mv(c,p['id'],'purchase',ref,qty,d.get('supplier_ref') or '');c.execute('update products set cost=? where id=?',(cost,p['id']))
    led(c,sup,'purchase',ref,total);led(c,sup,'payment',ref,-paid)
    if d.get('method','cash')=='cash':csh(c,'purchase',ref,-paid)
    if d.get('date'):
        try:time.strptime(d['date'],'%Y-%m-%d')
        except ValueError:raise Err('Invalid purchase date')
        for t in('docs','stock','ledger','cash'):c.execute('update %s set ts=? where ref=?'%t,(d['date']+' 12:00:00',ref))
    return {'id':i,'ref':ref}
@route('POST pay',True)
def _(c,q,d):
    p=Q(c,'select * from parties where id=?',d['party_id'])[0];a=num(d.get('amount'),'Amount');ref='PAY-%d-%d'%(p['id'],int(time.time()*1000))
    cust=p['kind']=='customer'
    led(c,p['id'],'collection' if cust else 'supplier_payment',ref,-a,d.get('note',''))
    if d.get('method','cash')=='cash':csh(c,'collection' if cust else 'supplier_payment',ref,a if cust else -a,d.get('note',''))
    return {'ref':ref}
@route('POST adjust_balance',True)
def _(c,q,d):
    pin(c,d)
    if not d.get('reason'):raise Err('Reason required')
    led(c,d['party_id'],'adjustment','ADJ-%d'%int(time.time()),num(d['amount'],'Amount',False),d['reason']);aud(c,'balance_adjust',json.dumps(d));return {}
@route('POST return',True)
def _(c,q,d):
    o=Q(c,'select * from docs where id=? and kind="sale" and status="done"',d['doc_id'])
    if not o:raise Err('Original sale not found')
    o=o[0];total=0;rows=[]
    for it in d.get('items') or []:
        qty=num(it['qty'],'Return qty');oi=Q(c,'select * from items where doc_id=? and product_id=?',o['id'],it['product_id'])[0]
        prev=one(c,'select sum(i.qty) from items i join docs r on r.id=i.doc_id where r.src=? and r.status="done" and i.product_id=?',o['id'],it['product_id'])
        if qty>oi['qty']-prev+1e-9:raise Err('Return exceeds sold quantity')
        a=R(qty*oi['rate']*(o['total']/(o['total']+o['discount']-o['tax']) if o['total']+o['discount']-o['tax'] else 1));total+=a;rows.append((oi,qty,a,bool(it.get('resalable',True))))
    if not rows:raise Err('Select items to return')
    total=R(total);refund=R(d.get('refund',0))
    if refund>total or refund<0:raise Err('Refund cannot exceed return value')
    if not o['party_id'] and abs(refund-total)>0.004:raise Err('Walk-in returns must be fully refunded')
    ref=nref(c,'sreturn','RET')
    i=c.execute('insert into docs(kind,ref,party_id,total,paid,src,note,tax)values("sreturn",?,?,?,?,?,?,?)',(ref,o['party_id'],total,refund,o['id'],d.get('reason'),R(total*o['tax']/o['total']) if o['total'] else 0)).lastrowid
    for oi,qty,a,res in rows:
        c.execute('insert into items(doc_id,product_id,qty,rate,cost,amount)values(?,?,?,?,?,?)',(i,oi['product_id'],qty,oi['rate'],oi['cost'] if res else 0,a))
        if res:mv(c,oi['product_id'],'sale_return',ref,qty)
        else:aud(c,'damaged_return',ref+' product %d qty %s'%(oi['product_id'],qty))
    led(c,o['party_id'],'return',ref,-total);led(c,o['party_id'],'refund',ref,refund);csh(c,'refund',ref,-refund);aud(c,'return',ref+' of '+o['ref']);return {'ref':ref}
@route('POST void',True)
def _(c,q,d):
    pin(c,d)
    o=Q(c,'select * from docs where id=? and status="done" and kind in("sale","purchase")',d['doc_id'])
    if not o:raise Err('Document not found or already void')
    o=o[0]
    if not d.get('reason'):raise Err('Void reason required')
    if one(c,'select count(*) from docs where src=? and status="done"',o['id']):raise Err('Void the linked returns first - not allowed; use returns workflow')
    r='VOID-'+o['ref']
    for t,cols in(('stock','product_id,type,qty,note'),('ledger','party_id,type,amount,note'),('cash','type,amount,note,category')):
        c.execute('insert into %s(%s,ref)select %s,? from %s where ref=?'%(t,cols,','.join(('-'+x if x in('qty','amount') else x) for x in cols.split(',')),t),(r,o['ref']))
    c.execute('update docs set status="void" where id=?',(o['id'],));aud(c,'void',o['ref']+': '+d['reason']);return {}
@route('POST preturn',True)
def _(c,q,d):
    o=Q(c,'select * from docs where id=? and kind="purchase" and status="done"',d['doc_id'])
    if not o:raise Err('Original purchase not found')
    o=o[0];rows=[];total=0
    for it in d.get('items') or []:
        qty=num(it['qty'],'Return qty');oi=Q(c,'select * from items where doc_id=? and product_id=?',o['id'],it['product_id'])[0]
        prev=one(c,'select sum(i.qty) from items i join docs r on r.id=i.doc_id where r.src=? and r.kind="preturn" and r.status="done" and i.product_id=?',o['id'],it['product_id'])
        if qty>oi['qty']-prev+1e-9:raise Err('Return exceeds purchased quantity')
        if stk(c,it['product_id'])<qty and S(c,'allow_negative')!='1':raise Err('Not enough stock to return')
        a=R(qty*oi['rate']);total+=a;rows.append((oi,qty,a))
    if not rows:raise Err('Select items to return')
    total=R(total);refund=R(d.get('refund',0))
    if refund<0 or refund>total:raise Err('Invalid refund')
    ref=nref(c,'preturn','PRT')
    i=c.execute('insert into docs(kind,ref,party_id,total,paid,src,note)values("preturn",?,?,?,?,?,?)',(ref,o['party_id'],total,refund,o['id'],d.get('reason'))).lastrowid
    for oi,qty,a in rows:
        c.execute('insert into items(doc_id,product_id,qty,rate,cost,amount)values(?,?,?,?,?,?)',(i,oi['product_id'],qty,oi['rate'],oi['cost'],a));mv(c,oi['product_id'],'purchase_return',ref,-qty,d.get('reason') or '')
    led(c,o['party_id'],'purchase_return',ref,-total);led(c,o['party_id'],'refund_received',ref,refund);csh(c,'purchase_refund',ref,refund);aud(c,'purchase_return',ref+' of '+o['ref']);return {'ref':ref}
@route('GET conv')
def _(c,q,d):return Q(c,'select * from conv')
@route('POST conv',True)
def _(c,q,d):
    f=num(d.get('factor'),'Factor')
    if not d.get('base') or not d.get('alt') or d['base']==d['alt']:raise Err('Invalid units')
    c.execute('insert or replace into conv values(?,?,?)',(d['base'],d['alt'],f));aud(c,'conversion','%s %s %s'%(d['base'],d['alt'],f));return {}
@route('GET me')
def _(c,q,d):return {'user':CUR.user,'role':CUR.role}
@route('GET users')
def _(c,q,d):return Q(c,'select id,username,role,active from users')
@route('POST users',True)
def _(c,q,d):
    if len(d.get('password',''))<6 or not d.get('username') or d.get('role') not in('admin','cashier'):raise Err('Username, role and 6+ char password required')
    s=secrets.token_hex(8)
    try:c.execute('insert into users(username,salt,hash,role)values(?,?,?,?)',(d['username'],s,hp(d['password'],s),d['role']))
    except sqlite3.IntegrityError:raise Err('Username exists')
    aud(c,'user_created',d['username']);return {}
@route('POST password',True)
def _(c,q,d):
    x=Q(c,'select * from users where username=?',CUR.user)[0]
    if hp(d.get('old',''),x['salt'])!=x['hash']:raise Err('Old password incorrect')
    if len(d.get('new',''))<6:raise Err('Password must be at least 6 characters')
    s=secrets.token_hex(8);c.execute('update users set salt=?,hash=? where id=?',(s,hp(d['new'],s),x['id']));return {}
def aged(c,pid):
    import datetime as dt
    rows=Q(c,'select * from ledger where party_id=? order by id',pid);bal=R(sum(r['amount'] for r in rows));out=[];t=dt.date.today()
    for r in reversed(rows):
        if bal<=0.004:break
        if r['amount']>0:
            o=min(r['amount'],bal);out.append({'ref':r['ref'],'ts':r['ts'],'type':r['type'],'amount':r['amount'],'open':R(o),'age':(t-dt.date.fromisoformat(r['ts'][:10])).days});bal=R(bal-o)
    return out
@route('GET open_items')
def _(c,q,d):return aged(c,int(q['party_id']))
@route('GET aging')
def _(c,q,d):
    res=[]
    for p in Q(c,'select id,name from parties where kind="customer"'):
        b={'0-30':0,'31-60':0,'61+':0}
        for x in aged(c,p['id']):b['0-30' if x['age']<=30 else '31-60' if x['age']<=60 else '61+']+=x['open']
        if sum(b.values())>0:res.append(dict(name=p['name'],total=R(sum(b.values())),**b))
    return res
@route('GET expiry')
def _(c,q,d):
    return Q(c,'select p.name,p.unit,i.expiry,d.ref,i.qty,(select coalesce(sum(qty),0) from stock where product_id=p.id) stock,case when date(i.expiry)<date("now","localtime") then "expired" else "near-expiry" end status from items i join docs d on d.id=i.doc_id join products p on p.id=i.product_id where d.kind="purchase" and d.status="done" and i.expiry is not null and i.expiry!="" and date(i.expiry)<=date("now","localtime","+%d days") and stock>0 order by i.expiry'%int(q.get('days',14)))
@route('POST expense',True)
def _(c,q,d):
    a=num(d.get('amount'),'Amount');ref=(d.get('reference') or '').strip() or 'EXP-%d'%int(time.time()*1000);src=d.get('source','cash drawer');csh(c,'expense',ref,-a,'[%s] %s'%(src,d.get('note','')),d.get('category','other'))
    if src!='cash drawer':csh(c,'expense_offset',ref,a,'paid from '+src);return {}
@route('POST wastage',True)
def _(c,q,d):
    p=Q(c,'select * from products where id=?',d['product_id'])[0];qty=num(d.get('qty'),'Quantity')
    if stk(c,p['id'])<qty and S(c,'allow_negative')!='1':raise Err('Wastage exceeds stock')
    if not d.get('reason'):raise Err('Reason required')
    mv(c,p['id'],'wastage','WST-%d'%int(time.time()*1000),-qty,d['reason']);return {}
@route('POST stockcount',True)
def _(c,q,d):
    pin(c,d)
    if not d.get('reason'):raise Err('Reason required')
    p=d['product_id'];diff=round(num(d['counted'],'Counted',False)-stk(c,p),3)
    if diff:mv(c,p,'adjustment','CNT-%d'%int(time.time()),diff,d['reason']);aud(c,'stock_count','product %s diff %s: %s'%(p,diff,d['reason']))
    return {'diff':diff}
@route('GET day')
def _(c,q,d):
    x=Q(c,'select * from days where closed is null')
    if x:x[0]['expected']=R(x[0]['opening']+one(c,'select sum(amount) from cash where id>?',x[0]['mark']));return x[0]
    return {}
@route('POST day_open',True)
def _(c,q,d):
    if Q(c,'select 1 from days where closed is null'):raise Err('A day is already open')
    c.execute('insert into days(opening,mark)values(?,?)',(num(d.get('opening',0),'Opening cash',False),one(c,'select max(id) from cash')));return {}
@route('POST day_close',True)
def _(c,q,d):
    x=Q(c,'select * from days where closed is null')
    if not x:raise Err('No open day (already closed?)')
    x=x[0];e=R(x['opening']+one(c,'select sum(amount) from cash where id>?',x['mark']));cn=num(d.get('counted'),'Counted cash',False)
    c.execute('update days set closed=datetime("now","localtime"),expected=?,counted=?,diff=?,note=? where id=?',(e,cn,R(cn-e),d.get('note'),x['id']));return {'expected':e,'diff':R(cn-e)}
def rep(c,f,t):
    w=' and date(d.ts) between ? and ? and d.status="done"';a=(f,t)
    def s(k):return one(c,'select sum(total) from docs d where kind=?'+w,k,*a)
    gross=one(c,'select sum(total+discount-tax) from docs d where kind="sale"'+w,*a);disc=one(c,'select sum(discount) from docs d where kind="sale"'+w,*a)
    ret=one(c,'select sum(total-tax) from docs d where kind="sreturn"'+w,*a);cg=lambda k:one(c,'select sum(i.cost*i.qty) from items i join docs d on d.id=i.doc_id where d.kind=?'+w,k,*a)
    cogs=R(cg('sale')-cg('sreturn'))
    wst=R(-one(c,'select sum(s.qty*p.cost) from stock s join products p on p.id=s.product_id where s.type="wastage" and date(s.ts) between ? and ?',f,t))
    exp=R(-one(c,'select sum(amount) from cash where type="expense" and date(ts) between ? and ?',f,t));net=R(gross-disc-ret)
    cs=lambda ty:one(c,'select sum(amount) from cash where type=? and date(ts) between ? and ?',ty,f,t)
    return dict(gross=R(gross),discounts=R(disc),returns=R(ret),net_sales=net,purchases=R(s('purchase')-s('preturn')),expenses=exp,wastage_cost=wst,cogs=cogs,gross_profit=R(net-cogs),net_profit=R(net-cogs-wst-exp),credit_sales=R(one(c,'select sum(total-paid) from docs d where kind="sale"'+w,*a)),collections=R(cs('collection')),supplier_payments=R(-cs('supplier_payment')),cash_received=R(one(c,'select sum(amount) from cash where amount>0 and type!="expense_offset" and date(ts) between ? and ?',f,t)),note='Estimated profit = net sales - COGS (last purchase cost method) - wastage - expenses')
@route('GET stockreport')
def _(c,q,d):
    f=q.get('from') or time.strftime('%Y-%m-%d');t=q.get('to') or f
    ty=lambda s,neg='':'coalesce((select %ssum(qty) from stock where product_id=p.id and date(ts) between ? and ? and type in(%s)),0)'%(neg,s)
    return Q(c,'select p.name,p.unit,coalesce((select sum(qty) from stock where product_id=p.id and date(ts)<?),0) opening,'+ty('"purchase","opening"')+' received,'+ty('"sale"','-')+' sold,'+ty('"sale_return"')+' returned,'+ty('"purchase_return"','-')+' supplier_returned,'+ty('"wastage"','-')+' wastage,'+ty('"adjustment"')+' adjustments,coalesce((select sum(qty) from stock where product_id=p.id and date(ts)<=?),0) closing from products p order by p.name',f,f,t,f,t,f,t,f,t,f,t,f,t,t)
@route('GET cashreport')
def _(c,q,d):
    f=q.get('from') or time.strftime('%Y-%m-%d');t=q.get('to') or f
    b=Q(c,'select type,sum(amount) amount from cash where type!="expense_offset" and date(ts) between ? and ? group by type',f,t)
    return {'by_type':b,'received':R(sum(x['amount'] for x in b if x['amount']>0)),'paid':R(-sum(x['amount'] for x in b if x['amount']<0)),'days':Q(c,'select * from days where date(opened) between ? and ? order by id desc',f,t)}
@route('GET report')
def _(c,q,d):
    f=q.get('from') or time.strftime('%Y-%m-%d');t=q.get('to') or f;r=rep(c,f,t)
    r['top_products']=Q(c,'select p.name,sum(i.qty) qty,sum(i.amount) revenue,sum(i.cost*i.qty) cost from items i join docs d on d.id=i.doc_id join products p on p.id=i.product_id where d.kind="sale" and d.status="done" and date(d.ts) between ? and ? group by p.id order by qty desc limit 10',f,t)
    r['slow']=Q(c,'select p.name,coalesce(sum(i.qty),0) qty from products p left join items i on i.product_id=p.id and i.doc_id in(select id from docs where kind="sale" and status="done" and date(ts) between ? and ?) where p.active=1 group by p.id order by qty,p.name limit 5',f,t)
    r['negative']=Q(c,'select p.name,sum(s.qty) stock from products p join stock s on s.product_id=p.id group by p.id having stock<0')
    r['by_supplier']=Q(c,'select p.name,sum(d.total) total,sum(d.paid) paid from docs d join parties p on p.id=d.party_id where d.kind="purchase" and d.status="done" and date(d.ts) between ? and ? group by p.id',f,t)
    r['by_category']=Q(c,'select coalesce(category,"other") category,-sum(amount) total from cash where type="expense" and date(ts) between ? and ? group by 1',f,t)
    return r
@route('GET dashboard')
def _(c,q,d):
    t=time.strftime('%Y-%m-%d');r=rep(c,q.get('from') or t,q.get('to') or q.get('from') or t)
    low=Q(c,'select p.name,p.unit,p.min_stock,(select coalesce(sum(qty),0) from stock where product_id=p.id) stock from products p where active=1')
    r.update(products=len(low),low=[x for x in low if 0<x['stock']<=x['min_stock']],out=[x for x in low if x['stock']<=0],cust_due=R(one(c,'select sum(amount) from ledger l join parties p on p.id=l.party_id where kind="customer"')),sup_due=R(one(c,'select sum(amount) from ledger l join parties p on p.id=l.party_id where kind="supplier"')),cash_balance=R(one(c,'select sum(amount) from cash')),recent=Q(c,'select d.*,p.name party from docs d left join parties p on p.id=d.party_id order by d.id desc limit 8'),top=rep_top(c,t));return r
def rep_top(c,t):return Q(c,'select p.name,sum(i.qty) qty from items i join docs d on d.id=i.doc_id join products p on p.id=i.product_id where d.kind="sale" and d.status="done" and date(d.ts)=? group by p.id order by qty desc limit 5',t)
@route('GET docs')
def _(c,q,d):
    lim=min(int(q.get('limit',20)),200);off=int(q.get('offset',0));f=q.get('from') or '2000-01-01';t=q.get('to') or '2999-01-01'
    w='d.kind=? and date(d.ts) between ? and ?';rows=Q(c,'select d.*,p.name party from docs d left join parties p on p.id=d.party_id where '+w+' order by d.id desc limit ? offset ?',q['kind'],f,t,lim,off)
    return {'rows':rows,'total':one(c,'select count(*) from docs d where '+w,q['kind'],f,t)}
@route('GET doc')
def _(c,q,d):
    x=Q(c,'select * from docs where id=?',q['id'])[0];x['items']=Q(c,'select i.*,p.name,p.unit,p.decimals from items i join products p on p.id=i.product_id where doc_id=?',q['id']);return x
@route('GET stock')
def _(c,q,d):
    f=q.get('from') or '2000-01-01';t=q.get('to') or '2999-01-01'
    return Q(c,'select s.*,p.name,p.unit,(select sum(qty) from stock where product_id=s.product_id and id<=s.id) balance from stock s join products p on p.id=s.product_id where (?=0 or s.product_id=?) and date(s.ts) between ? and ? order by s.id desc limit 300',int(q.get('product_id',0)),int(q.get('product_id',0)),f,t)
@route('GET ledger')
def _(c,q,d):return Q(c,'select l.*,(select sum(amount) from ledger where party_id=l.party_id and id<=l.id) balance from ledger l where party_id=? and date(ts) between ? and ? order by id desc',q['party_id'],q.get('from') or '2000-01-01',q.get('to') or '2999-01-01')
@route('GET cash')
def _(c,q,d):return Q(c,'select * from cash order by id desc limit 200')
@route('GET audit')
def _(c,q,d):return Q(c,'select * from audit order by id desc limit 200')
@route('GET payreceipt')
def _(c,q,d):
    x=Q(c,'select l.*,p.name from ledger l join parties p on p.id=l.party_id where l.ref=? limit 1',q['ref'])
    if not x:raise Err('Not found')
    x=x[0];b=one(c,'select sum(amount) from ledger where party_id=? and id<=?',x['party_id'],x['id'])
    return '<html><body style="width:72mm;font:12px monospace;margin:4mm"><h3 align=center>%s<br><small>Payment receipt</small></h3>%s<br>%s<hr>%s: <b>%.2f %s</b><br>%s<br>Balance after: %.2f<hr><p align=center>%s</p><script>print()</script></body></html>'%(S(c,'shop'),x['ts'],x['name'],x['type'].replace('_',' '),abs(x['amount']),S(c,'currency'),x['note'] or '',b,S(c,'footer'))
@route('GET receipt')
def receipt(c,q,d):
    x=Q(c,'select d.*,p.name party from docs d left join parties p on p.id=d.party_id where d.id=?',q['id'])[0];its=Q(c,'select i.*,p.name,p.unit,p.decimals from items i join products p on p.id=i.product_id where doc_id=?',x['id'])
    prev=0;bal=0
    if x['party_id']:
        m=one(c,'select min(id) from ledger where ref=?',x['ref']) or 10**9;prev=one(c,'select sum(amount) from ledger where party_id=? and id<?',x['party_id'],m);bal=one(c,'select sum(amount) from ledger where party_id=?',x['party_id'])
    cur=S(c,'currency');rows=''.join('<tr><td colspan=3>%s</td></tr><tr><td>%g %s</td><td>@%.2f</td><td align=right>%.2f</td></tr>'%(i['name'],i['qty'],i['unit'],i['rate'],i['amount']) for i in its)
    return '<html><body style="width:72mm;font:12px monospace;margin:4mm"><h3 align=center>%s<br><small>%s</small></h3>%s<br>%s<hr><table width=100%%>%s</table><hr>Discount: %.2f<br>Tax: %.2f<br><b>Total: %s %.2f</b><br>Paid: %.2f<br>Balance: %.2f<br>%s<hr><p align=center>%s</p><script>print()</script></body></html>'%((('<img src="%s" style="max-width:40mm"><br>'%S(c,'logo')) if S(c,'logo') else '')+S(c,'shop'),S(c,'phone'),x['ref'],x['ts']+(' | '+x['party'] if x['party'] else ' | Walk-in'),rows,x['discount'] or 0,x['tax'] or 0,cur,x['total'],x['paid'] or 0,x['total']-(x['paid'] or 0),('Previous due: %.2f<br>Remaining due: %.2f'%(prev,bal) if x['party_id'] else ''),S(c,'footer'))
@route('GET csv')
def _(c,q,d):
    f=q.get('from') or '2000-01-01';t=q.get('to') or '2999-01-01';n=q['name']
    sql={'sales':('select ref,ts,party_id,total,discount,paid,method,status from docs where kind="sale" and date(ts) between ? and ?',(f,t)),'purchases':('select ref,ts,party_id,total,paid,status from docs where kind="purchase" and date(ts) between ? and ?',(f,t)),'stock':('select p.name,p.unit,p.cost,sum(s.qty) stock,sum(s.qty)*p.cost value from products p left join stock s on s.product_id=p.id where 1 group by p.id',()),'customers':('select p.name,p.phone,coalesce(sum(l.amount),0) outstanding from parties p left join ledger l on l.party_id=p.id where p.kind="customer" group by p.id',()),'suppliers':('select p.name,p.phone,coalesce(sum(l.amount),0) payable from parties p left join ledger l on l.party_id=p.id where p.kind="supplier" group by p.id',()),'products':('select name,sku,barcode,category,unit,cost,price,min_stock,active from products',()),'ledger':('select p.name party,l.ts,l.type,l.ref,l.amount from ledger l join parties p on p.id=l.party_id where date(l.ts) between ? and ?',(f,t)),'expenses':('select ts,category,-amount amount,note from cash where type="expense" and date(ts) between ? and ?',(f,t))}[n]
    cur=c.execute(*sql);o=io.StringIO();w=csv.writer(o);w.writerow([x[0] for x in cur.description]);w.writerows(cur.fetchall());return ('csv',o.getvalue())
@route('POST backup')
def _(c,q,d):
    os.makedirs(os.path.join(D,'backups'),exist_ok=True);p=os.path.join(D,'backups','dairy-%s.db'%time.strftime('%Y%m%d-%H%M%S'))
    if os.path.exists(p):raise Err('Backup already exists')
    b=sqlite3.connect(p);c.backup(b);b.close();return {'file':p}
def conn():
    c=sqlite3.connect(DB,isolation_level=None,timeout=10);c.row_factory=sqlite3.Row;c.create_function('cu',0,lambda:getattr(CUR,'user','system'));c.execute('pragma foreign_keys=on');return c
def init(demo=False):
    c=conn();c.executescript(SCHEMA)
    try:c.execute('alter table docs add column tax real default 0')
    except sqlite3.OperationalError:pass
    for t in('docs','stock','cash','ledger'):
        try:c.execute('alter table %s add column user text'%t)
        except sqlite3.OperationalError:pass
        c.execute('create trigger if not exists u_%s after insert on %s begin update %s set user=cu() where id=new.id;end'%(t,t,t))
    for t,cols in(('parties',('notes',)),('products',('brand','notes'))):
        for k in cols:
            try:c.execute('alter table %s add column %s text'%(t,k))
            except sqlite3.OperationalError:pass
    for t in('products','parties','docs'):
        for k in('updated','updated_by'):
            try:c.execute('alter table %s add column %s text'%(t,k))
            except sqlite3.OperationalError:pass
        c.execute("create trigger if not exists upd_%s after update of name on %s begin update %s set updated=datetime('now','localtime'),updated_by=cu() where id=new.id;end"%(t,t,t) if t!='docs' else "create trigger if not exists upd_docs after update of status on docs begin update docs set updated=datetime('now','localtime'),updated_by=cu() where id=new.id;end")
    try:c.execute('alter table items add column expiry text')
    except sqlite3.OperationalError:pass
    if not one(c,'select count(*) from users'):
        s=secrets.token_hex(8);c.execute('insert into users(username,salt,hash,role)values("admin",?,?,"admin")',(s,hp(os.environ.get('ADMIN_PASSWORD','admin123'),s)));print('Default login: admin / admin123 (change it in Settings)')
    if demo and not one(c,'select count(*) from products'):
        c.execute('begin')
        for n,u,co,pr,o in(('Fresh Milk (DEMO)','liter',150,180,50),('Yogurt (DEMO)','kg',200,260,20)):
            i=c.execute('insert into products(name,unit,cost,price,min_stock,category)values(?,?,?,?,5,"demo")',(n,u,co,pr)).lastrowid;mv(c,i,'opening','OPEN-%d'%i,o)
        c.execute('insert into parties(kind,name) values("customer","Ali (DEMO)"),("supplier","Farm Supplier (DEMO)")');c.execute('commit')
    c.close()
class H(BaseHTTPRequestHandler):
    def log_message(s,*a):pass
    def send(s,code,body,ct,tok=None):
        b=body.encode() if isinstance(body,str) else body;s.send_response(code);s.send_header('Content-Type',ct);s.send_header('Content-Length',len(b));tok and s.send_header('Set-Cookie','tok=%s; Path=/; HttpOnly; SameSite=Strict'%tok);s.end_headers();s.wfile.write(b)
    def go(s,m):
        u=urlparse(s.path);q={k:v[0] for k,v in parse_qs(u.query).items()}
        if not u.path.startswith('/api/'):return s.send(200,open(os.path.join(D,'index.html'),'rb').read(),'text/html; charset=utf-8')
        c=conn()
        try:
            d=json.loads(s.rfile.read(int(s.headers.get('Content-Length') or 0)) or '{}') if m=='POST' else {}
            nm=m+' '+u.path[5:]
            if nm=='POST login':
                x=Q(c,'select * from users where username=? and active=1',d.get('username',''))
                if not x or hp(d.get('password',''),x[0]['salt'])!=x[0]['hash']:raise Err('Invalid username or password')
                tok=secrets.token_hex(16);SESS[tok]=(x[0]['username'],x[0]['role']);return s.send(200,json.dumps({'user':x[0]['username'],'role':x[0]['role']}),'application/json',tok)
            tk=dict(y.strip().split('=',1) for y in s.headers.get('Cookie','').split(';') if '=' in y).get('tok')
            if nm=='POST logout':SESS.pop(tk,None);return s.send(200,'{}','application/json')
            if tk not in SESS:return s.send(401,json.dumps({'error':'Login required','login':1}),'application/json')
            CUR.user,CUR.role=SESS[tk]
            if nm in ADMIN and CUR.role!='admin':raise Err('Admin access required')
            f,tx=ROUTES[nm]
            if tx:c.execute('begin immediate')
            r=f(c,q,d)
            if tx:c.execute('commit')
            if isinstance(r,tuple):return s.send(200,r[1],'text/csv')
            if isinstance(r,str):r=r.replace('width:72mm','width:%smm'%(S(c,'receipt_mm') or 72));return s.send(200,r,'text/html; charset=utf-8')
            s.send(200,json.dumps(r),'application/json')
        except Exception as e:
            try:c.execute('rollback')
            except:pass
            msg=str(e) if isinstance(e,Err) else ('Not found' if isinstance(e,KeyError) else 'Error: %s'%e)
            s.send(400,json.dumps({'error':msg}),'application/json')
        finally:c.close()
    def do_GET(s):s.go('GET')
    def do_POST(s):s.go('POST')
if __name__=='__main__':
    init()
    if '--demo' in sys.argv:import demo;demo.run()
    print('Dairy POS running at http://localhost:8000');ThreadingHTTPServer(('127.0.0.1',8000),H).serve_forever()
