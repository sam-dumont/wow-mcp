// Plausible's pageview for the static sites, only when the publisher has
// PLAUSIBLE_DOMAIN (wowstats/publish.py, analytics()). It runs before
// Plausible's `manual` script and queues one pageview whose address is the
// bundle's path alone (data-path: / or /zogzog/). Never location.href: the
// #fragment is /zogzog/'s key, and the query names the character and tab.
(function () {
  var path = (document.currentScript && document.currentScript.getAttribute('data-path')) || '/';
  window.plausible = window.plausible || function () {
    (window.plausible.q = window.plausible.q || []).push(arguments);
  };
  window.plausible('pageview', { u: location.origin + path });
})();
