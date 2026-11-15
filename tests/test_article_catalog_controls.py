"""Article index uses the product catalog's five controls and exact intro."""
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]


def test_identical_filter_shuffle_controls_and_css():
    product=BeautifulSoup((ROOT/'products.html').read_text(),'html.parser')
    article=BeautifulSoup((ROOT/'articles.html').read_text(),'html.parser')
    product_buttons=product.select('.filter-controls button')
    article_buttons=article.select('.filter-controls button')
    assert [(b.get_text(strip=True),b.get('id'),b.get('onclick'),b.get('class')) for b in article_buttons] == [
           (b.get_text(strip=True),b.get('id'),b.get('onclick'),b.get('class')) for b in product_buttons]
    product_css=(ROOT/'products.html').read_text().split('<style>',1)[1].split('</style>',1)[0]
    article_css=(ROOT/'articles.html').read_text().split('<style>',1)[1].split('</style>',1)[0]
    assert product_css==article_css
    assert article.select_one('.filter-controls #btn-all.active')


def test_intro_exact_user_words_and_static_fallback_cards_untouched():
    text=(ROOT/'articles.html').read_text()
    soup=BeautifulSoup(text,'html.parser')
    expected=(
        'Sellemyで紹介しているすべての記事を、カテゴリを超えて一覧表示しています。',
        '思わぬ出会いや、新しい気づきが生まれてほしいという想いで、シャッフル表示にも対応。',
        '絞り込みを行いたい方は、カテゴリボタンをクリックしてください。',
    )
    intro=soup.select_one('main .category-intro p')
    assert intro
    assert [x.strip() for x in intro.stripped_strings]==list(expected)
    assert text.count('<!-- STATIC:CATALOG:START -->')==1
    assert text.count('<!-- STATIC:CATALOG:END -->')==1
    assert len(soup.select('#articleList a.article-card'))>=440
    assert soup.select_one('a[href="/categories/"]').get_text(strip=True)=='テーマ'


def test_fetch_and_current_category_scoped_shuffle():
    script=(ROOT/'articles.html').read_text().rsplit('<script>',1)[-1].split('</script>',1)[0]
    assert "fetch('/json/articles.json')" in script
    assert "article.status === 'published'" in script
    assert "allArticles.filter(article => article.category === currentCategory)" in script
    assert 'renderArticles(shuffleArray(currentArticles()))' in script
    assert 'summary.textContent = article.summary ||' in script


def test_browser_logic_using_node_runtime():
    import subprocess
    result = subprocess.run(['node', 'tests/check_article_catalog_runtime.cjs'],
                            cwd=ROOT, text=True, capture_output=True, check=True)
    assert 'ARTICLE_CATALOG_RUNTIME_PASS 447 articles' in result.stdout
