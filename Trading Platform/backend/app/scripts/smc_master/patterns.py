# This file is subject to the Mozilla Public License 2.0, https://mozilla.org/MPL/2.0/.
# Shared Bjorgum/BjCandlePatterns/2 candle geometry. Context belongs to the caller.
from .technical import div

PATTERN_PRIORITY = ['BE', 'P', 'TB', 'HW', 'H', 'DD', 'LLS', 'D', 'ST']
BEARISH_CODES = {'H': 'SS', 'DD': 'GD', 'TB': 'TT', 'P': 'DCC', 'HW': 'HB', 'LLS': 'LUS'}


def pattern_formulas(p, row, prev, avg, prev_avg, atr):
    _,o,h,l,c,_=row
    body=abs(c-o); bh=max(o,c); bl=min(o,c); size=h-l; upper=h-bh; lower=bl-l
    _,po,ph,pl,pc,_=prev
    pb=abs(pc-po); pbh=max(po,pc); pbl=min(po,pc); tall=pb>prev_avg; short=body<avg
    doji=div(body,size)*100<=5; sized=size>=p['hammerSize']*atr; shadows=upper>.05*body and lower>.05*body
    rejection_b=p['rejectWickMax']==0 or div(upper,body)<p['rejectWickMax']/100; rejection_s=p['rejectWickMax']==0 or div(lower,body)<p['rejectWickMax']/100
    formulas=[
        ('D','Doji','d_',div(body,size)*100<=p['dojiSize'] and upper<=lower*p['dojiWickSize'] and lower<=upper*p['dojiWickSize'],div(body,size)*100<=p['dojiSize'] and upper<=lower*p['dojiWickSize'] and lower<=upper*p['dojiWickSize'],False,1,True),
        # Engulfing: an opposite-color candle whose body is larger than the previous body and than the average body
        # (TradingView's long-body rule); a small candle covering a doji or another small candle is not engulfing.
        ('BE','Engulfing','be_',pc<po and c>o and c>=po and o<=pc and body>pb and body>avg and rejection_b and (not p['ecWick'] or c>=ph),pc>po and c<o and c<=po and o>=pc and body>pb and body>avg and rejection_s and (not p['ecWick'] or c<=pl),True,2,False),
        ('H','Hammer / Shooting Star','hs_',body>0 and bl>=h-size*p['hammerFib']/100 and upper<=p['hsShadowPerc']/100*body and sized and (not p['colorMatch'] or c>o),body>0 and bh<=l+size*p['hammerFib']/100 and lower<=p['hsShadowPerc']/100*body and sized and (not p['colorMatch'] or c<o),False,1,False),
        ('DD','Dragonfly / Gravestone Doji','dg_',doji and upper<=body,doji and lower<=body,False,1,False),
        ('TB','Tweezer Bottom / Top','tw_',(not doji or shadows) and abs(l-pl)<=avg*.05 and pc<po and c>o and tall and (not p['closeHalf'] or c>(ph+pl)/2),(not doji or shadows) and abs(h-ph)<=avg*.05 and pc>po and c<o and tall and (not p['closeHalf'] or c<(ph+pl)/2),True,2,False),
        ('ST','Spinning Top','st_',lower>=size*.34 and upper>=size*.34 and not doji,lower>=size*.34 and upper>=size*.34 and not doji,False,1,True),
        # Opening beyond the previous low/high needs a price gap, which a 24-hour market like XAUUSD almost never prints
        # (2 piercing patterns in 10 weeks of 5m): the open only has to be at or beyond the previous close.
        ('P','Piercing / Dark Cloud Cover','pc_',pc<po and tall and c>o and o<=pc and c>(pb/2+pbl) and c<po,pc>po and tall and c<o and o>=pc and c<(pb/2+pbl) and c>po,False,2,False),
        ('HW','Harami','bh_',tall and pc<po and c>o and short and h<=pbh and l>=pbl,tall and pc>po and c<o and short and h<=pbh and l>=pbl,False,2,False),
        ('LLS','Long Lower / Upper Shadow','ls_',lower>size*p['luRatio']/100 and sized,upper>size*p['luRatio']/100 and sized,False,1,False),
    ]
    return formulas
