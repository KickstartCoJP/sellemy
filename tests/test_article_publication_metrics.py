from __future__ import annotations

import json
from pathlib import Path

import pipeline.article_publication_metrics as metrics


def _write_fixture(root: Path, rows: list[dict], sitemap_slugs: list[str]) -> None:
    (root / "json").mkdir(parents=True)
    (root / "json" / "articles.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    urls = "".join(
        f"<url><loc>https://www.sellemy.jp/article/gadget/{slug}.html</loc><lastmod>2026-09-26</lastmod></url>"
        for slug in sitemap_slugs
    )
    (root / "sitemap.xml").write_text(
        f"<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>{urls}</urlset>",
        encoding="utf-8",
    )


def test_generate_uses_sitemap_article_url_count(monkeypatch, tmp_path):
    rows = [
        {"article_id": "a", "slug": "a", "status": "published", "published_at": "2026-09-25T10:00:00+09:00"},
        {"article_id": "b", "slug": "b", "status": "published", "published_at": "2026-09-26T10:00:00+09:00"},
    ]
    _write_fixture(tmp_path, rows, ["a", "b"])
    monkeypatch.setattr(metrics, "ARTICLES", tmp_path / "json" / "articles.json")
    monkeypatch.setattr(metrics, "SITEMAP", tmp_path / "sitemap.xml")
    monkeypatch.setattr(metrics, "OUTPUT", tmp_path / "metrics.json")
    doc = metrics.generate()
    assert doc["published_articles"] == 2
    assert doc["sitemap_article_urls"] == 2
    assert doc["daily"][-1]["published_article_count"] == 2


def test_generate_fails_when_metadata_and_sitemap_disagree(monkeypatch, tmp_path):
    rows = [{"article_id": "a", "slug": "a", "status": "published", "published_at": "2026-09-25T10:00:00+09:00"}]
    _write_fixture(tmp_path, rows, ["a", "b"])
    monkeypatch.setattr(metrics, "ARTICLES", tmp_path / "json" / "articles.json")
    monkeypatch.setattr(metrics, "SITEMAP", tmp_path / "sitemap.xml")
    monkeypatch.setattr(metrics, "OUTPUT", tmp_path / "metrics.json")
    try:
        metrics.generate()
    except ValueError as exc:
        assert "metadata/sitemap mismatch" in str(exc)
    else:
        raise AssertionError("mismatch must fail closed")
