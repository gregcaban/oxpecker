// Capability probe: enumerates what this Rhino build actually exposes. Run once per Rhino
// version through the harness (oxpecker/gen/environment/__init__.py); the result drives the
// API generators so they only call methods that exist. Completion value: a JSON string.
// Each member is read once before its descriptor is taken: Rhino 1.9.2 keeps Array.prototype's
// unscopables under the empty name as a lazily loaded slot, and the descriptor of an untouched
// slot exposes the raw LazilyLoadedCtor, whose `typeof` throws. Anything still unreadable is
// recorded as `name:err`, which the Python side ignores.
(function () {
  var out = { globals: {}, constructors: {}, protos: {}, statics: {}, unreadable: [] };
  var g = this;
  var names = Object.getOwnPropertyNames(g).sort();
  for (var i = 0; i < names.length; i++) {
    var n = names[i];
    if (n === "__probe" || n === "__isProxy" || n === "__jtype" || n === "__pause" || n === "J" || n === "print") continue;
    var v;
    try { v = g[n]; } catch (e) { continue; }
    out.globals[n] = typeof v;
    try {
    if (typeof v === "function") {
      var fnLength = v.length;
      out.constructors[n] = { length: fnLength, isCtor: false };
      try { Reflect.construct(String, [], v); out.constructors[n].isCtor = true; } catch (e) {}
      var statics = [];
      var sn = Object.getOwnPropertyNames(v).sort();
      for (var j = 0; j < sn.length; j++) {
        var sname = sn[j];
        if (sname === "prototype" || sname === "length" || sname === "name" || sname === "arguments" || sname === "caller") continue;
        try {
          void v[sname];   // materialises a lazily loaded slot before the descriptor exposes it raw
          var d = Object.getOwnPropertyDescriptor(v, sname);
          if (d && typeof d.value === "function") statics.push(sname + "/" + d.value.length);
          else if (d && "value" in d) statics.push(sname + "=");
          else statics.push(sname + ":acc");
        } catch (e) { statics.push(sname + ":err"); }
      }
      out.statics[n] = statics;
      if (v.prototype && typeof v.prototype === "object") {
        var methods = [];
        var pn = Object.getOwnPropertyNames(v.prototype).sort();
        for (var k = 0; k < pn.length; k++) {
          var mname = pn[k];
          if (mname === "constructor") continue;
          try {
            void v.prototype[mname];
            var pd = Object.getOwnPropertyDescriptor(v.prototype, mname);
            if (pd && typeof pd.value === "function") methods.push(mname + "/" + pd.value.length);
            else if (pd && (pd.get || pd.set)) methods.push(mname + ":acc");
            else methods.push(mname + "=");
          } catch (e) { methods.push(mname + ":err"); }
        }
        var syms = Object.getOwnPropertySymbols(v.prototype);
        for (var s = 0; s < syms.length; s++) methods.push(String(syms[s]));
        out.protos[n] = methods;
      }
    } else if (v && typeof v === "object") {
      // Namespace objects: Math, JSON, Reflect, Atomics...
      var members = [];
      var on = Object.getOwnPropertyNames(v).sort();
      for (var q = 0; q < on.length; q++) {
        try {
          void v[on[q]];
          var od = Object.getOwnPropertyDescriptor(v, on[q]);
          if (od && typeof od.value === "function") members.push(on[q] + "/" + od.value.length);
          else members.push(on[q] + "=");
        } catch (e) { members.push(on[q] + ":err"); }
      }
      out.statics[n] = members;
    }
    } catch (e) {
      // Rhino 1.9.2: `With.prototype` is a NativeWith that Object.getOwnPropertyNames rejects.
      out.unreadable.push(n);
    }
  }
  // Typed-array methods live on the shared %TypedArray%.prototype, not on each constructor's.
  try {
    var ta = Object.getPrototypeOf(Int8Array.prototype);
    var tm = [];
    var tn = Object.getOwnPropertyNames(ta).sort();
    for (var t = 0; t < tn.length; t++) {
      if (tn[t] === "constructor") continue;
      try {
        void ta[tn[t]];
        var td = Object.getOwnPropertyDescriptor(ta, tn[t]);
        if (td && typeof td.value === "function") tm.push(tn[t] + "/" + td.value.length);
        else if (td && (td.get || td.set)) tm.push(tn[t] + ":acc");
      } catch (e) {}
    }
    out.protos["TypedArray"] = tm;
  } catch (e) {}
  print(JSON.stringify(out));
}).call(this);
