# -*- coding: utf-8 -*-
"""S2v13 + DT1 实盘信号引擎（只生成计划，不下单）
用法：
  python signal_engine.py            # 生成信号报告
  python signal_engine.py --json     # 输出 JSON 计划
数据源：okx CLI  market candles <instId> --bar 4H --limit N --json
"""
import sys, io, os, json, subprocess, argparse
from datetime import datetime, timezone
import numpy as np, pandas as pd
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

PROFILE = "策略接口1"
CORE = ["BTC","ETH","XRP","BNB","SOL","DOGE","ADA","TRX","LINK","DOT"]
ALT  = ["ZEC","PEPE","SAND","MANA","NEAR","SEI","ALGO","HBAR","UNI"]
SYMS = CORE + ALT
MEME = {"DOGE","PEPE","ZEC"}          # 道氏入场 + 日线趋势线出场
GROSS = 1.5        # 名义上限（×权益）
LEV = 3.0          # 逐仓杠杆
MAXPOS = 0.5       # 单品种上限（×权益）
MIN_BARS = 6       # 24h 最小持仓
DD_GATE = 0.20     # 账户回撤触发 EMA200 过滤
BATCH = 400        # 拉取 K 线根数

OKX_BIN = os.path.join(os.environ.get("APPDATA", r"C:\Users\nie\AppData\Roaming"), "npm", "okx.cmd")
def okx(*args, timeout=40):
    exe = OKX_BIN if os.path.exists(OKX_BIN) else "okx.cmd"
    cmd = [exe,"--profile",PROFILE,*args,"--json"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=False)
        if out.returncode != 0:
            if os.environ.get("SIGDEBUG"): print("[okx err]", (out.stderr or "")[:200])
            return None
        s = out.stdout.strip()
        i = s.find("[")
        if i < 0: return None
        return json.loads(s[i:])
    except Exception as e:
        if os.environ.get("SIGDEBUG"): print("[okx exc]", e)
        return None

def fetch(inst, bar="4H", n=300):
    j = okx("market","candles",inst,"--bar",bar,"--limit",str(n))
    if not j: return None
    rows = []
    for r in j:
        rows.append([int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])])
    df = pd.DataFrame(rows, columns=["ts","open","high","low","close"]).sort_values("ts").reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def ema(x,n): return pd.Series(x,dtype=float).ewm(span=n,adjust=False).mean()
def atr(df,n=60):
    pc = df.close.shift(1)
    tr = pd.concat([df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean()

def indicators(df):
    d = df.copy()
    d["e8"],d["e100"],d["e200"] = ema(d.close,8),ema(d.close,100),ema(d.close,200)
    d["atr60"] = atr(d,60)
    d["death"] = (d.e8.shift(1)>=d.e100.shift(1)) & (d.e8<d.e100)
    # 3 根 pivot high（已滞后 3 根，无未来函数）
    ph = pd.Series(np.nan,index=d.index)
    h = d.high.to_numpy()
    for i in range(3,len(d)-3):
        if h[i]==h[i-3:i+4].max(): ph.iloc[i]=h[i]
    d["lastH"] = ph.shift(3).ffill()      # 只使用已确认的 pivot
    # 日线趋势线
    dd = d.set_index("ts").resample("1D").agg({"high":"max","low":"min","close":"last"}).dropna()
    lo = dd.low.to_numpy(); pv = np.full(len(dd),np.nan)
    for i in range(2,len(dd)-2):
        if lo[i]==lo[i-2:i+3].min(): pv[i]=lo[i]
    tl = np.full(len(dd),np.nan); last=[]
    for i in range(len(dd)):
        j=i-2
        if j>=0 and not np.isnan(pv[j]): last.append((j,pv[j])); last=last[-2:]
        if len(last)==2:
            (t1,p1),(t2,p2)=last
            if t2>t1: tl[i]=p2+(p2-p1)*(i-t2)/(t2-t1)
    tld = pd.Series(tl,index=dd.index).shift(1).reindex(d.ts.dt.normalize()).ffill()
    d["tld"] = tld.to_numpy()
    # 月波动率：日收益 30 日标准差
    mv = dd.close.pct_change().rolling(30).std().reindex(d.ts.dt.normalize()).ffill()
    d["mvol"] = mv.to_numpy()
    return d

def pivot_depth(d):
    h=d.high.to_numpy(); l=d.low.to_numpy()
    ph=np.full(len(d),np.nan); pl=np.full(len(d),np.nan)
    for i in range(3,len(d)-3):
        if h[i]==h[i-3:i+4].max(): ph[i]=h[i]
        if l[i]==l[i-3:i+4].min(): pl[i]=l[i]
    hi=np.where(~np.isnan(ph))[0]; lo=np.where(~np.isnan(pl))[0]
    dep=[]
    for k in hi:
        nx=lo[lo>k]
        if len(nx): dep.append((h[k]-l[nx[0]])/h[k])
    return float(np.median(dep)) if dep else 0.05

def daily_tl(df1d):
    """日线趋势线（下沿两点连线，shift 1 避免未来函数）"""
    lo = df1d.low.to_numpy(); n=len(lo)
    pv = np.full(n,np.nan)
    for i in range(2,n-2):
        if lo[i]==lo[i-2:i+3].min(): pv[i]=lo[i]
    tl=np.full(n,np.nan); last=[]
    for i in range(n):
        j=i-2
        if j>=0 and not np.isnan(pv[j]): last.append((j,pv[j])); last=last[-2:]
        if len(last)==2:
            (t1,p1),(t2,p2)=last
            if t2>t1: tl[i]=p2+(p2-p1)*(i-t2)/(t2-t1)
    return float(pd.Series(tl).shift(1).iloc[-1])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--insts", default="")
    a = ap.parse_args()
    syms = a.insts.split(",") if a.insts else SYMS
    data = {}
    for s in syms:
        inst = f"{s}-USDT-SWAP"
        df = fetch(inst,"4H",300)
        if df is None or len(df) < 250:
            print(f"[跳过] {s} 数据不足"); continue
        d = indicators(df)
        d.attrs["pb"] = pivot_depth(df)
        data[s] = d
    if "BTC" not in data:
        print("[错误] 缺少 BTC，无法判断牛熊"); return
    btc = data["BTC"]
    # 200 日均线：用日线（4H 只能取到 300 根 = 50 天）
    bd = fetch("BTC-USDT-SWAP","1D",300)
    if bd is None or len(bd) < 210:
        print("[错误] BTC 日线不足"); return
    bma200 = bd.close.rolling(200).mean()
    bull = bool(bd.close.iloc[-1] > bma200.iloc[-1]) if pd.notna(bma200.iloc[-1]) else False
    btc_ma200 = float(bma200.iloc[-1]) if pd.notna(bma200.iloc[-1]) else None
    btc_px = float(bd.close.iloc[-1])
    # meme 品种日线趋势线
    tld_map={}
    for s in MEME:
        if s not in data: continue
        d1 = fetch(f"{s}-USDT-SWAP","1D",300)
        if d1 is not None and len(d1)>20: tld_map[s]=daily_tl(d1)
    mvol_btc = btc.mvol.iloc[-1]
    rows=[]
    for s,d in data.items():
        last = d.iloc[-1]; prev = d.iloc[-2]
        pb = d.attrs["pb"]
        if s in MEME:
            entry = (last.close > last.lastH) and (prev.close <= prev.lastH) and (last.close > last.e100) and np.isfinite(last.lastH)
            entry_type = "道氏破前高"
            exit_sig = (s in tld_map) and np.isfinite(tld_map[s]) and last.close < tld_map[s]
        else:
            entry = (last.e8 > last.e100 > last.e200) and (last.close > last.e100)
            entry_type = "三线排列"
            exit_sig = False
        hr = (not last.death) and False
        stop = max(last.close - 3*last.atr60, last.close*(1-1.2*pb))
        ratio = min(1.0, max(0.15, mvol_btc/last.mvol)) if np.isfinite(last.mvol) and last.mvol>0 and np.isfinite(mvol_btc) else 0.5
        rows.append(dict(sym=s, close=float(last.close), e8=float(last.e8), e100=float(last.e100), e200=float(last.e200),
                         atr60=float(last.atr60), stop=float(stop), stop_pct=float(stop/last.close-1),
                         mvol=float(last.mvol) if np.isfinite(last.mvol) else None, ratio=float(ratio),
                         entry=bool(entry), entry_type=entry_type, exit=bool(exit_sig),
                         death=bool(last.death), above200=bool(last.close>last.e200),
                         bars_ago=0))
    out = dict(time=str(datetime.now(timezone.utc).astimezone())[:19], bull=bool(bull),
               btc_close=btc_px, btc_ma200=btc_ma200,
               signal_bar=str(btc.ts.iloc[-1]), rows=rows)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1)); return
    print(f"\n{'='*96}")
    print(f"信号时间 {out['time']}  K线 {out['signal_bar']}")
    _m = out['btc_ma200']
    _ms = f"{_m:.1f}" if _m is not None else "n/a"
    print(f"BTC {out['btc_close']:.1f} vs MA200 {_ms}  →  闸门: {'✅ 牛市(可开仓)' if bull else '❌ 熊市(不开新仓)'}")
    print(f"{'='*96}")
    print(f"{'品种':6s}{'收盘':>11s}{'EMA100':>11s}{'EMA200':>11s}{'ATR%':>7s}{'止损%':>7s}{'月波':>7s}{'权重':>6s}{'入场':>6s}{'类型':>10s}")
    def fp(x):
        return f"{x:.6f}" if x<0.01 else (f"{x:.4f}" if x<1 else f"{x:.2f}")
    for r in sorted(rows,key=lambda x:-x["ratio"]):
        print(f"{r['sym']:6s}{fp(r['close']):>11s}{fp(r['e100']):>11s}{fp(r['e200']):>11s}"
              f"{100*r['atr60']/r['close']:6.1f}%{100*r['stop_pct']:6.1f}%"
              f"{(100*r['mvol'] if r['mvol'] else 0):6.1f}%{r['ratio']:6.2f}"
              f"{'  ★' if r['entry'] else '   -'}{r['entry_type'] if r['entry'] else '':>10s}")
    ent=[r for r in rows if r["entry"]]
    print(f"\n信号汇总：可开仓 {len(ent)} 个 → {[r['sym'] for r in ent] if ent else '无'}")
    if not bull: print("⚠️ 熊市闸门关闭，今天不开任何新仓")
    os.makedirs(os.path.dirname(__file__)+"/../runs",exist_ok=True)
    with open(os.path.dirname(__file__)+"/../runs/signal_last.json","w",encoding="utf-8") as f:
        json.dump(out,f,ensure_ascii=False,indent=1)
    print(f"已保存 runs/signal_last.json")

if __name__=="__main__":
    main()
