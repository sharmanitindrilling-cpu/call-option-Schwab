"""Explainable research agents. No brokerage integration or trade execution."""
from __future__ import annotations
import math
from datetime import date
from statistics import NormalDist
import numpy as np
import pandas as pd

N = NormalDist()

def technical_agent(close: pd.Series) -> dict:
    close = pd.to_numeric(close, errors='coerce').dropna().astype(float)
    if len(close) < 65 or (close <= 0).any():
        raise ValueError('At least 65 valid positive daily closing prices are required.')
    ret = np.log(close / close.shift(1)).dropna()
    vol = float(ret.tail(60).std(ddof=1) * math.sqrt(252))
    ma20 = float(close.tail(20).mean()); ma50 = float(close.tail(50).mean())
    delta = close.diff(); up = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    loss = float(down.iloc[-1]); gain = float(up.iloc[-1])
    rsi = 100.0 if loss == 0 else 100 - 100/(1 + gain/loss)
    change20 = float(close.iloc[-1] / close.iloc[-21] - 1)
    votes = [close.iloc[-1] > ma20, ma20 > ma50, change20 > 0, 40 <= rsi <= 70]
    strength = int(sum(votes))
    return {'spot':float(close.iloc[-1]), 'ma20':ma20, 'ma50':ma50,
            'rsi14':rsi, 'return20':change20, 'hist_vol60':vol,
            'technical_votes':strength, 'trend': 'Bullish' if strength >= 3 else ('Mixed' if strength == 2 else 'Weak')}

def option_agent(row:dict, spot:float, hist_vol:float, today:date, assumed_drift:float=0.0) -> dict:
    strike=float(row['strike']); premium=float(row['premium']); bid=float(row.get('bid') or 0); ask=float(row.get('ask') or 0)
    expiry=pd.to_datetime(row['expiration']).date(); days=(expiry-today).days
    if not (spot>0 and strike>0 and premium>0 and days>0):
        raise ValueError('Invalid quote: spot, strike and premium must be positive and expiration must be future.')
    if (bid<0 or ask<0 or (bid and ask and bid>ask)):
        raise ValueError('Bid/ask data is invalid.')
    t=days/365.25; sigma=max(0.01,float(hist_vol))
    # Deliberately assumption-based *physical* scenario distribution, NOT calibrated odds.
    mu=math.log(spot) + (assumed_drift-0.5*sigma*sigma)*t
    sd=sigma*math.sqrt(t)
    p_itm = 1-N.cdf((math.log(strike)-mu)/sd)
    breakeven=strike+premium
    p_profit=1-N.cdf((math.log(breakeven)-mu)/sd)
    # E[(S-K)+] under modeled lognormal scenario, net premium.
    d2=(mu-math.log(strike))/sd
    exp_st=math.exp(mu+0.5*sd*sd)
    call_expect=exp_st*N.cdf(d2+sd)-strike*N.cdf(d2)
    ev=100*(call_expect-premium)
    itm=max(0,spot-strike)
    spread_pct=((ask-bid)/((bid+ask)/2)*100) if bid>0 and ask>0 else None
    delta=row.get('delta', None); iv=row.get('iv', None)
    try: delta=float(delta) if delta not in ('',None) and not pd.isna(delta) else None
    except (ValueError,TypeError): delta=None
    try: iv=float(iv) if iv not in ('',None) and not pd.isna(iv) else None
    except (ValueError,TypeError): iv=None
    notes=[]
    if spread_pct is None: notes.append('Bid/ask not provided; execution liquidity not verified')
    elif spread_pct>12: notes.append('Wide displayed spread (>12%)')
    if premium < itm-0.05: notes.append('Premium below intrinsic value; verify quote timestamp')
    if iv is not None and (iv <= 0 or iv>5): notes.append('Implausible IV; use decimal format (e.g. 0.45)')
    if delta is not None and not 0 <= delta <= 1: notes.append('Call delta should be between 0 and 1')
    if days <= 7: notes.append('Very short DTE: elevated gamma and time-decay risk')
    return {'expiration':str(expiry),'strike':strike,'premium':premium,'dte':days,
            'break_even':breakeven,'max_loss':premium*100,'intrinsic':itm,
            'extrinsic':premium-itm,'p_itm_model':p_itm,'p_profit_model':p_profit,
            'expected_pnl_model':ev,'spread_pct':spread_pct,'delta':delta,'iv':iv,
            'warnings':notes,
            'pnl_if_minus10':(max(0,spot*.90-strike)-premium)*100,
            'pnl_if_flat':(max(0,spot-strike)-premium)*100,
            'pnl_if_plus10':(max(0,spot*1.10-strike)-premium)*100}

def risk_agent(options:list[dict], capital:float, max_risk_pct:float, asof_ok:bool, event_flag:bool) -> tuple[list[dict],str]:
    budget=capital*max_risk_pct/100
    results=[]
    for o in options:
        disqualifiers=[]
        if o['max_loss']>budget: disqualifiers.append('Premium exceeds per-trade risk budget')
        if not asof_ok: disqualifiers.append('Market and quote timestamps not confirmed')
        if o['spread_pct'] is None: disqualifiers.append('Bid/ask missing')
        elif o['spread_pct']>12: disqualifiers.append('Spread above 12%')
        if o['dte']<=7: disqualifiers.append('DTE of 7 days or less')
        if any('verify quote' in s or 'Implausible' in s or 'delta should' in s for s in o['warnings']):
            disqualifiers.append('Quote consistency issue')
        if event_flag: disqualifiers.append('Unresolved event risk flagged')
        o={**o, 'eligible':not disqualifiers, 'blockers':disqualifiers,
           'rank_score':round(100*o['p_profit_model'] +
                              max(-20,min(20, o['expected_pnl_model']/max(1,o['max_loss'])*20)) -
                              min(15,(o['spread_pct'] or 15)/2),1)}
        results.append(o)
    results.sort(key=lambda v:(not v['eligible'], -v['rank_score']))
    conclusion='NO TRADE / INSUFFICIENT EVIDENCE' if not any(x['eligible'] for x in results) else 'CANDIDATES FOR FURTHER REVIEW — NOT A BUY SIGNAL'
    return results, conclusion
