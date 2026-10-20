"""Demo data (clearly labelled "(DEMO)"). Loaded by `python3 server.py --demo` on an empty database.
Everything goes through the real business logic, so stock, ledgers, cash and reports stay consistent."""
import server as s
def call(name,d):
    c=s.conn();c.execute('begin immediate')
    try:r=s.ROUTES[name][0](c,{},d);c.execute('commit');return r
    except Exception:c.execute('rollback');raise
def run():
    c=s.conn();n=s.one(c,'select count(*) from products');c.close()
    if n:return print('Database not empty - demo data skipped')
    s.CUR.user='demo';s.CUR.role='admin'
    P=[('Fresh Milk','milk','liter',3,150,180,'1000001',20),('Buffalo Milk','milk','liter',3,170,210,'1000002',20),('Yogurt / Curd','yogurt','kg',3,200,260,'1000003',10),
       ('Lassi','lassi','liter',3,90,130,'1000004',10),('Butter','butter','kg',3,1100,1300,'1000005',3),('Desi Ghee','ghee','kg',3,2200,2600,'1000006',3),
       ('Cream','cream','kg',3,600,750,'1000007',2),('Cheese','cheese','kg',3,1300,1600,'1000008',2),('Milk Pack 1L','milk','pack',0,165,190,'1000009',12),
       ('Yogurt Cup 200g','yogurt','piece',0,45,60,'1000010',20),('Mineral Water 1.5L','drinks','piece',0,60,80,'1000011',10),('Bread','grocery','piece',0,120,150,'1000012',5)]
    ids={}
    for i,(nm,cat,u,dec,co,pr,bc,mn) in enumerate(P):
        ids[nm]=call('POST products',dict(name=nm+' (DEMO)',sku='D%03d'%(i+1),barcode=bc,category=cat,unit=u,decimals=dec,cost=co,price=pr,min_stock=mn))['id']
    sup=[call('POST parties',dict(kind='supplier',name=n+' (DEMO)',phone=p,address=a))['id'] for n,p,a in(('Green Valley Farm','0300-1111111','Lahore'),('Punjab Dairy Co','0301-2222222','Sheikhupura'),('City Grocery Wholesale','0302-3333333','Lahore'))]
    cus=[call('POST parties',dict(kind='customer',name=n+' (DEMO)',phone=p,opening=o))['id'] for n,p,o in(('Ali Khan','0310-1000001',0),('Sara Ahmed','0310-1000002',500),('Hotel Madina','0310-1000003',0),('Bilal Stores','0310-1000004',0))]
    L=lambda *k:[dict(product_id=ids[a],qty=q,cost=c,expiry=e) for a,q,c,e in k]
    call('POST purchase',dict(party_id=sup[0],supplier_ref='GV-101',items=L(('Fresh Milk',200,150,''),('Buffalo Milk',150,170,''),('Lassi',60,90,'2026-10-25')),paid=40000,method='cash'))
    call('POST purchase',dict(party_id=sup[1],supplier_ref='PD-550',items=L(('Yogurt / Curd',60,200,'2026-10-24'),('Butter',15,1100,'2026-12-01'),('Desi Ghee',20,2200,''),('Cream',10,600,'2026-10-23'),('Cheese',8,1300,'2026-11-30')),paid=20000,method='cash'))
    call('POST purchase',dict(party_id=sup[2],supplier_ref='CG-77',items=L(('Milk Pack 1L',60,165,'2026-10-28'),('Yogurt Cup 200g',80,45,'2026-10-26'),('Mineral Water 1.5L',48,60,''),('Bread',20,120,'2026-10-22')),paid=0,method='cash'))
    def sale(items,cu=0,paid=None,**k):
        d=dict(party_id=cu,items=[dict(product_id=ids[a],qty=q) for a,q in items],**k)
        if paid is not None:d['paid']=paid
        return call('POST sale',d)
    r1=sale([('Fresh Milk',1.75),('Yogurt / Curd',0.5)])
    sale([('Buffalo Milk',2.5),('Lassi',1.25)],paid=None)
    sale([('Desi Ghee',0.25),('Butter',0.5)],cus[0],paid=500,method='cash')
    sale([('Fresh Milk',20),('Yogurt / Curd',5)],cus[2],paid=0)
    sale([('Milk Pack 1L',6),('Yogurt Cup 200g',10),('Bread',3)],cus[1],paid=1000,method='card')
    sale([('Cheese',0.75),('Cream',0.5)],discount=100,reason='Regular customer')
    sale([('Mineral Water 1.5L',4),('Fresh Milk',0.5)])
    call('POST pay',dict(party_id=cus[2],amount=2000,note='Part payment',method='cash'))
    call('POST pay',dict(party_id=cus[1],amount=300,note='Collection',method='cash'))
    call('POST pay',dict(party_id=sup[2],amount=3000,note='Cheque 1042',method='cash'))
    call('POST return',dict(doc_id=r1['id'],items=[dict(product_id=ids['Fresh Milk'],qty=0.5,resalable=True)],reason='Customer changed mind',refund=90))
    call('POST wastage',dict(product_id=ids['Fresh Milk'],qty=2,reason='Spillage'))
    call('POST wastage',dict(product_id=ids['Yogurt / Curd'],qty=1.5,reason='Spoiled'))
    for cat,a,n in(('rent',15000,'Shop rent'),('electricity',4500,'Electricity bill'),('transport',1200,'Milk delivery van'),('wages',8000,'Helper wage'),('packaging',800,'Bags and cups')):call('POST expense',dict(category=cat,amount=a,note=n+' (DEMO)'))
    call('POST day_open',dict(opening=5000));print('Demo data loaded: 12 products, 3 suppliers, 4 customers, purchases, sales, returns, wastage, expenses.')
