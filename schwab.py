"""Read-only Schwab Trader API option chain adapter. Never sends orders."""
from __future__ import annotations
import pandas as pd
import requests

CHAIN_URL = 'https://api.schwabapi.com/marketdata/v1/chains'

def parse_schwab_calls(data: dict) -> pd.DataFrame:
    columns=['expiration','strike','premium','bid','ask','iv','delta','volume','open_interest','option_symbol']
    rows=[]
    for expiry_key, strikes in (data.get('callExpDateMap') or {}).items():
        expiry=expiry_key.split(':',1)[0]
        for strike_key, contracts in strikes.items():
            for c in contracts:
                # Skip adjusted contracts: this model assumes standard 100-share multiplier.
                if int(c.get('multiplier') or 100)!=100: continue
                bid=float(c.get('bid') or 0); ask=float(c.get('ask') or 0)
                if bid<=0 or ask<=0 or ask<bid: continue
                iv=c.get('volatility'); iv=(float(iv)/100 if iv is not None and float(iv)>3 else iv)
                rows.append({'expiration':expiry,'strike':float(c.get('strikePrice') or strike_key),
                             'premium':ask,'bid':bid,'ask':ask,'iv':iv,'delta':c.get('delta'),
                             'volume':c.get('totalVolume'),'open_interest':c.get('openInterest'),
                             'option_symbol':c.get('symbol','')})
    return pd.DataFrame(rows,columns=columns)

def fetch_schwab_calls(symbol: str, token: str, start: str, end: str) -> pd.DataFrame:
    if not symbol.isalnum(): raise ValueError('Ticker must be alphanumeric')
    response=requests.get(CHAIN_URL,params={'symbol':symbol,'contractType':'CALL',
        'strategy':'SINGLE','fromDate':start,'toDate':end,'strikeCount':20},
        headers={'Authorization':f'Bearer {token}','Accept':'application/json'},timeout=20)
    if response.status_code in (401,403): raise PermissionError('Token expired or lacks Market Data entitlement. Reauthorize through Schwab Developer Portal.')
    response.raise_for_status()
    return parse_schwab_calls(response.json())
