// js/ga4.js
window.dataLayer = window.dataLayer || [];
function gtag(){ dataLayer.push(arguments); }

gtag('js', new Date());
gtag('config', 'G-SZ5RQR5H7L', {
  anonymize_ip: true,
  transport_type: 'beacon'
});

(function () {
  const CUTOVER_DATE = '2026-09-18';

  function affiliatePlatform(href) {
    if (href.includes('amzn.to') || href.includes('amazon.co.jp')) return 'amazon';
    if (href.includes('rakuten.co.jp') || href.includes('rakuten.ne.jp')) return 'rakuten';
    if (href.includes('shopping.yahoo.co.jp') || href.includes('yahoo.co.jp') || href.includes('valuecommerce.com')) return 'yahoo';
    return null;
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.link-button').forEach(function (link) {
      link.addEventListener('click', function () {
        const href = link.href;
        const platform = affiliatePlatform(href);
        if (!platform) return;

        const card = link.closest('.item-card');
        const h3 = card ? card.querySelector('h3') : null;
        const productName = h3 ? h3.textContent.trim() : '';
        const productId = card ? (card.dataset.productId || '') : '';
        const asin = card ? (card.dataset.asin || '') : '';

        gtag('event', 'affiliate_click', {
          link_url: href,
          link_text: productName,
          link_classes: platform,
          link_id: productId || asin,
          page_location: window.location.href,
          page_path: window.location.pathname,
          measurement_version: 'affiliate_click_v1',
          measurement_cutover_date: CUTOVER_DATE
        });
      });
    });
  });
})();
