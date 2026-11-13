# -*- coding: utf-8 -*-
"""本地模拟盘（paper trading）：用真实行情驱动，逐步执行与回测完全一致的规则
状态文件：live/paper_state.json
用法：python paper_engine.py [--init 500] [--json]
"""
import sys, os, json, argparse
from datetime import datetime, timezone
import numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
sys.path.insert(0, os.path.dirname(__file__))
import signal_engine as se

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "paper_state.json")
COMM = 0.0005; SLIP = 0.0005; FUND = 0.10/365/6

def load_state(init=500.0):
    if os.path.exists(STATE):
        with open(STATE,encoding="utf-8") as f: return json.load(f)
    return dict(init_capital=init, cash=init, positions={}, hist=[], weekly_losses={}, last_bar=None)

def save_state(st):
    with open(STATE,"w",encoding="utf-8") as f: json.dump(st,f,ensure_ascii=False,indent=1)

def contract(sym):
    j = se.okx("market","instruments","--instType","SWAP","--instId",f"{sym}-USDT-SWAP")
    if not j: return None
    return dict(ctVal=float(j[0]["ctVal"]), minSz=float(j[0]["minSz"]), lotSz=float(j[0]["lotSz"]))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--init",type=float,default=500.0)
    ap.add_argument("--json",action="store_true"); a=ap.parse_args()
    st = load_state(a.init)
    # 1) 拉数据 & 算信号
    data={}
    for s in se.SYMS:
        df = se.fetch(f"{s}-USDT-SWAP","4H",300)
        if df is None or len(df)<250: continue
        d = se.indicators(df); d.attrs["pb"]=se.pivot_depth(df); data[s]=d
    bd = se.fetch("BTC-USDT-SWAP","1D",300)
    bma = bd.close.rolling(200).mean()
    bull = bool(bd.close.iloc[-1] > bma.iloc[-1])
    btc_ma200 = float(bma.iloc[-1])
    tld_map={}
    for s in se.MEME:
        if s not in data: continue
        d1=se.fetch(f"{s}-USDT-SWAP","1D",300)
        if d1 is not None and len(d1)>20: tld_map[s]=se.daily_tl(d1)
    bar = str(next(iter(data.values())).ts.iloc[-1])
    px = {s: float(d.close.iloc[-1]) for s,d in data.items()}
    pos = st["positions"]
    events=[]
    # 2) 盯市
    def mtm():
        v=0.0
        for s,p in pos.items():
            cur = px.get(s, p["entry"])
            v += p["margin"] + p["qty"]*(cur-p["entry"])
        return st["cash"]+v
    eq0 = mtm()
    # 3) 出场
    for s in list(pos):
        if s not in data: continue
        d=data[s]; last=d.iloc[-1]; p=pos[s]
        held = (pd.Timestamp(bar)-pd.Timestamp(p["entry_bar"])).total_seconds()/3600
        can = held>=24
        act=None; why=""
        if last.close <= p["stop"]: act="all"; why=f"止损 {p['stop']:.6g}"
        elif can and s in se.MEME and s in tld_map and np.isfinite(tld_map[s]) and last.close<tld_map[s]:
            act="all"; why="日线趋势线破位"
        elif can and s not in se.MEME and bool(last.death):
            mfe = p.get("mfe", p["entry"])/p["entry"]-1
            if not p.get("halved") and mfe>=0.10 and last.close>last.e200:
                act="half"; why="死叉减半(MFE≥10%)"
            else: act="all"; why="死叉全平"
        if act:
            q = p["qty"]*(0.5 if act=="half" else 1.0)
            mg = p["margin"]*(0.5 if act=="half" else 1.0)
            pnl = q*(last.close-p["entry"])
            st["cash"] += mg+pnl-q*last.close*COMM
            if pnl<=0:
                wk=str(pd.Timestamp(bar).isocalendar()[:2])
                st["weekly_losses"].setdefault(s,{}); st["weekly_losses"][s][wk]=st["weekly_losses"][s].get(wk,0)+1
            events.append(dict(act=act,sym=s,px=last.close,pnl=round(pnl,4),why=why))
            if act=="all": del pos[s]
            else: p["qty"]-=q; p["margin"]-=mg; p["halved"]=True
    eq1 = mtm()
    # 4) 入场
    dd = 1-eq1/max(st.get("peak",eq1),eq1)
    st["peak"]=max(st.get("peak",eq1),eq1)
    if bull:
        cands=[]
        for s,d in data.items():
            if s in pos: continue
            last=d.iloc[-1]; prev=d.iloc[-2]
            if s in se.MEME:
                ok = np.isfinite(last.lastH) and last.close>last.lastH and prev.close<=prev.lastH and last.close>last.e100
            else:
                ok = last.e8>last.e100>last.e200 and last.close>last.e100
            if dd>=se.DD_GATE and not (last.close>last.e200): ok=False
            if ok: cands.append(s)
        if cands:
            notional = sum(p["qty"]*px.get(s,p["entry"]) for s,p in pos.items())
            room = se.GROSS*eq1 - notional
            mvb = se.mvol_btc if hasattr(se,"mvol_btc") else None
            if room>0.02*eq1:
                btc_mv = float(data["BTC"].mvol.iloc[-1])
                rat=[]
                for s in cands:
                    mv=float(data[s].mvol.iloc[-1])
                    rat.append(min(1.0,max(0.15,btc_mv/mv)) if np.isfinite(mv) and mv>0 else 0.5)
                rat=np.array(rat); wt=rat/rat.sum()*room
                for s,w_ in zip(cands,wt):
                    amt=min(w_, se.MAXPOS*eq1)
                    if amt<0.01*eq1: continue
                    cs=contract(s)
                    if not cs: continue
                    p0=px[s]; qty=round(amt/p0/cs["ctVal"]/cs["lotSz"])*cs["lotSz"]*cs["ctVal"]
                    if qty<=0: continue
                    notional_i=qty*p0; mg=notional_i/se.LEV
                    if mg>st["cash"]: continue
                    st["cash"] -= mg + notional_i*COMM
                    d=data[s]; last=d.iloc[-1]
                    stop=max(last.close-3*last.atr60, last.close*(1-1.2*d.attrs["pb"]))
                    pos[s]=dict(entry=p0,qty=qty,margin=mg,stop=float(stop),entry_bar=bar,halved=False,mfe=p0)
                    events.append(dict(act="open",sym=s,px=p0,qty=qty,notional=round(notional_i,2),stop=round(stop,6)))
    eq2 = mtm()
    st["hist"].append(dict(bar=bar,eq=round(eq2,4),n=len(pos)))
    st["last_bar"]=bar
    save_state(st)
    out=dict(bar=bar,bull=bull,btc=float(bd.close.iloc[-1]),ma200=btc_ma200,equity=round(eq2,4),
             cash=round(st["cash"],4),positions={s:dict(qty=p["qty"],entry=p["entry"],stop=p["stop"]) for s,p in pos.items()},
             events=events)
    # 落日志（供任务健康监控）
    try:
        os.makedirs(os.path.join(HERE,"logs"),exist_ok=True)
        with open(os.path.join(HERE,"logs","paper.log"),"a",encoding="utf-8") as lf:
            lf.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] bar={bar} bull={bull} eq={eq2:.2f} pos={len(pos)} events={len(events)}\n")
    except Exception as e:
        pass
    if a.json: print(json.dumps(out,ensure_ascii=False,indent=1)); return
    print(f"\n{'='*80}\n模拟盘  {bar}   BTC {out['btc']:.0f} / MA200 {ma200_ if (ma200_:=out['ma200']) else 0:.0f}  → {'牛市' if bull else '熊市(不开仓)'}")
    print(f"权益 {eq2:.2f} USDT（初始 {st['init_capital']:.0f}，{100*(eq2/st['init_capital']-1):+.2f}%）  现金 {st['cash']:.2f}  持仓 {len(pos)}")
    if events:
        print("本次事件：")
        for e in events: print("  ", e)
    else:
        print("本次无交易动作")
    if pos:
        print("当前持仓：")
        for s,p in pos.items(): print(f"   {s:6s} qty={p['qty']:.6g}  entry={p['entry']:.6g}  stop={p['stop']:.6g}  浮盈={p['qty']*(px.get(s,p['entry'])-p['entry']):+.2f}")

if __name__=="__main__": main()
