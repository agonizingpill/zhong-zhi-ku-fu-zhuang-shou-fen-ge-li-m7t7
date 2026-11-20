from datetime import date
from fastapi import HTTPException
from sqlalchemy import select,update
from .models import BagCheck,BagReplacement,BagStock,Pollination,Regeneration
from .common import lock,pack,audit
from .bag_risk import MATERIALS,fit,predict

MATERIAL_LABELS={'paper':'纸袋','nonwoven':'无纺布袋','nylon':'尼龙网袋'}
OPEN_STATES=('queued','claimed')


def _material(material):
    if material not in MATERIALS:raise HTTPException(422,'袋材须为'+'、'.join(MATERIALS))
    return material


def rows(s):
    return [{'id':c.id,'material':c.material,'days_in_field':c.days_in_field,'prior_checks':c.prior_checks,'passed':c.result=='pass'}
            for c in s.scalars(select(BagCheck).order_by(BagCheck.id))]


def model_for(s):
    """只用已经落库的检查拟合，新检查的预测不会看到自己的结果。"""
    return fit(rows(s))


def _stock(s,material):
    stock=s.scalar(select(BagStock).where(BagStock.material==material).with_for_update().execution_options(populate_existing=True))
    if not stock:raise HTTPException(409,'该袋材尚未建立新袋库存')
    return stock


def _stock_row(s,material):
    return s.scalar(select(BagStock).where(BagStock.material==material).with_for_update())


def rerank(s,material):
    """锁定袋材库存行后重排，与领袋串行；未领的新袋按风险从高到低预留。"""
    _stock_row(s,material)
    queued=s.scalars(select(BagReplacement).where(BagReplacement.material==material,BagReplacement.state=='queued').with_for_update().order_by(BagReplacement.id)).all()
    # 风险未知（样本不足未拟合）排在有风险估计的后面，同风险按检查先后。
    ordered=sorted(queued,key=lambda r:(r.risk is None,-(r.risk or 0.0),r.id))
    for sequence,item in enumerate(ordered,1):item.reserve_seq=sequence


def _pollination(s,id):
    pre=s.get(Pollination,id)
    if not pre:raise HTTPException(404,'授粉记录不存在')
    # 与授粉确认、换袋保持同一锁序：复壮单 → 授粉单。
    r=lock(s,Regeneration,pre.regeneration_id);x=lock(s,Pollination,id)
    return r,x

def latest(s,pollination_id,bag_code=None):
    query=select(BagCheck).where(BagCheck.pollination_id==pollination_id)
    if bag_code:query=query.where(BagCheck.bag_code==bag_code)
    return s.scalar(query.order_by(BagCheck.id.desc()).limit(1))


def open_replacement(s,pollination_id,bag_code):
    return s.scalar(select(BagReplacement).where(BagReplacement.pollination_id==pollination_id,BagReplacement.old_bag_code==bag_code,BagReplacement.state.in_(OPEN_STATES)).order_by(BagReplacement.id.desc()).limit(1))


def inspect(s,d,u):
    r,x=_pollination(s,d.pollination_id)
    if r.state!='issued':raise HTTPException(409,'仅已领种且未收获的复壮单可做封口检查')
    if x.state!='prepared':raise HTTPException(409,'授粉已确认，袋签和当时的检查已冻结')
    material=_material(x.bag_material)
    if not x.bag_since:raise HTTPException(409,'先登记套袋日期才能计算在田天数')
    if d.check_date<date.fromisoformat(x.bag_since):raise HTTPException(409,'检查日期不得早于本袋签套袋日期')
    if s.scalar(select(BagCheck.id).where(BagCheck.code==d.code)):raise HTTPException(409,'封口检查编号已存在')
    days=(d.check_date-date.fromisoformat(x.bag_since)).days
    prior=0
    previous=latest(s,x.id,x.bag_code)
    if previous:prior=previous.prior_checks+1
    # 先拟合再落库：样本不足则 risk 为空并要求先复检，不编造概率。
    model=model_for(s)
    risk=predict(model,material,days,prior)
    passed=d.result=='pass'
    check=BagCheck(pollination_id=x.id,code=d.code,bag_code=x.bag_code,material=material,check_date=d.check_date.isoformat(),days_in_field=days,prior_checks=prior,result=d.result,risk=risk,model=model)
    s.add(check);s.flush()
    # 只有当前袋签最近一次合格检查才允许授粉；不合格立即取消套袋标记。
    x.bagged=passed
    note=''
    if passed:
        # 复检合格：未领新袋撤出队列；已领新袋保留，不回收。
        withdrawn=s.scalars(select(BagReplacement).where(BagReplacement.pollination_id==x.id,BagReplacement.old_bag_code==x.bag_code,BagReplacement.state=='queued')).all()
        for item in withdrawn:
            item.state='withdrawn';item.reserve_seq=None
        if withdrawn:note='复检合格，未领新袋已撤出换袋队列并重排'
        else:note='复检合格，当前袋签可授权授粉'
        rerank(s,material)
        audit(s,u,'封口复检合格 '+d.code+' '+x.bag_code)
    else:
        item=open_replacement(s,x.id,x.bag_code)
        if not item:
            item=BagReplacement(code='BR-'+d.code,pollination_id=x.id,check_id=check.id,material=material,old_bag_code=x.bag_code,risk=risk,state='queued')
            s.add(item);s.flush();note='封口检查不合格，已按风险进入换袋队列'
        else:
            # 同一袋签沿用原预留，不重复建队；风险按最新一次检查刷新。
            item.risk=risk if risk is not None else item.risk
            note='封口检查不合格，沿用已在换袋队列中的预留顺序'
        rerank(s,material)
        audit(s,u,'封口复检不合格 '+d.code+' '+x.bag_code)
        if not model['fitted']:note+='；'+model['reason']
    s.flush()
    item=open_replacement(s,x.id,x.bag_code)
    return {'check':pack(check),'model':model,'note':note,'replacement':pack(item) if item else None}


def claim(s,id,u,on=None):
    pre=s.get(BagReplacement,id)
    if not pre:raise HTTPException(404,'换袋预留不存在')
    if pre.state=='claimed':
        # 重试幂等：不重复扣袋，也不改新袋签。
        return pack(pre)|{'replayed':True}
    if pre.state!='queued':raise HTTPException(409,'该预留已撤出换袋队列，不可领袋')
    r,x=_pollination(s,pre.pollination_id)
    if r.state!='issued' or x.state!='prepared':raise HTTPException(409,'仅未确认授粉的复壮单可领换袋')
    # 锁序与复检一致：复壮单 → 授粉单 → 袋材库存 → 换袋预留；领袋与重排因此串行。
    rerank(s,pre.material)
    item=lock(s,BagReplacement,id)
    if item.state=='claimed':return pack(item)|{'replayed':True}
    if item.state!='queued':raise HTTPException(409,'该预留已撤出换袋队列，不可领袋')
    head=s.scalar(select(BagReplacement).where(BagReplacement.material==item.material,BagReplacement.state=='queued').order_by(BagReplacement.reserve_seq,BagReplacement.id).limit(1))
    if not head or head.id!=item.id:raise HTTPException(409,'须先领取同袋材中风险更高的预留新袋')
    # 条件更新兜底：两个事务同时读到同一预留或同一库存时，只有一个能改到行。
    if s.execute(update(BagReplacement).where(BagReplacement.id==item.id,BagReplacement.state=='queued')
                 .values(state='claimed',reserve_seq=None)).rowcount!=1:
        s.rollback();raise HTTPException(409,'该预留已被领取，请刷新后核对；本次未扣袋')
    if s.execute(update(BagStock).where(BagStock.material==item.material,BagStock.available>=1)
                 .values(available=BagStock.available-1)).rowcount!=1:
        s.rollback();raise HTTPException(409,'该袋材新袋库存不足，本次未扣袋')
    stock=_stock(s,item.material)
    item=s.get(BagReplacement,id,populate_existing=True)
    item.new_bag_code='BAG-'+item.material.upper()+'-%04d'%item.id
    # 新袋上田：换签、重算在田起点；旧袋的合格检查不再授权授粉，须对新袋签重新检查。
    checked=s.get(BagCheck,item.check_id)
    x.bag_code=item.new_bag_code
    # 新袋上田日期取领袋日与不合格检查日中较晚者，避免在田天数为负。
    x.bag_since=max(checked.check_date,(on or date.today()).isoformat())
    x.bagged=False
    audit(s,u,'领取'+MATERIAL_LABELS[item.material]+'新袋 '+item.new_bag_code)
    rerank(s,item.material)
    s.flush()
    return pack(item)|{'stock_available':stock.available,'replayed':False}


def current_check(s,x):
    """授粉确认只认当前袋签最近一次合格检查。"""
    check=latest(s,x.id,x.bag_code)
    if not check:raise HTTPException(409,'当前袋签还没有封口检查，先检查或复检')
    if check.result!='pass':raise HTTPException(409,'当前袋签最近一次封口检查不合格，先换袋并复检')
    return check


def seal_snapshot(s,x,check):
    claimed=s.scalar(select(BagReplacement).where(BagReplacement.pollination_id==x.id,BagReplacement.new_bag_code==x.bag_code,BagReplacement.state=='claimed').order_by(BagReplacement.id.desc()))
    return {'bag_code':x.bag_code,'bag_material':x.bag_material,'bag_since':x.bag_since,'check':pack(check),'replacement':pack(claimed) if claimed else None,'confirmed_on':date.today().isoformat()}
