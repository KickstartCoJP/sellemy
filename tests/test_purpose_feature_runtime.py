from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import purpose_feature_runtime as pfr
from renderer import render_article
from fixtures import valid_evidence, valid_payload


def test_related_feature_block_renders_after_article_content():
    rendered = render_article(valid_payload(), valid_evidence(), related_features=[{'title':'デスク環境を整える','url':'/themes/desk-environment.html'}])
    assert '関連する目的' in rendered
    assert '/themes/desk-environment.html' in rendered
    assert rendered.index('関連する目的') > rendered.index('選ぶときのポイント')


def test_schema_and_relation_readback(tmp_path):
    db = tmp_path / 'test.db'
    conn = sqlite3.connect(db)
    pfr.ensure_schema(conn)
    tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
    assert {'articles','features','article_feature_relations'} <= tables
    conn.close()


def test_replace_related_features_is_idempotent():
    base = '<html><main><p>x</p></main></html>'
    features = [{'title':'デスク環境を整える','url':'/themes/desk-environment.html'}]
    once = pfr.replace_related_features_block(base, features)
    twice = pfr.replace_related_features_block(once, features)
    assert once == twice
    assert once.count(pfr.START) == 1
    assert once.count(pfr.END) == 1


def test_discovery_prefers_db_for_migrated_feature(monkeypatch, tmp_path):
    import purpose_relation_discovery as prd
    db = tmp_path / 'reviewed.db'
    conn = sqlite3.connect(db)
    pfr.ensure_schema(conn)
    conn.execute("insert into articles(article_id,slug,title,category,summary,status,semantic_metadata_json) values('new','new-slug','New','gadget','','published','{}')")
    conn.execute("insert into features(feature_id,slug,title,purpose_statement,scope_include,scope_exclude,intro,lifecycle_status,navigation_visibility,seo_state,seo_title,meta_description,hero_image,editorial_priority,created_at,updated_at) values('PH-DESK','desk-environment','デスク環境を整える','','','','','published','listed','index','','','',1,'now','now')")
    conn.execute("insert into article_feature_relations(article_id,feature_id,relation_type,relevance_score,confidence,display_order,rationale,source,first_linked_at,last_revalidated_at,review_candidate,review_reason,active) values('new','PH-DESK','core',1,.9,1,'db','manual','now','now',0,'',1)")
    conn.commit(); conn.close()
    monkeypatch.setattr(prd, 'DB', db)
    seed=[
        {'feature_id':'PH-DESK','feature_title':'デスク環境を整える','article_id':'old','slug':'old-slug','article_title':'Old','relation_type':'core'},
        {'feature_id':'PH-OTHER','feature_title':'Other','article_id':'keep','slug':'keep-slug','article_title':'Keep','relation_type':'core'},
    ]
    rows=prd.load_reviewed_relations(seed)
    keys={(r['feature_id'],r['slug'],r['_source']) for r in rows}
    assert ('PH-DESK','new-slug','reviewed_db') in keys
    assert not any(r['feature_id']=='PH-DESK' and r['slug']=='old-slug' for r in rows)
    assert ('PH-OTHER','keep-slug','reviewed_seed') in keys
