"""Diagnostic evaluation on authored examples, not independent human ground truth."""
import json
from pathlib import Path
from esg_lens.nlp.registry import model_registry, ESG_MODEL

def evaluate(rows, predictions):
    labels=sorted({r['label'] for r in rows} | {p['label'] for p in predictions})
    if len(rows)!=len(predictions) or not rows:
        raise ValueError('Expected one prediction per nonempty example')
    confusion={a:{b:0 for b in labels} for a in labels}
    errors=[]
    for row,pred in zip(rows,predictions,strict=True):
        confusion[row['label']][pred['label']]+=1
        if row['label']!=pred['label']:
            errors.append({'text':row['text'],'expected':row['label'],'predicted':pred['label'],'confidence':pred['score']})
    per_label={}
    for label in labels:
        tp=confusion[label][label]; actual=sum(confusion[label].values()); predicted=sum(v[label] for v in confusion.values())
        per_label[label]={'support':actual,'precision':tp/predicted if predicted else 0,'recall':tp/actual if actual else 0,'f1':2*tp/(actual+predicted) if actual+predicted else 0}
    return {'n':len(rows),'accuracy':1-len(errors)/len(rows),'macro_f1':sum(v['f1'] for v in per_label.values())/len(labels),'per_label':per_label,'confusion':confusion,'errors':errors}

if __name__=='__main__':
    rows=json.loads(Path('tests/fixtures/esg_labels.json').read_text())
    predictions=model_registry.classifier([r['text'] for r in rows],batch_size=16,truncation=True)
    report={'model':ESG_MODEL,'dataset':'Authored synthetic diagnostic examples; AI-labelled, not human-reviewed or representative','results':evaluate(rows,predictions)}
    Path('label-evaluation.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
