// Canonicaliser. Compiled once per scope in *interpreter* mode before user code runs,
// so it behaves identically whichever mode is under test. Every builtin it needs is
// captured here, at install time, so later monkey-patching by user code cannot affect it.
//
// Evaluates to a function (helpers) -> { canon, errName }.
//   canon(value)  -> [text, javaTypes, soft]
//     text      : canonical, deterministic rendering of the value graph (hard-compared)
//     javaTypes : Java runtime class of each number/string leaf in traversal order
//                 (e.g. "Integer,Double,ConsString"); compared separately
//     soft      : error messages met during traversal (soft-compared)
//   errName(value) -> the error "name" of a thrown value, found without invoking getters
(function (helpers) {
  'use strict';
  var jtype = helpers.jtype;        // Java host: simple class name of the Java object
  var isProxy = helpers.isProxy;    // Java host: instanceof NativeProxy
  var MAX_DEPTH = 8, MAX_NODES = 600, MAX_ARRAY = 200, MAX_STRING = 400;

  var ownKeys = Reflect.ownKeys;
  var gopd = Object.getOwnPropertyDescriptor;
  var gpo = Object.getPrototypeOf;
  var is = Object.is;
  var isArray = Array.isArray;
  var objToString = Object.prototype.toString;
  var fnToString = Function.prototype.toString;
  var jsonStringify = JSON.stringify;
  var call = Function.prototype.call;
  var apply = Function.prototype.apply;
  var symToString = Symbol.prototype.toString;
  var symKeyFor = Symbol.keyFor;
  var numberValueOf = Number.prototype.valueOf;
  var stringValueOf = String.prototype.valueOf;
  var booleanValueOf = Boolean.prototype.valueOf;
  var bigintValueOf = typeof BigInt === 'function' ? BigInt.prototype.valueOf : null;
  var dateGetTime = Date.prototype.getTime;
  var regexpToString = RegExp.prototype.toString;
  var mapForEach = Map.prototype.forEach;
  var setForEach = Set.prototype.forEach;
  var mapSize = gopd(Map.prototype, 'size').get;
  var setSize = gopd(Set.prototype, 'size').get;
  var taProto = gpo(Int8Array.prototype);
  var taLengthDesc = gopd(taProto, 'length');
  var taLength = taLengthDesc && taLengthDesc.get;
  var isExtensible = Object.isExtensible;
  var toStr = String;
  var global = helpers.global;
  var symDescDesc = gopd(Symbol.prototype, 'description');
  var symDesc = symDescDesc && symDescDesc.get;

  var wellKnownSymbols = [];
  var wk = ['iterator', 'asyncIterator', 'hasInstance', 'isConcatSpreadable', 'match', 'matchAll',
            'replace', 'search', 'species', 'split', 'toPrimitive', 'toStringTag', 'unscopables'];
  for (var i = 0; i < wk.length; i++) {
    if (typeof Symbol[wk[i]] === 'symbol') wellKnownSymbols.push([Symbol[wk[i]], 'Symbol.' + wk[i]]);
  }

  var protoNames = [];
  var ctorNames = ['Object', 'Array', 'Function', 'String', 'Number', 'Boolean', 'Symbol', 'BigInt',
    'Date', 'RegExp', 'Error', 'TypeError', 'RangeError', 'SyntaxError', 'ReferenceError', 'EvalError',
    'URIError', 'Map', 'Set', 'WeakMap', 'WeakSet', 'Promise', 'ArrayBuffer', 'DataView',
    'Int8Array', 'Uint8Array', 'Uint8ClampedArray', 'Int16Array', 'Uint16Array', 'Int32Array',
    'Uint32Array', 'Float32Array', 'Float64Array', 'BigInt64Array', 'BigUint64Array'];
  for (var c = 0; c < ctorNames.length; c++) {
    var ctor = global[ctorNames[c]];
    if (typeof ctor === 'function' && ctor.prototype) protoNames.push([ctor.prototype, ctorNames[c] + '.prototype']);
  }
  if (taProto) protoNames.push([taProto, 'TypedArray.prototype']);
  var genProto = null;
  try { genProto = gpo(gpo((function* () {})())); if (genProto) protoNames.push([genProto, 'Generator.prototype']); } catch (e) {}
  // rhinoException/javaException are the Java throwables Rhino hangs off error objects.
  var excludedErrorKeys = { stack: 1, fileName: 1, lineNumber: 1, columnNumber: 1, rhinoException: 1, javaException: 1 };

  function tagOf(v) { try { return call.call(objToString, v); } catch (e) { return '[object ?]'; } }

  function isErrorLike(v, tag) {
    if (tag === '[object Error]') return true;
    // Walk the prototype chain looking for an Error.prototype.
    var p = v, guard = 0;
    try {
      while (p && guard++ < 50) {
        if (p === Error.prototype) return true;
        p = gpo(p);
      }
    } catch (e) {}
    return false;
  }

  // Looks a property up along the prototype chain without invoking accessors.
  function dataProp(obj, key) {
    var p = obj, guard = 0;
    try {
      while (p !== null && p !== undefined && guard++ < 50) {
        if (isProxy(p)) return undefined;
        var d = gopd(p, key);
        if (d) return ('value' in d) ? d.value : undefined;
        p = gpo(p);
      }
    } catch (e) {}
    return undefined;
  }

  function errName(v) {
    if (v === null || (typeof v !== 'object' && typeof v !== 'function')) return typeof v;
    var n = dataProp(v, 'name');
    return typeof n === 'string' ? n : 'Object';
  }

  function keyText(k) {
    if (typeof k === 'symbol') return symbolText(k);
    return jsonStringify(k);
  }

  function symbolText(s) {
    for (var i = 0; i < wellKnownSymbols.length; i++) if (wellKnownSymbols[i][0] === s) return wellKnownSymbols[i][1];
    var reg;
    try { reg = symKeyFor(s); } catch (e) { reg = undefined; }
    if (reg !== undefined) return 'Symbol.for(' + jsonStringify(reg) + ')';
    var d;
    try { d = symDesc ? call.call(symDesc, s) : undefined; } catch (e) { d = undefined; }
    if (d === undefined) { try { return call.call(symToString, s); } catch (e2) { return 'Symbol(?)'; } }
    return 'Symbol(' + jsonStringify(d) + ')';
  }

  function canon(root) {
    var out = [];
    var jtypes = [];
    var soft = [];
    var seen = [];       // objects in first-visit order; index = reference id
    var nodes = 0;

    function emit(s) { out.push(s); }

    function number(v) {
      jtypes.push(jtype(v));
      if (v !== v) return 'NaN';
      if (v === 0) return is(v, -0) ? '-0' : '0';
      return '' + v;   // primitive conversion; never consults Number.prototype
    }

    function string(v) {
      jtypes.push(jtype(v));
      if (v.length > MAX_STRING) return jsonStringify(v.slice(0, MAX_STRING)) + '…[len=' + v.length + ']';
      return jsonStringify(v);
    }

    function prim(v) {
      switch (typeof v) {
        case 'undefined': return 'undefined';
        case 'boolean': return v ? 'true' : 'false';
        case 'number': return number(v);
        case 'string': return string(v);
        case 'bigint': jtypes.push(jtype(v)); return '' + v + 'n';
        case 'symbol': return symbolText(v);
        default: return v === null ? 'null' : null;
      }
    }

    function protoText(v) {
      var p;
      try { p = gpo(v); } catch (e) { return '?'; }
      if (p === null) return 'null';
      for (var i = 0; i < protoNames.length; i++) if (protoNames[i][0] === p) return protoNames[i][1];
      for (var j = 0; j < seen.length; j++) if (seen[j] === p) return '@' + j;
      return '{…}';   // an unnamed, not-yet-visited prototype; its identity will show up if it is rendered later
    }

    function descriptor(obj, key) {
      var d;
      try { d = gopd(obj, key); } catch (e) { return '!' + e; }
      if (!d) return '<missing>';
      var flags = (d.enumerable ? 'e' : '-') + (d.configurable ? 'c' : '-');
      if ('get' in d || 'set' in d) {
        return '{' + flags + (d.get ? 'g' : '-') + (d.set ? 's' : '-') + '}';
      }
      return '{' + flags + (d.writable ? 'w' : '-') + '}';
    }

    function dataValue(obj, key, depth) {
      var d;
      try { d = gopd(obj, key); } catch (e) { return '!' + e; }
      if (!d || !('value' in d)) return '';
      return walk(d.value, depth);
    }

    function walk(v, depth) {
      var p = prim(v);
      if (p !== null) return p;
      if (++nodes > MAX_NODES) return '…nodes';
      for (var i = 0; i < seen.length; i++) if (seen[i] === v) return '@' + i;
      var id = seen.length;
      seen.push(v);
      var jt = jtype(v);
      if (jt === 'NativeJavaObject' || jt === 'NativeJavaArray' || jt === 'NativeJavaClass') return '@' + id + '[Java ' + helpers.javaClass(v) + ']';
      if (isProxy(v)) return '@' + id + '[Proxy]';
      // The global object and builtin prototypes are huge and identical in both modes; walking
      // them costs thousands of nodes per probe. Render them as tokens instead.
      if (v === global) return '@' + id + '[global]';
      for (var pi = 0; pi < protoNames.length; pi++) if (protoNames[pi][0] === v) return '@' + id + '[' + protoNames[pi][1] + ']';
      if (typeof v === 'function') {
        var nsrc;
        try { nsrc = call.call(fnToString, v); } catch (e) { nsrc = ''; }
        if (nsrc.indexOf('[native code]') >= 0) {
          var nname = dataProp(v, 'name');
          return '@' + id + '[native ' + (typeof nname === 'string' ? nname : '?') + '/' + dataProp(v, 'length') + ']';
        }
      }
      if (depth > MAX_DEPTH) return '@' + id + '…depth';
      var tag = tagOf(v);
      var head = '@' + id + tag.slice(8, -1);
      var parts = [];
      var errorLike = isErrorLike(v, tag);

      if (typeof v === 'function') {
        var name = dataProp(v, 'name'), len = dataProp(v, 'length');
        head = '@' + id + 'function';
        parts.push('name=' + (typeof name === 'string' ? jsonStringify(name) : '?'));
        parts.push('length=' + (typeof len === 'number' ? len : '?'));
        var src;
        try { src = call.call(fnToString, v); } catch (e) { src = '!' + e; }
        parts.push('src=' + jsonStringify(src.length > MAX_STRING ? src.slice(0, MAX_STRING) + '…' : src));
      } else if (tag === '[object Number]' || tag === '[object String]' || tag === '[object Boolean]' || tag === '[object BigInt]') {
        var vo = tag === '[object Number]' ? numberValueOf : tag === '[object String]' ? stringValueOf : tag === '[object Boolean]' ? booleanValueOf : bigintValueOf;
        try { parts.push('boxed=' + walk(call.call(vo, v), depth + 1)); } catch (e) { parts.push('boxed=!'); }
      } else if (tag === '[object Date]') {
        try { parts.push('time=' + walk(call.call(dateGetTime, v), depth + 1)); } catch (e) { parts.push('time=!'); }
      } else if (tag === '[object RegExp]') {
        try { parts.push('re=' + call.call(regexpToString, v)); } catch (e) { parts.push('re=!'); }
      } else if (errorLike) {
        var msg = dataProp(v, 'message');
        if (typeof msg === 'string') soft.push(msg);
      }

      // Map / Set contents, found by brand check (the getters throw on non-instances).
      var mapLike = null;
      try { call.call(mapSize, v); mapLike = 'map'; } catch (e) {}
      if (!mapLike) { try { call.call(setSize, v); mapLike = 'set'; } catch (e) {} }
      if (mapLike === 'map') {
        var ments = [];
        try { call.call(mapForEach, v, function (val, key) { ments.push(walk(key, depth + 1) + '=>' + walk(val, depth + 1)); }); } catch (e) { ments.push('!' + e); }
        parts.push('entries=[' + ments.join(',') + ']');
      } else if (mapLike === 'set') {
        var sents = [];
        try { call.call(setForEach, v, function (val) { sents.push(walk(val, depth + 1)); }); } catch (e) { sents.push('!' + e); }
        parts.push('values=[' + sents.join(',') + ']');
      }

      // Typed arrays: elements are not own-key enumerable in a useful way; render them explicitly.
      var taLen = -1;
      if (taLength) { try { taLen = call.call(taLength, v); } catch (e) { taLen = -1; } }
      if (typeof taLen === 'number' && taLen >= 0) {
        var tel = [];
        var tn = taLen > MAX_ARRAY ? MAX_ARRAY : taLen;
        for (var ti = 0; ti < tn; ti++) tel.push(walk(v[ti], depth + 1));
        if (taLen > tn) tel.push('…[len=' + taLen + ']');
        parts.push('elements=[' + tel.join(',') + ']');
      }

      var keys;
      try { keys = ownKeys(v); } catch (e) { keys = null; }
      if (keys === null) {
        parts.push('keys=!');
      } else if (isArray(v)) {
        var len = dataProp(v, 'length');
        var n = typeof len === 'number' ? len : 0;
        var shown = n > MAX_ARRAY ? MAX_ARRAY : n;
        var els = [];
        for (var ai = 0; ai < shown; ai++) {
          var d;
          try { d = gopd(v, ai); } catch (e) { d = null; }
          if (!d) els.push('<hole>');
          else if ('get' in d || 'set' in d) els.push(descriptor(v, ai));
          else els.push(walk(d.value, depth + 1) + (d.writable && d.enumerable && d.configurable ? '' : descriptor(v, ai)));
        }
        if (n > shown) els.push('…');
        parts.push('length=' + n);
        parts.push('[' + els.join(',') + ']');
        for (var k = 0; k < keys.length; k++) {
          var key = keys[k];
          if (typeof key === 'string' && (key === 'length' || (toStr(key >>> 0) === key && (key >>> 0) < shown))) continue;
          parts.push(keyText(key) + descriptor(v, key) + ':' + dataValue(v, key, depth + 1));
        }
      } else {
        for (var k2 = 0; k2 < keys.length; k2++) {
          var key2 = keys[k2];
          if (errorLike && typeof key2 === 'string' && excludedErrorKeys[key2] === 1) continue;
          if (errorLike && key2 === 'message') { parts.push('"message"' + descriptor(v, key2)); continue; }
          if (typeof v === 'function' && (key2 === 'arguments' || key2 === 'caller')) continue;
          parts.push(keyText(key2) + descriptor(v, key2) + ':' + dataValue(v, key2, depth + 1));
        }
      }
      var ext;
      try { ext = isExtensible(v); } catch (e) { ext = '?'; }
      if (ext === false) parts.push('non-extensible');
      return head + '<' + protoText(v) + '>{' + parts.join(';') + '}';
    }

    var text = walk(root, 0);
    return [text, jtypes.join(','), soft.join('\n')];
  }

  return { canon: canon, errName: errName };
})
