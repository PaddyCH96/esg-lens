import pytest
from scripts.evaluate_labels import evaluate

def test_confusion_and_macro_f1():
    result=evaluate([{'text':'a','label':'E'},{'text':'b','label':'S'}],[{'label':'E','score':.9},{'label':'E','score':.8}])
    assert result['accuracy']==.5
    assert result['macro_f1']==pytest.approx(1/3)
    assert result['per_label']['S']['recall']==0
    assert len(result['errors'])==1

def test_length_mismatch_rejected():
    with pytest.raises(ValueError): evaluate([],[])

def test_syndicated_headlines_count_once():
    from esg_lens.jobs.runner import unique_documents
    rows=[{'id':1,'title':'Company cuts emissions!','domain':'unknown.example','source':'google_rss'},
          {'id':2,'title':'Company cuts emissions','domain':'reuters.com','source':'gdelt'},
          {'id':3,'title':'Different news','domain':'reuters.com','source':'gdelt'}]
    assert [r['id'] for r in unique_documents(rows)]==[2,3]
