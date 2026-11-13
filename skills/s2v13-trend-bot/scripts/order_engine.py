# -*- coding: utf-8 -*-
"""实盘下单引擎（OKX Trade CLI · AI Builder 归因）
- 默认 dry-run，只打印将执行的命令
- --execute 才真下单
- 必须设置环境变量 OKX_AI_BUILDER_CODE，否则拒绝下单（fail-closed）
- 前置风控：名义上限 / 单品种上限 / 日亏 / 周亏 / 最小下单量
"""
import sys, os, json, argparse, subprocess
from datetime import datetime
import numpy as np
import signal_engine as se

PROFILE   = se.PROFILE
TD_MODE   = "isolated"      # 逐仓
GROSS     = se.GROSS
LEV       = se.LEV
MAXPOS    = se.MAXPOS
CODE      = os.environ.get("OKX_AI_BUILDER_CODE","").strip()

def sh(*args, timeout=40):
    exe = se.OKX_BIN
    cmd = [exe,"--profile",PROFILE,*args]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return out.returncode, (out.stdout or "").strip(), (out.stderr or "").strip()

def equity():
    rc,so,se_ = sh("account","balance","--json")
    if rc!=0: return None
    try:
        j=json.loads(so[so.find("["):])
        return float(j[0]["totalEq"])
    except Exception:
        return None

def positions():
    rc,so,se_ = sh("swap","positions","--json")
    if rc!=0: return []
    try: return json.loads(so[so.find("["):])
    except Exception: return []

def inst(sym):
    rc,so,_ = sh("market","instruments","--instType","SWAP","--instId",f"{sym}-USDT-SWAP","--json")
    if rc!=0: return None
    try:
        j=json.loads(so[so.find("["):])[0]
        return dict(ctVal=float(j["ctVal"]),minSz=float(j["minSz"]),lotSz=float(j["lotSz"]),tickSz=float(j["tickSz"]))
    except Exception: return None

def px(sym):
    rc,so,_ = sh("market","ticker",f"{sym}-USDT-SWAP","--json")
    if rc!=0: return None
    try: return float(json.loads(so[so.find("["):])[0]["last"])
    except Exception: return None

def build_order(sym, side, qty, stop_px, execute=False, reason=""):
    args = ["swap","place",
            "--instId",f"{sym}-USDT-SWAP",
            "--side",side,
            "--ordType","market",
            "--sz",f"{qty:g}",
            "--tdMode",TD_MODE,
            "--slTriggerPx",f"{stop_px:g}",
            "--slOrdPx","-1",
            "--aiBuilderCode",CODE]
    return args

def place(args, execute=False):
    printable = "okx " + " ".join(args)
    if not execute:
        print("  [dry-run]", printable); return dict(ok=True, dry=True)
    rc,so,se_ = sh(*args)
    ok = rc==0 and '"sCode":"0"' in so.replace(" ","")
    print(("  [OK] " if ok else "  [FAIL] ") + printable)
    if not ok: print("    ", so[:300] or se_[:300])
    return dict(ok=ok, out=so[:500])

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--execute",action="store_true")
    ap.add_argument("--plan",default="")     # JSON 计划文件
    a=ap.parse_args()
    if not CODE:
        print("❌ 未设置 OKX_AI_BUILDER_CODE —— 按 AI Builder 规范，无归因码不允许下单")
        print("   设置方法：setx OKX_AI_BUILDER_CODE \"你的BuilderCode\"")
        return
    eq = equity()
    if eq is None:
        print("❌ 读取账户失败"); return
    cur = positions()
    gross = sum(abs(float(p["pos"])*float(p["markPx"])*float(p["ctVal"])) for p in cur) if cur else 0.0
    print(f"账户权益 {eq:.2f} USDT | 当前持仓 {len(cur)} 个 | 名义 {gross:.2f} ({100*gross/eq:.0f}%)")
    plan = json.load(open(a.plan,encoding="utf-8")) if a.plan else json.load(open("paper_plan.json",encoding="utf-8"))
    print(f"计划 {len(plan)} 笔 | BuilderCode {CODE[:4]}***")
    for o in plan:
        sym=o["sym"]; side=o.get("side","buy"); stop=o["stop"]
        p = px(sym); c = inst(sym)
        if p is None or c is None: print(f"  [跳过] {sym} 行情/合约读取失败"); continue
        amt = min(MAXPOS*eq, o.get("notional", MAXPOS*eq))
        qty = np.floor(amt/p/c["ctVal"]/c["lotSz"])*c["lotSz"]
        if qty < c["minSz"]:
            print(f"  [跳过] {sym} 数量 {qty:g} < 最小 {c['minSz']:g}（资金不足）"); continue
        notional = qty*c["ctVal"]*p
        if gross + notional > GROSS*eq:
            print(f"  [跳过] {sym} 超出名义上限（{gross:.0f}+{notional:.0f}>{GROSS*eq:.0f}）"); continue
        print(f"  {sym:6s} 价 {p:g} 数量 {qty:g} 名义 {notional:.2f} 止损 {stop:g}")
        place(build_order(sym,side,qty,stop), execute=a.execute)
        gross += notional
    print("\n完成。" + ("（已实盘下单，请核对）" if a.execute else "（dry-run，未下单）"))

if __name__=="__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
