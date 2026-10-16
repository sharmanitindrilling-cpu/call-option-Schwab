from __future__ import annotations
from datetime import date, datetime
import io
import json
import os
import pandas as pd
import streamlit as st
import yfinance as yf
from agents.schwab import fetch_schwab_calls, parse_schwab_calls
from agents.core import technical_agent, option_agent, risk_agent
from agents.v2 import walk_forward_signal_test, get_yahoo_events, research_packet, gemini_research_brief

st.set_page_config(page_title='Options Research Desk', page_icon='📊', layout='wide')
st.title('📊 Options Research Desk — V2')
st.caption('Explainable multi-agent call-option research • Schwab.com manual quote entry + optional read-only Schwab API • No trading or broker access')
st.warning('Educational scenario estimates, not investment recommendations. Model probabilities are **not calibrated forecasts**; an option can lose 100% of its premium.')

@st.cache_data(ttl=900,show_spinner=False)
def load_prices(symbol):
    history=yf.Ticker(symbol).history(period='1y',interval='1d',auto_adjust=True)
    if history.empty or 'Close' not in history:
        raise ValueError('Historical prices not returned by Yahoo Finance. Try again or upload a CSV.')
    return history

with st.sidebar:
    st.header('Analysis inputs')
    symbol=st.text_input('Ticker', 'NVDA').strip().upper()
    capital=st.number_input('Options risk capital ($)',min_value=100.,value=10000.,step=500.)
    risk_pct=st.slider('Maximum loss per trade (% of risk capital)',0.5,10.0,2.0,0.5)
    st.caption(f'Maximum acceptable premium loss: ${capital*risk_pct/100:,.0f}')
    price_source=st.radio('Historical prices', ['Yahoo Finance (research feed)','Upload daily price CSV'])
    file=st.file_uploader('Upload CSV containing Date and Close columns',type='csv') if price_source.startswith('Upload') else None
    st.divider()
    st.markdown('**Model assumptions**')
    drift=st.number_input('Assumed annualized price drift (%)',value=0.0,min_value=-50.0,max_value=50.0,step=1.0)/100
    use_override=st.checkbox('Override model volatility')
    vol_override=st.number_input('Annualized volatility (%)',value=50.0,min_value=5.0,max_value=250.0,step=5.0)/100 if use_override else None
    st.caption('Default drift is 0%. Historical volatility is backward-looking and can understate event risk.')
    st.divider()
    backtest_horizon=st.selectbox('Backtest direction horizon (trading days)',[5,10,20,40],index=2)
    use_yahoo_events=st.checkbox('Attempt Yahoo headlines and earnings metadata',value=False,help='Optional and not guaranteed; may be unavailable or incomplete.')

prices=None; err=None
try:
    if file is not None:
        raw=pd.read_csv(file)
        if not {'Date','Close'}.issubset(raw.columns): raise ValueError('CSV needs Date and Close columns')
        raw['Date']=pd.to_datetime(raw['Date'],utc=True,errors='coerce')
        prices=raw.dropna(subset=['Date','Close']).sort_values('Date').drop_duplicates('Date').set_index('Date')
    elif price_source.startswith('Yahoo'):
        prices=load_prices(symbol)
except Exception as e: err=str(e)

if prices is None:
    st.info('Load historical data to enable analysis.')
    if err: st.error(err)
    st.stop()
try: tech=technical_agent(prices['Close'])
except Exception as e: st.error(str(e)); st.stop()
last=pd.Timestamp(prices.index[-1]).date()
if last < date.today(): st.warning(f"Last daily market bar: {last}. Today's move may not be included.")
spot=tech['spot']
cols=st.columns(5)
for c,(name,val) in zip(cols,[('Last daily close',f'${spot:,.2f}'),('60D realized vol',f'{tech["hist_vol60"]:.1%}'),('RSI (14)',f'{tech["rsi14"]:.1f}'),('MA20',f'${tech["ma20"]:.2f}'),('Trend',tech['trend'])]):c.metric(name,val)
with st.expander('Agent 1 • Historical & technical evidence',expanded=True):
    st.line_chart(prices['Close'].tail(130),height=210)
    st.write(f'20-day return: {tech["return20"]:+.1%}; 20-day average ${tech["ma20"]:.2f}; 50-day average ${tech["ma50"]:.2f}; technical votes {tech["technical_votes"]}/4. Trend labels are heuristics, not predictions.')

st.subheader('Enter Charles Schwab call quotes')
st.markdown('On **schwab.com → Trade → Options → Chains**, search the ticker, select an expiration and copy each call strike, bid, ask and optional Greeks. [Schwab options chain](https://chains-spa-external.schwab.com/)')
st.caption('Manual is the default. Automatic import is **read-only** and requires a separately authorized Schwab Trader API bearer token. No orders are submitted.')
st.caption('One row per contract. Enter premium per SHARE (e.g., $4.10), not the $410 contract cost. For IV, enter decimals (0.50 = 50%). Quotes should be from the same market snapshot.')
initial=pd.DataFrame([
    {'expiration':str(date.today()+pd.Timedelta(days=35)),'strike':round(spot*.97,1),'premium':round(spot*.05,2),'bid':0.,'ask':0.,'iv':None,'delta':None,'volume':None,'open_interest':None,'option_symbol':''},
    {'expiration':str(date.today()+pd.Timedelta(days=35)),'strike':round(spot,1),'premium':round(spot*.035,2),'bid':0.,'ask':0.,'iv':None,'delta':None,'volume':None,'open_interest':None,'option_symbol':''},
    {'expiration':str(date.today()+pd.Timedelta(days=35)),'strike':round(spot*1.05,1),'premium':round(spot*.02,2),'bid':0.,'ask':0.,'iv':None,'delta':None,'volume':None,'open_interest':None,'option_symbol':''},
])
if 'quotes' not in st.session_state: st.session_state.quotes=initial
with st.expander('Optional: Import call quotes from Schwab Trader API (read-only)'):
    st.caption('Requires approved Market Data Production API access and OAuth authorization. Token is entered only through Streamlit Secrets; never into GitHub or a text input.')
    try: schwab_token=st.secrets.get('SCHWAB_ACCESS_TOKEN','')
    except (FileNotFoundError,KeyError): schwab_token=''
    if not schwab_token: st.info('Set SCHWAB_ACCESS_TOKEN in Streamlit app Secrets after obtaining an authorized token. Tokens expire; this starter does not automate OAuth token refresh.')
    dcol1,dcol2=st.columns(2)
    with dcol1: start_api=st.date_input('Earliest expiration',value=date.today())
    with dcol2: end_api=st.date_input('Latest expiration',value=date.today()+pd.Timedelta(days=75))
    if st.button('Import Schwab call quotes',disabled=not bool(schwab_token)):
        try:
            imported=fetch_schwab_calls(symbol,schwab_token,str(start_api),str(end_api))
            if imported.empty: st.warning('The API returned no standard call contracts for this filter.')
            else:
                st.session_state.quotes=imported
                st.session_state.schwab_quote_fetched=datetime.now().astimezone().isoformat()
                st.success(f'Imported {len(imported)} call contracts. Inspect values and click the analysis button after confirming timestamps.')
                st.rerun()
        except Exception as e: st.error(f'Schwab import failed: {type(e).__name__}: {e}')
q=st.data_editor(st.session_state.quotes,num_rows='dynamic',hide_index=True,use_container_width=True,key='quote_editor',column_config={'expiration':st.column_config.TextColumn('Expiration YYYY-MM-DD'),'strike':st.column_config.NumberColumn('Strike ($)'),'premium':st.column_config.NumberColumn('Premium ($/share)'),'bid':st.column_config.NumberColumn('Bid ($/share)'),'ask':st.column_config.NumberColumn('Ask ($/share)'),'iv':st.column_config.NumberColumn('IV (decimal)'),'delta':st.column_config.NumberColumn('Delta (0–1)'),'volume':st.column_config.NumberColumn('Volume'),'open_interest':st.column_config.NumberColumn('Open interest'),'option_symbol':st.column_config.TextColumn('Schwab option symbol')})
st.info('The prefilled rows are illustrative placeholders, NOT Schwab quotes. Replace every value before interpreting results.')

c1,c2,c3=st.columns(3)
with c1: quote_time=st.text_input('Schwab quote timestamp (include timezone)',placeholder='2026-10-15 12:25 ET')
with c2: synced=st.checkbox('I verified current spot and quotes are from same time')
with c3: event=st.checkbox('Unresolved earnings / major event / material news risk')
news=st.text_area('Agent 3 • Investing.com Pro research notes (manual, dated)',placeholder='Paste your own concise observations: consensus, earnings date, analyst revisions, catalysts, source and timestamp. No account credentials.')
st.caption('Investing.com Pro credentials are not used. This MVP takes your manually supplied observations; automatic access needs a licensed, documented data interface.')

if st.button('Run multi-agent analysis',type='primary',use_container_width=True):
    st.session_state.pop('gemini_brief',None)
    st.session_state.pop('gemini_packet',None)
    rows=[]; errors=[]
    v=vol_override if use_override else tech['hist_vol60']
    for i,row in q.iterrows():
        try: rows.append(option_agent(row.to_dict(),spot,v,date.today(),drift))
        except Exception as e: errors.append(f'Row {i+1}: {e}')
    for e in errors: st.error(e)
    if not rows: st.stop()
    scored,conclusion=risk_agent(rows,capital,risk_pct,synced and bool(quote_time.strip()) and last==date.today(),event)
    st.session_state.report={'ticker':symbol,'timestamp_utc':datetime.now().astimezone().isoformat(),
        'source':'Yahoo Finance via yfinance or user CSV','daily_bar_date':str(last),'spot':spot,
        'market_agent':tech,'scenario_assumptions':{'annual_drift':drift,'annual_volatility':v,'distribution':'lognormal','model_is_calibrated':False},
        'schwab_quote_timestamp':quote_time,'investing_com_user_notes':news,'market_and_quotes_synced':synced,
        'event_risk':event,'options':scored,'conclusion':conclusion}
if 'report' in st.session_state:
    rep=st.session_state.report
    st.subheader('Agent 4 • Decision & risk committee')
    if rep['conclusion'].startswith('NO TRADE'):st.error(rep['conclusion'])
    else:st.warning(rep['conclusion'])
    st.caption('Ranking is a transparent heuristic, not predicted alpha or a calibrated confidence score. Ineligible rows are displayed below eligible rows.')
    view=[]
    for x in rep['options']:
        view.append({'Expiry':x['expiration'],'Strike':x['strike'],'Premium':x['premium'],
                     'DTE':x['dte'],'Cost (1 contract)':x['max_loss'], 'Break-even':round(x['break_even'],2),
                     'Modeled P(profit)':f'{x["p_profit_model"]:.1%}', 'Modeled EV ($)':round(x['expected_pnl_model'],2),
                     'Spread %':round(x['spread_pct'],1) if x['spread_pct'] is not None else None,
                     'Review eligibility':'PASS' if x['eligible'] else 'BLOCKED',
                     'Warnings':'; '.join(x['blockers']+x['warnings'])})
    st.dataframe(pd.DataFrame(view),hide_index=True,use_container_width=True)
    st.subheader('Expiration payoff scenarios per 1 contract')
    st.dataframe(pd.DataFrame([{'Strike':x['strike'],'Expiration':x['expiration'],'Stock −10%':round(x['pnl_if_minus10'],2),'Stock unchanged':round(x['pnl_if_flat'],2),'Stock +10%':round(x['pnl_if_plus10'],2)} for x in rep['options']]),hide_index=True,use_container_width=True)
    st.markdown('**Independent agent findings**')
    st.write(f'**Technical agent:** {rep["market_agent"]["trend"]} ({rep["market_agent"]["technical_votes"]}/4 votes). This is backward-looking.')
    st.write(f'**Scenario agent:** Applied {rep["scenario_assumptions"]["annual_volatility"]:.1%} annualized volatility and {rep["scenario_assumptions"]["annual_drift"]:.1%} price drift in a lognormal scenario model. Probabilities are conditional on those assumptions.')
    st.write('**Catalyst agent:** '+(news if news else 'No dated catalyst notes supplied; earnings and news risks have not been independently checked.'))
    st.write('**Risk agent:** No automatic recommendation to buy. Check spreads, quote freshness, event timing, maximum acceptable loss, and probability assumptions.')
    st.download_button('Download auditable JSON report',data=json.dumps(rep,indent=2,default=str),file_name=f'{symbol}_options_research.json',mime='application/json')
    st.download_button('Download ranked quotes CSV',data=pd.DataFrame(view).to_csv(index=False),file_name=f'{symbol}_options_ranked.csv',mime='text/csv')
    st.info('Next iteration: licensed news/event API, earnings calendar, actual Schwab quote timestamp validation, walk-forward backtesting, probabilistic calibration, and optional grounded Gemini narrative agent.')


st.divider()
st.header('Version 2 • Evidence and AI research committee')
st.caption('Directional backtest evaluates STOCK direction, not option profitability. Historical results do not establish future odds.')
bt=walk_forward_signal_test(prices['Close'],horizon=backtest_horizon)
if bt['status']=='insufficient_data':
    st.info(bt['reason'])
else:
    a,b,c=st.columns(3)
    a.metric('Historical bullish windows',bt['bullish_signals'])
    b.metric('Stock-up hit rate',f"{bt['directional_hit_rate']:.1%}" if bt['directional_hit_rate'] is not None else 'Not available')
    c.metric('95% descriptive interval',f"{bt['wilson_95_interval'][0]:.0%}–{bt['wilson_95_interval'][1]:.0%}" if bt['wilson_95_interval'] else 'Not available')
    st.warning(bt['caution'])
    if bt['status']=='descriptive_only': st.warning('Too few bullish test observations to support a reliable empirical confidence claim.')
    with st.expander('Audit historical backtest windows'):
        st.dataframe(pd.DataFrame(bt['results']),use_container_width=True,hide_index=True)
        st.download_button('Download stock-signal backtest CSV',pd.DataFrame(bt['results']).to_csv(index=False),file_name=f'{symbol}_directional_backtest.csv')

if 'market_events' not in st.session_state or st.session_state.get('market_events_symbol')!=symbol:
    st.session_state.market_events={'earnings_dates':[],'headlines':[],'errors':['Not retrieved'],'retrieved_utc':None}
    st.session_state.market_events_symbol=symbol
if use_yahoo_events and st.button('Fetch Yahoo event metadata (best effort)'):
    with st.spinner('Checking available event metadata'):
        st.session_state.market_events=get_yahoo_events(symbol)
events=st.session_state.market_events
with st.expander('Catalysts • earnings metadata and Yahoo headlines',expanded=False):
    st.write('Potential earnings dates:',events.get('earnings_dates') or 'Unavailable / not verified')
    for item in events.get('headlines',[]):
        st.markdown(f"- **{item['title']}** — {item['publisher']}, {item['published']}")
        if item['url'].startswith('https://'): st.caption(item['url'])
    for error in events.get('errors',[]):st.caption(error)
    st.caption('Headlines are unverified claims; Yahoo metadata is an unofficial feed. Confirm earnings dates with company investor relations.')

st.subheader('Gemini evidence-only research brief')
st.caption('Gemini interprets the local research packet. It cannot trade, access your Schwab account or independently verify sources.')
try:
    secret_key=st.secrets.get('GEMINI_API_KEY','')
except (FileNotFoundError,KeyError):
    secret_key=''
api_key=os.getenv('GEMINI_API_KEY') or secret_key
model_name=st.text_input('Gemini model',value='gemini-2.5-flash',help='Edit if this model is unavailable on your API account.')
if not api_key: st.info('Set GEMINI_API_KEY in Streamlit secrets to enable the research brief. Core research still runs without it.')
if st.button('Generate grounded Gemini committee brief',disabled=not (api_key and 'report' in st.session_state)):
    try:
        packet=research_packet(st.session_state.report,bt,events,st.session_state.report.get('investing_com_user_notes',''))
        with st.spinner('Generating evidence-only research brief'):
            st.session_state.gemini_brief=gemini_research_brief(packet,api_key,model_name)
        st.session_state.gemini_packet=packet
    except Exception as e:
        st.error(f'Gemini service unavailable: {type(e).__name__}: {e}')
if 'gemini_brief' in st.session_state:
    st.warning('This Gemini brief reflects the last generated evidence snapshot. Re-run core analysis and regenerate after changing inputs.')
    st.markdown(st.session_state.gemini_brief)
    st.download_button('Download evidence packet JSON',json.dumps(st.session_state.get('gemini_packet',{}),indent=2,default=str),file_name=f'{symbol}_research_evidence.json')
