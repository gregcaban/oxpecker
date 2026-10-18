// Determinism prelude. Run in interpreter mode before every case, in every mode,
// so the stubs themselves never vary between modes.
// Evaluates to a function (seed) that patches the global object it is called on.
(function (seed) {
  var global = this;

  // Math.random -> mulberry32 seeded from the request.
  var s = (seed >>> 0) || 0x9E3779B9;
  var imul = Math.imul;
  Math.random = function random() {
    s = (s + 0x6D2B79F5) | 0;
    var t = s;
    t = imul(t ^ (t >>> 15), t | 1);
    t ^= t + imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  // Date: Date.now(), Date() and new Date() with no arguments return a fixed instant.
  var FIXED = 1700000000000;   // 2023-11-14T22:13:20Z
  var OrigDate = Date;
  var D = function Date(a, b, c, d, e, f, g) {
    if (!(this instanceof D)) return new OrigDate(FIXED).toString();
    switch (arguments.length) {
      case 0: return new OrigDate(FIXED);
      case 1: return new OrigDate(a);
      case 2: return new OrigDate(a, b);
      case 3: return new OrigDate(a, b, c);
      case 4: return new OrigDate(a, b, c, d);
      case 5: return new OrigDate(a, b, c, d, e);
      case 6: return new OrigDate(a, b, c, d, e, f);
      default: return new OrigDate(a, b, c, d, e, f, g);
    }
  };
  D.prototype = OrigDate.prototype;
  Object.defineProperty(OrigDate.prototype, 'constructor', { value: D, writable: true, configurable: true, enumerable: false });
  D.now = function now() { return FIXED; };
  D.parse = OrigDate.parse;
  D.UTC = OrigDate.UTC;
  global.Date = D;

  // GC-timing-dependent features are removed outright.
  delete global.WeakRef;
  delete global.FinalizationRegistry;
})
