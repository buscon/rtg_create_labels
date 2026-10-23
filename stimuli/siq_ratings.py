# Mean ISO pleasantness/eventfulness and sound-source ratings per recording of the
# Silenzi in Quota dataset (v1.2, https://zenodo.org/records/20719737, CC BY 4.0).
#     python stimuli/siq_ratings.py SiQ_Dataset_v_1.2.csv OUT.csv
import sys, numpy as np, pandas as pd
d = pd.read_csv(sys.argv[1], low_memory=False)
paq = ['Q5.1','Q5.2','Q5.3','Q5.4','Q5.5','Q5.6','Q5.7','Q5.8']  # pl ch vi un ca an ev mo
for c in paq: d[c] = pd.to_numeric(d[c], errors='coerce')
pl,ch,vi,un,ca,an,ev,mo = [d[c] for c in paq]
k = 1/(4+np.sqrt(32))
d['P'] = ((pl-an) + np.cos(np.pi/4)*(ca-ch) + np.cos(np.pi/4)*(vi-mo))*k
d['E'] = ((ev-un) + np.cos(np.pi/4)*(ch-ca) + np.cos(np.pi/4)*(vi-mo))*k
print('check vs given P/E: max diff', (d['P']-pd.to_numeric(d['UNADJ. ISOPleasantness'],errors='coerce')).abs().max(),
      (d['E']-pd.to_numeric(d['UNADJ. ISOEventfulness'],errors='coerce')).abs().max())
src = {'Q4.1 ':'traffic','Q4.2':'other_noise','Q4.3':'human','Q4.4':'animals','Q4.5':'wind','Q4.6':'water'}
for c in list(src)+['Q6','Q7']: d[c] = pd.to_numeric(d[c], errors='coerce')
g = d.groupby('RecID_GroupID')
a = pd.DataFrame({
 'site': g['Location_ID'].first(), 'point': g['Evaluation_point'].first(), 'date': g['Date_YYYY/MM/DD'].first(),
 'n': g['P'].count(), 'P': g['P'].mean(), 'E': g['E'].mean(), 'P_sd': g['P'].std(), 'E_sd': g['E'].std(),
 'LAeq': pd.to_numeric(g['LAeqL(A)'].first(), errors='coerce'),
 'quality': g['Q6'].mean()})
for c,nm in src.items(): a[nm] = g[c].mean()
a['angle'] = (np.degrees(np.arctan2(a['E'], a['P'])) % 360)
a['r'] = np.hypot(a['P'], a['E'])
a.round(2).to_csv(sys.argv[2])
pd.set_option('display.width', 250)
print(a.round(2).sort_values('P', ascending=False).to_string())
