import os,json,threading,urllib.request as u,http.cookiejar,time
OP=u.build_opener(u.HTTPCookieProcessor(http.cookiejar.CookieJar()))
os.environ['POS_DB']='/tmp/t.db'
if os.path.exists('/tmp/t.db'):os.remove('/tmp/t.db')
import server as s
s.init(True);from http.server import ThreadingHTTPServer
sv=ThreadingHTTPServer(('127.0.0.1',8765),s.H);threading.Thread(target=sv.serve_forever,daemon=True).start()
def a(p,b=None):
    try:return json.load(OP.open(u.Request('http://127.0.0.1:8765/api/'+p,json.dumps(b).encode() if b is not None else None)))
    except Exception as e:return json.load(e)
print('login',a('login',{'username':'admin','password':'admin123'}));ps=a('products');cu=a('parties?kind=customer')[0]['id'];su=a('parties?kind=supplier')[0]['id'];m=ps[0]['id']
print('purchase',a('purchase',{'party_id':su,'items':[{'product_id':m,'qty':10.5,'cost':150}],'paid':500}))
r=a('sale',{'key':'k1','party_id':cu,'items':[{'product_id':m,'qty':1.75}],'paid':100});print('sale',r,a('sale',{'key':'k1','party_id':cu,'items':[{'product_id':m,'qty':1.75}],'paid':100}))
print('stock',[x['stock'] for x in a('products')][0],'(expect 58.75-1.75=... 50+10.5-1.75=58.75)')
print('oversell',a('sale',{'items':[{'product_id':m,'qty':999}]}))
print('return',a('return',{'doc_id':r['id'],'items':[{'product_id':m,'qty':0.5,'resalable':True}],'refund':0}))
print('collect',a('pay',{'party_id':cu,'amount':50}),'cust bal',a('parties?kind=customer')[0]['balance'])
print('expense',a('expense',{'amount':100,'category':'rent'}),a('wastage',{'product_id':m,'qty':1,'reason':'spill'}))
print(a('day_open',{'opening':1000}),a('day_close',{'counted':1300}),a('day_close',{'counted':1}))
t=time.strftime('%Y-%m-%d');print(a('report?from=%s&to=%s'%(t,t)))
print('void',a('void',{'doc_id':r['id'],'reason':'x','pin':'1234'}))

print('500ml sale',a('sale',{'items':[{'product_id':m,'qty':500,'unit':'ml'}]}),'no-conv',a('sale',{'items':[{'product_id':m,'qty':1,'unit':'bogus'}]}))
a('settings',{'values':{'tax_pct':'10'}});r2=a('sale',{'items':[{'product_id':m,'qty':1}]});print('tax sale',a('doc?id=%d'%r2['id'])['total'],'(expect 198.0)')
pu=a('docs?kind=purchase')['rows'][0];print('purchase return',a('preturn',{'doc_id':pu['id'],'items':[{'product_id':m,'qty':2}],'refund':100}),'supplier bal',a('parties?kind=supplier')[0]['balance'])
a('users',{'username':'cash1','password':'secret1','role':'cashier'})
print(a('logout',{}),'unauth',a('products'));print('cashier',a('login',{'username':'cash1','password':'secret1'}),'void',a('void',{'doc_id':r2['id'],'reason':'t'}),'users',a('users'))

cu0=a('login',{'username':'admin','password':'admin123'})
b=a('sale',{'party_id':cu,'items':[{'product_id':m,'qty':1}],'paid':0});print('credit sale',b)
print('open invoices',a('open_items?party_id=%d'%cu),'aging',a('aging'))
a('purchase',{'party_id':su,'items':[{'product_id':m,'qty':1,'cost':150,'expiry':'2020-01-01'}],'paid':0});print('expiry',a('expiry'))
print('user stamp',a('docs?kind=sale')['rows'][0]['user'],a('stock')[0]['user'])
a('settings',{'values':{'logo':'data:image/png;base64,AAAA'}});print('logo in receipt','AAAA' in OP.open('http://127.0.0.1:8765/api/receipt?id=%d'%b['id']).read().decode())
