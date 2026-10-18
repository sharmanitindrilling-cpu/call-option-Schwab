import numpy as np
import pandas as pd
from agents.v2 import walk_forward_signal_test, normalize_headlines, research_packet

def test_backtest_no_future_leak_and_range():
    x=pd.Series(np.linspace(100,200,500),index=pd.bdate_range('2024-01-01',periods=500))
    r=walk_forward_signal_test(x,20)
    assert r['bullish_signals']>0
    assert 0 <= r['directional_hit_rate'] <= 1
    assert len(r['results'])==len(range(60,len(x)-20,20))

def test_backtest_insufficient_data():
    assert walk_forward_signal_test(pd.Series([1,2,3]),20)['status']=='insufficient_data'

def test_news_normalization():
    assert normalize_headlines([{'title':'A','publisher':'Source'}])[0]['title']=='A'

def test_evidence_packet_excludes_backtest_detail():
    p=research_packet({'ticker':'NVDA','options':[]},{'results':[{'a':1}],'status':'descriptive_only'},{'errors':[]},'notes')
    assert 'results' not in p['historical_signal_test']
