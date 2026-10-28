// [symbol]
var f0 = Symbol;
var v2 = f0("");
// [builtin_object_instance]
const o4 = new WeakSet();
// [null]
const v5 = null;
// [update]
let v6 = v2;
v6 <<= v2;
__probe("step", v6, v5, o4, v2, f0);
// [builtin]
var f7 = encodeURIComponent;
__probe("step", f7, v6, v5, o4, v2, f0);
// [trivial_function]
var f8 = function () {
    return v5;
};
__probe("step", f8, f7, v6, v5, o4, v2);
