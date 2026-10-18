from datetime import date, timedelta
import numpy as np
import pandas as pd
from agents.core import technical_agent, option_agent, risk_agent

def test_pipeline():
    series=pd.Series(100*np.exp(np.cumsum(np.full(100,0.001))))
    tech=technical_agent(series)
    assert tech['spot']>0
    q={'expiration':str(date.today()+timedelta(days=30)),'strike':105,'premium':3,'bid':2.9,'ask':3.1}
    opt=option_agent(q,100,.35,date.today())
    assert opt['break_even']==108 and opt['max_loss']==300
    ranked,conclusion=risk_agent([opt],10000,2,True,False)
    assert not ranked[0]['eligible'] and conclusion.startswith('NO TRADE')
