"""Schwab manual CSV and Gemini screenshot ingestion. No orders or authentication."""
from __future__ import annotations
import io
import json
import re
from datetime import date
import pandas as pd

COLUMNS=['expiration','strike','premium','bid','ask','iv','delta','volume','open_interest','option_symbol']
ALIASES={'expiry':'expiration','expiration date':'expiration','expiration yyyy-mm-dd':'expiration',
 'strike price':'strike','strike ($)':'strike','premium ($/share)':'premium','mark':'premium',
 'bid ($/share)':'bid','ask ($/share)':'ask','implied volatility':'iv','iv (decimal)':'iv',
 'delta (0-1)':'delta','oi':'open_interest','open interest':'open_interest',
 'option symbol':'option_symbol','schwab option symbol':'option_symbol'}
NUMERIC=['strike','premium','bid','ask','iv','delta','volume','open_interest']

def normalize(frame:pd.DataFrame, *, iv_percent=False, fill_premium=False)->pd.DataFrame:
    df=frame.copy()
    df.columns=[ALIASES.get(str(x).strip().lower(),str(x).strip().lower().replace(' ','_')) for x in df.columns]
    if df.columns.duplicated().any():raise ValueError('Duplicate normalized CSV columns; please remove duplicate headings.')
    for key in COLUMNS:
        if key not in df:df[key]=None
    df=df[COLUMNS].copy()
    for key in NUMERIC:df[key]=pd.to_numeric(df[key].replace('',None),errors='coerce')
    if iv_percent:df['iv']=df['iv']/100
    df['expiration']=df['expiration'].map(lambda x: str(x).strip()[:10] if pd.notna(x) else '')
    df['option_symbol']=df['option_symbol'].fillna('').astype(str).replace('nan','')
    if fill_premium:df['premium']=df['premium'].fillna(df['ask'])
    return df.reset_index(drop=True)

def parse_csv(content:bytes, *, iv_percent=False)->pd.DataFrame:
    if len(content)>2_000_000:raise ValueError('CSV exceeds 2 MB limit.')
    frame=pd.read_csv(io.BytesIO(content),sep=None,engine='python',encoding='utf-8-sig')
    headers={ALIASES.get(str(x).strip().lower(),str(x).strip().lower().replace(' ','_')) for x in frame.columns}
    if not {'expiration','strike','bid','ask'}.issubset(headers):
        raise ValueError('CSV requires expiration, strike, bid, and ask columns. Optional: premium, IV, delta, volume, OI, symbol.')
    if len(frame)>500:raise ValueError('Limit each import to 500 rows.')
    return normalize(frame,iv_percent=iv_percent,fill_premium=True)

def validate(frame:pd.DataFrame, *, expected_ticker='', as_of=None)->list[str]:
    df=normalize(frame)
    errors=[]; seen=set()
    as_of=as_of or date.today()
    for i,r in df.iterrows():
        ix=i+1
        try:
            ex=date.fromisoformat(r.expiration)
            if ex<as_of:errors.append(f'Row {ix}: expiration {r.expiration} is in the past.')
        except ValueError:errors.append(f'Row {ix}: expiration must be YYYY-MM-DD.')
        if pd.isna(r.strike) or r.strike<=0:errors.append(f'Row {ix}: strike must be positive.')
        if pd.isna(r.ask) or pd.isna(r.bid) or r.ask<=0 or r.bid<0 or r.bid>r.ask:
            errors.append(f'Row {ix}: require 0 ≤ bid ≤ ask with ask > 0.')
        if pd.isna(r.premium) or r.premium<=0:errors.append(f'Row {ix}: premium must be positive.')
        if pd.notna(r.iv) and not 0<r.iv<5:errors.append(f'Row {ix}: IV must be decimal (e.g. 0.405 for 40.5%).')
        if pd.notna(r.delta) and not 0<=r.delta<=1:errors.append(f'Row {ix}: call delta must be between 0 and 1.')
        for k in ['volume','open_interest']:
            if pd.notna(r[k]) and (r[k]<0 or r[k]%1!=0):errors.append(f'Row {ix}: {k} must be a nonnegative whole number.')
        if pd.notna(r.strike) and r.expiration:
            key=(r.expiration,float(r.strike))
            if key in seen:errors.append(f'Row {ix}: duplicate expiration and strike.')
            seen.add(key)
        if r.option_symbol and expected_ticker and not r.option_symbol.upper().lstrip('.').startswith(expected_ticker.upper()):
            errors.append(f'Row {ix}: option symbol does not appear to match {expected_ticker}.')
    return errors

def extract_gemini(files:list[tuple[str,bytes,str]], api_key:str, model:str='gemini-2.5-flash')->tuple[pd.DataFrame,list[str]]:
    """Return candidate rows only, never silently approve or import them."""
    from google import genai
    from google.genai import types
    if not files:raise ValueError('Upload at least one screenshot.')
    parts=[types.Part.from_text(text=(
      'Extract ONLY call-side options chain data from the supplied Schwab screenshots. '
      'Screenshots may show different columns and expiration groups. Join rows ONLY when expiration '
      'and strike both match. Do not substitute put-side fields for calls. Do not guess or fill missing data. '
      'For each contract return expiration YYYY-MM-DD, strike, bid, ask, premium (null unless explicitly '
      'shown), iv (decimal, convert screenshot IV percent by dividing by 100), delta, volume, '
      'open_interest, option_symbol. Use null for unavailable fields. '
      'If expiry is not visible, do not include its rows: describe the issue in warnings. '
      'Ignore rows where the relevant date or strike is ambiguous. '
      'Reply only with a JSON object with keys rows (array of objects) and warnings (array of strings).'))]
    for name,data,mime in files:
        if len(data)>8_000_000:raise ValueError(f'{name} exceeds 8 MB.')
        parts.append(types.Part.from_text(text=f'File: {name}'))
        parts.append(types.Part.from_bytes(data=data,mime_type=mime))
    client=genai.Client(api_key=api_key)
    response=client.models.generate_content(model=model,contents=parts,
        config=types.GenerateContentConfig(response_mime_type='application/json',temperature=0))
    output=json.loads(response.text)
    if not isinstance(output,dict) or not isinstance(output.get('rows'),list):
        raise ValueError('Gemini did not return a usable rows array.')
    if len(output['rows'])>200:raise ValueError('Gemini returned too many rows; split your screenshots.')
    parsed=normalize(pd.DataFrame(output['rows']),fill_premium=False)
    # Preserve missing premium in review. User may explicitly choose ask as premium.
    return parsed,[str(w) for w in output.get('warnings',[])][:30]
