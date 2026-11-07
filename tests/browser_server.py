"""Offline browser-test server. Synthetic evidence, never used for the live recording."""
import runpy
from datetime import timedelta
from pathlib import Path
from esg_lens.api.main import create_app
from esg_lens.jobs import runner
from esg_lens.nlp import pipeline

fixtures = runpy.run_path(str(Path(__file__).parent / 'integration/test_release_flow.py'))
pipeline.Controversies = fixtures['StubControversies']

async def collect(conn, ticker, since, sources, job_id, force_refresh):
    conn.execute('INSERT OR IGNORE INTO companies(ticker,name,fetched_at) VALUES(?,?,?)',
                 (ticker, ticker + ' (synthetic browser test)', runner.utcnow().isoformat()))
    for i in range(5 if ticker == 'AAPL' else 1):
        conn.execute('''INSERT OR IGNORE INTO raw_documents
            (ticker,source,doc_type,title,url,domain,published_at,content_hash) VALUES(?,?,?,?,?,?,?,?)''',
            (ticker, 'google_rss', 'headline', f'{ticker} pollution fine fixture {i}',
             'https://example.org/evidence', 'reuters.com',
             (runner.utcnow()-timedelta(days=1)).isoformat(), str(i)))
    conn.commit()
    return ['yfinance failed'] if 'yfinance' in sources else []

runner.collect = collect
app = create_app(fixtures['Models']())
