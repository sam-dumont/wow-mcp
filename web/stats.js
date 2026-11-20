// Plausible's pageview for the static sites and the live full view, only
// with PLAUSIBLE_DOMAIN (wowstats/publish.py, analytics(); the full view
// serves it as /privacy/stats.js). It runs before Plausible's `manual`
// script and queues one pageview whose address is the page's path alone
// (data-path: /, /zogzog/, /settings...). Never location.href: the
// #fragment is /zogzog/'s key, and the query names the character and tab.
(function () {
  var path = (document.currentScript && document.currentScript.getAttribute('data-path')) || '/';
  window.plausible = window.plausible || function () {
    (window.plausible.q = window.plausible.q || []).push(arguments);
  };
  window.plausible('pageview', { u: location.origin + path });
})();
