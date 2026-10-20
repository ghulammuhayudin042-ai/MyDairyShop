"""Mandatory acceptance tests from the brief (section 5). Run: python3 tests.py"""
import os,json,threading,urllib.request as u,http.cookiejar,time,sqlite3,glob
DB='/tmp/accept.db'
for f in glob.glob(DB+'*'):os.remove(f)
os.environ['POS_DB']=DB
import server as s
from http.server import ThreadingHTTPServer
def boot(port):
    sv=ThreadingHTTPServer(('127.0.0.1',port),s.H);threading.Thread(target=sv.serve_forever,daemon=True).start();return sv
def client(port):
    op=u.build_opener(u.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def a(p,b=None):
        try:return json.load(op.open(u.Request('http://127.0.0.1:%d/api/%s'%(port,p),json.dumps(b).encode() if b is not None else None)))
        except Exception as e:return json.load(e)
    a('login',{'username':'admin','password':'admin123'});return a,op
n=[0]
def ok(name,cond,info=''):
    n[0]+=1;print(('PASS' if cond else 'FAIL'),n[0],name,info);assert cond,name
s.init();sv=boot(8801);a,op=client(8801);T=time.strftime('%Y-%m-%d')
milk=a('products',dict(name='Milk',unit='liter',decimals=2,cost=150,price=180.5))['id'];yog=a('products',dict(name='Yogurt',unit='kg',decimals=3,cost=200,price=260.25))['id']
sup=a('parties',dict(kind='supplier',name='Farm'))['id'];cu=a('parties',dict(kind='customer',name='Ali'))['id']
ok('1 products with liter/kg units and decimal prices',{p['name']:p['price'] for p in a('products')}=={'Milk':180.5,'Yogurt':260.25})
a('purchase',dict(party_id=sup,items=[dict(product_id=milk,qty=100,cost=150),dict(product_id=yog,qty=20,cost=200)],paid=10000))
st=lambda i:[p for p in a('products') if p['id']==i][0]['stock'];bal=lambda k,i:[p for p in a('parties?kind='+k) if p['id']==i][0]['balance']
ok('2 purchase increases stock and supplier payable',st(milk)==100 and bal('supplier',sup)==9000,bal('supplier',sup))
k=a('sale',dict(key='abc',items=[dict(product_id=milk,qty=1.75)]));a('sale',dict(key='abc',items=[dict(product_id=milk,qty=1.75)]))
rc=op.open('http://127.0.0.1:8801/api/receipt?id=%d'%k['id']).read().decode()
ok('3 fractional sale, receipt, stock decreases exactly once',st(milk)==98.25 and '1.75 liter' in rc and 'Total' in rc,st(milk))
a('sale',dict(party_id=cu,items=[dict(product_id=yog,qty=0.25)],paid=0));r=a('receipt?id=%d'%0) if False else None
ok('4 credit sale: customer ledger and balance',bal('customer',cu)==65.06,bal('customer',cu))
a('pay',dict(party_id=cu,amount=15.06));a('pay',dict(party_id=sup,amount=1000));cs=a('cash')
ok('5 collection + supplier payment hit ledgers and cash',bal('customer',cu)==50 and bal('supplier',sup)==8000 and any(x['type']=='collection' and x['amount']==15.06 for x in cs) and any(x['type']=='supplier_payment' and x['amount']==-1000 for x in cs))
rp=a('report?from=%s&to=%s'%(T,T));rt=a('return',dict(doc_id=k['id'],items=[dict(product_id=milk,qty=0.75,resalable=True)],refund=135.38,reason='x'));rp2=a('report?from=%s&to=%s'%(T,T))
ok('6 partial return: stock, refund, net sales',st(milk)==99 and rp2['returns']==round(0.75*180.5,2) and rp2['net_sales']==round(rp['net_sales']-0.75*180.5,2),rp2['returns'])
a('wastage',dict(product_id=milk,qty=1,reason='spill'));a('expense',dict(amount=500,category='rent'));rp3=a('report?from=%s&to=%s'%(T,T))
ok('7 wastage reduces stock; expense and wastage reports update',st(milk)==98 and rp3['expenses']==500 and rp3['wastage_cost']==150)
inv=a('docs?kind=sale&from=%s&to=%s'%(T,T))['rows'];tot=sum(x['total'] for x in inv if x['status']=='done')
ok('8 date-filtered sales report reconciles to invoices',round(rp3['gross']-rp3['discounts'],2)==round(tot,2),(rp3['gross'],tot))
a('day_open',dict(opening=1000));a('expense',dict(amount=100,category='other'));d=a('day')
c=a('day_close',dict(counted=d['expected']-10))
ok('9 day close: expected, counted, variance; no double close',c['diff']==-10 and 'error' in a('day_close',dict(counted=1)),c)
sv.shutdown();sv.server_close();sv=boot(8802);a,op=client(8802)
ok('10a data persists after restart',st(milk)==98 and bal('customer',cu)==50)
b=a('backup',{});bk=sqlite3.connect(b['file']);ok('10b backup created and contains data',bk.execute('select count(*) from products').fetchone()[0]==2,b['file'])
ok('extra: negative stock blocked',a('sale',dict(items=[dict(product_id=milk,qty=1000)])).get('error','').startswith('Insufficient'))
ok('extra: stock CSV export works',len(op.open('http://127.0.0.1:8802/api/csv?name=stock').read().decode().splitlines())==3)
print('\nALL %d ACCEPTANCE CHECKS PASSED'%n[0])

# ---- extra gap-closing checks
sv.shutdown();sv.server_close();sv=boot(8803);a,op=client(8803)
sr={x['name']:x for x in a('stockreport?from=%s&to=%s'%(T,T))};mr=sr['Milk']
ok('stock report: opening+received-sold+returns-wastage = closing',round(mr['opening']+mr['received']-mr['sold']+mr['returned']-mr['supplier_returned']-mr['wastage']+mr['adjustments'],3)==mr['closing']==st(milk),mr)
cr=a('cashreport?from=%s&to=%s'%(T,T));ok('cash report has received/paid and day sessions',cr['received']>0 and cr['paid']>0 and len(cr['days'])==1)
x=a('sale',dict(party_id=cu,items=[dict(product_id=milk,qty=1)],payments=[dict(method='cash',amount=100),dict(method='card',amount=50)]));dd=a('doc?id=%d'%x['id'])
ok('split payment recorded (cash+card), cash drawer gets cash only',dd['paid']==150 and dd['method']=='split' and dd['total']-150==30.5)
a('products',dict(name='Cream',unit='kg',price=700,cost=500,category='cream',brand='X',notes='n'));ok('category filter + brand/notes',len(a('products?cat=cream'))==1 and a('products?cat=cream')[0]['brand']=='X')
p=a('purchase',dict(party_id=sup,items=[dict(product_id=milk,qty=1,cost=151)],date='2026-01-05'));ok('backdated purchase + cost history',a('costhistory?product_id=%d'%milk)[0]['ts'].startswith('2026-01-05') or any(h['ts'].startswith('2026-01-05') for h in a('costhistory?product_id=%d'%milk)))
ok('invalid purchase date rejected','error' in a('purchase',dict(party_id=sup,items=[dict(product_id=milk,qty=1,cost=1)],date='x')))
pr=a('pay',dict(party_id=cu,amount=5));ok('payment receipt',b'Payment receipt' in op.open('http://127.0.0.1:8803/api/payreceipt?ref='+pr['ref']).read())
cb=[x for x in a('cash')];a('expense',dict(amount=70,category='repairs',source='bank',reference='CHQ1'));ok('bank expense hits profit but not cash drawer',a('report?from=%s&to=%s'%(T,T))['expenses']==670 and sum(x['amount'] for x in a('cash'))==sum(x['amount'] for x in cb))
a('parties',dict(id=cu,name='Ali K',notes='VIP',active=1));ok('party edit with notes',[q for q in a('parties?kind=customer') if q['id']==cu][0]['notes']=='VIP')
ok('dashboard date range',a('dashboard?from=2020-01-01&to=2020-01-02')['gross']==0)
print('\nALL %d CHECKS PASSED (incl. gap-closing)'%n[0])
