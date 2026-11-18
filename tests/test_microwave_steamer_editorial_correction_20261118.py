"""Regression guard for the CEO-requested prose-only published-article correction."""
import json
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SLUG = "microwave-steamer-containers-for-weeknight-meals"
EXPECTED_ASINS = (
    "B07SPRGHMC", "B07Z4WQ4P2", "B0B5QHXZ76",
    "B09V2DDY8D", "B098JTTZZN", "B08M316C1L",
)


def test_corrected_article_and_payload_match_without_changing_product_identity():
    payload = json.loads((ROOT / f"data/payloads/{SLUG}.json").read_text())
    evidence = json.loads((ROOT / f"data/evidence/{SLUG}.json").read_text())
    dom = BeautifulSoup(
        (ROOT / f"article/dailygoods/{SLUG}.html").read_text(), "html.parser"
    )
    assert payload["slug"] == SLUG
    assert dom.find("h1").get_text(strip=True) == payload["h1"]
    assert dom.find("p", class_="lead").get_text() == payload["lead"]
    assert dom.find("p", class_="summary").get_text() == payload["summary"]
    assert dom.find("p", class_="conclusion").get_text() == payload["conclusion"]
    assert [card.get("data-asin") for card in dom.select(".item-card")] == list(EXPECTED_ASINS)
    assert [product["asin"] for product in evidence["products"]] == list(EXPECTED_ASINS)
    assert [card.p.get_text() for card in dom.select(".item-card")] == [
        product["description"] for product in payload["products"]
    ]
    assert dom.find("link", rel="canonical")["href"] == evidence["canonical_url"]
    assert not any(phrase in dom.get_text() for phrase in (
        "青い鍋を食卓に置くところまで",
        "手持ちのレンジに置く場面も思い描きやすい",
        "道具があれば、ざるで野菜を蒸して",
        "食洗機と食器乾燥機にも対応しているので、食洗機や食器乾燥機",
        "食卓での使い道が広がります",
        "その先の料理にも使える蒸し器",
    ))
