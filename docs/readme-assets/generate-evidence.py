"""Rebuild historical README charts; requires matplotlib, not an app dependency."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
out=Path(__file__).resolve().parent
import csv
rows=list(csv.DictReader((out/'url-probe.csv').open()))
counts=[int(r['correct_refusals']) for r in rows]
vals=[int(r['correct_refusals'])/int(r['attempts'])*100 for r in rows]
labels=[r['url_type'] for r in rows]
for mobile in [False,True]:
 fig,ax=plt.subplots(figsize=(5.0,5.3) if mobile else (10,4.3),dpi=160)
 bg='#090f26';fg='#f6efea';muted='#c7b9bd';gold='#ffb055'
 fig.patch.set_facecolor(bg);ax.set_facecolor(bg)
 if mobile:
  fig.subplots_adjust(left=.08,right=.15+0.80,top=.69,bottom=.20)
  fig.text(.08,.92,'A harder test revealed limits',fontsize=16,color=fg,weight='bold')
  fig.text(.08,.855,'Earlier test: instructions alone',fontsize=12,color=gold)
  fig.text(.08,.80,'Correct refusals · higher is better',fontsize=11,color=muted)
  yy=[2.8,1.4,0]
  ax.barh(yy,vals,height=.40,color=gold)
  for y,label,val,n in zip(yy,labels,vals,counts):
   ax.text(0,y+.31,label,color=fg,fontsize=11,va='bottom');ax.text(val+2,y,f'{n}/24',color=fg,fontsize=11,va='center')
  ax.set_ylim(-.5,3.55);ax.set_yticks([])
  fig.text(.08,.08,'Historical project results · llama3.2\nNot an overall accuracy score.',fontsize=10,color=muted,linespacing=1.5)
 else:
  fig.subplots_adjust(left=.25,right=.96,top=.66,bottom=.23)
  fig.text(.045,.89,'A harder test revealed limits',fontsize=22,color=fg,weight='bold')
  fig.text(.045,.79,'Earlier test: instructions alone · correct refusals · higher is better',fontsize=12,color=gold)
  yy=[2,1,0];ax.barh(yy,vals,height=.48,color=gold)
  ax.set_yticks(yy,labels,color=fg,fontsize=12)
  for y,val,n in zip(yy,vals,counts):ax.text(val+1.6,y,f'{n}/24',color=fg,fontsize=12,va='center')
  ax.set_ylim(-.6,2.6)
  fig.text(.045,.07,'Historical project results · llama3.2 · one machine · not an overall accuracy score',fontsize=11,color=muted)
 ax.set_xlim(0,106);ax.set_xticks([0,25,50,75,100]);ax.xaxis.set_major_formatter(PercentFormatter(100));ax.tick_params(axis='x',colors=muted,labelsize=10,length=0,pad=8);ax.tick_params(axis='y',length=0,pad=10)
 ax.set_axisbelow(True);ax.grid(axis='x',color='#343447',linewidth=.7)
 for spine in ax.spines.values():spine.set_visible(False)
 fig.savefig(out/('url-probe-mobile.png' if mobile else 'url-probe.png'),facecolor=bg)
 plt.close(fig)
