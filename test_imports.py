from datetime import date
import pandas as pd
from agents.imports import parse_csv,normalize,validate

def test_csv_schwab_aliases():
    data=b'Expiration YYYY-MM-DD,Strike ($),Bid ($/share),Ask ($/share),IV (decimal),Delta (0-1),Open interest\n2026-11-20,210,13.25,13.40,0.39402,0.587,78484\n'
    q=parse_csv(data)
    assert q.iloc[0]['premium']==13.4
    assert not validate(q,as_of=date(2026,10,16),expected_ticker='NVDA')

def test_percent_and_blocks():
    q=parse_csv(b'expiration,strike,bid,ask,iv\n2026-11-20,210,13,12,40.5\n',iv_percent=True)
    assert abs(q.iloc[0]['iv']-0.405)<1e-8
    assert 'bid' in ' '.join(validate(q,as_of=date(2026,10,16)))

def test_missing_fields_not_fabricated():
    q=normalize(pd.DataFrame([{'expiration':'2026-11-20','strike':205,'bid':16.35,'ask':16.60}]))
    assert pd.isna(q.iloc[0]['premium'])
