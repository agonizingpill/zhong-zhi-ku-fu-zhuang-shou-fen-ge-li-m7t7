from sqlalchemy import select,func
from .db import Base,engine,Session
from .models import *
from .auth import hashed

def seed():
    Base.metadata.create_all(engine)
    with Session() as s:
        for role in ('operator','supervisor','viewer'):
            email=role+'@qinghe.demo'
            if not s.scalar(select(User).where(User.email==email)):s.add(User(email=email,role=role,password_hash=hashed('Seeds2026!')))
        a=s.scalar(select(Accession).where(Accession.code=='QH-021'))
        if not a:a=Accession(code='QH-021',crop='青豆',name='青禾早熟青豆',origin='青禾北圃2023采集',minimum=300);s.add(a);s.flush()
        for code,generation,available in [('QH-021-G3',3,960),('QH-021-POLLEN',3,400)]:
            if not s.scalar(select(Lot).where(Lot.code==code)):s.add(Lot(accession_id=a.id,code=code,generation=generation,available=available,stored_months=36,temperature=4,initial_rate=94))
        other=s.scalar(select(Accession).where(Accession.code=='QH-087'))
        if not other:other=Accession(code='QH-087',crop='青豆',name='异源晚熟青豆',origin='南坡异源对照',minimum=300);s.add(other);s.flush()
        if not s.scalar(select(Lot).where(Lot.code=='QH-087-G2')):s.add(Lot(accession_id=other.id,code='QH-087-G2',generation=2,available=800,stored_months=18,temperature=-8,initial_rate=96))
        for i in range(18):
            code=chr(65+i//6)+str(i%6+1)
            if not s.scalar(select(Plot).where(Plot.code==code)):s.add(Plot(code=code,row=i//6,position=i%6,capacity=250))
        for material,available in (('paper',120),('nonwoven',80),('nylon',60)):
            if not s.scalar(select(BagStock).where(BagStock.material==material)):s.add(BagStock(material=material,available=available))
        s.flush();lot=s.scalar(select(Lot).where(Lot.code=='QH-021-G3'))
        if not s.scalar(select(GerminationTest).where(GerminationTest.code=='GT-SEED-80')):s.add(GerminationTest(lot_id=lot.id,code='GT-SEED-80',sampled=100,germinated=80,state='recorded'))
        # 历史封口检查（未绑定授粉单）作为风险拟合样本；按检查编号只补缺，不覆盖人工记录。
        history=[('BC-H-01','paper',46,0,'fail'),('BC-H-02','paper',12,0,'pass'),('BC-H-03','nonwoven',38,1,'fail'),
                 ('BC-H-04','nonwoven',9,0,'pass'),('BC-H-05','nylon',51,0,'fail'),('BC-H-06','nylon',16,1,'pass'),
                 ('BC-H-07','paper',29,1,'fail'),('BC-H-08','paper',5,0,'pass'),('BC-H-09','nonwoven',22,0,'fail'),
                 ('BC-H-10','nonwoven',33,1,'fail'),('BC-H-11','nylon',7,0,'pass'),('BC-H-12','nylon',27,1,'fail'),
                 ('BC-H-13','paper',19,0,'fail'),('BC-H-14','nonwoven',3,0,'pass')]
        for code,material,days,prior,result in history:
            if not s.scalar(select(BagCheck).where(BagCheck.code==code)):
                s.add(BagCheck(pollination_id=None,code=code,bag_code='ARCHIVE-'+code,material=material,check_date='2026-10-01',
                               days_in_field=days,prior_checks=prior,result=result,risk=None,model=None))
        s.commit();return {cls.__tablename__:s.scalar(select(func.count()).select_from(cls))for cls in (User,Accession,Lot,GerminationTest,Regeneration,Plot,Flowering,Pollination,BagStock,BagCheck,BagReplacement,Harvest,Distribution)}
