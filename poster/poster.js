// Fills the QR captions from qr/targets.js (generated from qr-targets.conf), drops a QR
// slot whose target is empty, and — with ?check in the URL — measures the smallest text
// on the page so export-pdf.sh can refuse to build a poster with text under 20 pt.
(function () {
  var targets = window.POSTER_QR || {};

  document.querySelectorAll('[data-qr]').forEach(function (slot) {
    var key = slot.getAttribute('data-qr');
    var url = targets[key];
    if (!url) { slot.remove(); return; }
    var img = slot.querySelector('img');
    img.src = 'qr/' + key + '.svg';
    img.onerror = function () { slot.remove(); };
    // Caption without the scheme; a line may break only after a "/", never mid-word.
    var cap = slot.querySelector('.url');
    url.replace(/^https?:\/\//, '').replace(/\/$/, '').split('/').forEach(function (part, i) {
      if (i) { cap.appendChild(document.createTextNode('/')); cap.appendChild(document.createElement('wbr')); }
      cap.appendChild(document.createTextNode(part));
    });
  });

  if (!/[?&]check\b/.test(location.search)) return;

  // Effective size of every visible, non-blank text node, in pt. For SVG the computed
  // font-size is in user units, so it is multiplied by the element's screen scale.
  function sizePx(el) {
    var fs = parseFloat(getComputedStyle(el).fontSize);
    if (el instanceof SVGElement && el.getScreenCTM) {
      var m = el.getScreenCTM();
      fs *= Math.sqrt(m.a * m.a + m.b * m.b);
    }
    return fs;
  }
  var min = Infinity, at = '';
  var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (var n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.nodeValue.trim()) continue;
    var el = n.parentElement;
    if (!el || el.closest('script,style,title')) continue;
    if (!el.getClientRects().length) continue;            // not rendered
    var pt = sizePx(el) * 0.75;                           // 1 CSS px = 0.75 pt
    if (pt < min) { min = pt; at = n.nodeValue.trim().slice(0, 40); }
  }

  // Nothing may reach the footer bar: every element's bottom must sit at least 4 mm above
  // it. (Comparing scrollHeight alone misses content that spills into main's padding.)
  var overflow = [];
  var mm = 3.7795;
  var limit = document.querySelector('.bar.foot').getBoundingClientRect().top - 4 * mm;
  var worst = 0, worstAt = '';
  document.querySelectorAll('main *').forEach(function (el) {
    var r = el.getBoundingClientRect();
    if (!r.height) return;
    if (r.bottom - limit > worst) { worst = r.bottom - limit; worstAt = (el.textContent || el.tagName).trim().slice(0, 30); }
  });
  if (worst > 0.5) overflow.push('by ' + (worst / mm).toFixed(1) + ' mm at "' + worstAt + '"');
  var lastBottom = Math.max.apply(null, Array.prototype.map.call(document.querySelectorAll('main > *'),
    function (el) { return el.getBoundingClientRect().bottom; }));
  var slack = ((limit - lastBottom) / mm).toFixed(1);

  var root = document.documentElement;
  root.setAttribute('data-min-pt', min.toFixed(2));
  root.setAttribute('data-min-at', at.replace(/"/g, "'"));
  root.setAttribute('data-overflow', overflow.join('; '));
  root.setAttribute('data-slack-mm', slack);
})();
